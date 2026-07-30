"""Published CPI expenditure weights, and the taxonomy that makes them safe to use.

The weights dataflow carries every level of the CPI structure in one series set,
each level summing to 100:

    All groups CPI          1 code    100
    groups                 11 codes   100
    sub-groups             33 codes   100
    expenditure classes    87 codes   100
                          ---------  -----
                          132 codes   400

So the first thing anyone does with this file — sum the weights — gives 400 unless
the level is known, and the level cannot be read off the code. 20001 "Food and
non-alcoholic beverages" is a group, 30002 "Bread and cereal products" is a
sub-group, and 126670 "Insurance and financial services" is a group again.

The level comes from the codelist, which is hierarchical:

    40005 Bread -> 30002 Bread and cereal products -> 20001 Food -> 10001 All groups

Depth from the root is the level, and `weights_at` refuses to return a level that
does not sum to 100, because a silently wrong weight set would misaggregate every
component forecast downstream without ever raising.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from auscpi.parsers.abs_cpi import REGION_AUSTRALIA, parse_sdmx_json

#: Measure 1, "Percentage contribution to the All groups CPI" — the share of the
#: basket. Measure 2 is a capital-city share that is uniformly 100 nationally, and
#: 3 is a points contribution on a different base.
MEASURE_PERCENT_OF_ALL_GROUPS = "1"

ALL_GROUPS_CODE = "10001"

#: Depth from the root of the codelist hierarchy.
LEVEL_NAMES = {
    0: "all_groups",
    1: "group",
    2: "sub_group",
    3: "expenditure_class",
}
EXPENDITURE_CLASS = "expenditure_class"

#: A level's weights must sum to 100. Tolerance covers published rounding only —
#: the observed national total is 100.0006 across 87 classes.
WEIGHT_SUM_TOLERANCE = 0.5

TAXONOMY_COLUMNS = ["index_id", "name", "parent_id", "depth", "level"]


def parse_taxonomy(doc: dict[str, Any]) -> pd.DataFrame:
    """Flatten the SDMX codelist into one row per code, with its depth and level.

    Depth is computed by walking parents rather than trusted from the payload, so a
    cycle or a dangling parent surfaces here instead of corrupting an aggregation.
    """
    codelists = doc.get("data", {}).get("codelists") or []
    if not codelists:
        raise ValueError("codelist payload contains no codelists")
    codes = codelists[0].get("codes") or []
    if not codes:
        raise ValueError("codelist payload contains no codes")

    parents = {c["id"]: c.get("parent") for c in codes}
    names = {c["id"]: c.get("name", "") for c in codes}

    def depth_of(code: str) -> int:
        seen: set[str] = set()
        steps = 0
        cursor = code
        while True:
            parent = parents.get(cursor)
            if not parent:
                return steps
            if parent in seen or parent == cursor:
                raise ValueError(f"cycle in the CPI hierarchy at {cursor!r}")
            if parent not in parents:
                raise ValueError(
                    f"code {cursor!r} has parent {parent!r}, which is not in the codelist"
                )
            seen.add(parent)
            cursor = parent
            steps += 1

    rows = [
        (
            code_id,
            names[code_id],
            parents[code_id],
            depth_of(code_id),
            LEVEL_NAMES.get(depth_of(code_id), "deeper"),
        )
        for code_id in parents
    ]
    frame = pd.DataFrame(rows, columns=TAXONOMY_COLUMNS)
    return frame.sort_values(["depth", "index_id"]).reset_index(drop=True)


def parse_weights(doc: dict[str, Any]) -> pd.DataFrame:
    """The weights data as a tidy panel.

    Reuses the price-side flattener: the weights dataflow has no TSEST dimension,
    which that function tolerates by leaving the column blank.
    """
    return parse_sdmx_json(doc)


def weights_panel(payload: dict[str, Any]) -> pd.DataFrame:
    """Join one collected snapshot into weights carrying their level.

    `payload` is what the collector stored: {"weights": ..., "taxonomy": ...}.
    """
    for part in ("weights", "taxonomy"):
        if part not in payload:
            raise ValueError(f"snapshot is missing {part!r}; expected a weights payload")

    weights = parse_weights(payload["weights"])
    taxonomy = parse_taxonomy(payload["taxonomy"])

    merged = weights.merge(
        taxonomy[["index_id", "parent_id", "depth", "level"]], on="index_id", how="left"
    )
    unmatched = merged["level"].isna().sum()
    if unmatched:
        missing = sorted(set(merged.loc[merged["level"].isna(), "index_id"]))[:5]
        raise ValueError(
            f"{unmatched} weight rows have no taxonomy entry (e.g. {missing}); "
            "the codelist and the weights are out of step"
        )
    return merged


def weights_at(
    payload: dict[str, Any],
    *,
    period: str | None = None,
    level: str = EXPENDITURE_CLASS,
    region: str = REGION_AUSTRALIA,
) -> pd.Series:
    """Weights for one level and period, indexed by expenditure code.

    Defaults to the expenditure classes at the newest reweight, which is the level
    a bottom-up aggregation needs. Raises when the selection does not sum to 100,
    since that means the level or the measure is wrong and every aggregate built on
    it would be quietly mis-scaled.
    """
    panel = weights_panel(payload)
    subset = panel[
        (panel["measure"] == MEASURE_PERCENT_OF_ALL_GROUPS)
        & (panel["region"] == region)
        & (panel["level"] == level)
        & panel["value"].notna()
    ]
    if subset.empty:
        raise ValueError(f"no weights for level={level!r} region={region!r}")

    period = period or str(subset.loc[subset["period_end"].idxmax(), "period"])
    subset = subset[subset["period"] == period]
    if subset.empty:
        raise ValueError(f"no weights for period {period!r} at level={level!r}")

    total = float(subset["value"].sum())
    if abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            f"weights for level={level!r} period={period!r} sum to {total:.4f}, not 100. "
            "The dataflow carries every hierarchy level at once, so this usually means "
            "the wrong level or measure was selected."
        )

    out = pd.Series(
        subset["value"].to_numpy(),
        index=subset["index_id"].to_numpy(),
        name=f"weight_{period}",
    )
    return out.sort_index()


def latest_reweight(payload: dict[str, Any]) -> str:
    """The newest period present in the weights snapshot."""
    panel = parse_weights(payload["weights"])
    if panel.empty:
        raise ValueError("weights snapshot parsed to zero rows")
    return str(panel.loc[panel["period_end"].idxmax(), "period"])
