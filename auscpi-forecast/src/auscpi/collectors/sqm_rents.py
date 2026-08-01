"""SQM Research — weekly asking rents by postcode. DISABLED: DO NOT SCRAPE.

Status: ruled out on 2026-07-28. SQM's Terms of Service prohibit automated
access in three separate clauses — see docs/DATA_SOURCES.md, "Sources ruled
out", which quotes them. The licence they grant is expressly conditional on
compliance with that section, so scraping voids it outright.

`robots.txt` permits this (`User-agent: *` → `Allow: /`, no Crawl-delay) and an
honest non-browser client is served HTTP 200. That is not permission. robots.txt
is a crawler convention; the Terms of Service are the contract, and where the two
disagree the contract wins. Do not let a green robots.txt talk you back into it.

The replacement is NSW Rental Bond Lodgements: new-lease rents by postcode,
CC-BY, monthly, backfillable to 2021. It measures rents on tenancies as they
start, which is the same lead-over-measured-rents property that made asking rents
worth collecting, so the rent roll-through model loses nothing important.

This module is kept, rather than deleted, because it is the record of what was
checked and why the answer was no — and because it becomes usable immediately if
SQM ever grants a data licence or written consent. It cannot run: `enabled` is
False and fetch() raises before opening a connection. Removing that guard without
a licence in hand would breach the terms and the conduct rules in
docs/DATA_SOURCES.md.

What was verified while the question was still open, kept because it saves
redoing the work if a licence is ever obtained: each weekly-rents page embeds the
full weekly history back to 2009 inline as a `var data = [...]` JSON array, so
one GET per postcode captures everything and no JS rendering is required.
"""

from __future__ import annotations

import csv
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from auscpi.collectors.base import Collector
from auscpi.config import REPO_ROOT

WEEKLY_RENTS_URL = "https://sqmresearch.com.au/weekly-rents.php"
TERMS_URL = "https://sqmresearch.com.au/terms-of-service"
POSTCODE_FILE = REPO_ROOT / "config" / "sqm_postcodes.csv"
TIMEOUT = httpx.Timeout(60.0)

# Raised by fetch(). Spelled out rather than a bare `pass` so that anyone who hits
# it gets the reason and the alternative, not just a stack trace.
BLOCKED_MESSAGE = (
    "sqm_rents is disabled: SQM Research's Terms of Service prohibit automated "
    f"access ({TERMS_URL}). robots.txt permitting it is not permission — the terms "
    "are the binding document. Use NSW Rental Bond Lodgement data instead "
    "(new-lease rents by postcode, CC-BY, backfillable). See docs/DATA_SOURCES.md, "
    "'Sources ruled out'. Do not remove this guard without a data licence or "
    "written consent from SQM."
)

# One request every few seconds, were this ever licensed. robots.txt sets no
# Crawl-delay, so the courteous floor is self-imposed.
REQUEST_SPACING_S = 4.0

# Honest identification with a contact URL, as the conduct rules require. This is
# intentionally NOT a browser User-Agent: we do not disguise the client.
USER_AGENT = (
    "auscpi-forecast/0.1 (+https://github.com/robcambridge/auscpi-forecast; "
    "research rent-index collector)"
)

# Capital-city GPO postcodes: a small, fixed starter basket used when
# config/sqm_postcodes.csv is absent. Strings, not ints — NT postcodes carry a
# leading zero (Darwin is 0800) that int() would silently eat.
DEFAULT_POSTCODES: tuple[str, ...] = (
    "2000",
    "3000",
    "4000",
    "5000",
    "6000",
    "7000",
    "0800",
    "2600",
)


def load_postcodes() -> list[str]:
    """The postcode basket from config/sqm_postcodes.csv, or the default if absent.

    One postcode per line; a `postcode` header and `#` comment lines are ignored.
    Values stay strings because leading zeros are significant.
    """
    if not POSTCODE_FILE.exists():
        return list(DEFAULT_POSTCODES)
    postcodes: list[str] = []
    with POSTCODE_FILE.open(encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            value = row[0].strip()
            if not value or value.startswith("#") or value.lower() == "postcode":
                continue
            postcodes.append(value)
    return postcodes or list(DEFAULT_POSTCODES)


def _is_transient(exc: BaseException) -> bool:
    """Only retry errors that a retry could plausibly fix.

    Timeouts and 5xx/429 are worth another go; a definitive 403/404 is not, and
    retrying it three times just hammers a server that has already said no.
    """
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
def _fetch_postcode(client: httpx.Client, postcode: str) -> str:
    resp = client.get(WEEKLY_RENTS_URL, params={"postcode": postcode, "t": "1"}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


class SQMRentsCollector(Collector):
    source = "sqm_rents"
    cadence = "weekly"
    # Terms of Service prohibit automated access. Not a scheduling decision — do
    # not flip this without a licence from SQM. fetch() refuses regardless.
    enabled = False
    # Not a scheduling decision: their terms prohibit automated access.
    ruled_out = True

    def fetch(self) -> tuple[Any, str, int | None]:
        # Deliberately before any network call: flipping `enabled` alone must not
        # be enough to start scraping a source whose terms forbid it. base.run()
        # turns this into a logged error snapshot rather than a crash.
        raise RuntimeError(BLOCKED_MESSAGE)
