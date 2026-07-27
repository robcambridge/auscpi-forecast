"""Benchmarks.

The move from nowcast to forecast makes the benchmark question much harder, and
this is the honest part of the project. A nowcast competes against a naive
carry-forward and usually wins comfortably, because you have observed the prices.
A forecast competes against benchmarks that are genuinely difficult:

    random_walk_yoy       Tomorrow's annual inflation is today's annual
                          inflation. Embarrassingly hard to beat past h=3.

    atkeson_ohanian       Atkeson & Ohanian (2001) showed that the average of
                          the last twelve months of inflation beats Phillips
                          curve forecasts at the one-year horizon. It has held
                          up depressingly well. This is the benchmark that
                          humbles most academic inflation models.

    seasonal_naive_mom    This month's m/m equals the same calendar month last
                          year. Strong for a CPI with heavy seasonality and
                          administered prices that reset on the same date
                          annually.

    target_midpoint       2.5%, the middle of the RBA's band. If the RBA is
                          credible, this is close to unbeatable at long
                          horizons — which is itself the reason not to claim
                          skill at h=12.

    rba_smp               The RBA's own published forecasts. Free, public,
                          quarterly, and the benchmark that actually means
                          something. Beating the SMP at h=1-2 quarters is a
                          real claim; beating a random walk is table stakes.

Report skill against these by horizon, and expect your edge to decay fast. Being
straight about where the model stops adding value is more persuasive than a
uniform claim of superiority, which no one believes anyway.
"""

from __future__ import annotations

from collections.abc import Sequence

RBA_TARGET_MIDPOINT = 2.5


def random_walk_yoy(yoy_history: Sequence[float], horizon: int = 1) -> float:
    """Carry the last observed year-ended rate forward, unchanged, to any horizon."""
    if not yoy_history:
        raise ValueError("need at least one observation")
    return float(yoy_history[-1])


def atkeson_ohanian(mom_history: Sequence[float], window: int = 12) -> float:
    """Annualised average of the last `window` monthly rates.

    Returns a year-ended rate. With monthly per-cent changes, compounding the
    mean is the right aggregation, not multiplying it by twelve.
    """
    if len(mom_history) < window:
        raise ValueError(f"need {window} observations, got {len(mom_history)}")
    recent = mom_history[-window:]
    mean_mom = sum(recent) / window
    return float(((1 + mean_mom / 100) ** 12 - 1) * 100)


def seasonal_naive_mom(mom_history: Sequence[float], lag: int = 12) -> float:
    """This month's m/m equals the same calendar month `lag` periods ago."""
    if len(mom_history) < lag:
        raise ValueError(f"need {lag} observations, got {len(mom_history)}")
    return float(mom_history[-lag])


def target_midpoint(*_: object) -> float:
    """The RBA target midpoint. Deceptively strong at long horizons."""
    return RBA_TARGET_MIDPOINT
