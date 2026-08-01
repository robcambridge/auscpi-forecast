"""Swap component forecasts into the headline path on published weights.

The top-down model projects the All groups index and implicitly forecasts every
expenditure class inside it. A component model — the rent roll-through, later fuel
and food — claims to know one of those classes better. This is where that claim is
cashed: take the top-down headline, subtract what it implied for the class, add
what the component says, weight the difference by the class's published share.

    headline_adjusted = headline + sum over classes of w_i * (override_i - baseline_i)

WHY THE BASELINE IS COMPUTED RATHER THAN ASSUMED ZERO. It is tempting to add
`w * override` and be done. That double-counts: the headline projection already
contains a rents forecast, arrived at by projecting the All groups index. Only the
DIFFERENCE between the two views is new information, so the class's own top-down
projection has to be produced explicitly and subtracted. `component_baseline` runs
exactly the rule the headline uses, on the class's own index, so the two are
consistent by construction.

THIS IS FIRST-ORDER, AND THE ERROR IS SMALLER THAN THE EFFECT. A year-ended rate is
not exactly a weighted mean of component year-ended rates: the exact identity runs
through index levels with expenditure shares that drift between reweights. Treating
it as a weighted mean is right for a Laspeyres index at its base and approximately
right after, and the residual is second-order. Stated because it stops mattering
only while the effects being measured are large, and right now they are not.

WHAT THIS MEASURED, AND IT IS THE OPPOSITE OF ENCOURAGING. Swapping the rent
roll-through into the headline moves it by at most 0.072pp at h=12 and by 0.02-0.05pp
across most of the path — BELOW the 0.1pp step the ABS rounds its published rates to.
The arithmetic is not subtle: rents are 6.613% of the basket, the two rent views
differ by at most about 1.1pp, and 0.066 x 1.1 is 0.07.

The roadmap has called the rent roll-through the main forecasting edge. On this
evidence it cannot be, on its own — not because the component is wrong but because
one class at 6.6% cannot move a headline by a publishable amount unless it disagrees
with the naive baseline by several points. Two things follow:

  - The edge has to be the COMBINATION. Phase 3 scopes fuel, food, rents and energy
    at 35-40% of the basket. Four components each contributing 0.05pp is still only
    0.2pp; components that disagree sharply with the baseline are worth more than
    components that are merely well modelled.
  - A component is worth building for its own sake too. Rents at 6.6% is a
    forecastable series in its own right and the machinery here reports its
    contribution separately, so a component that is right for the right reason stays
    visible even when its headline effect rounds away.

Nothing here is wired into `auscpi forecast` or the public log. A swap that moves the
answer by less than the rounding step would change the logged model without changing
any published number, which is cost without benefit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from auscpi.forecast import RULES, History
from auscpi.parsers.abs_cpi import (
    MEASURE_CHANGE_PREV_PERIOD,
    MEASURE_CHANGE_PREV_YEAR,
    MEASURE_INDEX_NUMBER,
    TSEST_ORIGINAL,
    TSEST_SEASONALLY_ADJUSTED,
    series_for,
)

#: Weights are published as per cent of the All groups basket.
WEIGHT_TOTAL = 100.0


@dataclass(frozen=True)
class ComponentSwap:
    """One class's top-down baseline against a component model's view of it."""

    index_id: str
    label: str
    baseline: Mapping[str, float]  # reference month -> year-ended, from the top-down rule
    override: Mapping[str, float]  # reference month -> year-ended, from the component


@dataclass(frozen=True)
class Contribution:
    """What one swap did to the headline, in percentage points."""

    index_id: str
    label: str
    weight: float
    reference_month: str
    baseline: float
    override: float

    @property
    def effect_pp(self) -> float:
        return self.weight / WEIGHT_TOTAL * (self.override - self.baseline)


def component_history(panel: pd.DataFrame, index_id: str) -> History:
    """A History for any expenditure class, not just the three logged targets.

    `forecast.build_history` covers the targets and knows their seasonally adjusted
    counterparts by name. Components are chosen at runtime, so the adjusted series is
    looked up optimistically and left as None when the class does not have one — in
    which case the seasonal projection falls back to the flat one rather than
    inventing a factor.
    """
    try:
        sa_level = series_for(
            panel,
            index_id,
            MEASURE_INDEX_NUMBER,
            TSEST_SEASONALLY_ADJUSTED,
            name=f"{index_id}_sa_level",
        )
    except ValueError:
        sa_level = None

    return History(
        own=series_for(
            panel, index_id, MEASURE_CHANGE_PREV_YEAR, TSEST_ORIGINAL, name=f"{index_id}_yoy"
        ),
        mom=series_for(
            panel, index_id, MEASURE_CHANGE_PREV_PERIOD, TSEST_ORIGINAL, name=f"{index_id}_mom"
        ),
        level=series_for(
            panel, index_id, MEASURE_INDEX_NUMBER, TSEST_ORIGINAL, name=f"{index_id}_level"
        ),
        seasonally_adjusted=False,
        sa_level=sa_level,
    )


def component_baseline(
    panel: pd.DataFrame,
    index_id: str,
    months: Sequence[str],
    *,
    rule: str = "seasonal_index_projection",
) -> dict[str, float]:
    """What the top-down rule implies for one class, so the swap nets against it."""
    if rule not in RULES:
        raise KeyError(f"unknown rule {rule!r}; have {sorted(RULES)}")
    points = RULES[rule](component_history(panel, index_id), list(months))
    return dict(zip(months, points, strict=True))


def load_weights() -> dict[str, float]:
    """Published expenditure-class weights, as per cent summing to 100.

    Read from the curated CSV that `weights_at` already validated as summing to 100 —
    the level distinction matters here more than anywhere, because overriding a group
    and one of its classes would count the class twice.
    """
    from auscpi.config import settings

    path = settings.curated_dir / "abs_cpi_weights_expenditure_classes.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"no weights at {path}. Run `auscpi build` after collecting abs_cpi_weights."
        )
    frame = pd.read_csv(path)
    return {str(row.index_id): float(row.weight) for row in frame.itertuples()}


def swap_components(
    headline: Mapping[str, float],
    swaps: Sequence[ComponentSwap],
    weights: Mapping[str, float],
) -> tuple[dict[str, float], list[Contribution]]:
    """Apply component views to a headline path. Returns the path and the workings.

    Every swap must name a class present in the expenditure-class weights, and no
    class twice. Both are refused rather than warned about: a class that is really a
    group would be weighted at its group share, and a repeated class would be counted
    twice, and neither shows up as anything but a slightly wrong number.
    """
    seen: set[str] = set()
    for swap in swaps:
        if swap.index_id not in weights:
            raise KeyError(
                f"{swap.index_id!r} is not a published expenditure class; it may be a "
                f"group, whose weight would double-count its children"
            )
        if swap.index_id in seen:
            raise ValueError(f"class {swap.index_id!r} swapped twice; it would count twice")
        seen.add(swap.index_id)

    adjusted = dict(headline)
    contributions: list[Contribution] = []
    for swap in swaps:
        weight = weights[swap.index_id]
        for month in headline:
            if month not in swap.baseline or month not in swap.override:
                continue
            contribution = Contribution(
                index_id=swap.index_id,
                label=swap.label,
                weight=weight,
                reference_month=month,
                baseline=float(swap.baseline[month]),
                override=float(swap.override[month]),
            )
            adjusted[month] += contribution.effect_pp
            contributions.append(contribution)

    return adjusted, contributions
