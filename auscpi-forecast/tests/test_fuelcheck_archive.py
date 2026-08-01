from __future__ import annotations

import pytest

from auscpi.collectors.fuelcheck import (
    _check_archive_payload,
    _parse_archive_period,
    discover_archive_files,
    month_number,
)


def resource(name: str, url: str = "", fmt: str = "XLSX", size: int | None = 1000) -> dict:
    return {"name": name, "url": url or f"https://x/{name}.xlsx", "format": fmt, "size": size}


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("January", 1),
        ("Jan", 1),
        ("Feb", 2),
        ("Sep", 9),
        ("Sept", 9),
        ("September", 9),
        ("Dec", 12),
        ("December", 12),
        ("May", 5),
        ("Smarch", None),
    ],
)
def test_month_number_accepts_the_forms_the_archive_actually_uses(token, expected):
    assert month_number(token) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("FuelCheck Price History June 2026", (2026, 6)),
        ("FuelCheck Price History Feb 2024", (2024, 2)),
        ("Fuelcheck Price History Dec 2018.xlsx", (2018, 12)),
        ("Service Station & Price History Sep 2019", (2019, 9)),
        ("FuelCheck FAQ", None),
    ],
)
def test_period_is_read_from_the_published_title(title, expected):
    """The archive mixes full and abbreviated months, and varies the title prefix.

    Matching only full month names skipped 23 of 119 published files, including ten
    consecutive months of 2024. This pins the forms that actually occur.
    """
    assert _parse_archive_period(title) == expected


def test_discovery_ignores_everything_that_is_not_price_history():
    """The dataset also carries FAQs, a data quality statement and website links."""
    package = {
        "resources": [
            resource("FuelCheck Price History June 2026"),
            resource("FuelCheck Price History May 2026"),
            resource("FuelCheck FAQ", fmt="website link"),
            resource("FuelCheck Data Quality Statement - PDF", fmt="DQS - PDF"),
        ]
    }
    files = discover_archive_files(package)
    assert [f.period for f in files] == ["2026-06", "2026-05"]


def test_discovery_is_newest_first_and_deduplicates_urls():
    package = {
        "resources": [
            resource("FuelCheck Price History Mar 2024", url="https://x/a"),
            resource("FuelCheck Price History Jan 2024", url="https://x/b"),
            resource("FuelCheck Price History Mar 2024", url="https://x/a"),  # same file again
        ]
    }
    files = discover_archive_files(package)
    assert [f.period for f in files] == ["2024-03", "2024-01"]


def test_a_resource_without_a_readable_period_is_skipped_not_guessed():
    package = {"resources": [resource("FuelCheck Price History"), resource("Prices 2024")]}
    assert discover_archive_files(package) == []


def test_an_html_error_page_is_refused_rather_than_snapshotted():
    """A CMS answering 200 with a not-found page is how a collector rots quietly."""
    html = b"<!DOCTYPE html><html><body>Not found</body></html>"
    with pytest.raises(RuntimeError, match="expected an xlsx"):
        _check_archive_payload(html, "https://x/a", "XLSX")
    with pytest.raises(RuntimeError, match="got markup"):
        _check_archive_payload(html, "https://x/a", "CSV")


def test_a_real_xlsx_and_a_real_csv_both_pass():
    """The archive mixes formats, so the guard has to accept both."""
    _check_archive_payload(b"PK\x03\x04rest of a zip", "https://x/a", "XLSX")
    _check_archive_payload(b"PK\x03\x04rest of a zip", "https://x/a", "excel (.xlsx)")
    _check_archive_payload(b"ServiceStationName,Address,Price\n7-Eleven,...", "https://x/a", "CSV")


def test_an_empty_response_is_refused():
    with pytest.raises(RuntimeError, match="empty response"):
        _check_archive_payload(b"", "https://x/a", "CSV")
