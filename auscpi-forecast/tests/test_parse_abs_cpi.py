from __future__ import annotations

from datetime import date

import pytest
from sdmx_fixtures import (
    HEADLINE_MOM_KEY,
    HEADLINE_YOY_KEY,
    TRIMMED_YOY_KEY,
    sdmx,
)

from auscpi.parsers.abs_cpi import (
    TARGETS,
    parse_sdmx_json,
    target_series,
    targets_frame,
)
from auscpi.periods import period_end, period_freq


def test_period_helpers():
    assert period_end("2026-06") == date(2026, 6, 30)
    assert period_end("2026-Q2") == date(2026, 6, 30)
    assert period_end("2024-02") == date(2024, 2, 29)
    assert period_freq("2026-06") == "M"
    assert period_freq("2026-Q2") == "Q"


def test_period_end_rejects_nonsense():
    with pytest.raises(ValueError):
        period_end("2026-13")


def test_parse_decodes_dimension_positions():
    doc = sdmx(
        {HEADLINE_YOY_KEY: {"observations": {"0": [3.8], "1": [4.0]}}}, ["2026-06", "2026-05"]
    )
    panel = parse_sdmx_json(doc)

    assert len(panel) == 2
    row = panel[panel["period"] == "2026-06"].iloc[0]
    assert row["measure"] == "3"
    assert row["measure_name"] == "Percentage change from previous year"
    assert row["index_id"] == "10001"
    assert row["index_name"] == "All groups CPI"
    assert row["tsest"] == "10"
    assert row["region"] == "50"
    assert row["freq"] == "M"
    assert row["value"] == 3.8
    assert row["period_end"] == date(2026, 6, 30)


def test_parse_takes_only_element_zero_of_an_observation():
    # Real payloads carry attribute positions after the datum: [value, 0, 2, ...].
    doc = sdmx({HEADLINE_YOY_KEY: {"observations": {"0": [3.8, 0, 2]}}}, ["2026-06"])
    assert parse_sdmx_json(doc)["value"].tolist() == [3.8]


def test_parse_keeps_missing_observations_as_null():
    doc = sdmx(
        {HEADLINE_YOY_KEY: {"observations": {"0": [None], "1": [4.0]}}}, ["2026-06", "2026-05"]
    )
    panel = parse_sdmx_json(doc)
    assert panel["value"].isna().sum() == 1


def test_parse_handles_both_structure_spellings():
    for plural in (True, False):
        doc = sdmx({HEADLINE_YOY_KEY: {"observations": {"0": [3.8]}}}, ["2026-06"], plural=plural)
        assert len(parse_sdmx_json(doc)) == 1


def test_parse_rejects_a_key_of_the_wrong_width():
    doc = sdmx({"0:0:0": {"observations": {"0": [1.0]}}}, ["2026-06"])
    with pytest.raises(ValueError, match="dimensions"):
        parse_sdmx_json(doc)


def test_parse_empty_returns_typed_empty_frame():
    panel = parse_sdmx_json(sdmx({}, ["2026-06"]))
    assert panel.empty
    assert "value" in panel.columns


def test_parse_sorts_oldest_first_within_a_series():
    doc = sdmx(
        {HEADLINE_YOY_KEY: {"observations": {"0": [3.8], "1": [4.0], "2": [4.2]}}},
        ["2026-06", "2026-05", "2026-04"],
    )
    panel = parse_sdmx_json(doc)
    assert panel["period"].tolist() == ["2026-04", "2026-05", "2026-06"]


def _full_doc() -> dict:
    """Two periods, delivered newest-first as the API does, with all three targets."""
    return sdmx(
        {
            HEADLINE_YOY_KEY: {"observations": {"0": [3.8], "1": [4.0]}},
            HEADLINE_MOM_KEY: {"observations": {"0": [-0.1], "1": [-0.7]}},
            TRIMMED_YOY_KEY: {"observations": {"0": [3.6], "1": [3.6]}},
        },
        ["2026-06", "2026-05"],
    )


def test_target_series_uses_the_verified_triples():
    panel = parse_sdmx_json(_full_doc())

    assert target_series(panel, "headline_yoy").tolist() == [4.0, 3.8]
    assert target_series(panel, "headline_mom").tolist() == [-0.7, -0.1]
    # The trap: the trimmed mean is Seasonally Adjusted, not Original.
    assert target_series(panel, "trimmed_mean_yoy").tolist() == [3.6, 3.6]


def test_target_series_is_indexed_by_period_oldest_first():
    panel = parse_sdmx_json(_full_doc())
    s = target_series(panel, "headline_yoy")
    assert list(s.index) == ["2026-05", "2026-06"]


def test_target_series_raises_rather_than_returning_empty():
    """An empty target means the triple is wrong; silence would poison benchmarks."""
    doc = sdmx({HEADLINE_YOY_KEY: {"observations": {"0": [3.8]}}}, ["2026-06"])
    panel = parse_sdmx_json(doc)
    with pytest.raises(ValueError, match="resolved to no observations"):
        target_series(panel, "trimmed_mean_yoy")


def test_target_series_rejects_unknown_name():
    panel = parse_sdmx_json(_full_doc())
    with pytest.raises(KeyError):
        target_series(panel, "core_inflation_vibes")


def test_targets_frame_puts_targets_side_by_side():
    frame = targets_frame(parse_sdmx_json(_full_doc()))
    assert frame["period"].tolist() == ["2026-05", "2026-06"]
    for target in TARGETS:
        assert target in frame.columns
    assert frame.loc[frame["period"] == "2026-06", "headline_yoy"].item() == 3.8


def test_targets_frame_skips_targets_absent_from_the_vintage():
    doc = sdmx({HEADLINE_YOY_KEY: {"observations": {"0": [3.8]}}}, ["2026-06"])
    frame = targets_frame(parse_sdmx_json(doc))
    assert "headline_yoy" in frame.columns
    assert "trimmed_mean_yoy" not in frame.columns


def test_benchmarks_consume_a_target_series():
    """The panel has to feed benchmarks.py without further massaging."""
    from auscpi import benchmarks

    periods = [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01"]
    obs = {str(i): [0.3] for i in range(len(periods))}
    doc = sdmx({HEADLINE_MOM_KEY: {"observations": obs}}, periods)

    mom = target_series(parse_sdmx_json(doc), "headline_mom").tolist()
    assert benchmarks.seasonal_naive_mom(mom) == 0.3
    assert benchmarks.atkeson_ohanian(mom) == pytest.approx(3.66, abs=0.05)
