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
from datetime import UTC, datetime
from typing import Any

from auscpi.storage import write_snapshot

registry: dict[str, type[Collector]] = {}


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
    #: set False to keep the code but stop scheduling it
    enabled: bool = True

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
