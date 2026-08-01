"""NSW FuelCheck — daily station-level retail fuel prices.

Why this is collector number one:

Automotive fuel is roughly 3.5% of the CPI basket but a far larger share of
month-to-month headline variance, and it is the one component where a nowcast
can be close to deterministic rather than statistical. NSW publishes every
prescribed fuel price at every station in the state. If you collect it daily and
weight it properly, you are not forecasting fuel — you are measuring it, ahead of
the ABS, and the only real error left is the NSW-to-national gap.

Register for a free key at https://api.nsw.gov.au/Product/Index/22
(the product is "Fuel API - Fuel Prices in Real-Time").

Auth is OAuth2 client-credentials against api.onegov.nsw.gov.au. Tokens are
short-lived, so we mint one per run rather than caching.

NOTE ON HISTORY: unlike groceries, fuel IS backfillable. NSW publishes archived
FuelCheck price files on data.nsw.gov.au. Backfilling those is a separate job
(see docs/DATA_SOURCES.md) and should be done early, because it gives you a
multi-year training sample for the fuel component immediately.
"""

from __future__ import annotations

import base64
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from auscpi.collectors.base import Collector
from auscpi.config import settings

TOKEN_URL = "https://api.onegov.nsw.gov.au/oauth/client_credential/accesstoken"
PRICES_URL = "https://api.onegov.nsw.gov.au/FuelPriceCheck/v1/fuel/prices"
TIMEOUT = httpx.Timeout(60.0)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=20))
def _get_token(key: str, secret: str) -> str:
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    resp = httpx.get(
        TOKEN_URL,
        params={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


class FuelCheckCollector(Collector):
    source = "fuelcheck"
    cadence = "daily"

    def fetch(self) -> tuple[Any, str, int | None]:
        if not settings.fuelcheck_api_key or not settings.fuelcheck_api_secret:
            raise RuntimeError(
                "FUELCHECK_API_KEY / FUELCHECK_API_SECRET not set. "
                "Register at https://api.nsw.gov.au/Product/Index/22 and copy "
                ".env.example to .env."
            )

        token = _get_token(settings.fuelcheck_api_key, settings.fuelcheck_api_secret)
        now = datetime.now(UTC)
        headers = {
            "Authorization": f"Bearer {token}",
            "apikey": settings.fuelcheck_api_key,
            "transactionid": str(uuid.uuid4()),
            "requesttimestamp": now.strftime("%d/%m/%Y %I:%M:%S %p"),
            "Content-Type": "application/json",
        }
        resp = httpx.get(PRICES_URL, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        n = len(data.get("prices", [])) if isinstance(data, dict) else None
        return data, PRICES_URL, n


# --- archive backfill -----------------------------------------------------
#
# The live API above returns a snapshot of prices RIGHT NOW and nothing else, so it
# cannot answer what fuel cost last March. NSW publishes that separately as monthly
# price-history files on data.nsw.gov.au, which is what makes fuel backfillable at
# all — and the whole reason the roadmap says to do it early, since it turns a
# component with one day of history into one with years.

CKAN_PACKAGE_URL = "https://data.nsw.gov.au/data/api/3/action/package_show"
CKAN_PACKAGE_ID = "fuel-check"

#: Courtesy spacing between archive downloads. These files run to tens of megabytes.
ARCHIVE_SPACING_S = 3.0
ARCHIVE_TIMEOUT = httpx.Timeout(300.0)

ARCHIVE_USER_AGENT = (
    "auscpi-forecast/0.1 (+https://github.com/robcambridge/auscpi-forecast; "
    "research fuel price collector)"
)

#: Month names as the archive actually writes them, which is inconsistently. Titles
#: mix full names ("FuelCheck Price History June 2026") with abbreviations ("Feb
#: 2024", "Sep 2019"), and the prefix varies too ("Service Station & Price History
#: Mar 2019"). Matching only full names skipped 23 of 119 published files, including
#: ten consecutive months of 2024 — a backfill that quietly captures two thirds of
#: the archive is worse than one that fails, because the gap only shows up much later
#: as a model trained on less data than it claims.
MONTH_PATTERNS: tuple[tuple[str, int], ...] = (
    (r"jan(?:uary)?", 1),
    (r"feb(?:ruary)?", 2),
    (r"mar(?:ch)?", 3),
    (r"apr(?:il)?", 4),
    (r"may", 5),
    (r"jun(?:e)?", 6),
    (r"jul(?:y)?", 7),
    (r"aug(?:ust)?", 8),
    (r"sep(?:t(?:ember)?)?", 9),
    (r"oct(?:ober)?", 10),
    (r"nov(?:ember)?", 11),
    (r"dec(?:ember)?", 12),
)

_MONTH_RE = re.compile(r"\b(" + "|".join(p for p, _ in MONTH_PATTERNS) + r")\b", re.IGNORECASE)


def month_number(token: str) -> int | None:
    """Calendar month for a name or abbreviation, or None if it is neither."""
    for pattern, number in MONTH_PATTERNS:
        if re.fullmatch(pattern, token, re.IGNORECASE):
            return number
    return None


@dataclass(frozen=True)
class ArchiveFile:
    """One published monthly price-history file, located but not opened."""

    url: str
    name: str
    year: int
    month: int
    fmt: str
    size: int | None

    @property
    def period(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def _parse_archive_period(name: str) -> tuple[int, int] | None:
    """Pull (year, month) out of a resource title like 'FuelCheck Price History June 2026'."""
    month_hit = _MONTH_RE.search(name)
    year_hit = re.search(r"(20\d{2})", name)
    if not month_hit or not year_hit:
        return None
    month = month_number(month_hit.group(1))
    if month is None:
        return None
    return int(year_hit.group(1)), month


def discover_archive_files(package: dict[str, Any]) -> list[ArchiveFile]:
    """The monthly price-history resources in a CKAN package payload.

    Deciding WHICH resource to fetch is navigation, not parsing — nothing about a
    fuel price is interpreted here. The dataset also carries FAQ links, a data
    quality statement and other odds and ends, so resources are matched on the
    "price history" title rather than taken wholesale.
    """
    out: dict[str, ArchiveFile] = {}
    for res in package.get("resources", []):
        name = str(res.get("name") or "")
        if "price history" not in name.lower():
            continue
        period = _parse_archive_period(name)
        url = str(res.get("url") or "")
        if period is None or not url or url in out:
            continue
        year, month = period
        size = res.get("size")
        out[url] = ArchiveFile(
            url=url,
            name=name,
            year=year,
            month=month,
            fmt=str(res.get("format") or ""),
            size=int(size) if size else None,
        )
    return sorted(out.values(), key=lambda f: (f.year, f.month), reverse=True)


def _check_archive_payload(blob: bytes, url: str, fmt: str) -> None:
    """Refuse an error page dressed up as data.

    A CMS answering 200 with an HTML "not found" body is how a collector rots
    quietly: runs stay green for months and the snapshots are worthless. The archive
    mixes xlsx and csv, so the check is per format — xlsx is a zip and must start
    with PK, csv must not start with markup.
    """
    head = blob[:200].lstrip()
    if b"xlsx" in fmt.lower().encode() or fmt.upper() == "XLSX":
        if not blob.startswith(b"PK"):
            raise RuntimeError(
                f"expected an xlsx (PK...) from {url}, got {len(blob)} bytes: "
                f"{head[:80].decode('utf-8', 'replace')!r}"
            )
    elif head[:1] == b"<":
        raise RuntimeError(
            f"expected data from {url}, got markup: {head[:80].decode('utf-8', 'replace')!r}"
        )
    if not blob:
        raise RuntimeError(f"empty response from {url}")


def fetch_archive_index() -> dict[str, Any]:
    """The CKAN package describing the FuelCheck archive."""
    resp = httpx.get(
        CKAN_PACKAGE_URL,
        params={"id": CKAN_PACKAGE_ID},
        headers={"User-Agent": ARCHIVE_USER_AGENT},
        timeout=ARCHIVE_TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"CKAN refused {CKAN_PACKAGE_ID}: {body!r}")
    return body["result"]


def backfill(
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> list[str]:
    """Snapshot published monthly price-history files. Returns paths written.

    `since` and `until` are inclusive "YYYY-MM" bounds; omit for everything
    published. The archive runs to roughly 400 MB back to 2016, and data/raw is
    tracked in git, so bounding it is usually the right call rather than the
    exception.

    Already-captured URLs are skipped, so this resumes rather than restarting and is
    safe to re-run with a wider range later.

    Provenance consequence, same as the bond backfill: these snapshots honestly
    record fetched_at = now, so snapshots_as_at() will not pretend we held 2023 fuel
    prices in 2023. Correct, and it means the archive can train the fuel component
    but cannot support a claim of real-time skill over the backfilled period.
    """
    from auscpi.storage import read_manifest, write_snapshot

    already = {m["url"] for m in read_manifest(FuelCheckCollector.source)}
    files = discover_archive_files(fetch_archive_index())
    todo = [
        f
        for f in files
        if f.url not in already
        and (since is None or f.period >= since)
        and (until is None or f.period <= until)
    ]
    if limit is not None:
        todo = todo[:limit]

    written: list[str] = []
    with httpx.Client(
        headers={"User-Agent": ARCHIVE_USER_AGENT}, follow_redirects=True
    ) as client:
        for i, archive in enumerate(todo):
            if i:
                time.sleep(ARCHIVE_SPACING_S)
            resp = client.get(archive.url, timeout=ARCHIVE_TIMEOUT)
            resp.raise_for_status()
            blob = resp.content
            _check_archive_payload(blob, archive.url, archive.fmt)
            snap = write_snapshot(
                FuelCheckCollector.source,
                blob,
                url=archive.url,
                fetched_at=datetime.now(UTC),
                note=f"archive {archive.period}",
            )
            written.append(snap.payload_path)
    return written
