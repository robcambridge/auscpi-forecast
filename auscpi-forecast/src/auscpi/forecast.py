"""Produce a forecast path from the built panel.

This is version zero and it is deliberately naive. That is not an apology: the
roadmap says log a path before the next release even though the model is bad,
because a track record starting at n=1 with a weak model beats one starting at
n=0 with a good one, and for a forecast the gap widens fast — you need many
settled observations before skill is visible at all.

What is honest about it:

  - Every point carries its horizon, because a model that is fine at h=1 and
    useless at h=6 is a different object from one that is mediocre throughout
    (rule 5).
  - Every point carries a benchmark, because 0.3pp of error means nothing until
    you know what the naive rule scored.
  - The model and the benchmark are always DIFFERENT rules. Logging a benchmark
    against itself would report skill of exactly zero forever and look like
    diligence.

What is weak about it, stated here so nobody has to discover it:

  - The year-ended paths are FLAT. Both atkeson_ohanian and random_walk carry one
    number to every horizon, so the h=12 point is the h=0 point. A flat path is a
    real forecast, but it is not a model of the future.
  - Only the m/m path varies with horizon, via seasonal naive.
  - The monthly sample is ~26 observations. Nothing estimated here is
    statistically meaningful yet.
  - Deriving the y/y path from a projected index — the right answer — needs base
    effects and is not attempted.
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
    TARGETS,
    TSEST_ORIGINAL,
    TSEST_SEASONALLY_ADJUSTED,
    series_for,
    target_series,
)
from auscpi.track_record import ForecastRecord, months_between

DEFAULT_HORIZONS = tuple(range(13))  # h=0..12

#: A path rule: given the target's own history and the m/m companion, return one
#: value per requested reference month.
PathRule = Callable[[pd.Series, pd.Series, Sequence[str]], list[float]]


def add_months(reference_month: str, n: int) -> str:
    year, month = (int(x) for x in reference_month.split("-"))
    index = (year * 12 + month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _clean(series: pd.Series) -> list[float]:
    """Observed values only, oldest first.

    Nothing in benchmarks.py handles NaN — every function would happily return
    NaN — and the year-ended series is ragged at the start, so dropping is
    mandatory rather than cosmetic.
    """
    return [float(v) for v in series.dropna().tolist()]


def _seasonal_naive(own: pd.Series, mom: pd.Series, months: Sequence[str]) -> list[float]:
    """Same calendar month, most recent year for which it was actually observed.

    Plain seasonal naive wants month M of last year, but at h>1 that month is
    itself unobserved — the h=12 point would need the h=0 forecast as an input. So
    walk back a year at a time until a real observation turns up, which keeps the
    rule strictly backward-looking at every horizon.
    """
    observed = own.dropna()
    out: list[float] = []
    for month in months:
        value: float | None = None
        for years_back in range(1, 12):
            candidate = add_months(month, -12 * years_back)
            if candidate in observed.index:
                value = float(observed.loc[candidate])
                break
        if value is None:
            # No same-month history at all: fall back to the 12-month mean rather
            # than dropping the horizon, so the path stays complete.
            value = benchmarks.mean_mom(_clean(own))
        out.append(value)
    return out


def _flat(fn: Callable[[list[float]], float], use_mom: bool) -> PathRule:
    """Wrap a scalar benchmark into a constant path across every horizon."""

    def rule(own: pd.Series, mom: pd.Series, months: Sequence[str]) -> list[float]:
        history = _clean(mom if use_mom else own)
        return [fn(history)] * len(months)

    return rule


RULES: dict[str, PathRule] = {
    # Varies with horizon.
    "seasonal_naive": _seasonal_naive,
    # Flat paths.
    "atkeson_ohanian": _flat(benchmarks.atkeson_ohanian, use_mom=True),
    "random_walk": _flat(lambda h: benchmarks.random_walk_yoy(h), use_mom=False),
    "mean_mom": _flat(benchmarks.mean_mom, use_mom=False),
    "target_midpoint": _flat(lambda _h: benchmarks.target_midpoint(), use_mom=False),
}

#: (model, benchmark) per target. Always two different rules — see the docstring.
DEFAULT_PAIRS: dict[str, tuple[str, str]] = {
    "headline_mom": ("seasonal_naive", "mean_mom"),
    "headline_yoy": ("atkeson_ohanian", "random_walk"),
    "trimmed_mean_yoy": ("atkeson_ohanian", "random_walk"),
}

#: The m/m companion each target needs for the rules that consume monthly rates.
_MOM_COMPANION: dict[str, tuple[str, str, str]] = {
    "headline_mom": ("10001", MEASURE_CHANGE_PREV_PERIOD, TSEST_ORIGINAL),
    "headline_yoy": ("10001", MEASURE_CHANGE_PREV_PERIOD, TSEST_ORIGINAL),
    "trimmed_mean_yoy": ("999902", MEASURE_CHANGE_PREV_PERIOD, TSEST_SEASONALLY_ADJUSTED),
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
    for name in (model, benchmark):
        if name not in RULES:
            raise KeyError(f"unknown rule {name!r}; have {sorted(RULES)}")
    if model == benchmark:
        raise ValueError(
            f"model and benchmark are both {model!r}; skill would be zero by "
            "construction. Pick a different benchmark."
        )

    frame = panel if panel is not None else load_panel("abs_cpi_monthly", as_at=as_at)
    own = target_series(frame, target)
    mom = series_for(frame, *_MOM_COMPANION[target], name=f"{target}_mom")

    observed = own.dropna()
    if observed.empty:
        raise ValueError(f"target {target!r} has no observed values to forecast from")
    cutoff = str(observed.index[-1])

    origin = _origin_month(today or datetime.now(UTC).date())
    months = [add_months(origin, h) for h in horizons]

    points = RULES[model](own, mom, months)
    bench_points = RULES[benchmark](own, mom, months)

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
                model_version="v0-naive",
                benchmark_name=benchmark,
                benchmark_point=round(float(bench), 3),
                note=(
                    f"h={h} naive v0; prints {released.isoformat()}"
                    if released
                    else f"h={h} naive v0; release date not in calendar"
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
