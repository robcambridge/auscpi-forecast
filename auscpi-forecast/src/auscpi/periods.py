"""SDMX period ids, shared by the collectors and the build step.

The ABS mixes monthly ("2026-06") and quarterly ("2026-Q2") period ids in one
dataflow, and both have to sort and compare correctly against a calendar date.
Kept here rather than in either caller so the collector's staleness check and the
parser's panel ordering cannot drift apart.
"""

from __future__ import annotations

import calendar
from datetime import date

MONTHLY = "M"
QUARTERLY = "Q"


def period_freq(period: str) -> str:
    """Either "M" or "Q", inferred from the id shape."""
    _, _, rest = period.partition("-")
    return QUARTERLY if rest[:1].upper() == "Q" else MONTHLY


def period_end(period: str) -> date:
    """Last day covered by an SDMX period id: '2026-06' or '2026-Q2'.

    The END of the period, not the start. Staleness checks compare this against
    today, and using the start would overstate staleness by up to a quarter and
    raise false alarms right after a release.
    """
    year_str, _, rest = period.partition("-")
    year = int(year_str)
    if rest[:1].upper() == "Q":
        month = int(rest[1:]) * 3
    else:
        month = int(rest)
    if not 1 <= month <= 12:
        raise ValueError(f"period {period!r} does not name a real month or quarter")
    return date(year, month, calendar.monthrange(year, month)[1])
