"""Produce a forecast path from the built panel.

The year-ended targets are projected through the index level rather than carried
flat, which is the first thing here that is a model rather than a benchmark.

WHY PROJECT THE INDEX. A year-ended rate is a ratio of two index levels twelve
months apart, so at any horizon most of it is already observed. Forecasting from
data through June 2026, the July 2026 year-ended rate needs eleven monthly
movements the ABS has already published and exactly one that has not happened
yet. Those known movements — base effects — roll out of the annual window on a
fixed schedule, and that schedule is knowable today. Carrying the last year-ended
rate flat throws all of it away.

The fraction that is genuinely forecast grows with horizon: one twelfth at h=0,
all twelve numerator months by h=11, and beyond that the base is projected too.
So this decays into a naive rule at long horizons rather than pretending to skill
it does not have.

VALIDATED, not assumed. Recomputing year-ended rates from the published index
reproduces the published rates to within 0.048pp for headline and 0.045pp for the
trimmed mean, which is half of the 0.1 rounding step the ABS publishes at. The
identity holds, so the projection rests on arithmetic rather than a fitted
relationship.

WHY LEVEL-DERIVED MONTHLY RATES. The projection compounds monthly movements
derived from the index level, not the published m/m series, because the published
series is rounded to one decimal place. Compounding thirteen values each carrying
up to 0.05pp of rounding error accumulates a few tenths of drift, which is the
same order as the thing being forecast. The published m/m is still what
`headline_mom` forecasts and is scored on — that is the quantity of interest — but
it is the wrong input to a compounding calculation.

WHAT IS STILL WEAK:

  - The monthly driver is a 12-month mean. The base effects are exact arithmetic;
    the months not yet observed are a flat guess. See project_levels on why a
    seasonal-naive driver is not merely worse but degenerate here.
  - Consequently the path converges on the annualised recent mean at long
    horizons, which is Atkeson-Ohanian by another route. The value added over that
    benchmark is concentrated at short horizons, where the base dominates — which
    is the honest place to expect it.
  - THE DRIVER IS WORST EXACTLY WHERE AUSTRALIA NEEDS IT MOST. A flat mean
    replaces every future month with the same number, but Australian administered
    prices reset on 1 July — electricity determinations take effect then, and the
    July 2025 index rose 1.31% against a 0.31% average. So a path made now marks
    July 2026 down by about a point on the base effect, when much of that rise is
    an annual reset likely to recur. The true answer sits between this projection
    and the flat carry, and neither knows which. Reading the determinations is the
    only way to close that gap: it is what Phase 5 exists for, and it is why the
    administered-price calendar matters more than any refinement of this driver.
  - The sample is ~27 monthly index observations. Nothing here is statistically
    meaningful, and no claim of skill should be made from it.
  - No uncertainty bands. Quantiles by horizon are Phase 6.
  - Every point still carries a benchmark and a horizon, and the model is never
    logged against itself — see below.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandas as pd

from auscpi import benchmarks, release_calendar
from auscpi.build import load_panel
from auscpi.parsers.abs_cpi import (
    MEASURE_CHANGE_PREV_PERIOD,
    MEASURE_CHANGE_PREV_YEAR,
    MEASURE_INDEX_NUMBER,
    TARGETS,
    TSEST_ORIGINAL,
    TSEST_SEASONALLY_ADJUSTED,
    series_for,
    target_series,
)
from auscpi.periods import period_end
from auscpi.track_record import ForecastRecord, months_between

DEFAULT_HORIZONS = tuple(range(13))  # h=0..12

#: Index levels needed before a year-ended rate can be computed at all: twelve
#: months of base plus the month itself.
MIN_LEVELS_FOR_YEAR_ENDED = 13


def add_months(reference_month: str, n: int) -> str:
    year, month = (int(x) for x in reference_month.split("-"))
    index = (year * 12 + month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@dataclass(frozen=True)
class History:
    """Everything a path rule may read, for one target.

    `seasonally_adjusted` is carried because it changes which naive monthly rule
    is defensible: seasonal naive on an SA series is close to meaningless, the
    seasonality having already been taken out.
    """

    own: pd.Series  # the target's own published history
    mom: pd.Series  # published month-on-month companion
    level: pd.Series  # index level companion
    seasonally_adjusted: bool


#: A path rule: given the history, return one value per requested reference month.
PathRule = Callable[[History, Sequence[str]], list[float]]


def _clean(series: pd.Series) -> list[float]:
    """Observed values only, oldest first.

    Nothing in benchmarks.py handles NaN — every function would happily return
    NaN — and the year-ended series is ragged at the start, so dropping is
    mandatory rather than cosmetic.
    """
    return [float(v) for v in series.dropna().tolist()]


def _same_month_or_mean(series: pd.Series, months: Sequence[str]) -> list[float]:
    """Same calendar month, most recent year for which it was actually observed.

    Plain seasonal naive wants month M of last year, but at h>1 that month is
    itself unobserved — the h=12 point would need the h=0 forecast as an input. So
    walk back a year at a time until a real observation turns up, which keeps the
    rule strictly backward-looking at every horizon. With no same-month history at
    all, fall back to the mean so the path stays complete.
    """
    observed = series.dropna()
    out: list[float] = []
    for month in months:
        value: float | None = None
        for years_back in range(1, 12):
            candidate = add_months(month, -12 * years_back)
            if candidate in observed.index:
                value = float(observed.loc[candidate])
                break
        out.append(value if value is not None else benchmarks.mean_mom(_clean(series)))
    return out


def _monthly_from_levels(level: pd.Series) -> pd.Series:
    """Month-on-month per cent implied by the index level, unrounded."""
    observed = level.dropna()
    values = observed.to_numpy(dtype="float64")
    rates = (values[1:] / values[:-1] - 1.0) * 100.0
    return pd.Series(rates, index=observed.index[1:], name="mom_from_level")


def _months_between_inclusive(start: str, end: str) -> list[str]:
    out: list[str] = []
    cursor = start
    while period_end(cursor) <= period_end(end):
        out.append(cursor)
        cursor = add_months(cursor, 1)
    return out


def project_levels(hist: History, through: str) -> dict[str, float]:
    """Observed index levels, extended to `through` by compounding a monthly path.

    THE DRIVER IS A MEAN, AND SEASONAL NAIVE WOULD BE WRONG HERE — not merely
    worse, but degenerate. If the projected twelve monthly rates replicate the
    previous twelve, then

        level(m) = level(T) * level(m-12) / level(T-12)

    so level(m) / level(m-12) collapses to level(T) / level(T-12), a constant. A
    seasonal-naive driver therefore reproduces the last observed year-ended rate at
    every horizon and the projection silently becomes a year-ended random walk,
    cancelling exactly the base effects it was built to use. Verified by test.

    Using the mean of the last twelve implied rates instead, the projected months
    grow at a constant rate while the DENOMINATOR keeps stepping through real
    history, so known months rolling out of the annual window move the answer.
    That is the base effect, and it is the part of a year-ended forecast that is
    genuinely knowable in advance.

    Seasonal factors on top of the mean would be the next refinement, giving
    sensible within-year shape while keeping the annual aggregate. Not attempted:
    with roughly 27 monthly observations there are about two per calendar month, so
    the estimated factors would be close to noise. `hist.seasonally_adjusted`
    records which series would need them at all.

    Returned as one mapping so callers cannot accidentally treat a projected level
    as an observation without noticing which months they asked for.
    """
    level = hist.level.dropna()
    if len(level) < MIN_LEVELS_FOR_YEAR_ENDED:
        raise ValueError(
            f"need {MIN_LEVELS_FOR_YEAR_ENDED} index levels to compute a year-ended "
            f"rate, have {len(level)}"
        )

    combined = {str(k): float(v) for k, v in level.items()}
    last_observed = str(level.index[-1])
    if period_end(through) <= period_end(last_observed):
        return combined

    future = _months_between_inclusive(add_months(last_observed, 1), through)
    implied = _monthly_from_levels(level)
    rates = [benchmarks.mean_mom(_clean(implied))] * len(future)

    running = combined[last_observed]
    for month, rate in zip(future, rates, strict=True):
        running *= 1.0 + rate / 100.0
        combined[month] = running
    return combined


def _index_projection(hist: History, months: Sequence[str]) -> list[float]:
    """Year-ended rate from projected index levels, so base effects are exact."""
    horizon_end = max(months, key=period_end)
    levels = project_levels(hist, horizon_end)

    out: list[float] = []
    for month in months:
        base = add_months(month, -12)
        if month not in levels or base not in levels:
            raise ValueError(
                f"cannot compute a year-ended rate for {month}: index level for "
                f"{base if base not in levels else month} is unavailable"
            )
        out.append((levels[month] / levels[base] - 1.0) * 100.0)
    return out


def _flat(fn: Callable[[list[float]], float], *, use_mom: bool) -> PathRule:
    """Wrap a scalar benchmark into a constant path across every horizon."""

    def rule(hist: History, months: Sequence[str]) -> list[float]:
        history = _clean(hist.mom if use_mom else hist.own)
        return [fn(history)] * len(months)

    return rule


def _seasonal_naive(hist: History, months: Sequence[str]) -> list[float]:
    return _same_month_or_mean(hist.own, months)


RULES: dict[str, PathRule] = {
    # Varies with horizon.
    "index_projection": _index_projection,
    "seasonal_naive": _seasonal_naive,
    # Flat paths, for benchmarks.
    "atkeson_ohanian": _flat(benchmarks.atkeson_ohanian, use_mom=True),
    "random_walk": _flat(benchmarks.random_walk_yoy, use_mom=False),
    "mean_mom": _flat(benchmarks.mean_mom, use_mom=False),
    "target_midpoint": _flat(lambda _h: benchmarks.target_midpoint(), use_mom=False),
}

#: Rules that produce a year-ended rate and are meaningless for a m/m target.
YEAR_ENDED_ONLY_RULES = frozenset({"index_projection"})

#: (model, benchmark) per target. Always two different rules — see below.
DEFAULT_PAIRS: dict[str, tuple[str, str]] = {
    "headline_mom": ("seasonal_naive", "mean_mom"),
    "headline_yoy": ("index_projection", "random_walk"),
    "trimmed_mean_yoy": ("index_projection", "random_walk"),
}

#: Companions each target needs: (index_id, tsest, seasonally_adjusted).
_COMPANIONS: dict[str, tuple[str, str, bool]] = {
    "headline_mom": ("10001", TSEST_ORIGINAL, False),
    "headline_yoy": ("10001", TSEST_ORIGINAL, False),
    "trimmed_mean_yoy": ("999902", TSEST_SEASONALLY_ADJUSTED, True),
}


@dataclass
class Path:
    target: str
    model: str
    benchmark: str
    origin: str  # "YYYY-MM" the path was made in
    information_cutoff: str  # newest observed period, the look-ahead audit trail
    records: list[ForecastRecord]


def _origin_month(today: date) -> str:
    return f"{today.year:04d}-{today.month:02d}"


def build_history(panel: pd.DataFrame, target: str) -> History:
    index_id, tsest, seasonally_adjusted = _COMPANIONS[target]
    return History(
        own=target_series(panel, target),
        mom=series_for(panel, index_id, MEASURE_CHANGE_PREV_PERIOD, tsest, name=f"{target}_mom"),
        level=series_for(panel, index_id, MEASURE_INDEX_NUMBER, tsest, name=f"{target}_level"),
        seasonally_adjusted=seasonally_adjusted,
    )


def _check_rule(name: str, target: str) -> None:
    if name not in RULES:
        raise KeyError(f"unknown rule {name!r}; have {sorted(RULES)}")
    if name in YEAR_ENDED_ONLY_RULES and TARGETS[target][1] != MEASURE_CHANGE_PREV_YEAR:
        raise ValueError(
            f"rule {name!r} produces a year-ended rate and cannot forecast {target!r}, "
            "which is a month-on-month series"
        )


def forecast_path(
    target: str,
    *,
    model: str | None = None,
    benchmark: str | None = None,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    as_at: datetime | None = None,
    today: date | None = None,
    panel: pd.DataFrame | None = None,
) -> Path:
    """Build (but do not log) a full path for one target.

    `as_at` is threaded to the panel load so a backtest reads the vintage that
    existed then (rule 3). `information_cutoff` records the newest period actually
    used, which is what makes look-ahead detectable after the fact.
    """
    if target not in TARGETS:
        raise KeyError(f"unknown target {target!r}; have {sorted(TARGETS)}")

    default_model, default_benchmark = DEFAULT_PAIRS[target]
    model = model or default_model
    benchmark = benchmark or default_benchmark
    _check_rule(model, target)
    _check_rule(benchmark, target)
    if model == benchmark:
        raise ValueError(
            f"model and benchmark are both {model!r}; skill would be zero by "
            "construction. Pick a different benchmark."
        )

    frame = panel if panel is not None else load_panel("abs_cpi_monthly", as_at=as_at)
    hist = build_history(frame, target)

    observed = hist.own.dropna()
    if observed.empty:
        raise ValueError(f"target {target!r} has no observed values to forecast from")
    # The index runs ahead of the published year-ended series, and the projection
    # reads levels, so the cutoff is whichever input actually extends furthest.
    cutoff = max([str(observed.index[-1]), str(hist.level.dropna().index[-1])], key=period_end)

    origin = _origin_month(today or datetime.now(UTC).date())
    months = [add_months(origin, h) for h in horizons]

    points = RULES[model](hist, months)
    bench_points = RULES[benchmark](hist, months)

    now = datetime.now(UTC).isoformat()
    records = []
    for h, month, point, bench in zip(horizons, months, points, bench_points, strict=True):
        released = release_calendar.release_date(month)
        records.append(
            ForecastRecord(
                made_at=now,
                reference_month=month,
                horizon_months=months_between(origin, month),
                target=target,
                point=round(float(point), 3),
                # Not made_at: the newest observation, so a backtest that used a
                # later vintage than it claims is visible in the log.
                information_cutoff=cutoff,
                model=model,
                model_version="v1-index-projection" if model == "index_projection" else "v0-naive",
                benchmark_name=benchmark,
                benchmark_point=round(float(bench), 3),
                note=(
                    f"h={h}; prints {released.isoformat()}"
                    if released
                    else f"h={h}; release date not in calendar"
                ),
            )
        )

    return Path(
        target=target,
        model=model,
        benchmark=benchmark,
        origin=origin,
        information_cutoff=cutoff,
        records=records,
    )


def forecast_all(
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    as_at: datetime | None = None,
    today: date | None = None,
) -> list[Path]:
    """A path for every target the current vintage can support.

    A target absent from this vintage is skipped rather than fatal — the quarterly
    slice has no monthly m/m, and a caller wanting "everything available" should
    not have to know which.
    """
    frame = load_panel("abs_cpi_monthly", as_at=as_at)
    paths: list[Path] = []
    for target in TARGETS:
        try:
            paths.append(
                forecast_path(target, horizons=horizons, as_at=as_at, today=today, panel=frame)
            )
        except ValueError:
            continue
    return paths
