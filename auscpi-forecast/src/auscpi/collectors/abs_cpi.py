"""ABS Consumer Price Index — the target series.

Everything else in this repository collects predictors. This collects the thing
being predicted, so it is also what makes the benchmarks runnable, what fills
`actual` in forecasts/log.csv, and what supplies the *dependent* variable for the
rent roll-through model (bond lodgements are only its left-hand side).

Reference month N is released on the last Wednesday of month N+1 at 11:30am
AEST/AEDT. From February 2027 this moves to the FOURTH Wednesday of the month —
do not hardcode "last Wednesday" anywhere; read config/release_calendar.csv.

DATAFLOW IDENTIFIER, verified against the live API on 2026-07-30:

    ABS,CPI,2.0.0    both monthly and quarterly, current

The previous placeholder here was `ABS,CPI_M,1.0.0`, and it was wrong twice over.
`GET /rest/dataflow/ABS` lists four CPI flows: CPI 2.0.0, CPI_M 1.2.0,
CPI_Q 1.0.0 and CPI_WEIGHTS 1.0.0. There is no CPI_M version 1.0.0 at all — that
request 404s with "Could not find Dataflow and/or DSD". More dangerous, CPI_M
*1.2.0* resolves but is the retired monthly *indicator*: its latest observation is
2025-09, it carries 39 index values rather than 166, and it is not being updated.
Collecting it would have produced a green pipeline built on a target series frozen
ten months in the past, which is exactly the silent failure this project exists to
avoid. The ABS consolidated monthly and quarterly into CPI 2.0.0.

KEY STRUCTURE. Dimension order is MEASURE.INDEX.TSEST.REGION.FREQ, so an empty
slot is a wildcard and `...50.M` means "every measure, every index, every
adjustment, Australia, monthly". Confirmed by size: `...50.M` returns 1,191 series
where `....M` (all regions) returns 8,055.

WHAT THE SLICE CONTAINS. All 166 index values, including 10001 "All groups CPI"
and 999902 "Trimmed Mean" — the two headline targets in track_record — plus every
expenditure class for the bottom-up build, at MEASURE 1 (index numbers), 2 (change
on previous period) and 3 (change on previous year). Monthly runs 2017-09 to
2026-06 and quarterly back to 1948-Q3, which is the long sample the
Atkeson–Ohanian and random-walk benchmarks need.

Capital-city detail (REGION 1..8) is deliberately left out: it is six times the
payload every month and the forecast target is the national series. Add a region
collector if a city breakdown is ever wanted.

RE-REFERENCING. The quarterly series was re-referenced in the December 2025
release from 2011-12 = 100.0 to September month 2025 = 100.00, published to two
decimal places. Mixing pre- and post-re-referencing vintages without applying the
conversion factors gives silently wrong index levels. Factors are series-specific
and unrounded — recompute rather than reusing a published rounded factor. This
collector stores vintages untouched; it is the build step's problem.

SCHEDULING: `enabled = False` means "not in the daily run", not "broken" — cf.
nsw_rental_bonds. `collect --all` is daily and this changes monthly, so it runs
from .github/workflows/collect-abs.yml on the 1st, safely after the
last-Wednesday release. Naming a source explicitly ignores the flag:

    auscpi collect abs_cpi_monthly
    auscpi collect abs_cpi_quarterly
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from auscpi.collectors.base import Collector
from auscpi.periods import period_end

DATAFLOW_BASE = "https://data.api.abs.gov.au/rest"
DATAFLOW = "ABS,CPI,2.0.0"
TIMEOUT = httpx.Timeout(300.0)

# SDMX-JSON. The ABS also serves XML, but JSON round-trips losslessly through
# storage.write_snapshot and matches what the other collectors return.
ACCEPT = "application/vnd.sdmx.data+json"

USER_AGENT = (
    "auscpi-forecast/0.1 (+https://github.com/robcambridge/auscpi-forecast; "
    "research CPI forecast collector)"
)

# Dimension order: MEASURE.INDEX.TSEST.REGION.FREQ. Empty slot = wildcard.
REGION_AUSTRALIA = "50"
KEY_MONTHLY = f"...{REGION_AUSTRALIA}.M"
KEY_QUARTERLY = f"...{REGION_AUSTRALIA}.Q"


def _structure(doc: dict[str, Any]) -> dict[str, Any]:
    """The structure block, whichever SDMX-JSON spelling the service used."""
    data = doc["data"]
    if "structures" in data:
        return data["structures"][0]
    return data["structure"]


def observation_periods(doc: dict[str, Any]) -> list[str]:
    """Every TIME_PERIOD id present in the response, unsorted as delivered."""
    obs = _structure(doc)["dimensions"]["observation"]
    for dim in obs:
        if dim["id"] in ("TIME_PERIOD", "TIME"):
            return [v["id"] for v in dim["values"]]
    return [v["id"] for v in obs[0]["values"]]


def latest_period(doc: dict[str, Any]) -> tuple[str, date]:
    periods = observation_periods(doc)
    if not periods:
        raise RuntimeError("ABS response contained no time periods")
    dated = [(p, period_end(p)) for p in periods]
    return max(dated, key=lambda pair: pair[1])


def series_count(doc: dict[str, Any]) -> int | None:
    datasets = doc["data"].get("dataSets") or []
    if not datasets:
        return None
    return len(datasets[0].get("series", {}))


class _ABSCPICollector(Collector):
    """Shared fetch for the CPI dataflow. Subclasses pick the frequency slice."""

    key: str
    #: How stale the newest observation may be before we treat the series as dead.
    #: Generous enough to survive normal release timing, tight enough to catch a
    #: retired dataflow — the CPI_M trap above was ten months stale.
    max_staleness_days: int

    def fetch(self) -> tuple[Any, str, int | None]:
        url = f"{DATAFLOW_BASE}/data/{DATAFLOW}/{self.key}"
        resp = httpx.get(
            url,
            headers={"Accept": ACCEPT, "User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        doc = resp.json()

        n = series_count(doc)
        if not n:
            raise RuntimeError(f"ABS returned no series for {DATAFLOW} key {self.key!r}")

        period, ends = latest_period(doc)
        stale_days = (datetime.now(UTC).date() - ends).days
        if stale_days > self.max_staleness_days:
            # Refuse rather than snapshot a dead series. Recoverable: the API
            # serves history on demand, so nothing is lost by failing loudly, and
            # base.run() records the reason in the manifest.
            raise RuntimeError(
                f"{DATAFLOW} key {self.key!r} looks retired: newest observation is "
                f"{period} ({stale_days} days old, limit {self.max_staleness_days}). "
                "Re-check the dataflow id against "
                f"{DATAFLOW_BASE}/dataflow/ABS before trusting this series."
            )

        return doc, url, n


class ABSMonthlyCPICollector(_ABSCPICollector):
    source = "abs_cpi_monthly"
    cadence = "monthly"
    enabled = False  # monthly cadence, daily runner — see SCHEDULING above
    key = KEY_MONTHLY
    # Month N prints late in N+1, so ~65 days is normal at worst.
    max_staleness_days = 150


class ABSQuarterlyCPICollector(_ABSCPICollector):
    source = "abs_cpi_quarterly"
    cadence = "quarterly"
    enabled = False
    key = KEY_QUARTERLY
    # A quarter prints about four weeks after it ends, so ~120 days is normal.
    max_staleness_days = 240
