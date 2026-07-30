"""The ABS release schedule, read from config/release_calendar.csv.

CLAUDE.md rule 6: do not hardcode the schedule. Reference month N is currently
released on the last Wednesday of N+1, but that becomes the FOURTH Wednesday from
February 2027, and the rule breaks anyway around holidays — the shipped calendar
has 2026-11 releasing on 2027-01-06, which is neither. Any code that computes
release dates instead of reading them will be quietly wrong.

The calendar is short and hand-maintained, so it runs out. Callers must treat a
missing month as "unknown", never as "not released": a forecast path extends past
the last calendar row by design.
"""

from __future__ import annotations

import csv
from datetime import date
from functools import lru_cache
from pathlib import Path

from auscpi.config import REPO_ROOT

CALENDAR_PATH = REPO_ROOT / "config" / "release_calendar.csv"


@lru_cache(maxsize=8)
def _load(path: Path) -> dict[str, date]:
    if not path.exists():
        return {}
    out: dict[str, date] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            month = (row.get("reference_month") or "").strip()
            released = (row.get("release_date") or "").strip()
            if month and released:
                out[month] = date.fromisoformat(released)
    return out


def load(path: Path | None = None) -> dict[str, date]:
    """reference_month -> release date. Empty if the file is absent."""
    return dict(_load(path or CALENDAR_PATH))


def release_date(reference_month: str, path: Path | None = None) -> date | None:
    """When `reference_month` prints, or None if the calendar does not cover it."""
    return _load(path or CALENDAR_PATH).get(reference_month)


def is_released(reference_month: str, on: date, path: Path | None = None) -> bool | None:
    """Whether `reference_month` had printed by `on`.

    None means the calendar cannot say. That is deliberately distinct from False:
    treating "unknown" as "not released" would silently drop settled months from
    scoring, and treating it as "released" would invent actuals.
    """
    known = release_date(reference_month, path)
    if known is None:
        return None
    return on >= known
