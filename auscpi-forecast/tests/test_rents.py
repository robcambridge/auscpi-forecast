from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from auscpi.rents import (
    ROLL_THROUGH_MONTHS,
    Calibration,
    add_months,
    backtest,
    calibrate,
    project_new_lease,
    rent_path,
    smoothed_new_lease,
)


def months(start: str, n: int) -> list[str]:
    return [add_months(start, i) for i in range(n)]


def series(start: str, values: list[float]) -> pd.Series:
    return pd.Series(values, index=months(start, len(values)), dtype="float64")


def growing(start: str, n: int, monthly_pct: float, base: float = 100.0) -> pd.Series:
    return series(start, [base * (1 + monthly_pct / 100) ** i for i in range(n)])


def wavy(start: str, n: int, base: float = 100.0) -> pd.Series:
    """A flow whose GROWTH RATE varies.

    Constant growth gives a constant year-ended rate, which leaves the calibration
    regressor with no variance and nothing to identify beta from. Anything testing
    the fit needs a path that accelerates and decelerates.
    """
    values = [base]
    for i in range(1, n):
        rate = 0.6 + 0.5 * np.sin(i / 5.0)
        values.append(values[-1] * (1 + rate / 100))
    return series(start, values)


def test_smoothing_is_a_moving_average_of_the_flow():
    flow = series("2022-01", [100.0] * 6 + [200.0] * 6)
    smooth = smoothed_new_lease(flow, roll_through_months=12)
    # One full window only, half at each level.
    assert len(smooth) == 1
    assert smooth.iloc[0] == pytest.approx(150.0)


def test_the_stock_lags_the_flow():
    """The mechanism: a step in new leases reaches the stock gradually, not at once."""
    flow = series("2022-01", [100.0] * 12 + [110.0] * 12)
    smooth = smoothed_new_lease(flow, roll_through_months=12)
    # Immediately after the step the stock has barely moved; a year later it has
    # fully absorbed it.
    assert smooth.loc["2022-12"] == pytest.approx(100.0)
    assert smooth.loc["2023-01"] == pytest.approx(100.833, abs=1e-3)
    # Absorbed only once the whole window is post-step — twelve months, not eleven.
    assert smooth.loc["2023-11"] == pytest.approx(109.167, abs=1e-3)
    assert smooth.loc["2023-12"] == pytest.approx(110.0)


def test_calibration_recovers_a_planted_pass_through():
    """beta is the pass-through, and it must come back out of a constructed case."""
    flow = wavy("2022-01", 48)
    smooth = smoothed_new_lease(flow, roll_through_months=12)
    # Construct measured rents to satisfy measured_yoy = 2 + 0.5 * smoothed_yoy
    # exactly, so the fit has a known right answer to recover.
    smooth_yoy = (smooth / smooth.shift(12) - 1) * 100
    target = 2.0 + 0.5 * smooth_yoy.dropna()
    idx = list(target.index)
    measured = pd.Series(index=months(add_months(idx[0], -12), len(idx) + 12), dtype="float64")
    measured.iloc[:12] = 100.0
    for period in idx:
        base = measured.loc[add_months(period, -12)]
        measured.loc[period] = base * (1 + target.loc[period] / 100)

    fit = calibrate(measured.dropna(), flow, roll_through_months=12)
    assert fit.beta == pytest.approx(0.5, abs=1e-6)
    assert fit.alpha == pytest.approx(2.0, abs=1e-6)


def test_calibration_refuses_a_sample_too_short_to_mean_anything():
    flow = growing("2025-01", 18, 0.5)
    measured = growing("2025-01", 18, 0.3)
    with pytest.raises(ValueError, match="year-ended pairs"):
        calibrate(measured, flow, roll_through_months=12)


def test_projection_carries_the_last_new_lease_level_flat():
    flow = growing("2022-01", 24, 1.0)
    projected = project_new_lease(flow, "2024-06")
    last = float(flow.iloc[-1])
    assert projected.loc["2024-01"] == pytest.approx(last)
    assert projected.loc["2024-06"] == pytest.approx(last)
    # Observed months are untouched.
    assert projected.loc["2023-12"] == pytest.approx(last)


def test_observed_share_falls_to_zero_at_the_roll_through_horizon():
    """The honest headline on each point: how much of it is arithmetic."""
    flow = wavy("2021-01", 60)
    measured = growing("2021-01", 60, 0.3)
    points, _ = rent_path(
        measured, flow, origin="2025-12", horizons=list(range(13)), roll_through_months=12
    )
    by_h = {p.horizon_months: p.observed_share for p in points}
    # Origin is the last observed new-lease month, so its whole window is history
    # and each further horizon carries one more month of it. The field is rounded
    # to three places, which sets the tolerance.
    assert by_h[0] == pytest.approx(1.0, abs=1e-3)
    assert by_h[6] == pytest.approx(0.5, abs=1e-3)
    assert by_h[11] == pytest.approx(1 / 12, abs=1e-3)
    assert by_h[12] == pytest.approx(0.0, abs=1e-3)


def test_every_point_carries_a_benchmark():
    """The project's standing rule: a track record without a benchmark is unreadable."""
    flow = wavy("2021-01", 60)
    measured = growing("2021-01", 60, 0.3)
    points, _ = rent_path(
        measured, flow, origin="2025-12", horizons=list(range(13)), roll_through_months=12
    )
    assert points
    for p in points:
        assert p.benchmark_point is not None
        assert p.horizon_months is not None
    # The benchmark carries the last observed year-ended rate, so it is flat.
    assert len({p.benchmark_point for p in points}) == 1


def test_a_faster_new_lease_path_raises_the_projection():
    """The sign of the mechanism, with the fit held fixed so only the flow moves.

    Refitting on each flow would confound the two: a faster flow changes both the
    smoothed input and the pass-through estimated from it, and the test could pass
    or fail for the wrong reason.
    """
    slow_flow = wavy("2021-01", 60)
    fast_flow = wavy("2021-01", 60)
    fast_flow.iloc[-12:] = [float(v) * 1.05 for v in fast_flow.iloc[-12:]]
    measured = growing("2021-01", 60, 0.3)

    held = Calibration(
        alpha=2.0, beta=0.5, n=30, roll_through_months=12, information_cutoff="2025-12"
    )
    kw = dict(origin="2025-12", horizons=[6], roll_through_months=12, calibration=held)
    slow, _ = rent_path(measured, slow_flow, **kw)
    fast, _ = rent_path(measured, fast_flow, **kw)
    assert fast[0].point > slow[0].point


def test_a_calibration_fitted_for_another_window_is_refused():
    """beta is specific to K; reusing one across windows would be silently wrong."""
    flow = wavy("2021-01", 60)
    measured = growing("2021-01", 60, 0.3)
    held = Calibration(
        alpha=2.0, beta=0.5, n=30, roll_through_months=16, information_cutoff="2025-12"
    )
    with pytest.raises(ValueError, match="specific to the window"):
        rent_path(
            measured,
            flow,
            origin="2025-12",
            horizons=[6],
            roll_through_months=12,
            calibration=held,
        )


def measured_from(flow: pd.Series, *, alpha: float, beta: float, K: int = 12) -> pd.Series:
    """Measured rents that satisfy the roll-through exactly, for testing machinery."""
    smooth = smoothed_new_lease(flow, roll_through_months=K)
    target = alpha + beta * ((smooth / smooth.shift(12) - 1) * 100).dropna()
    idx = list(target.index)
    measured = pd.Series(index=months(add_months(idx[0], -12), len(idx) + 12), dtype="float64")
    measured.iloc[:12] = 100.0
    for period in idx:
        base = measured.loc[add_months(period, -12)]
        measured.loc[period] = base * (1 + target.loc[period] / 100)
    return measured.dropna()


def test_backtest_finds_skill_where_the_roll_through_is_exactly_true():
    """End-to-end check on the machinery, not a claim about the real data.

    When measured rents ARE the pass-through of smoothed new leases, the only error
    left is the flat carry of future flow, so the model must beat carrying measured
    rents flat by a wide margin at the horizons the window still covers.
    """
    flow = wavy("2019-01", 84)
    measured = measured_from(flow, alpha=2.0, beta=0.5)

    results = backtest(measured, flow, roll_through_months=12, horizons=range(1, 13))
    assert results
    by_h = {r.horizon_months: r for r in results}
    assert by_h[3].skill > 0.5, by_h[3]
    assert by_h[6].skill > 0.3, by_h[6]
    # Every point is scored against a benchmark, as everywhere else in this repo.
    assert all(r.benchmark_mae > 0 for r in results)


def test_backtest_runs_out_of_targets_as_the_horizon_grows():
    """n is forecasts, not independent observations, and it thins with horizon."""
    flow = wavy("2019-01", 84)
    measured = measured_from(flow, alpha=2.0, beta=0.5)
    by_h = {r.horizon_months: r.n for r in backtest(measured, flow, roll_through_months=12)}
    assert by_h[1] > by_h[12]


def test_backtest_returns_nothing_rather_than_raising_on_a_short_sample():
    """`build_all` and the CLI run against half-built histories; this must not throw."""
    flow = wavy("2025-01", 14)
    measured = growing("2025-01", 14, 0.3)
    assert backtest(measured, flow, roll_through_months=12) == []


def test_the_default_roll_through_is_twelve_months():
    """Structural, not tuned. Changing it is a modelling decision, not a knob."""
    assert ROLL_THROUGH_MONTHS == 12


def test_calibration_records_its_information_cutoff():
    """Rule 3's audit trail: what the fit could actually see."""
    flow = wavy("2021-01", 60)
    measured = growing("2021-01", 48, 0.3)
    fit = calibrate(measured, flow, roll_through_months=12)
    assert fit.information_cutoff == str(flow.index[-1])
    assert fit.n >= 12


def test_add_months_crosses_year_boundaries():
    assert add_months("2026-01", -1) == "2025-12"
    assert add_months("2026-12", 1) == "2027-01"
    assert add_months("2026-06", -12) == "2025-06"


def test_a_regressor_with_no_variation_is_refused_not_fitted():
    """Constant growth means a constant year-ended rate and an unidentified beta.

    numpy answers this with an SVD convergence error several frames down. A flat or
    perfectly smooth bond index is a real possibility, so it has to fail by saying
    what is wrong.
    """
    flow = series("2021-01", [100.0] * 60)
    measured = growing("2021-01", 60, 0.3)
    with pytest.raises(ValueError, match="not identified"):
        calibrate(measured, flow, roll_through_months=12)

    steady = growing("2021-01", 60, 0.5)  # constant rate -> constant year-ended rate
    with pytest.raises(ValueError, match="not identified"):
        calibrate(measured, steady, roll_through_months=12)
