from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sdmx_fixtures import HEADLINE_MOM_KEY, HEADLINE_YOY_KEY, TRIMMED_YOY_KEY, sdmx

from auscpi.forecast import DEFAULT_PAIRS, add_months, forecast_path
from auscpi.parsers.abs_cpi import parse_sdmx_json

# Two years of monthly history so seasonal naive has a same-month value to find.
PERIODS = [f"{y}-{m:02d}" for y in (2024, 2025) for m in range(1, 13)] + [
    f"2026-{m:02d}" for m in range(1, 7)
]


def panel_with_history() -> pd.DataFrame:
    n = len(PERIODS)
    # m/m rises through the calendar year so seasonal naive picks a distinctive
    # value per month rather than a constant that any rule would reproduce.
    mom = {str(i): [round(0.1 * (i % 12), 3)] for i in range(n)}
    return parse_sdmx_json(
        sdmx(
            {
                HEADLINE_MOM_KEY: {"observations": mom},
                HEADLINE_YOY_KEY: {"observations": {str(i): [3.5] for i in range(n)}},
                TRIMMED_YOY_KEY: {"observations": {str(i): [3.0] for i in range(n)}},
            },
            PERIODS,
        )
    )


def test_add_months_crosses_year_boundaries():
    assert add_months("2026-07", 0) == "2026-07"
    assert add_months("2026-07", 6) == "2027-01"
    assert add_months("2026-07", 12) == "2027-07"
    assert add_months("2026-01", -1) == "2025-12"
    assert add_months("2026-07", -12) == "2025-07"


def test_path_covers_every_horizon_with_correct_reference_months():
    path = forecast_path(
        "headline_mom", horizons=range(13), today=date(2026, 7, 30), panel=panel_with_history()
    )

    assert len(path.records) == 13
    assert [r.horizon_months for r in path.records] == list(range(13))
    assert path.records[0].reference_month == "2026-07"
    assert path.records[12].reference_month == "2027-07"


def test_every_point_carries_a_horizon_and_a_benchmark():
    """Rule 5, plus the benchmarks.py insistence on a benchmark beside each point."""
    path = forecast_path("headline_mom", today=date(2026, 7, 30), panel=panel_with_history())
    for r in path.records:
        assert r.horizon_months is not None
        assert r.benchmark_point is not None
        assert r.benchmark_name


def test_model_and_benchmark_are_always_different_rules():
    for target, (model, benchmark) in DEFAULT_PAIRS.items():
        assert model != benchmark, target


def test_refuses_a_benchmark_identical_to_the_model():
    with pytest.raises(ValueError, match="skill would be zero"):
        forecast_path(
            "headline_mom",
            model="mean_mom",
            benchmark="mean_mom",
            today=date(2026, 7, 30),
            panel=panel_with_history(),
        )


def test_information_cutoff_is_the_newest_observation_not_today():
    """The field that makes look-ahead detectable after the fact."""
    path = forecast_path("headline_mom", today=date(2026, 7, 30), panel=panel_with_history())
    assert path.information_cutoff == "2026-06"
    assert all(r.information_cutoff == "2026-06" for r in path.records)


def test_seasonal_naive_varies_across_horizons():
    path = forecast_path(
        "headline_mom",
        model="seasonal_naive",
        horizons=range(13),
        today=date(2026, 7, 30),
        panel=panel_with_history(),
    )
    points = [r.point for r in path.records]
    assert len(set(points)) > 1, "a seasonal rule that returns a flat path is not seasonal"


def test_seasonal_naive_only_ever_looks_backwards():
    """At h>1 the same month last year is itself unobserved, so it must walk back."""
    panel = panel_with_history()
    path = forecast_path(
        "headline_mom",
        model="seasonal_naive",
        horizons=[12],
        today=date(2026, 7, 30),
        panel=panel,
    )
    # Reference 2027-07: 2026-07 is unobserved, so the value must come from 2025-07.
    from auscpi.parsers.abs_cpi import target_series

    observed = target_series(panel, "headline_mom").dropna()
    assert path.records[0].reference_month == "2027-07"
    assert path.records[0].point == pytest.approx(float(observed.loc["2025-07"]), abs=1e-6)


def test_year_ended_default_path_is_flat_and_that_is_documented():
    path = forecast_path(
        "headline_yoy", horizons=range(13), today=date(2026, 7, 30), panel=panel_with_history()
    )
    assert len({r.point for r in path.records}) == 1
    assert path.model == "atkeson_ohanian"
    assert path.benchmark == "random_walk"


def test_model_version_is_recorded():
    path = forecast_path("headline_yoy", today=date(2026, 7, 30), panel=panel_with_history())
    assert all(r.model_version == "v0-naive" for r in path.records)


def test_note_carries_the_release_date_when_the_calendar_knows_it():
    path = forecast_path(
        "headline_mom", horizons=[0], today=date(2026, 7, 30), panel=panel_with_history()
    )
    # config/release_calendar.csv has 2026-07 printing 2026-08-26.
    assert "2026-08-26" in path.records[0].note


def test_unknown_target_and_unknown_rule_raise():
    panel = panel_with_history()
    with pytest.raises(KeyError):
        forecast_path("cpi_but_vibes", today=date(2026, 7, 30), panel=panel)
    with pytest.raises(KeyError):
        forecast_path("headline_mom", model="lstm", today=date(2026, 7, 30), panel=panel)


def test_nan_history_does_not_poison_the_path():
    """Year-ended series are ragged at the start; NaN must be dropped, not carried."""
    n = len(PERIODS)
    yoy = {str(i): [None if i < 14 else 3.5] for i in range(n)}
    panel = parse_sdmx_json(
        sdmx(
            {
                HEADLINE_MOM_KEY: {"observations": {str(i): [0.3] for i in range(n)}},
                HEADLINE_YOY_KEY: {"observations": yoy},
            },
            PERIODS,
        )
    )
    path = forecast_path(
        "headline_yoy",
        model="random_walk",
        benchmark="target_midpoint",
        today=date(2026, 7, 30),
        panel=panel,
    )
    assert all(r.point == pytest.approx(3.5) for r in path.records)
