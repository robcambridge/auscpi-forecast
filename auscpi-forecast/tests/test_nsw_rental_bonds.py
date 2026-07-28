from __future__ import annotations

import pytest

from auscpi.collectors import nsw_rental_bonds as bonds
from auscpi.collectors import registry
from auscpi.collectors.nsw_rental_bonds import (
    NSWRentalBondsCollector,
    _check_is_xlsx,
    _parse_period,
    discover_lodgement_files,
)

# Real published filenames. The naming is inconsistent enough that these are the
# actual test surface: underscores vs hyphens, "lodgement" vs "lodgements",
# two-digit years, duplicate suffixes, and varying capitalisation.
REAL_NAMES = [
    ("rentalbond_lodgements_june_2026.xlsx", (2026, 6)),
    ("rentalbond_lodgements_september25.xlsx", (2025, 9)),
    ("rental-bond-lodgement-data-july-2025.xlsx", (2025, 7)),
    ("rentalbond_lodgements_june_2025_0.xlsx", (2025, 6)),
    ("rental-bond-lodgements-data-january-2025.xlsx", (2025, 1)),
    ("RentalBond_Lodgements_December_2023.xlsx", (2023, 12)),
    ("RentalBond_Lodgements_February_2023-2.xlsx", (2023, 2)),
    ("Rental-bond-lodgements-April-2022.xlsx", (2022, 4)),
    ("rentalbond_lodgements_year_2025.xlsx", (2025, None)),
    ("rental-bond-lodgements-year-2024_1.xlsx", (2024, None)),
]


@pytest.mark.parametrize(("name", "expected"), REAL_NAMES)
def test_parse_period_handles_real_filenames(name, expected):
    assert _parse_period(name) == expected


def test_parse_period_rejects_a_file_with_no_period():
    assert _parse_period("rental-bond-holdings.xlsx") is None


def test_registered_and_monthly():
    assert registry.get("nsw_rental_bonds") is NSWRentalBondsCollector
    assert NSWRentalBondsCollector.cadence == "monthly"


INDEX_HTML = """
<a href="/sites/default/files/noindex/2026-07/rentalbond_lodgements_june_2026.xlsx">Jun</a>
<a href="/sites/default/files/noindex/2026-06/rentalbond_lodgements_may_2026.xlsx">May</a>
<a href="/sites/default/files/noindex/2025-10/rentalbond_lodgements_september25.xlsx">Sep</a>
<a href="/sites/default/files/noindex/2026-01/rentalbond_lodgements_year_2025.xlsx">2025</a>
<a href="/sites/default/files/noindex/2026-07/rentalbond_refunds_june_2026.xlsx">Refunds</a>
<a href="/sites/default/files/noindex/2026-07/rental-bond-holdings-2026.xlsx">Holdings</a>
"""


def test_discover_finds_lodgements_only_and_sorts_newest_first():
    found = discover_lodgement_files(INDEX_HTML)
    assert [f.label for f in found] == ["2026-06", "2026-05", "2025-09", "2025"]
    # Refunds and holdings are different series on the same page.
    assert all("refund" not in f.filename.lower() for f in found)
    assert all("holding" not in f.filename.lower() for f in found)


def test_discover_makes_urls_absolute():
    assert discover_lodgement_files(INDEX_HTML)[0].url.startswith("https://www.nsw.gov.au/")


def test_annual_files_are_flagged():
    annual = [f for f in discover_lodgement_files(INDEX_HTML) if f.is_annual]
    assert [f.label for f in annual] == ["2025"]


def test_check_is_xlsx_rejects_an_html_error_page():
    # The silent-rot failure mode: a CMS answering 200 with a "not found" page.
    with pytest.raises(RuntimeError, match="expected an xlsx"):
        _check_is_xlsx(b"<!DOCTYPE html><html>Not found</html>", "http://x/y.xlsx")


def test_check_is_xlsx_accepts_a_zip_header():
    _check_is_xlsx(b"PK\x03\x04rest-of-workbook", "http://x/y.xlsx")


def test_fetch_returns_raw_bytes_of_the_newest_monthly(monkeypatch):
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, text: str = "", content: bytes = b""):
            self.text = text
            self.content = content

    def fake_get(client, url):
        calls.append(url)
        if url == bonds.INDEX_URL:
            return FakeResponse(text=INDEX_HTML)
        return FakeResponse(content=b"PK\x03\x04workbook-bytes")

    monkeypatch.setattr(bonds, "_get", fake_get)
    monkeypatch.setattr(bonds, "_client", lambda: _NullClient())

    payload, url, n = NSWRentalBondsCollector().fetch()

    # The newest MONTHLY file, not the annual compilation.
    assert url.endswith("rentalbond_lodgements_june_2026.xlsx")
    assert payload == b"PK\x03\x04workbook-bytes"
    assert n is None
    assert calls[0] == bonds.INDEX_URL


def test_fetch_raises_if_the_page_layout_changes(monkeypatch):
    class FakeResponse:
        text = "<html>no spreadsheets here</html>"
        content = b""

    monkeypatch.setattr(bonds, "_get", lambda client, url: FakeResponse())
    monkeypatch.setattr(bonds, "_client", lambda: _NullClient())

    with pytest.raises(RuntimeError, match="no monthly lodgement spreadsheets"):
        NSWRentalBondsCollector().fetch()


class _NullClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
