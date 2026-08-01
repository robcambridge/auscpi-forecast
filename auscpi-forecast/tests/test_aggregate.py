from __future__ import annotations

import pytest
from sdmx_fixtures import all_targets_doc

from auscpi.aggregate import (
    ComponentSwap,
    Contribution,
    component_baseline,
    component_history,
    swap_components,
)
from auscpi.parsers.abs_cpi import parse_sdmx_json

PERIODS = [f"{y}-{m:02d}" for y in (2024, 2025) for m in range(1, 13)] + [
    f"2026-{m:02d}" for m in range(1, 7)
]

WEIGHTS = {"30014": 6.6129, "10001": 100.0, "20001": 17.439}


def test_a_swap_moves_the_headline_by_weight_times_the_disagreement():
    """The whole arithmetic of the module, on numbers small enough to check by hand."""
    headline = {"2026-07": 3.00, "2026-08": 3.00}
    swap = ComponentSwap(
        index_id="30014",
        label="Rents",
        baseline={"2026-07": 3.00, "2026-08": 3.00},
        override={"2026-07": 4.00, "2026-08": 1.00},  # +1.00pp, then -2.00pp
    )
    adjusted, contributions = swap_components(headline, [swap], WEIGHTS)

    # 6.6129% of a 1pp disagreement is 0.066pp.
    assert adjusted["2026-07"] == pytest.approx(3.0 + 0.066129, abs=1e-9)
    assert adjusted["2026-08"] == pytest.approx(3.0 - 2 * 0.066129, abs=1e-9)
    assert len(contributions) == 2
    assert contributions[0].effect_pp == pytest.approx(0.066129, abs=1e-9)


def test_agreeing_with_the_baseline_changes_nothing():
    """A component that says what the top-down rule already said adds no information."""
    headline = {"2026-07": 3.21, "2026-08": 3.34}
    swap = ComponentSwap(
        index_id="30014", label="Rents", baseline=dict(headline), override=dict(headline)
    )
    adjusted, _ = swap_components(headline, [swap], WEIGHTS)
    assert adjusted == pytest.approx(headline)


def test_the_baseline_is_netted_off_rather_than_added_to():
    """Adding w * override without subtracting the baseline would double-count.

    The headline projection already contains a view of rents. If the baseline were
    ignored, a component that merely agreed with it would still shift the headline
    by 6.6% of the whole rate, which is the bug this test exists to catch.
    """
    headline = {"2026-07": 3.00}
    swap = ComponentSwap(
        index_id="30014", label="Rents", baseline={"2026-07": 3.00}, override={"2026-07": 3.00}
    )
    adjusted, _ = swap_components(headline, [swap], WEIGHTS)
    assert adjusted["2026-07"] == pytest.approx(3.00), "baseline was not netted off"


def test_a_group_rather_than_an_expenditure_class_is_refused():
    """A group's weight covers its children; using it would count them twice."""
    headline = {"2026-07": 3.0}
    swap = ComponentSwap(
        index_id="99999", label="not a class", baseline=headline, override=headline
    )
    with pytest.raises(KeyError, match="not a published expenditure class"):
        swap_components(headline, [swap], WEIGHTS)


def test_the_same_class_swapped_twice_is_refused():
    headline = {"2026-07": 3.0}
    swap = ComponentSwap("30014", "Rents", {"2026-07": 3.0}, {"2026-07": 4.0})
    with pytest.raises(ValueError, match="would count twice"):
        swap_components(headline, [swap, swap], WEIGHTS)


def test_months_the_component_cannot_cover_are_left_alone():
    """A component path shorter than the headline path must not truncate it."""
    headline = {"2026-07": 3.0, "2026-08": 3.0, "2026-09": 3.0}
    swap = ComponentSwap("30014", "Rents", {"2026-07": 3.0}, {"2026-07": 5.0})
    adjusted, contributions = swap_components(headline, [swap], WEIGHTS)

    assert adjusted["2026-08"] == pytest.approx(3.0)
    assert adjusted["2026-09"] == pytest.approx(3.0)
    assert len(contributions) == 1


def test_two_components_both_contribute_and_stay_attributable():
    headline = {"2026-07": 3.0}
    swaps = [
        ComponentSwap("30014", "Rents", {"2026-07": 3.0}, {"2026-07": 4.0}),
        ComponentSwap("20001", "Food", {"2026-07": 3.0}, {"2026-07": 2.0}),
    ]
    adjusted, contributions = swap_components(headline, swaps, WEIGHTS)

    expected = 3.0 + 0.066129 - 0.17439
    assert adjusted["2026-07"] == pytest.approx(expected, abs=1e-9)
    # Attribution survives aggregation — which component moved it stays visible.
    assert {c.label for c in contributions} == {"Rents", "Food"}


def test_contribution_reports_percentage_points_not_per_cent():
    c = Contribution("30014", "Rents", 6.6129, "2026-07", baseline=3.0, override=4.0)
    assert c.effect_pp == pytest.approx(0.066129, abs=1e-9)


# --- the baseline, against the real projection machinery -------------------


def panel_with_rents():
    """The three-target fixture plus a rents class growing at a distinctive rate.

    component_history needs the year-ended, month-on-month and index series for the
    class, so all three are added; the seasonally adjusted counterpart is left out
    deliberately, because most expenditure classes do not have one.
    """
    from sdmx_fixtures import RENTS_LEVEL_KEY, compound, observations

    n = len(PERIODS)
    rates = [0.4] * n
    doc = all_targets_doc(PERIODS)
    series = doc["data"]["dataSets"][0]["series"]
    series["0:4:0:0:0"] = observations([4.9] * n)  # year-ended
    series["1:4:0:0:0"] = observations(rates)  # month-on-month
    series[RENTS_LEVEL_KEY] = observations(compound(rates))
    return parse_sdmx_json(doc)


def test_component_baseline_runs_the_same_rule_the_headline_uses():
    """A rents index compounding at 0.4%/month implies a known year-ended rate."""
    panel = panel_with_rents()
    baseline = component_baseline(panel, "30014", ["2026-07"], rule="index_projection")
    assert baseline["2026-07"] == pytest.approx(((1.004**12) - 1) * 100, abs=1e-2)


def test_component_history_tolerates_a_class_without_an_adjusted_series():
    """Most expenditure classes have no seasonally adjusted counterpart published."""
    panel = panel_with_rents()
    hist = component_history(panel, "30014")
    assert hist.sa_level is None
    assert hist.seasonally_adjusted is False
    assert not hist.level.dropna().empty


def test_an_unknown_rule_is_refused():
    panel = panel_with_rents()
    with pytest.raises(KeyError, match="unknown rule"):
        component_baseline(panel, "30014", ["2026-07"], rule="lstm")


def test_a_class_absent_from_the_panel_raises():
    panel = panel_with_rents()
    with pytest.raises(ValueError):
        component_baseline(panel, "30002", ["2026-07"], rule="index_projection")
