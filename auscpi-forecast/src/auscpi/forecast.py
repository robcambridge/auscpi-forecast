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
same order as the thing being forecast.

ONE PROJECTED INDEX, THREE TARGETS. The m/m path is read off that same projected
level series, as level(m) / level(m-1). It used to be a separate seasonal-naive
rule, and the two disagreed about the same month: for July 2026 they said +1.30%
and +0.70%, a 0.60pp contradiction sitting in the public log. Two rules over one
index cannot both be right, and nothing in the output told a reader which number
the model actually believed. Derived from one projection they are arithmetically
the same forecast — compound the m/m path across the twelve months of an annual
window and that window's year-ended point comes back out — so the targets can be
read together.

The residual gap is rounding, and it is one-sided. `headline_mom` is scored
against the published m/m, which the ABS rounds to one decimal place, so a
level-derived point carries up to 0.05pp of error that is measurement rather than
model. That is a floor on measured accuracy at h=0 and negligible against the
errors at any longer horizon. Rounding the forecast to make the numbers agree
would hide the model's actual view to flatter the scorecard.

WHAT IS STILL WEAK:

  - The monthly driver is a flat trend with the published seasonal shape put back
    on, and every target now inherits it. The base effects are exact arithmetic;
    the months not yet observed are a trend guess. See project_levels on why a
    seasonal-naive driver is not merely worse but degenerate here.
  - Coherence is not accuracy. Three targets that agree can be wrong together, and
    a single driver means one bad trend estimate now moves all of them the same
    way. What it buys is that an error is attributable to the projection rather
    than to an unexplained disagreement between rules.
  - Consequently the path converges on the annualised recent trend at long
    horizons, which is Atkeson-Ohanian by another route. The seasonal correction
    also cancels out there, appearing in both numerator and denominator once the
    base is itself projected — measured live, it moves h=0 by +0.40pp and h=12 by
    -0.03pp. The value added is concentrated at short horizons where the base is
    observed, which is the honest place to expect it.
  - WHAT REMAINS AROUND 1 JULY IS THE YEAR-SPECIFIC PART, AND IT IS LARGE.
    Seasonality is now taken from the published adjustment (see seasonal_factors),
    which handles the *recurring* calendar shape. It does not handle the rest, and
    decomposing July 2025 shows how much rest there is: the Original index rose
    1.31%, of which roughly 0.41pp is the recurring July factor and about 0.90pp
    was that year's own administered movement. July 2024 was adjusted -0.16%. So
    the non-seasonal July component swung by more than a point between two
    consecutive years, and no seasonal factor can know which way it goes next.
    Only the determinations can — announced months ahead, with quantified effects
    and effective dates. That is Phase 5, and it remains worth more than any
    further refinement of this driver.
  - The sample is ~27 monthly index observations, so the seasonal factors rest on
    about two observations per calendar month. They are the ABS's estimates rather
    than ours, which is why they are usable at all, but nothing here is
    statistically meaningful and no claim of skill should be made from it.
  - No uncertainty bands. Quantiles by horizon are Phase 6.
  - Every point still carries a benchmark and a horizon, and the model is never
    logged against itself — see below.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import fmean

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

#: Index levels needed before the projection can run: twelve months to average the
#: driver over, plus the anchor it compounds from. That is also exactly what a
#: year-ended rate needs — twelve months of base plus the month itself.
MIN_LEVELS_FOR_PROJECTION = 13


def add_months(reference_month: str, n: int) -> str:
    year, month = (int(x) for x in reference_month.split("-"))
    index = (year * 12 + month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@dataclass(frozen=True)
class History:
    """Everything a path rule may read, for one target.

    `seasonally_adjusted` decides which monthly rule is defensible: on a series the
    ABS has already adjusted there is no seasonality left to model, so a flat mean
    is right and a seasonal correction would double-count.
    """

    own: pd.Series  # the target's own published history
    mom: pd.Series  # published month-on-month companion
    level: pd.Series  # index level companion
    seasonally_adjusted: bool
    #: The ABS's own seasonally adjusted counterpart of `level`, when the target is
    #: an Original series. None when the target is already adjusted, or when the
    #: counterpart is missing from the vintage.
    sa_level: pd.Series | None = None


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


def _month_number(period: str) -> int | None:
    """Calendar month of a monthly period id, or None if it is not monthly."""
    _, _, rest = period.partition("-")
    return None if rest[:1].upper() == "Q" else int(rest)


def seasonal_factors(original: pd.Series, sa: pd.Series) -> dict[int, float]:
    """Original / seasonally-adjusted, averaged per calendar month.

    This is the ABS's own seasonal estimate, not one fitted here. Fitting
    seasonality to ~27 observations would be close to noise, whereas the ABS
    adjusts with far more information; the ratio of the two published series
    exposes it directly.

    The factor also absorbs the base difference between the two series — they are
    referenced differently, so the ratio sits near 0.96 rather than 1.0 — which is
    why it is applied multiplicatively to a projected adjusted level rather than
    being interpreted as a percentage on its own.
    """
    shared = [p for p in original.index if p in sa.index]
    grouped: dict[int, list[float]] = {}
    for period in shared:
        month = _month_number(str(period))
        adjusted = float(sa.loc[period])
        if month is None or not adjusted:
            continue
        grouped.setdefault(month, []).append(float(original.loc[period]) / adjusted)
    return {month: fmean(values) for month, values in grouped.items()}


def _months_between_inclusive(start: str, end: str) -> list[str]:
    out: list[str] = []
    cursor = start
    while period_end(cursor) <= period_end(end):
        out.append(cursor)
        cursor = add_months(cursor, 1)
    return out


def project_levels(hist: History, through: str, *, seasonal: bool = False) -> dict[str, float]:
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
    if len(level) < MIN_LEVELS_FOR_PROJECTION:
        raise ValueError(
            f"need {MIN_LEVELS_FOR_PROJECTION} index levels to project the index, "
            f"have {len(level)}"
        )

    combined = {str(k): float(v) for k, v in level.items()}
    last_observed = str(level.index[-1])
    if period_end(through) <= period_end(last_observed):
        return combined

    future = _months_between_inclusive(add_months(last_observed, 1), through)

    if seasonal:
        projected = _project_via_seasonal_adjustment(hist, last_observed, future)
        if projected is not None:
            combined.update(projected)
            return combined

    implied = _monthly_from_levels(level)
    rates = [benchmarks.mean_mom(_clean(implied))] * len(future)

    running = combined[last_observed]
    for month, rate in zip(future, rates, strict=True):
        running *= 1.0 + rate / 100.0
        combined[month] = running
    return combined


def _project_via_seasonal_adjustment(
    hist: History, last_observed: str, future: Sequence[str]
) -> dict[str, float] | None:
    """Project the adjusted level, then put the seasonal shape back on.

    Growing the seasonally adjusted series at a flat rate is the defensible naive
    step — that series has no seasonality left to get wrong — and multiplying by
    the published factor restores the calendar pattern the Original series has.
    Compounding a flat rate on the Original series instead implicitly assumes every
    future month carries average seasonality, which is what this fixes.

    Returns None when the inputs cannot support it, so the caller falls back to the
    flat projection rather than inventing a factor.
    """
    if hist.sa_level is None or hist.seasonally_adjusted:
        return None

    adjusted = hist.sa_level.dropna()
    if adjusted.empty:
        return None
    # The adjusted series drives the projection, so it must reach the anchor. If it
    # lags the Original, extrapolating from a stale anchor would be worse than the
    # flat fallback.
    if str(adjusted.index[-1]) != last_observed:
        return None

    factors = seasonal_factors(hist.level.dropna(), adjusted)
    needed = {_month_number(m) for m in future}
    if not needed.issubset(factors.keys()):
        return None

    implied = _monthly_from_levels(adjusted)
    if len(implied.dropna()) < 12:
        return None
    rate = benchmarks.mean_mom(_clean(implied))

    out: dict[str, float] = {}
    running = float(adjusted.loc[last_observed])
    for month in future:
        running *= 1.0 + rate / 100.0
        out[month] = running * factors[_month_number(month)]
    return out


def _year_ended_from_levels(hist: History, months: Sequence[str], *, seasonal: bool) -> list[float]:
    horizon_end = max(months, key=period_end)
    levels = project_levels(hist, horizon_end, seasonal=seasonal)

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


def _month_on_month_from_levels(
    hist: History, months: Sequence[str], *, seasonal: bool
) -> list[float]:
    """Monthly rate implied by the same projected levels the year-ended rules read.

    Requesting the projection through the furthest month also produces every month
    before it, so the immediately preceding level is always available — including
    for h=0, whose predecessor is the last observation rather than a projection.
    """
    horizon_end = max(months, key=period_end)
    levels = project_levels(hist, horizon_end, seasonal=seasonal)

    out: list[float] = []
    for month in months:
        previous = add_months(month, -1)
        if month not in levels or previous not in levels:
            raise ValueError(
                f"cannot compute a monthly rate for {month}: index level for "
                f"{previous if previous not in levels else month} is unavailable"
            )
        out.append((levels[month] / levels[previous] - 1.0) * 100.0)
    return out


def _index_projection(hist: History, months: Sequence[str]) -> list[float]:
    """Year-ended rate from projected index levels, so base effects are exact."""
    return _year_ended_from_levels(hist, months, seasonal=False)


def _seasonal_index_projection(hist: History, months: Sequence[str]) -> list[float]:
    """As above, but the projected months carry the published seasonal shape.

    Falls back to the flat projection when the adjusted counterpart cannot support
    it, so the rule is always safe to select.
    """
    return _year_ended_from_levels(hist, months, seasonal=True)


def _seasonal_index_mom(hist: History, months: Sequence[str]) -> list[float]:
    """Month-on-month from the same projection `seasonal_index_projection` uses.

    Seasonality matters more here than it does for the year-ended target. A July
    factor moves a year-ended rate once and then leaves it, since both ends of the
    annual window eventually carry it; on a monthly rate it is the whole story, and
    a flat driver would forecast the same rate for every month of the year.

    `seasonal=True` is safe to hardcode: project_levels declines the seasonal step
    on an already-adjusted series, where re-applying a factor would double-count,
    and falls back to the flat projection when the vintage has no adjusted
    counterpart to take factors from.
    """
    return _month_on_month_from_levels(hist, months, seasonal=True)


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
    "seasonal_index_projection": _seasonal_index_projection,
    "seasonal_index_mom": _seasonal_index_mom,
    "index_projection": _index_projection,
    "seasonal_naive": _seasonal_naive,
    # Flat paths, for benchmarks.
    "atkeson_ohanian": _flat(benchmarks.atkeson_ohanian, use_mom=True),
    "random_walk": _flat(benchmarks.random_walk_yoy, use_mom=False),
    "mean_mom": _flat(benchmarks.mean_mom, use_mom=False),
    "target_midpoint": _flat(lambda _h: benchmarks.target_midpoint(), use_mom=False),
}

#: Rules that produce a year-ended rate and are meaningless for a m/m target.
YEAR_ENDED_ONLY_RULES = frozenset({"index_projection", "seasonal_index_projection"})

#: And the reverse. Both guards exist because the two families read the same
#: projected index and differ only in which pair of levels they divide, so a
#: mismatched rule returns a plausible-looking number rather than failing.
MONTH_ON_MONTH_ONLY_RULES = frozenset({"seasonal_index_mom"})

#: Model version recorded per rule, so the track record shows what produced a row.
#: The m/m and year-ended headline rules share a version deliberately: they are one
#: projection read two ways, and versioning them apart would suggest otherwise.
MODEL_VERSIONS = {
    "seasonal_index_projection": "v2-seasonal-index",
    "seasonal_index_mom": "v2-seasonal-index",
    "index_projection": "v1-index-projection",
}
DEFAULT_MODEL_VERSION = "v0-naive"

#: (model, benchmark) per target. Always two different rules — see below.
DEFAULT_PAIRS: dict[str, tuple[str, str]] = {
    # Both headline targets read one projected index, so they cannot contradict each
    # other. seasonal_naive drops to the benchmark slot: it is what this replaced,
    # which makes the skill number answer the question the change actually raises.
    "headline_mom": ("seasonal_index_mom", "seasonal_naive"),
    # Original series, so the published seasonal shape is worth restoring.
    "headline_yoy": ("seasonal_index_projection", "random_walk"),
    # Already seasonally adjusted: a seasonal correction here would double-count.
    "trimmed_mean_yoy": ("index_projection", "random_walk"),
}


@dataclass(frozen=True)
class TargetSeries:
    """Which panel series a target reads, and its adjusted counterpart if any."""

    index_id: str
    tsest: str
    seasonally_adjusted: bool
    sa_index_id: str | None = None
    sa_tsest: str | None = None


_COMPANIONS: dict[str, TargetSeries] = {
    # 999901 is "All groups CPI, seasonally adjusted" — the ABS's own adjustment of
    # 10001, which is what supplies the seasonal factors.
    "headline_mom": TargetSeries(
        "10001", TSEST_ORIGINAL, False, "999901", TSEST_SEASONALLY_ADJUSTED
    ),
    "headline_yoy": TargetSeries(
        "10001", TSEST_ORIGINAL, False, "999901", TSEST_SEASONALLY_ADJUSTED
    ),
    "trimmed_mean_yoy": TargetSeries("999902", TSEST_SEASONALLY_ADJUSTED, True),
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
    spec = _COMPANIONS[target]

    sa_level = None
    if spec.sa_index_id and spec.sa_tsest:
        try:
            sa_level = series_for(
                panel,
                spec.sa_index_id,
                MEASURE_INDEX_NUMBER,
                spec.sa_tsest,
                name=f"{target}_sa_level",
            )
        except ValueError:
            # Older vintages may not carry the adjusted counterpart. The seasonal
            # rule falls back rather than failing.
            sa_level = None

    return History(
        own=target_series(panel, target),
        mom=series_for(
            panel, spec.index_id, MEASURE_CHANGE_PREV_PERIOD, spec.tsest, name=f"{target}_mom"
        ),
        level=series_for(
            panel, spec.index_id, MEASURE_INDEX_NUMBER, spec.tsest, name=f"{target}_level"
        ),
        seasonally_adjusted=spec.seasonally_adjusted,
        sa_level=sa_level,
    )


def _check_rule(name: str, target: str) -> None:
    if name not in RULES:
        raise KeyError(f"unknown rule {name!r}; have {sorted(RULES)}")
    measure = TARGETS[target][1]
    if name in YEAR_ENDED_ONLY_RULES and measure != MEASURE_CHANGE_PREV_YEAR:
        raise ValueError(
            f"rule {name!r} produces a year-ended rate and cannot forecast {target!r}, "
            "which is a month-on-month series"
        )
    if name in MONTH_ON_MONTH_ONLY_RULES and measure != MEASURE_CHANGE_PREV_PERIOD:
        raise ValueError(
            f"rule {name!r} produces a month-on-month rate and cannot forecast "
            f"{target!r}, which is a year-ended series"
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
                model_version=MODEL_VERSIONS.get(model, DEFAULT_MODEL_VERSION),
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
