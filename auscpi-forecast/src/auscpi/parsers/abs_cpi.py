"""Flatten an ABS SDMX-JSON CPI payload into a tidy panel.

SDMX-JSON is compact rather than readable. The dimension values live once in
`structures[0].dimensions`, and each series is keyed by its *positions* in those
lists:

    "series": { "0:3:0:0:0": { "observations": { "0": [0.9], "1": [1.0] } } }

The key "0:3:0:0:0" indexes MEASURE.INDEX.TSEST.REGION.FREQ, and each observation
key indexes the TIME_PERIOD list. An observation value is a list whose first
element is the number and whose remaining elements are attribute positions
(OBS_STATUS, DECIMALS, OBS_COMMENT), so only element zero is data, and it may be
null for a suppressed or missing observation.

Target definitions were verified against the live API on 2026-07-30 rather than
assumed, because the obvious guess is wrong: index 10001 "All groups CPI" is
published ONLY as Original, while 999902 "Trimmed Mean" is published ONLY as
Seasonally Adjusted. Pairing the trimmed mean with Original returns an empty
series and no error, which is the worst possible outcome.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from auscpi.periods import period_end, period_freq

# Dimensions we flatten into columns, in the order the ABS declares them.
SERIES_DIMENSIONS = ("MEASURE", "INDEX", "TSEST", "REGION", "FREQ")

# MEASURE ids worth naming.
MEASURE_INDEX_NUMBER = "1"
MEASURE_CHANGE_PREV_PERIOD = "2"
MEASURE_CHANGE_PREV_YEAR = "3"

# INDEX ids worth naming.
INDEX_ALL_GROUPS = "10001"
INDEX_ALL_GROUPS_SA = "999901"
INDEX_TRIMMED_MEAN = "999902"

TSEST_ORIGINAL = "10"
TSEST_SEASONALLY_ADJUSTED = "20"

REGION_AUSTRALIA = "50"

#: The three targets in track_record.ForecastRecord, as (index, measure, tsest).
#: Verified against the API — see the module docstring on why tsest is not
#: uniform across these.
TARGETS: dict[str, tuple[str, str, str]] = {
    "headline_mom": (INDEX_ALL_GROUPS, MEASURE_CHANGE_PREV_PERIOD, TSEST_ORIGINAL),
    "headline_yoy": (INDEX_ALL_GROUPS, MEASURE_CHANGE_PREV_YEAR, TSEST_ORIGINAL),
    "trimmed_mean_yoy": (INDEX_TRIMMED_MEAN, MEASURE_CHANGE_PREV_YEAR, TSEST_SEASONALLY_ADJUSTED),
}

PANEL_COLUMNS = [
    "period",
    "period_end",
    "freq",
    "measure",
    "measure_name",
    "index_id",
    "index_name",
    "tsest",
    "tsest_name",
    "region",
    "region_name",
    "value",
]


def _structures(doc: dict[str, Any]) -> list[dict[str, Any]]:
    data = doc["data"]
    if "structures" in data:
        return data["structures"]
    return [data["structure"]]


def _dimension_lookup(structure: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(series dimensions in key order, TIME_PERIOD ids in observation order)."""
    series_dims = structure["dimensions"]["series"]
    obs_dims = structure["dimensions"]["observation"]
    time_dim = next(
        (d for d in obs_dims if d["id"] in ("TIME_PERIOD", "TIME")),
        obs_dims[0] if obs_dims else None,
    )
    if time_dim is None:
        raise ValueError("SDMX response has no observation dimension")
    return series_dims, [v["id"] for v in time_dim["values"]]


def parse_sdmx_json(doc: dict[str, Any]) -> pd.DataFrame:
    """Return one row per (series, period) observation.

    Empty input yields an empty frame with the right columns rather than raising,
    so a caller can concatenate several vintages without special-casing.
    """
    rows: list[tuple[Any, ...]] = []

    for structure in _structures(doc):
        series_dims, periods = _dimension_lookup(structure)
        dim_names = [d["id"] for d in series_dims]

        for dataset in doc["data"].get("dataSets", []):
            for series_key, series_obj in dataset.get("series", {}).items():
                positions = [int(p) for p in series_key.split(":")]
                if len(positions) != len(series_dims):
                    raise ValueError(
                        f"series key {series_key!r} has {len(positions)} parts but the "
                        f"structure declares {len(series_dims)} dimensions"
                    )
                coded = {}
                for name, dim, pos in zip(dim_names, series_dims, positions, strict=True):
                    value = dim["values"][pos]
                    coded[name] = (value.get("id"), value.get("name", ""))

                for obs_key, obs in series_obj.get("observations", {}).items():
                    period = periods[int(obs_key)]
                    # Element zero is the datum; the rest are attribute positions.
                    raw = obs[0] if obs else None
                    rows.append(
                        (
                            period,
                            period_end(period),
                            period_freq(period),
                            coded.get("MEASURE", ("", ""))[0],
                            coded.get("MEASURE", ("", ""))[1],
                            coded.get("INDEX", ("", ""))[0],
                            coded.get("INDEX", ("", ""))[1],
                            coded.get("TSEST", ("", ""))[0],
                            coded.get("TSEST", ("", ""))[1],
                            coded.get("REGION", ("", ""))[0],
                            coded.get("REGION", ("", ""))[1],
                            None if raw is None else float(raw),
                        )
                    )

    panel = pd.DataFrame(rows, columns=PANEL_COLUMNS)
    if panel.empty:
        return panel
    panel["value"] = panel["value"].astype("float64")
    return panel.sort_values(["index_id", "measure", "tsest", "period_end"]).reset_index(drop=True)


def target_series(panel: pd.DataFrame, target: str, *, region: str = REGION_AUSTRALIA) -> pd.Series:
    """One target as a float Series indexed by period id, oldest first.

    Raises on an unknown target name, and on a target that resolves to nothing —
    an empty series here means the (index, measure, tsest) triple is wrong, and
    silently returning it would poison every benchmark downstream.

    MISSING VALUES ARE PRESERVED as NaN rather than dropped, because a gap in the
    CPI is information and hiding it would misstate the sample. The monthly series
    is short and ragged — around 26 observations of m/m and fewer of the year-ended
    rates, since monthly collection only widened to the full basket recently — so
    `.dropna()` before passing this to anything in benchmarks.py, none of which
    handles NaN and all of which will happily return NaN if fed one.
    """
    if target not in TARGETS:
        raise KeyError(f"unknown target {target!r}; have {sorted(TARGETS)}")
    index_id, measure, tsest = TARGETS[target]

    hit = panel[
        (panel["index_id"] == index_id)
        & (panel["measure"] == measure)
        & (panel["tsest"] == tsest)
        & (panel["region"] == region)
    ]
    if hit.empty:
        raise ValueError(
            f"target {target!r} resolved to no observations "
            f"(index={index_id}, measure={measure}, tsest={tsest}, region={region})"
        )

    hit = hit.sort_values("period_end")
    return pd.Series(hit["value"].to_numpy(), index=hit["period"].to_numpy(), name=target)


def targets_frame(panel: pd.DataFrame, *, region: str = REGION_AUSTRALIA) -> pd.DataFrame:
    """The available targets side by side, one row per period.

    Targets missing from this vintage are skipped rather than fatal: the quarterly
    slice has no monthly m/m, and a caller asking for "whatever is here" should
    not have to know which.
    """
    columns: dict[str, pd.Series] = {}
    for target in TARGETS:
        try:
            columns[target] = target_series(panel, target, region=region)
        except ValueError:
            continue
    if not columns:
        return pd.DataFrame(columns=["period", *TARGETS])

    out = pd.DataFrame(columns)
    out.index.name = "period"
    out = out.reset_index()
    order = {p: period_end(p) for p in out["period"]}
    return out.sort_values("period", key=lambda s: s.map(order)).reset_index(drop=True)
