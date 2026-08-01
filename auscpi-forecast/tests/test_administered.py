from __future__ import annotations

from datetime import date

import pytest
from sdmx_fixtures import RENTS_LEVEL_KEY, all_targets_doc, compound, observations

from auscpi.administered import (
    AdministeredEvent,
    estimate_passthrough,
    event_value,
    for_class,
    load_events,
    override_path,
    visible_at,
)
from auscpi.parsers.abs_cpi import parse_sdmx_json

PERIODS = [f"{y}-{m:02d}" for y in (2024, 2025) for m in range(1, 13)] + [
    f"2026-{m:02d}" for m in range(1, 7)
]

SOURCE = "https://example.gov.au/announcement"


def event(**kw) -> AdministeredEvent:
    base = dict(
        index_id="30014",
        label="Test event",
        announced_date=date(2026, 2, 17),
        effective_month="2026-04",
        announced_pct=4.41,
        passthrough=0.8,
        confidence="announced",
        source_url=SOURCE,
    )
    base.update(kw)
    return AdministeredEvent(**base)


def test_class_effect_is_the_announcement_times_the_passthrough():
    """The announced number is not the CPI effect; the class is broader than the item."""
    assert event(announced_pct=4.41, passthrough=0.8).class_effect_pct == pytest.approx(3.528)


def test_an_unsourced_event_is_refused():
    """An administered event nobody can check must not reach a forecast."""
    with pytest.raises(ValueError, match="no source_url"):
        event(source_url="")


def test_an_unknown_confidence_is_refused():
    with pytest.raises(ValueError, match="not one of"):
        event(confidence="pretty sure")


def test_an_event_announced_after_its_month_ended_is_refused():
    """It could not have informed a forecast of that month, by definition."""
    with pytest.raises(ValueError, match="cannot be an administered forecast input"):
        event(announced_date=date(2026, 5, 2), effective_month="2026-04")


def test_an_event_announced_during_its_effective_month_is_allowed():
    """Late but not impossible: it can still inform later horizons and pass-through."""
    assert event(announced_date=date(2026, 4, 10), effective_month="2026-04").announced_pct


# --- the leakage guard ----------------------------------------------------


def test_visible_at_filters_on_announcement_not_effect():
    """Rule 3 for documents. Filtering on effective_month is the bug this prevents."""
    announced_feb = event(announced_date=date(2026, 2, 17), effective_month="2026-04")

    # A forecast made in January could not have known about a February announcement,
    # even though the change lands in April either way.
    assert visible_at([announced_feb], date(2026, 1, 31)) == []
    assert visible_at([announced_feb], date(2026, 2, 17)) == [announced_feb]
    assert visible_at([announced_feb], date(2026, 3, 1)) == [announced_feb]


def test_visible_at_is_inclusive_of_the_announcement_day():
    e = event(announced_date=date(2026, 2, 17))
    assert visible_at([e], date(2026, 2, 17)) == [e]
    assert visible_at([e], date(2026, 2, 16)) == []


def test_for_class_selects_only_the_named_class():
    a = event(index_id="30014")
    b = event(index_id="40091")
    assert for_class([a, b], "40091") == [b]


# --- turning an event into a component override ---------------------------


def panel_with_rents():
    n = len(PERIODS)
    rates = [0.3] * n
    doc = all_targets_doc(PERIODS)
    series = doc["data"]["dataSets"][0]["series"]
    series["0:4:0:0:0"] = observations([3.7] * n)
    series["1:4:0:0:0"] = observations(rates)
    series[RENTS_LEVEL_KEY] = observations(compound(rates))
    return parse_sdmx_json(doc)


def hist_for(panel):
    from auscpi.aggregate import component_history

    return component_history(panel, "30014")


def months_from(start: str, n: int) -> list[str]:
    from auscpi.forecast import add_months

    return [add_months(start, i) for i in range(n)]


def test_no_events_leaves_the_baseline_untouched():
    panel = panel_with_rents()
    months = months_from("2026-07", 13)
    baseline = override_path(hist_for(panel), [], months)

    from auscpi.forecast import _seasonal_index_projection

    expected = _seasonal_index_projection(hist_for(panel), months)
    for month, value in zip(months, expected, strict=True):
        assert baseline[month] == pytest.approx(value, abs=1e-9)


def test_an_event_raises_the_year_ended_rate_for_twelve_months_then_leaves():
    """A level step shows up in y/y for exactly a year, which the ratio handles itself."""
    panel = panel_with_rents()
    months = months_from("2026-07", 25)
    plain = override_path(hist_for(panel), [], months)
    with_event = override_path(
        hist_for(panel),
        [event(index_id="30014", effective_month="2026-09", announced_pct=5.0, passthrough=1.0)],
        months,
    )

    assert with_event["2026-08"] == pytest.approx(plain["2026-08"], abs=1e-9)  # before
    assert with_event["2026-09"] > plain["2026-09"] + 3.0  # the step lands
    assert with_event["2027-08"] > plain["2027-08"] + 3.0  # still inside the window
    # Twelve months on, both ends of the annual window carry it and it cancels.
    assert with_event["2027-09"] == pytest.approx(plain["2027-09"], abs=1e-6)


def test_the_baseline_movement_is_netted_off_not_added_to():
    """The projection already assumes the class drifts up in the effective month.

    An event whose effect equals what the projection already expected must change
    nothing. Here the fixture compounds at 0.3%/month, so a 0.3% announced change
    with full pass-through is exactly what the baseline assumed.
    """
    panel = panel_with_rents()
    months = months_from("2026-07", 13)
    plain = override_path(hist_for(panel), [], months)
    same = override_path(
        hist_for(panel),
        [event(index_id="30014", effective_month="2026-09", announced_pct=0.3, passthrough=1.0)],
        months,
    )
    for month in months:
        assert same[month] == pytest.approx(plain[month], abs=1e-6), month


def test_an_event_outside_the_projected_span_is_ignored_not_fatal():
    panel = panel_with_rents()
    months = months_from("2026-07", 13)
    far = event(index_id="30014", effective_month="2030-01", announced_date=date(2026, 2, 17))
    assert override_path(hist_for(panel), [far], months) == pytest.approx(
        override_path(hist_for(panel), [], months)
    )


def test_two_events_on_one_class_both_apply():
    panel = panel_with_rents()
    months = months_from("2026-07", 13)
    plain = override_path(hist_for(panel), [], months)
    both = override_path(
        hist_for(panel),
        [
            event(index_id="30014", effective_month="2026-08", announced_pct=2.0, passthrough=1.0),
            event(index_id="30014", effective_month="2026-09", announced_pct=2.0, passthrough=1.0),
        ],
        months,
    )
    assert both["2026-10"] > plain["2026-10"] + 2.0


# --- the store ------------------------------------------------------------


def test_a_missing_calendar_is_empty_not_an_error():
    from pathlib import Path

    assert load_events(Path("no-such-calendar.csv")) == []


def test_the_shipped_calendar_loads_and_every_event_is_sourced():
    events = load_events()
    assert events, "the shipped calendar should not be empty"
    for e in events:
        assert e.source_url.startswith("http")
        assert e.confidence in ("announced", "scheduled", "estimated")
        assert 0.0 < e.passthrough <= 1.5
        # The property the whole module exists for.
        assert e.announced_date < date.fromisoformat(f"{e.effective_month}-01"), (
            f"{e.label} was not announced before its effective month began"
        )


def test_event_value_scores_the_event_against_what_printed():
    """Knowing about a planted jump must beat not knowing about it."""
    n = len(PERIODS)
    rates = [0.3] * n
    jump = PERIODS.index("2025-06")
    rates[jump] = 3.0
    doc = all_targets_doc(PERIODS)
    series = doc["data"]["dataSets"][0]["series"]
    series["0:4:0:0:0"] = observations([3.7] * n)
    series["1:4:0:0:0"] = observations(rates)
    series[RENTS_LEVEL_KEY] = observations(compound(rates))
    panel = parse_sdmx_json(doc)

    e = event(
        index_id="30014",
        effective_month="2025-06",
        announced_pct=3.0,
        passthrough=1.0,
        announced_date=date(2025, 4, 15),
    )
    result = event_value(panel, e, horizons=3)
    assert result["mae_with"] < result["mae_without"]
    assert result["n"] > 0


def test_event_value_honours_a_passthrough_override():
    """The only honest way to score an event whose stored ratio came from the outcome."""
    n = len(PERIODS)
    rates = [0.3] * n
    rates[PERIODS.index("2025-06")] = 3.0
    doc = all_targets_doc(PERIODS)
    series = doc["data"]["dataSets"][0]["series"]
    series["0:4:0:0:0"] = observations([3.7] * n)
    series["1:4:0:0:0"] = observations(rates)
    series[RENTS_LEVEL_KEY] = observations(compound(rates))
    panel = parse_sdmx_json(doc)

    e = event(index_id="30014", effective_month="2025-06", announced_pct=3.0, passthrough=1.0,
              announced_date=date(2025, 4, 15))
    assert event_value(panel, e, passthrough=0.5)["passthrough"] == pytest.approx(0.5)
    assert event_value(panel, e)["passthrough"] == pytest.approx(1.0)


def test_estimate_passthrough_recovers_a_planted_ratio():
    """Realised class movement over announced movement, per past event."""
    n = len(PERIODS)
    rates = [0.3] * n
    # Plant a 2% jump in the rents class in 2025-06.
    jump = PERIODS.index("2025-06")
    rates[jump] = 2.0
    doc = all_targets_doc(PERIODS)
    series = doc["data"]["dataSets"][0]["series"]
    series["0:4:0:0:0"] = observations([3.7] * n)
    series["1:4:0:0:0"] = observations(rates)
    series[RENTS_LEVEL_KEY] = observations(compound(rates))
    panel = parse_sdmx_json(doc)

    # Announced 4%, realised 2% -> pass-through 0.5.
    e = event(
        index_id="30014",
        effective_month="2025-06",
        announced_pct=4.0,
        announced_date=date(2025, 3, 1),
    )
    [(_, ratio)] = estimate_passthrough(panel, [e])
    assert ratio == pytest.approx(0.5, abs=1e-6)
