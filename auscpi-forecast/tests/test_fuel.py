from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from auscpi.fuel import (
    FUEL_INDEX_ID,
    Calibration,
    add_months,
    calibrate,
    component_path,
    project_index,
)


def months(start: str, n: int) -> list[str]:
    return [add_months(start, i) for i in range(n)]


def series(start: str, values: list[float]) -> pd.Series:
    return pd.Series(values, index=months(start, len(values)), dtype="float64")


def wavy(start: str, n: int, base: float = 100.0, scale: float = 1.0) -> pd.Series:
    values = [base]
    for i in range(1, n):
        values.append(values[-1] * (1 + scale * (3.0 * np.sin(i / 3.0) + 0.2) / 100))
    return series(start, values)


def held(cal: Calibration | None = None) -> Calibration:
    return cal or Calibration(
        alpha=0.0, beta=1.0, n=41, correlation=0.97, residual_sd=1.5,
        region="50", information_cutoff="2026-06",
    )


def test_the_fuel_class_is_automotive_fuel():
    assert FUEL_INDEX_ID == "40081"


def test_calibration_recovers_a_planted_mapping():
    fc = wavy("2023-01", 40)
    fc_mom = (fc / fc.shift(1) - 1) * 100
    target = (0.5 + 1.2 * fc_mom).dropna()
    level = [100.0]
    for value in target:
        level.append(level[-1] * (1 + value / 100))
    measured = series(target.index[0], level[1:])
    measured = pd.Series(level[1:], index=list(target.index), dtype="float64")

    cal = calibrate(measured, fc)
    assert cal.beta == pytest.approx(1.2, abs=1e-6)
    assert cal.alpha == pytest.approx(0.5, abs=1e-6)
    assert cal.correlation == pytest.approx(1.0, abs=1e-6)
    assert cal.residual_sd == pytest.approx(0.0, abs=1e-6)


def test_calibration_refuses_a_sample_too_short():
    fc = wavy("2026-01", 6)
    with pytest.raises(ValueError, match="monthly pairs"):
        calibrate(fc * 1.01, fc)


def test_calibration_refuses_a_regressor_with_no_variation():
    flat = series("2023-01", [100.0] * 30)
    with pytest.raises(ValueError, match="not identified"):
        calibrate(wavy("2023-01", 30), flat)


def test_calibration_records_its_cutoff():
    fc = wavy("2023-01", 30)
    cal = calibrate(wavy("2023-01", 30, scale=1.1), fc)
    assert cal.information_cutoff == str(fc.index[-1])


# --- projecting the index -------------------------------------------------


def test_a_month_fuelcheck_has_seen_steps_the_index():
    """The whole point: FuelCheck is real-time, the ABS is four weeks late."""
    measured = series("2026-01", [100.0, 101.0, 102.0])  # ABS through 2026-03
    fc = series("2026-01", [200.0, 202.0, 204.0, 224.4])  # FuelCheck has April: +10%

    levels = project_index(measured, fc, "2026-04", calibration=held())
    assert levels["2026-04"] == pytest.approx(102.0 * 1.10, abs=1e-6)


def test_beyond_fuelcheck_the_level_is_carried_not_extrapolated():
    """A random walk in levels, which is the honest benchmark for petrol."""
    measured = series("2026-01", [100.0, 101.0, 102.0])
    fc = series("2026-01", [200.0, 202.0, 204.0, 224.4])

    levels = project_index(measured, fc, "2026-07", calibration=held())
    assert levels["2026-05"] == pytest.approx(levels["2026-04"])
    assert levels["2026-07"] == pytest.approx(levels["2026-04"])


def test_the_calibration_is_applied_not_the_raw_price_move():
    """beta near one is a finding, not an assumption the code may rely on."""
    measured = series("2026-01", [100.0, 100.0, 100.0])
    fc = series("2026-01", [100.0, 100.0, 100.0, 110.0])  # +10%

    doubled = Calibration(
        alpha=0.0, beta=2.0, n=41, correlation=0.9, residual_sd=1.0,
        region="50", information_cutoff="2026-03",
    )
    levels = project_index(measured, fc, "2026-04", calibration=doubled)
    assert levels["2026-04"] == pytest.approx(120.0, abs=1e-6)


# --- the component path ---------------------------------------------------


def fixture_pair():
    """ABS ending one month behind FuelCheck, as it does in reality.

    The four-week publication lag IS the component's edge, and if the fixture lets
    both series end together there is no month for the calibration to act on — the
    index is merely carried and every calibration gives the same answer.
    """
    fc = wavy("2023-01", 42)
    fc_mom = (fc / fc.shift(1) - 1) * 100
    target = fc_mom.dropna()
    level = [100.0]
    for value in target:
        level.append(level[-1] * (1 + value / 100))
    measured = pd.Series(level[1:], index=list(target.index), dtype="float64")
    return measured.iloc[:-1], fc


def test_every_point_carries_a_benchmark_and_a_horizon():
    measured, fc = fixture_pair()
    points, _ = component_path(
        measured, fc, origin=add_months(str(measured.index[-1]), 1), horizons=list(range(13))
    )
    assert points
    for p in points:
        assert p.horizon_months is not None
        assert p.benchmark_point is not None
    # The benchmark carries the last observed year-ended rate, so it is flat.
    assert len({p.benchmark_point for p in points}) == 1


def test_measured_marks_months_fuelcheck_actually_saw():
    """The honest label on each point: knowledge versus a carried level."""
    measured = series("2025-01", [100.0 + i for i in range(18)])  # ABS through 2026-06
    fc = series("2025-01", [200.0 + 2 * i for i in range(19)])  # FuelCheck has 2026-07

    points, _ = component_path(
        measured, fc, origin="2026-07", horizons=[0, 1], calibration=held()
    )
    by_h = {p.horizon_months: p.measured for p in points}
    assert by_h[0] is True, "FuelCheck observed 2026-07, so it is measured"
    assert by_h[1] is False, "nothing has seen 2026-08"


def test_a_supplied_calibration_is_used_rather_than_refitted():
    measured, fc = fixture_pair()
    origin = add_months(str(measured.index[-1]), 1)
    doubled = Calibration(
        alpha=0.0, beta=2.0, n=41, correlation=0.9, residual_sd=1.0,
        region="50", information_cutoff="2026-06",
    )
    plain, _ = component_path(measured, fc, origin=origin, horizons=[0], calibration=held())
    strong, returned = component_path(
        measured, fc, origin=origin, horizons=[0], calibration=doubled
    )
    assert returned is doubled
    assert plain[0].point != strong[0].point


def test_add_months_crosses_year_boundaries():
    assert add_months("2026-12", 1) == "2027-01"
    assert add_months("2026-01", -1) == "2025-12"
