from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sdmx_fixtures import (
    HEADLINE_LEVEL_KEY,
    HEADLINE_MOM_KEY,
    HEADLINE_YOY_KEY,
    all_targets_doc,
    observations,
    sdmx,
)

from auscpi.forecast import DEFAULT_PAIRS, add_months, forecast_path, project_levels
from auscpi.parsers.abs_cpi import parse_sdmx_json

# Two years of monthly history so seasonal naive has a same-month value to find.
PERIODS = [f"{y}-{m:02d}" for y in (2024, 2025) for m in range(1, 13)] + [
    f"2026-{m:02d}" for m in range(1, 7)
]


def panel_with_history() -> pd.DataFrame:
    # Seasonal shape (so seasonal naive picks a distinctive value per month) plus a
    # year-on-year drift. A rate pattern that is EXACTLY 12-periodic makes the
    # year-ended rate genuinely constant — every 12-month window compounds to the
    # same product — which would hide whether a rule responds to base effects.
    return parse_sdmx_json(
        all_targets_doc(PERIODS, mom=lambda i: round(0.1 * (i % 12) + 0.05 * (i // 12), 3))
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


def test_year_ended_default_is_the_index_projection_and_varies_by_horizon():
    path = forecast_path(
        "headline_yoy", horizons=range(13), today=date(2026, 7, 30), panel=panel_with_history()
    )
    assert path.model == "index_projection"
    assert path.benchmark == "random_walk"
    # The old flat carry-forward gave one number for every horizon. Base effects
    # rolling out of the annual window must move it.
    assert len({r.point for r in path.records}) > 1


def test_model_version_distinguishes_the_projection_from_the_naive_rules():
    projected = forecast_path("headline_yoy", today=date(2026, 7, 30), panel=panel_with_history())
    assert all(r.model_version == "v1-index-projection" for r in projected.records)

    naive = forecast_path("headline_mom", today=date(2026, 7, 30), panel=panel_with_history())
    assert all(r.model_version == "v0-naive" for r in naive.records)


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


def test_projection_reproduces_the_year_ended_identity_on_observed_months():
    """The validation the model rests on: y/y is a ratio of index levels.

    With a constant 0.3% monthly rate, twelve months compound to
    (1.003**12 - 1) * 100, and the projection must return exactly that rather
    than something merely close.
    """
    panel = parse_sdmx_json(all_targets_doc(PERIODS, mom=lambda _i: 0.3))
    path = forecast_path("headline_yoy", horizons=[0], today=date(2026, 7, 30), panel=panel)
    expected = ((1.003**12) - 1) * 100
    assert path.records[0].point == pytest.approx(expected, abs=1e-3)


def test_projection_carries_known_base_effects_at_h0():
    """At h=0 only one month is forecast; the other eleven are already observed.

    A step change in the level twelve months back must therefore move the h=0
    point, which is exactly what carrying the last y/y flat cannot do.
    """
    panel = parse_sdmx_json(all_targets_doc(PERIODS, mom=lambda i: 5.0 if i == 18 else 0.2))
    projected = forecast_path(
        "headline_yoy", horizons=range(13), today=date(2026, 7, 30), panel=panel
    )
    flat = forecast_path(
        "headline_yoy",
        model="random_walk",
        benchmark="target_midpoint",
        horizons=range(13),
        today=date(2026, 7, 30),
        panel=panel,
    )
    assert len({r.point for r in flat.records}) == 1
    assert len({r.point for r in projected.records}) > 1


def test_projection_needs_thirteen_index_levels():
    short = [f"2026-{m:02d}" for m in range(1, 7)]
    panel = parse_sdmx_json(all_targets_doc(short))
    with pytest.raises(ValueError, match="index levels"):
        forecast_path("headline_yoy", horizons=[0], today=date(2026, 7, 30), panel=panel)


def test_project_levels_leaves_observed_months_untouched():
    from auscpi.forecast import build_history

    panel = panel_with_history()
    hist = build_history(panel, "headline_yoy")
    levels = project_levels(hist, "2027-07")

    observed = hist.level.dropna()
    for period, value in observed.items():
        assert levels[str(period)] == pytest.approx(float(value))
    # And it extended past the last observation rather than stopping there.
    assert "2027-07" in levels
    assert "2026-06" in levels


def test_projection_uses_unrounded_rates_from_the_level_series():
    """Published m/m is rounded to 0.1; compounding it would drift by tenths.

    The level series here implies 0.25%/month while the published m/m column says
    0.2. If the projection read the published column the twelve-month figure would
    come out near 2.43 instead of 3.04.
    """
    n = len(PERIODS)
    rates = [0.25] * n
    from sdmx_fixtures import compound

    panel = parse_sdmx_json(
        sdmx(
            {
                HEADLINE_YOY_KEY: observations([3.0] * n),
                HEADLINE_MOM_KEY: observations([0.2] * n),  # deliberately rounded
                HEADLINE_LEVEL_KEY: observations(compound(rates)),
            },
            PERIODS,
        )
    )
    point = (
        forecast_path("headline_yoy", horizons=[0], today=date(2026, 7, 30), panel=panel)
        .records[0]
        .point
    )
    assert point == pytest.approx(((1.0025**12) - 1) * 100, abs=1e-3)


def test_index_projection_is_rejected_for_a_month_on_month_target():
    with pytest.raises(ValueError, match="year-ended"):
        forecast_path(
            "headline_mom",
            model="index_projection",
            today=date(2026, 7, 30),
            panel=panel_with_history(),
        )


def test_history_records_whether_the_series_is_seasonally_adjusted():
    from auscpi.forecast import build_history

    panel = panel_with_history()
    assert build_history(panel, "trimmed_mean_yoy").seasonally_adjusted is True
    assert build_history(panel, "headline_yoy").seasonally_adjusted is False


def test_a_seasonal_naive_driver_would_be_degenerate():
    """Why the driver is a mean: replicating last year's rates cancels base effects.

    Projecting the previous twelve monthly rates forward gives
    level(m) = level(T) * level(m-12) / level(T-12), so the ratio
    level(m)/level(m-12) is constant and the projection becomes a year-ended random
    walk. This pins the algebra so nobody "improves" the driver back into it.
    """
    from auscpi.forecast import History, _monthly_from_levels, _same_month_or_mean, add_months

    panel = panel_with_history()
    from auscpi.forecast import build_history

    hist: History = build_history(panel, "headline_yoy")

    level = hist.level.dropna()
    implied = _monthly_from_levels(level)
    last = str(level.index[-1])
    future = [add_months(last, i) for i in range(1, 14)]

    # Project using seasonal naive, the tempting choice.
    rates = _same_month_or_mean(implied, future)
    levels = {str(k): float(v) for k, v in level.items()}
    running = levels[last]
    for month, rate in zip(future, rates, strict=True):
        running *= 1 + rate / 100
        levels[month] = running

    yoy = [
        (levels[m] / levels[add_months(m, -12)] - 1) * 100
        for m in future[:12]
        if add_months(m, -12) in levels
    ]
    assert len({round(v, 6) for v in yoy}) == 1, "seasonal naive should collapse to a constant"

    # The shipped mean driver must NOT collapse.
    projected = forecast_path(
        "headline_yoy", horizons=range(13), today=date(2026, 7, 30), panel=panel
    )
    assert len({r.point for r in projected.records}) > 1


def test_nan_history_does_not_poison_the_path():
    """Year-ended series are ragged at the start; NaN must be dropped, not carried."""
    from sdmx_fixtures import compound

    n = len(PERIODS)
    rates = [0.3] * n
    panel = parse_sdmx_json(
        sdmx(
            {
                HEADLINE_MOM_KEY: observations(rates),
                HEADLINE_LEVEL_KEY: observations(compound(rates)),
                HEADLINE_YOY_KEY: observations([None if i < 14 else 3.5 for i in range(n)]),
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
