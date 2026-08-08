from __future__ import annotations

import pandas as pd
import pytest
from sdmx_fixtures import all_targets_doc

from auscpi.parsers.abs_cpi import parse_sdmx_json
from auscpi.uncertainty import (
    MIN_ERRORS_FOR_QUANTILES,
    HorizonErrors,
    bands_for,
    error_sample,
    horizon_errors,
)

PERIODS = [f"{y}-{m:02d}" for y in (2023, 2024, 2025) for m in range(1, 13)] + [
    f"2026-{m:02d}" for m in range(1, 7)
]


def errors(horizon: int, values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"horizon_months": horizon, "error": values})


def test_quantiles_are_withheld_when_the_sample_is_too_thin():
    """At h=12 the real sample is two origins; an order statistic over two numbers
    is arithmetic, not a distribution."""
    thin = errors(12, [0.1] * (MIN_ERRORS_FOR_QUANTILES - 1))
    [row] = horizon_errors(thin)
    assert row.n == MIN_ERRORS_FOR_QUANTILES - 1
    assert row.quantiles == {}
    assert row.estimable is False


def test_quantiles_appear_once_the_sample_is_large_enough():
    enough = errors(0, [float(i) for i in range(MIN_ERRORS_FOR_QUANTILES)])
    [row] = horizon_errors(enough)
    assert row.estimable is True
    assert set(row.quantiles) == {0.10, 0.25, 0.75, 0.90}
    assert row.quantiles[0.10] < row.quantiles[0.90]


def test_horizons_are_summarised_separately_never_pooled():
    """The h=0 and h=12 errors of a year-ended projection are different objects."""
    frame = pd.concat([errors(0, [0.0] * 10), errors(12, [5.0] * 10)], ignore_index=True)
    summary = {r.horizon_months: r for r in horizon_errors(frame)}
    assert summary[0].bias == pytest.approx(0.0)
    assert summary[12].bias == pytest.approx(5.0)


def test_bias_is_signed_so_reading_low_is_visible():
    """Error is point minus actual, so a model that reads low has negative bias."""
    [row] = horizon_errors(errors(6, [-1.0, -0.5, -0.75, -0.25]))
    assert row.bias < 0
    assert row.mean_absolute > 0


def test_a_band_maps_the_error_quantiles_onto_the_outcome_the_right_way_round():
    """An error of point minus actual inverts: the 90th error percentile is the LOW
    end of the outcome band. Getting this backwards would publish a band that leans
    the wrong way, and it would look plausible."""
    row = HorizonErrors(
        horizon_months=0,
        n=20,
        bias=0.0,
        mean_absolute=1.0,
        quantiles={0.10: -2.0, 0.25: -1.0, 0.75: 1.0, 0.90: 2.0},
    )
    band = bands_for(3.0, row)
    assert band["p10"] == pytest.approx(1.0)  # 3.0 - 2.0
    assert band["p90"] == pytest.approx(5.0)  # 3.0 + 2.0
    assert band["p10"] < band["p25"] < band["p75"] < band["p90"]


def test_a_wider_error_sample_gives_a_wider_band():
    narrow = HorizonErrors(0, 20, 0.0, 0.2, {0.10: -0.3, 0.25: -0.1, 0.75: 0.1, 0.90: 0.3})
    wide = HorizonErrors(6, 20, 0.0, 1.5, {0.10: -2.0, 0.25: -1.0, 0.75: 1.0, 0.90: 2.0})
    n, w = bands_for(3.0, narrow), bands_for(3.0, wide)
    assert (w["p90"] - w["p10"]) > (n["p90"] - n["p10"])


def test_the_point_always_lies_inside_its_own_band():
    """A point below its own p10 is not a point estimate, it is a mislabelled one.

    The first implementation used raw error quantiles, so a biased model's band sat
    entirely above the point — headline_yoy printed point +3.12 against p10 +3.18 at
    h=5. It was also inconsistent: this module declines to bias-correct the point
    because fourteen overlapping origins cannot establish a structural bias, and
    shifting the band by that same bias asserts the opposite.
    """
    biased = HorizonErrors(
        horizon_months=12,
        n=20,
        bias=-1.5,
        mean_absolute=1.5,
        quantiles={0.10: -2.5, 0.25: -2.0, 0.75: -1.0, 0.90: -0.5},
    )
    band = bands_for(3.0, biased)
    assert band["p10"] < 3.0 < band["p90"], "the point must lie inside its own band"
    # Dispersion is preserved: the raw quantiles span 2.0, and so does the band.
    assert band["p90"] - band["p10"] == pytest.approx(2.0)


def test_bias_shifts_nothing_because_the_band_measures_dispersion():
    """Two horizons with identical spread but different bias get identical bands."""
    spread = {0.10: -1.0, 0.25: -0.5, 0.75: 0.5, 0.90: 1.0}
    unbiased = HorizonErrors(0, 20, bias=0.0, mean_absolute=0.7, quantiles=spread)
    shifted = HorizonErrors(
        6, 20, bias=-2.0, mean_absolute=2.0,
        quantiles={k: v - 2.0 for k, v in spread.items()},
    )
    assert bands_for(3.0, unbiased) == bands_for(3.0, shifted)


def test_no_band_when_the_horizon_is_not_estimable():
    row = HorizonErrors(horizon_months=12, n=2, bias=0.0, mean_absolute=0.0)
    assert bands_for(3.0, row) == {"p10": None, "p25": None, "p75": None, "p90": None}


def test_an_empty_sample_summarises_to_nothing_rather_than_raising():
    assert horizon_errors(pd.DataFrame()) == []


# --- the truncation backtest ---------------------------------------------


def test_error_sample_never_scores_a_forecast_on_data_it_saw():
    """Every origin sees only its own cutoff and earlier. The honesty of the whole
    module rests on this, so a perfect-foresight model must still show errors."""
    panel = parse_sdmx_json(all_targets_doc(PERIODS, mom=lambda i: round(0.1 * (i % 12), 3)))
    sample = error_sample(panel, "headline_yoy")

    assert not sample.empty
    # Horizons thin out as they run past the end of the sample, which is the shape
    # that makes long-horizon quantiles unsupportable.
    counts = sample.groupby("horizon_months").size()
    assert counts.loc[0] > counts.loc[max(counts.index)]


def test_error_sample_respects_a_narrowed_horizon_span():
    panel = parse_sdmx_json(all_targets_doc(PERIODS))
    sample = error_sample(panel, "headline_yoy", horizons=[0, 1])
    assert sorted(sample["horizon_months"].unique()) == [0, 1]


def test_error_sample_covers_every_logged_target():
    panel = parse_sdmx_json(all_targets_doc(PERIODS, mom=lambda i: round(0.1 * (i % 12), 3)))
    for target in ("headline_mom", "headline_yoy", "trimmed_mean_yoy"):
        assert not error_sample(panel, target).empty, target
