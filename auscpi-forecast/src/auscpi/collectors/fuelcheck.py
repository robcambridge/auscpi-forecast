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
import uuid
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
