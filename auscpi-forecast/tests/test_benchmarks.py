import pytest

from auscpi import benchmarks


def test_random_walk_carries_last_value():
    assert benchmarks.random_walk_yoy([3.2, 3.8, 4.0], horizon=6) == 4.0


def test_atkeson_ohanian_compounds_not_multiplies():
    """0.5% per month is 6.17% annualised, not 6.0%. Getting this wrong
    understates the benchmark and flatters the model."""
    result = benchmarks.atkeson_ohanian([0.5] * 12)
    assert result == pytest.approx(6.1678, abs=1e-3)


def test_seasonal_naive_reaches_back_twelve_months():
    history = [float(i) for i in range(13)]  # 0..12
    assert benchmarks.seasonal_naive_mom(history) == 1.0


def test_benchmarks_refuse_short_history():
    with pytest.raises(ValueError):
        benchmarks.atkeson_ohanian([0.3, 0.4])
