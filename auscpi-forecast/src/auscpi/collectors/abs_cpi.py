"""ABS Consumer Price Index, Australia (the complete Monthly CPI).

Reference month N is released on the last Wednesday of month N+1 at 11:30am
AEST/AEDT. From February 2027 this moves to the FOURTH Wednesday of the month —
do not hardcode "last Wednesday" anywhere; read config/release_calendar.csv.

The monthly series and the monthly trimmed mean run back to April 2024, when the
ABS moved most expenditure classes to monthly collection. That is your estimation
sample. The quarterly series runs back decades and was re-referenced in the
December 2025 release to September month 2025 = 100.00 — if you mix old and new
quarterly vintages without applying the conversion factors you will get silently
wrong index levels.

STATUS: this collector is a stub. The ABS Data API dataflow identifier for the
new monthly CPI publication needs to be confirmed against
https://data.api.abs.gov.au/rest/dataflow — do that first, from a machine with
network access, before writing the parser. See docs/DATA_SOURCES.md.
"""

from __future__ import annotations

from typing import Any

import httpx

from auscpi.collectors.base import Collector

DATAFLOW_BASE = "https://data.api.abs.gov.au/rest"
TIMEOUT = httpx.Timeout(120.0)

# TODO(verify): confirm the dataflow id for the complete Monthly CPI.
# `GET {DATAFLOW_BASE}/dataflow/ABS?format=jsondata` lists what is available.
MONTHLY_CPI_DATAFLOW = "ABS,CPI_M,1.0.0"


class ABSMonthlyCPICollector(Collector):
    source = "abs_cpi_monthly"
    cadence = "monthly"
    enabled = False  # flip to True once the dataflow id above is confirmed

    def fetch(self) -> tuple[Any, str, int | None]:
        url = f"{DATAFLOW_BASE}/data/{MONTHLY_CPI_DATAFLOW}/all"
        resp = httpx.get(
            url,
            headers={"Accept": "application/vnd.sdmx.data+json"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.json(), url, None
