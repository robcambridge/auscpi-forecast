"""SQM Research — weekly asking rents by postcode.

Why this collector is a priority even though it will not run in CI:

Asking rents lead the ABS *measured* rents series, because the ABS prices rents
on the whole stock of dwellings, not on newly advertised leases. When market
rents move, the measured index only catches up as existing leases roll over,
which takes roughly a year. So today's asking rents mechanically constrain
measured rents six to twelve months out — that lag is the rent roll-through
model, the project's main forecasting edge (see docs/DATA_SOURCES.md).

Unlike an API series this is NOT backfillable: there is no archive to reprocess
if we start late, so every week not collected is a week permanently missing from
the sample. That is why it ships now rather than after the parser exists.

Scraping conduct (docs/DATA_SOURCES.md, "Scraping conduct"):

  - robots.txt was checked on 2026-07-27. The generic `User-agent: *` group is
    `Allow: /` with no Crawl-delay. Named AI-training crawlers (ClaudeBot,
    GPTBot, CCBot, Google-Extended, ...) are each `Disallow: /` by token; this
    collector is none of them, identifies honestly as auscpi-forecast, and so
    falls under the permissive `*` group. The site's `Content-Signal: ai-train=no`
    reservation is respected — these snapshots feed a time-series rent index, and
    must never be used to train or fine-tune a model.
  - One request every few seconds (REQUEST_SPACING_S); honest User-Agent with a
    contact URL, deliberately not disguised as a browser. If honest access ever
    stops being served, that is a signal to stop and contact SQM, not to evade.
  - Only the raw HTML is stored. SQM's per-postcode figures are never
    republished; only the derived, aggregated rent index is ever published.

Where the data is: each weekly-rents page embeds the full weekly history back to
2009 inline as a `var data = [...]` JSON array, so a single GET per postcode
captures everything. Extracting it is the build step's job — fetch() returns the
HTML untouched, per the collectors-do-not-parse rule.

Running it: disabled by default. SQM sits behind Cloudflare, which blocks
datacentre IP ranges, so this 403s from GitHub Actions (see collect.yml); run it
weekly from a residential IP with `auscpi collect sqm_rents`. Confirm SQM's Terms
of Use permit automated access before enabling.
"""

from __future__ import annotations

import csv
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from auscpi.collectors.base import Collector
from auscpi.config import REPO_ROOT

WEEKLY_RENTS_URL = "https://sqmresearch.com.au/weekly-rents.php"
POSTCODE_FILE = REPO_ROOT / "config" / "sqm_postcodes.csv"
TIMEOUT = httpx.Timeout(60.0)

# robots.txt sets no Crawl-delay, so we self-impose one. A weekly snapshot of a
# small basket has no reason to hurry, and a few seconds between hits is the
# courteous floor the conduct rules ask for.
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
    # Off by default: Cloudflare blocks datacentre IPs so this 403s in CI, the
    # daily collect.yml would run it far too often, and SQM's Terms of Use should
    # be confirmed first. Enable it on a residential-IP machine on a weekly job.
    enabled = False

    def fetch(self) -> tuple[Any, str, int | None]:
        postcodes = load_postcodes()
        pages: dict[str, str] = {}
        failed: dict[str, str] = {}

        with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
            for i, postcode in enumerate(postcodes):
                if i:
                    time.sleep(REQUEST_SPACING_S)
                try:
                    pages[postcode] = _fetch_postcode(client, postcode)
                except Exception as exc:  # noqa: BLE001
                    # One dead postcode must not lose the rest of the basket. Record
                    # the reason so the gap is explained rather than silent.
                    failed[postcode] = f"{type(exc).__name__}: {exc}"

        if not pages:
            # An empty run is a real failure — most likely the whole IP is blocked.
            # Raise so base.run() writes an error snapshot instead of a useless ok.
            raise RuntimeError(
                f"SQM returned no pages for any of {len(postcodes)} postcodes; "
                f"first error: {next(iter(failed.values()), 'none')}"
            )

        # Raw HTML only, keyed by the postcode we requested. The weekly series is
        # embedded in each page; parsing it belongs in the build step, not here.
        return {"pages": pages, "failed": failed}, WEEKLY_RENTS_URL, len(pages)
