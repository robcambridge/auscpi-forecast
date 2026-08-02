"""Error distribution by horizon: how wrong the path usually is, and which way.

WHY THIS IS THE PRODUCT, per docs/ROADMAP.md Phase 6. A point forecast with no band
invites confident wrong sizing. "CPI will be 3.2%" and "CPI will be 3.2%, and we are
routinely out by half a point at this horizon" are different products, and only the
second is honest about a model built on 27 monthly observations.

ERRORS AT DIFFERENT HORIZONS ARE DIFFERENT OBJECTS and are never pooled here. The
h=0 error of a year-ended projection is one unobserved month against eleven published
ones; the h=12 error is twelve unobserved months and a projected base. Reporting one
band across horizons would understate the long end and overstate the short.

WHAT THE SAMPLE ACTUALLY SUPPORTS, WHICH IS LESS THAN THE PHASE ASKS FOR. The monthly
CPI gives about 27 index observations, and a year-ended target needs thirteen levels
before a single forecast exists, so the usable origins run to fourteen. Every extra
horizon costs one:

    h=0   14 origins      h=6   8 origins      h=12   2 origins

At h=12 a "10th percentile" is an order statistic over two numbers. `MIN_ERRORS_FOR_
QUANTILES` therefore refuses to emit quantiles rather than emitting arithmetic that
looks like a distribution. This will loosen on its own as the track record grows; it
is a sample problem, not a method problem.

THE MEASUREMENT THAT MATTERS MORE THAN THE BAND. The year-ended models are BIASED
LOW, increasingly with horizon:

    headline_yoy       bias  -0.00 at h=0   -0.65 at h=6   -1.63 at h=12
    trimmed_mean_yoy   bias  -0.01 at h=0   -0.17 at h=6   -0.48 at h=12
    headline_mom       bias  +0.00 at h=0   -0.04 at h=6   +0.31 at h=12

The month-on-month model is unbiased at every horizon; both year-ended models
under-forecast, and the gap widens roughly linearly. That is the documented weakness
of the flat-trend driver — the projection converges on the annualised recent trend, so
in a sample where inflation ran above recent trend it reads low — and it is now
measured rather than described.

IT IS NOT CORRECTED, DELIBERATELY. Fourteen overlapping origins spanning one inflation
episode cannot distinguish a structural bias from a sample that happened to rise.
Subtracting a bias fitted on that would be curve-fitting the recent past into the
forecast, and it would look like an improvement on exactly the data that produced it.
The bias is reported so a reader can apply judgement; the model is left alone.

NOT A REAL-TIME BACKTEST. The panel is today's vintage truncated by period, not the
vintage as it stood at each origin, because the ABS snapshots only start in 2026-07.
The ABS revises seasonally adjusted series, so a true as-at backtest would be
slightly different and slightly worse. Bands from here are a lower bound on
uncertainty.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from auscpi.forecast import DEFAULT_PAIRS, RULES, add_months, build_history
from auscpi.parsers.abs_cpi import target_series

#: Errors needed at a horizon before quantiles are reported. Below this the order
#: statistics are arithmetic on a handful of overlapping points, and printing them
#: would dress up ignorance as a distribution.
MIN_ERRORS_FOR_QUANTILES = 8

#: Percentiles carried on ForecastRecord.
QUANTILE_LEVELS = (0.10, 0.25, 0.75, 0.90)


@dataclass(frozen=True)
class HorizonErrors:
    """What the model did at one horizon, across every evaluable origin."""

    horizon_months: int
    n: int
    bias: float  # mean signed error, point minus actual
    mean_absolute: float
    #: Percentile -> signed error. Empty when the sample is too small to support it.
    quantiles: dict[float, float] = field(default_factory=dict)

    @property
    def estimable(self) -> bool:
        return bool(self.quantiles)


def error_sample(
    panel: pd.DataFrame,
    target: str,
    *,
    model: str | None = None,
    horizons: Sequence[int] | None = None,
) -> pd.DataFrame:
    """Signed errors by horizon, from re-forecasting at every evaluable origin.

    Each origin sees only data up to and including its own cutoff, so the forecast
    never reads a month it is about to be scored on. That is the truncation the
    honesty of everything below rests on.
    """
    model = model or DEFAULT_PAIRS[target][0]
    span = list(horizons) if horizons is not None else list(range(13))
    actual = target_series(panel, target).dropna()
    periods = sorted(str(p) for p in panel["period"].unique())

    rows: list[dict[str, float | int]] = []
    for cutoff in periods:
        known = panel[panel["period"] <= cutoff]
        origin = add_months(cutoff, 1)
        months = [add_months(origin, h) for h in span]
        try:
            points = RULES[model](build_history(known, target), months)
        except (ValueError, KeyError):
            continue
        for horizon, month, point in zip(span, months, points, strict=True):
            if month in actual.index and pd.notna(actual[month]) and pd.notna(point):
                rows.append(
                    {"horizon_months": horizon, "error": float(point) - float(actual[month])}
                )
    return pd.DataFrame(rows)


def horizon_errors(errors: pd.DataFrame) -> list[HorizonErrors]:
    """Summarise an error sample, refusing quantiles where the sample is too thin."""
    if errors.empty:
        return []
    out: list[HorizonErrors] = []
    for horizon, group in errors.groupby("horizon_months"):
        series = group["error"]
        quantiles = (
            {level: float(series.quantile(level)) for level in QUANTILE_LEVELS}
            if len(series) >= MIN_ERRORS_FOR_QUANTILES
            else {}
        )
        out.append(
            HorizonErrors(
                horizon_months=int(horizon),
                n=len(series),
                bias=float(series.mean()),
                mean_absolute=float(series.abs().mean()),
                quantiles=quantiles,
            )
        )
    return out


def bands_for(
    point: float, errors: HorizonErrors
) -> dict[str, float | None]:
    """Turn a point forecast into p10/p25/p75/p90, or Nones if not estimable.

    The band is the point plus the error quantiles, so it inherits any bias in the
    model rather than being centred on the point. That is intentional: if the model
    reads low, an honest band should sit low too, and hiding that inside a symmetric
    interval would misreport where the outcome is likely to fall.

    Note the sign. An error is point minus actual, so a NEGATIVE error means the model
    read low and the actual was higher — the 90th percentile of the error maps to the
    LOW end of the outcome band.
    """
    if not errors.estimable:
        return {"p10": None, "p25": None, "p75": None, "p90": None}
    return {
        "p10": round(point - errors.quantiles[0.90], 3),
        "p25": round(point - errors.quantiles[0.75], 3),
        "p75": round(point - errors.quantiles[0.25], 3),
        "p90": round(point - errors.quantiles[0.10], 3),
    }
