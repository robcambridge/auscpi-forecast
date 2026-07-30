from __future__ import annotations

import pytest

from auscpi.collectors import registry
from auscpi.collectors.abs_cpi import ABSCPIWeightsCollector
from auscpi.parsers.abs_cpi_weights import (
    ALL_GROUPS_CODE,
    EXPENDITURE_CLASS,
    latest_reweight,
    parse_taxonomy,
    weights_at,
    weights_panel,
)

# Dimension order in the weights dataflow is MEASURE.INDEX.REGION.FREQ — no TSEST.
WEIGHT_DIMENSIONS = [
    {
        "id": "MEASURE",
        "values": [
            {"id": "1", "name": "Percentage contribution to the All groups CPI"},
            {"id": "3", "name": "Points contribution to All groups CPI"},
        ],
    },
    {
        "id": "INDEX",
        "values": [
            {"id": "10001", "name": "All groups CPI"},
            {"id": "20001", "name": "Food and non-alcoholic beverages"},
            {"id": "30002", "name": "Bread and cereal products"},
            {"id": "40005", "name": "Bread"},
            {"id": "40006", "name": "Cakes and biscuits"},
        ],
    },
    {"id": "REGION", "values": [{"id": "50", "name": "Australia"}]},
    {"id": "FREQ", "values": [{"id": "Q", "name": "Quarterly"}]},
]

# 40005 Bread -> 30002 Bread and cereal products -> 20001 Food -> 10001 All groups
TAXONOMY = {
    "data": {
        "codelists": [
            {
                "codes": [
                    {"id": "10001", "name": "All groups CPI"},
                    {"id": "20001", "name": "Food and non-alcoholic beverages", "parent": "10001"},
                    {"id": "30002", "name": "Bread and cereal products", "parent": "20001"},
                    {"id": "40005", "name": "Bread", "parent": "30002"},
                    {"id": "40006", "name": "Cakes and biscuits", "parent": "30002"},
                ]
            }
        ]
    }
}


def weights_doc(values: dict[str, float], periods: list[str] | None = None) -> dict:
    """One period of measure-1 national weights, keyed by INDEX position."""
    periods = periods or ["2024-Q4"]
    index_pos = {v["id"]: i for i, v in enumerate(WEIGHT_DIMENSIONS[1]["values"])}
    series = {
        f"0:{index_pos[code]}:0:0": {"observations": {"0": [value]}}
        for code, value in values.items()
    }
    structure = {
        "dimensions": {
            "series": WEIGHT_DIMENSIONS,
            "observation": [{"id": "TIME_PERIOD", "values": [{"id": p} for p in periods]}],
        }
    }
    return {"data": {"dataSets": [{"series": series}], "structures": [structure]}}


def payload(values: dict[str, float] | None = None) -> dict:
    # Each level sums to 100 independently, as the real dataflow does.
    values = values or {
        ALL_GROUPS_CODE: 100.0,
        "20001": 100.0,
        "30002": 100.0,
        "40005": 60.0,
        "40006": 40.0,
    }
    return {"weights": weights_doc(values), "taxonomy": TAXONOMY}


# --- collector ---


def test_registered_with_its_own_dataflow():
    assert registry["abs_cpi_weights"] is ABSCPIWeightsCollector
    assert ABSCPIWeightsCollector.dataflow == "ABS,CPI_WEIGHTS,1.0.0"
    # No TSEST dimension here, so the key is one field shorter than the price key.
    assert ABSCPIWeightsCollector.key == "..50.Q"


def test_staleness_limit_tolerates_a_late_reweight():
    """Reweighting is annual and lags; as at mid-2026 the newest was 2024-Q4."""
    assert ABSCPIWeightsCollector.max_staleness_days > 600


# --- taxonomy ---


def test_taxonomy_depth_gives_the_level():
    frame = parse_taxonomy(TAXONOMY).set_index("index_id")
    assert frame.loc["10001", "level"] == "all_groups"
    assert frame.loc["20001", "level"] == "group"
    assert frame.loc["30002", "level"] == "sub_group"
    assert frame.loc["40005", "level"] == EXPENDITURE_CLASS
    assert frame.loc["40005", "depth"] == 3
    assert frame.loc["40005", "parent_id"] == "30002"


def test_taxonomy_rejects_a_cycle():
    broken = {
        "data": {
            "codelists": [
                {
                    "codes": [
                        {"id": "A", "name": "a", "parent": "B"},
                        {"id": "B", "name": "b", "parent": "A"},
                    ]
                }
            ]
        }
    }
    with pytest.raises(ValueError, match="cycle"):
        parse_taxonomy(broken)


def test_taxonomy_rejects_a_dangling_parent():
    broken = {"data": {"codelists": [{"codes": [{"id": "A", "name": "a", "parent": "GHOST"}]}]}}
    with pytest.raises(ValueError, match="not in the codelist"):
        parse_taxonomy(broken)


def test_taxonomy_rejects_an_empty_payload():
    with pytest.raises(ValueError, match="no codelists"):
        parse_taxonomy({"data": {}})


# --- weights ---


def test_expenditure_class_weights_sum_to_one_hundred():
    series = weights_at(payload())
    assert set(series.index) == {"40005", "40006"}
    assert series.sum() == pytest.approx(100.0)
    assert series.loc["40005"] == pytest.approx(60.0)


def test_summing_every_level_would_have_given_four_hundred():
    """The trap this module exists to prevent."""
    panel = weights_panel(payload())
    national = panel[(panel["measure"] == "1") & (panel["region"] == "50")]
    assert national["value"].sum() == pytest.approx(400.0)
    # ...while the correct level sums to 100.
    assert weights_at(payload()).sum() == pytest.approx(100.0)


def test_refuses_a_level_that_does_not_sum_to_one_hundred():
    bad = payload({ALL_GROUPS_CODE: 100.0, "40005": 60.0, "40006": 25.0})
    with pytest.raises(ValueError, match="sum to 85"):
        weights_at(bad)


def test_can_select_a_higher_level():
    series = weights_at(payload(), level="group")
    assert list(series.index) == ["20001"]
    assert series.sum() == pytest.approx(100.0)


def test_unknown_level_raises():
    with pytest.raises(ValueError, match="no weights for level"):
        weights_at(payload(), level="banana")


def test_panel_rejects_weights_with_no_taxonomy_entry():
    orphan = payload()
    orphan["taxonomy"] = {
        "data": {"codelists": [{"codes": [{"id": "10001", "name": "All groups CPI"}]}]}
    }
    with pytest.raises(ValueError, match="no taxonomy entry"):
        weights_panel(orphan)


def test_payload_missing_a_part_raises():
    with pytest.raises(ValueError, match="missing 'taxonomy'"):
        weights_panel({"weights": weights_doc({ALL_GROUPS_CODE: 100.0})})


def test_latest_reweight_picks_the_newest_period():
    values = {ALL_GROUPS_CODE: 100.0, "40005": 60.0, "40006": 40.0}
    two = {"weights": weights_doc(values, ["2023-Q4"]), "taxonomy": TAXONOMY}
    assert latest_reweight(two) == "2023-Q4"
    assert latest_reweight(payload()) == "2024-Q4"


def test_weights_join_the_price_panel_taxonomy():
    """A weight is only useful if its code matches an INDEX in the price panel."""
    from sdmx_fixtures import all_targets_doc

    from auscpi.parsers.abs_cpi import parse_sdmx_json

    prices = parse_sdmx_json(all_targets_doc(["2026-05", "2026-06"]))
    price_codes = set(prices["index_id"])
    # The fixture panel carries 10001; the real overlap is all 132 weight codes.
    assert ALL_GROUPS_CODE in price_codes
