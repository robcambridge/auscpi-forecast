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

Capital-city detail (REGION 1..8) is deliberately left out of the national slices:
all indexes across all regions is 8,055 series against 1,191, and the forecast
target is the national series. `abs_cpi_regional` below is that region collector,
and it stays narrow — a short list of expenditure classes rather than the lot.

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

#: The published expenditure weights, and the codelist that gives them a hierarchy.
DATAFLOW_WEIGHTS = "ABS,CPI_WEIGHTS,1.0.0"
WEIGHTS_CODELIST = "CL_CPI_WEIGHTS_INDEX"
ACCEPT_STRUCTURE = "application/vnd.sdmx.structure+json;version=1.0"

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

    dataflow: str = DATAFLOW
    key: str
    #: How stale the newest observation may be before we treat the series as dead.
    #: Generous enough to survive normal release timing, tight enough to catch a
    #: retired dataflow — the CPI_M trap above was ten months stale.
    max_staleness_days: int

    def _get_slice(self) -> tuple[dict[str, Any], str, int]:
        url = f"{DATAFLOW_BASE}/data/{self.dataflow}/{self.key}"
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
            raise RuntimeError(f"ABS returned no series for {self.dataflow} key {self.key!r}")

        period, ends = latest_period(doc)
        stale_days = (datetime.now(UTC).date() - ends).days
        if stale_days > self.max_staleness_days:
            # Refuse rather than snapshot a dead series. Recoverable: the API
            # serves history on demand, so nothing is lost by failing loudly, and
            # base.run() records the reason in the manifest.
            raise RuntimeError(
                f"{self.dataflow} key {self.key!r} looks retired: newest observation is "
                f"{period} ({stale_days} days old, limit {self.max_staleness_days}). "
                "Re-check the dataflow id against "
                f"{DATAFLOW_BASE}/dataflow/ABS before trusting this series."
            )

        return doc, url, n

    def fetch(self) -> tuple[Any, str, int | None]:
        doc, url, n = self._get_slice()
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


#: Expenditure classes worth having capital-city detail for. Deliberately a short
#: list, not a wildcard: all indexes across all regions is 8,055 series where this
#: is 56, and every entry has to earn its place by being a component whose
#: predictor is city-specific.
#:
#: 30014 is Rents, the target of the roll-through model. 115522 is ALSO published
#: as "Rents" — same name, different code — so both are captured and the build
#: decides. Collecting one and silently getting the other is the kind of mistake
#: that only shows up months later.
REGIONAL_INDEX_CLASSES = ("30014", "115522")

#: SDMX unions codes on a dimension with "+".
KEY_REGIONAL_MONTHLY = f".{'+'.join(REGIONAL_INDEX_CLASSES)}...M"


class ABSRegionalCPICollector(_ABSCPICollector):
    """Capital-city detail for the few classes whose predictors are city-specific.

    WHY THIS EXISTS. The rent roll-through is estimated from NSW bond lodgements
    against national measured rents, and the pass-through comes out near 0.5 — new
    leases move about twice as much as measured rents. Some of that gap is
    geography rather than economics: Sydney ran hotter than the national average
    over the sample, so a NSW predictor against an eight-city target is mismatched
    by construction. Sydney rents make that testable instead of assumed.

    NSW bond lodgements are Sydney-dominated but not Sydney-only, so this narrows
    the mismatch rather than removing it. Removing it needs the ABS postcode
    correspondence and a Sydney-only bond index; whether that is worth building
    depends on what this shows first.

    Region codes, from the live response: 1 Sydney, 2 Melbourne, 3 Brisbane,
    4 Adelaide, 5 Perth, 6 Hobart, 7 Darwin, 8 Canberra, 50 Australia. Monthly
    regional rents run 2022-07 to 2026-06, the same span as the national series,
    so nothing is lost by having come to this late.
    """

    source = "abs_cpi_regional"
    cadence = "monthly"
    enabled = False  # monthly cadence, daily runner — see SCHEDULING above
    key = KEY_REGIONAL_MONTHLY
    max_staleness_days = 150


class ABSCPIWeightsCollector(_ABSCPICollector):
    """Published expenditure weights, plus the codelist that gives them a hierarchy.

    The weights are what let component forecasts be aggregated to a headline path,
    and they are useless without the hierarchy: the dataflow carries all four levels
    of the CPI structure at once — 1 All groups, 11 groups, 33 sub-groups and 87
    expenditure classes — each level summing to 100. Sum the lot indiscriminately
    and you get 400. The level is not recoverable from the code, either: 20001
    "Food and non-alcoholic beverages" is a group while 30002 "Bread and cereal
    products" is a sub-group and 126670 "Insurance and financial services" is a
    group again.

    So this captures the codelist in the same snapshot. It is hierarchical —
    40005 Bread -> 30002 Bread and cereal products -> 20001 Food -> 10001 All
    groups — and depth from the root is the level. Keeping both in one vintage means
    a weight can always be interpreted with the taxonomy that shipped alongside it,
    which matters because the ABS restructures the basket at reweights.

    Dimension order here is MEASURE.INDEX.REGION.FREQ — no TSEST, unlike the price
    dataflow. Measure 1 is the percentage contribution to All groups, which is the
    one worth having; 2 is a capital-city share that is uniformly 100 at the
    national level, and 3 is a points contribution on a different base.
    """

    source = "abs_cpi_weights"
    cadence = "yearly"
    enabled = False
    dataflow = DATAFLOW_WEIGHTS
    key = f"..{REGION_AUSTRALIA}.Q"
    # Reweighting is annual and publication lags: as at mid-2026 the newest weights
    # were 2024-Q4, already ~19 months old and entirely normal. The limit is
    # therefore generous — it exists to catch a retired dataflow, not a late
    # reweight.
    max_staleness_days = 900

    def fetch(self) -> tuple[Any, str, int | None]:
        doc, url, n = self._get_slice()

        taxonomy_url = f"{DATAFLOW_BASE}/codelist/ABS/{WEIGHTS_CODELIST}"
        resp = httpx.get(
            taxonomy_url,
            headers={"Accept": ACCEPT_STRUCTURE, "User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        taxonomy = resp.json()

        codes = taxonomy.get("data", {}).get("codelists", [{}])[0].get("codes", [])
        if not codes:
            raise RuntimeError(f"{WEIGHTS_CODELIST} returned no codes from {taxonomy_url}")
        if not any(c.get("parent") for c in codes):
            # Without parents there is no hierarchy, and the weights cannot be
            # summed safely. Fail rather than snapshot an unusable taxonomy.
            raise RuntimeError(
                f"{WEIGHTS_CODELIST} has no parent references; the CPI hierarchy "
                "cannot be derived and weights would double-count"
            )

        return {"weights": doc, "taxonomy": taxonomy}, url, n
