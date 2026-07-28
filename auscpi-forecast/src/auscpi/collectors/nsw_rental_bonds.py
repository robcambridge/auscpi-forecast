"""NSW rental bond lodgements — monthly new-lease rents by postcode.

This is the rent indicator for the roll-through model, replacing SQM asking
rents (ruled out: their terms prohibit automated access — see
docs/DATA_SOURCES.md, "Sources ruled out").

Why bond lodgements are the right series, not merely the available one:

A bond is lodged when a tenancy *starts*, so every row is a newly agreed rent.
The ABS prices rents on the whole stock of dwellings, so measured rents only move
as existing leases roll over — roughly a year. New-lease rents therefore lead
measured rents by 6–12 months almost mechanically, which is the entire basis of
the roll-through model. These are transacted rents on signed leases rather than
advertised asking prices, so unlike asking rents they carry no listing or
withdrawal bias.

Each monthly file is record-level: one row per lodgement, with columns
Lodgement Date, Postcode, Dwelling Type, Bedrooms, Weekly Rent (~25k rows,
~675 KB). Nothing is pre-aggregated, so the build step can weight and aggregate
however it likes. Annual compilation files also exist (~322k rows, ~8 MB) and are
just the year's monthlies concatenated.

Published by NSW Fair Trading under Creative Commons Attribution, from the same
data.nsw.gov.au family already used for the FuelCheck archives. robots.txt on
both www.nsw.gov.au and data.nsw.gov.au was checked on 2026-07-28: the
`User-agent: *` group allows these paths (its Disallow entries are Drupal
admin/core boilerplate).

NSW only, where SQM was national — the same limitation the fuel component already
has, so estimate the NSW-to-national gap the same way.

SCHEDULING: `enabled = False` here means "not part of the daily run", NOT
"prohibited" — the distinction matters, cf. sqm_rents. cli.collect --all has no
cadence filter, so leaving it enabled would re-download the same 675 KB file
every day for a series that changes monthly. It runs on its own monthly schedule
(.github/workflows/collect-monthly.yml), and naming the source explicitly works
regardless of this flag:

    auscpi collect nsw_rental_bonds

BACKFILL: unlike asking rents this is backfillable — monthly files run back to
January 2022 plus annual compilations. `backfill()` captures them. Note the
provenance consequence: backfilled snapshots honestly record fetched_at = now, so
snapshots_as_at() will not pretend we held 2023 data in 2023. That is correct, and
it means this data can estimate the roll-through lag distribution but cannot
support a claim of real-time forecast skill over the backfilled period.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from auscpi.collectors.base import Collector
from auscpi.storage import write_snapshot

ORIGIN = "https://www.nsw.gov.au"
INDEX_URL = (
    "https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data"
)
TIMEOUT = httpx.Timeout(120.0)

# Courtesy spacing between file downloads during a backfill. robots.txt sets no
# Crawl-delay; a few seconds is the floor the conduct rules ask for.
REQUEST_SPACING_S = 3.0

USER_AGENT = (
    "auscpi-forecast/0.1 (+https://github.com/robcambridge/auscpi-forecast; "
    "research rent-index collector)"
)

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True)
class BondFile:
    """One published spreadsheet, located but not opened."""

    url: str
    filename: str
    year: int
    month: int | None  # None for an annual compilation

    @property
    def is_annual(self) -> bool:
        return self.month is None

    @property
    def label(self) -> str:
        return f"{self.year}" if self.month is None else f"{self.year}-{self.month:02d}"

    @property
    def sort_key(self) -> tuple[int, int]:
        # Annual files sort before that year's monthlies; they are a summary of it.
        return (self.year, self.month or 0)


def _parse_period(filename: str) -> tuple[int, int | None] | None:
    """Pull (year, month) out of a filename, or None if it is not a period file.

    The published names are wildly inconsistent — `rentalbond_lodgements_june_2026`,
    `rental-bond-lodgement-data-july-2025`, `rentalbond_lodgements_september25`,
    `RentalBond_Lodgements_December_2023`, `..._February_2023-2`, `..._june_2025_0`
    — so nothing can be templated and the period has to be read back off the name.
    The containing directory is no help either: /2024-05/ holds files for March,
    February and January 2024 as well as December 2023.
    """
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()

    # Annual compilations: "..._year_2025", "...-year-2024_1".
    annual = re.search(r"year[_-](\d{4})", stem)
    if annual:
        return int(annual.group(1)), None

    month_hit = re.search(r"(" + "|".join(MONTHS) + r")", stem)
    if not month_hit:
        return None
    month = MONTHS[month_hit.group(1)]

    # Year may be four digits ("june_2026") or two glued to the month
    # ("september25"). Look only at what follows the month name so a stray number
    # earlier in the name cannot be mistaken for the year.
    tail = stem[month_hit.end() :]
    four = re.search(r"(20\d{2})", tail)
    if four:
        return int(four.group(1)), month
    two = re.match(r"[_-]?(\d{2})(?!\d)", tail)
    if two:
        return 2000 + int(two.group(1)), month
    return None


def discover_lodgement_files(html: str) -> list[BondFile]:
    """Locate the lodgement spreadsheets linked from the index page.

    Deciding *which* URL to fetch is navigation, not parsing — no bond data is
    interpreted here, and the manifest records the exact file URL captured. If
    this picked the wrong link the mistake is recoverable, because NSW keeps the
    archive up; that is precisely what is not true of scraped prices.
    """
    files: dict[str, BondFile] = {}
    for href in re.findall(r'href="([^"]+\.xlsx)"', html, re.IGNORECASE):
        name = href.rsplit("/", 1)[-1]
        low = name.lower()
        # Refunds and holdings are different series published on the same page.
        if "lodge" not in low:
            continue
        period = _parse_period(name)
        if period is None:
            continue
        url = href if href.startswith("http") else ORIGIN + href
        if url in files:
            continue
        year, month = period
        files[url] = BondFile(url=url, filename=name, year=year, month=month)
    return sorted(files.values(), key=lambda f: f.sort_key, reverse=True)


def _is_transient(exc: BaseException) -> bool:
    """Retry only what a retry could fix; never hammer a definitive 403/404."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)
def _get(client: httpx.Client, url: str) -> httpx.Response:
    resp = client.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def _check_is_xlsx(blob: bytes, url: str) -> None:
    """Guard against silently snapshotting an error page as if it were data.

    A CMS that answers 200 with an HTML "not found" page is the classic way a
    collector rots quietly: the run goes green for months and the snapshots are
    worthless. xlsx is a zip, so it must start with PK.
    """
    if not blob.startswith(b"PK"):
        head = blob[:80].decode("utf-8", "replace").strip()
        raise RuntimeError(f"expected an xlsx (PK...) from {url}, got {len(blob)} bytes: {head!r}")


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True)


class NSWRentalBondsCollector(Collector):
    source = "nsw_rental_bonds"
    cadence = "monthly"
    # Not part of `collect --all`: that job is daily and this file changes monthly.
    # See SCHEDULING in the module docstring. This is a cadence decision, not a
    # permission one — the data is CC-BY and free to collect.
    enabled = False

    def fetch(self) -> tuple[Any, str, int | None]:
        with _client() as client:
            index = _get(client, INDEX_URL).text
            files = [f for f in discover_lodgement_files(index) if not f.is_annual]
            if not files:
                raise RuntimeError(
                    f"no monthly lodgement spreadsheets found at {INDEX_URL}; "
                    "the page layout has probably changed"
                )
            newest = files[0]
            blob = _get(client, newest.url).content

        _check_is_xlsx(blob, newest.url)
        # Raw bytes, exactly as published. The build step reads the workbook.
        return blob, newest.url, None


def backfill(*, include_annual: bool = False, limit: int | None = None) -> list[str]:
    """Snapshot the published history, one snapshot per file. Returns paths written.

    Monthly files run back to January 2022. Annual compilations (~8 MB each) cover
    the same rows as that year's monthlies, so they are off by default — taking
    both would duplicate every record and double the repository weight.

    Files already captured are skipped, so this is safe to re-run: it resumes
    rather than restarting, and never rewrites data/raw.
    """
    from auscpi.storage import read_manifest

    already = {m["url"] for m in read_manifest(NSWRentalBondsCollector.source)}
    written: list[str] = []

    with _client() as client:
        index = _get(client, INDEX_URL).text
        files = discover_lodgement_files(index)
        if not include_annual:
            files = [f for f in files if not f.is_annual]
        todo = [f for f in files if f.url not in already]
        if limit is not None:
            todo = todo[:limit]

        for i, bond_file in enumerate(todo):
            if i:
                time.sleep(REQUEST_SPACING_S)
            blob = _get(client, bond_file.url).content
            _check_is_xlsx(blob, bond_file.url)
            snap = write_snapshot(
                NSWRentalBondsCollector.source,
                blob,
                url=bond_file.url,
                fetched_at=datetime.now(UTC),
                note=f"backfill {bond_file.label}",
            )
            written.append(snap.payload_path)

    return written
