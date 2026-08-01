"""Collector base class, registry, and health reporting.

Every source is a Collector. The contract is deliberately small:

    - fetch() hits the source and returns the payload plus the URL used
    - run()  wraps fetch() with retry, timing, snapshot writing and error capture

A collector must never transform data. Parsing belongs in the build step, which
reads from data/raw. Keeping fetch dumb means that when you later discover your
parser was wrong, you can fix it and reprocess history, because history is still
sitting there in its original form.
"""

from __future__ import annotations

import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from auscpi.storage import write_snapshot

registry: dict[str, type[Collector]] = {}

#: How stale a source's last SUCCESSFUL FETCH may be before the pipeline should be
#: treated as broken rather than merely between runs. Generous against the cadence
#: itself, because one missed run is timing variation and several is a fault.
#:
#: This is about fetch recency, not data recency. The ABS collectors separately carry
#: `max_staleness_days`, which refuses a series whose newest OBSERVATION is too old —
#: that catches a retired dataflow, this catches a scheduler that stopped.
CADENCE_TOLERANCE: dict[str, timedelta] = {
    "daily": timedelta(days=2),
    "weekly": timedelta(days=10),
    "monthly": timedelta(days=40),
    "quarterly": timedelta(days=120),
    "yearly": timedelta(days=400),
}


def overdue_after(cadence: str) -> timedelta:
    """Tolerated age for a cadence. Unknown cadences get the strictest answer."""
    return CADENCE_TOLERANCE.get(cadence, CADENCE_TOLERANCE["daily"])


@dataclass
class CollectorResult:
    source: str
    ok: bool
    seconds: float
    n_records: int | None = None
    error: str = ""
    path: str = ""


class Collector(ABC):
    #: short slug, used as the directory name under data/raw/
    source: str
    #: how often this should run, for the scheduler and the health check
    cadence: str = "daily"
    #: set False to keep the code but stop scheduling it in `collect --all`
    enabled: bool = True
    #: True when the source must never be collected at all. This is a licensing or
    #: terms decision, not a scheduling one, and it is deliberately separate from
    #: `enabled`: several collectors set `enabled = False` only because the daily
    #: runner would re-fetch a monthly file thirty times. Health reports a ruled-out
    #: source as ruled out rather than as permanently overdue.
    ruled_out: bool = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "source", None):
            registry[cls.source] = cls

    @abstractmethod
    def fetch(self) -> tuple[Any, str, int | None]:
        """Return (payload, url, n_records). Raise on failure."""

    def run(self) -> CollectorResult:
        started = time.perf_counter()
        fetched_at = datetime.now(UTC)
        try:
            payload, url, n = self.fetch()
        except Exception as exc:  # noqa: BLE001
            # Record the failure in the manifest too. A gap in the data with no
            # explanation is much worse than a gap with a logged reason.
            write_snapshot(
                self.source,
                {"error": str(exc), "traceback": traceback.format_exc()},
                url="",
                fetched_at=fetched_at,
                status="error",
                note=type(exc).__name__,
            )
            return CollectorResult(
                source=self.source,
                ok=False,
                seconds=time.perf_counter() - started,
                error=f"{type(exc).__name__}: {exc}",
            )

        snap = write_snapshot(self.source, payload, url=url, fetched_at=fetched_at, n_records=n)
        return CollectorResult(
            source=self.source,
            ok=True,
            seconds=time.perf_counter() - started,
            n_records=n,
            path=snap.payload_path,
        )
