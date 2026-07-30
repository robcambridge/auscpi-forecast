from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from auscpi.collectors import abs_cpi, registry
from auscpi.collectors.abs_cpi import (
    ABSMonthlyCPICollector,
    ABSQuarterlyCPICollector,
    latest_period,
    observation_periods,
    period_end,
    series_count,
)


def sdmx_doc(periods: list[str], n_series: int = 3, *, plural: bool = True) -> dict:
    """Minimal SDMX-JSON shaped like the ABS response."""
    structure = {
        "dimensions": {
            "observation": [{"id": "TIME_PERIOD", "values": [{"id": p} for p in periods]}],
            "series": [{"id": "MEASURE", "values": [{"id": "1"}]}],
        }
    }
    data = {
        "dataSets": [{"series": {f"0:0:0:0:{i}": {} for i in range(n_series)}}],
    }
    data["structures" if plural else "structure"] = [structure] if plural else structure
    return {"data": data}


def test_period_end_monthly():
    assert period_end("2026-06") == datetime(2026, 6, 30).date()
    assert period_end("2026-02") == datetime(2026, 2, 28).date()
    # Leap year: the end of February moves, and using the period start instead
    # would misreport staleness.
    assert period_end("2024-02") == datetime(2024, 2, 29).date()


def test_period_end_quarterly():
    assert period_end("2026-Q1") == datetime(2026, 3, 31).date()
    assert period_end("2026-Q2") == datetime(2026, 6, 30).date()
    assert period_end("1948-Q3") == datetime(1948, 9, 30).date()
    assert period_end("2025-Q4") == datetime(2025, 12, 31).date()


def test_latest_period_ignores_delivery_order():
    # The API returns periods unsorted, so max() must be by date, not position.
    doc = sdmx_doc(["2026-06", "2017-09", "2026-01"])
    period, ends = latest_period(doc)
    assert period == "2026-06"
    assert ends == datetime(2026, 6, 30).date()


def test_observation_periods_handles_both_structure_spellings():
    assert observation_periods(sdmx_doc(["2026-06"], plural=True)) == ["2026-06"]
    assert observation_periods(sdmx_doc(["2026-06"], plural=False)) == ["2026-06"]


def test_series_count():
    assert series_count(sdmx_doc(["2026-06"], n_series=7)) == 7


def test_both_collectors_registered_with_distinct_keys():
    assert registry["abs_cpi_monthly"] is ABSMonthlyCPICollector
    assert registry["abs_cpi_quarterly"] is ABSQuarterlyCPICollector
    assert ABSMonthlyCPICollector.cadence == "monthly"
    assert ABSQuarterlyCPICollector.cadence == "quarterly"
    # Dimension order is MEASURE.INDEX.TSEST.REGION.FREQ — Australia, then freq.
    assert ABSMonthlyCPICollector.key == "...50.M"
    assert ABSQuarterlyCPICollector.key == "...50.Q"


def test_private_base_is_not_registered():
    assert not any(slug.startswith("_") for slug in registry)
    assert abs_cpi._ABSCPICollector not in registry.values()


class FakeResponse:
    def __init__(self, doc: dict):
        self._doc = doc

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._doc


def _patch_response(monkeypatch, doc: dict) -> list[str]:
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return FakeResponse(doc)

    monkeypatch.setattr(abs_cpi.httpx, "get", fake_get)
    return seen


def _recent_month() -> str:
    today = datetime.now(UTC).date()
    return f"{today.year:04d}-{today.month:02d}"


def test_fetch_returns_document_url_and_series_count(monkeypatch):
    doc = sdmx_doc([_recent_month()], n_series=1191)
    seen = _patch_response(monkeypatch, doc)

    payload, url, n = ABSMonthlyCPICollector().fetch()

    assert payload is doc
    assert n == 1191
    assert url.endswith("/data/ABS,CPI,2.0.0/...50.M")
    assert seen == [url]


def test_fetch_rejects_a_retired_series(monkeypatch):
    """The CPI_M trap: a dataflow that resolves but stopped being updated."""
    stale = datetime.now(UTC).date() - timedelta(days=400)
    _patch_response(monkeypatch, sdmx_doc([f"{stale.year:04d}-{stale.month:02d}"]))

    with pytest.raises(RuntimeError, match="looks retired"):
        ABSMonthlyCPICollector().fetch()


def test_fetch_rejects_an_empty_response(monkeypatch):
    _patch_response(monkeypatch, sdmx_doc([_recent_month()], n_series=0))

    with pytest.raises(RuntimeError, match="no series"):
        ABSMonthlyCPICollector().fetch()


def test_quarterly_tolerates_a_longer_gap_than_monthly(monkeypatch):
    """A quarter prints ~4 weeks after it ends, so the monthly limit is too tight."""
    assert ABSQuarterlyCPICollector.max_staleness_days > ABSMonthlyCPICollector.max_staleness_days

    # 200 days stale: dead by monthly standards, normal-ish for quarterly.
    old = datetime.now(UTC).date() - timedelta(days=200)
    quarter = f"{old.year:04d}-Q{(old.month - 1) // 3 + 1}"
    _patch_response(monkeypatch, sdmx_doc([quarter]))

    payload, _url, _n = ABSQuarterlyCPICollector().fetch()
    assert payload is not None

    _patch_response(monkeypatch, sdmx_doc([f"{old.year:04d}-{old.month:02d}"]))
    with pytest.raises(RuntimeError, match="looks retired"):
        ABSMonthlyCPICollector().fetch()
