"""Shared SDMX-JSON fixtures.

Not a test module. Kept separate so the parser and build tests do not import each
other, and so the fake payload mirrors the real ABS encoding in exactly one place:
dimension order MEASURE.INDEX.TSEST.REGION.FREQ, series keyed by dimension
position, observations keyed by period position with the datum at element zero.
"""

from __future__ import annotations

# MEASURE.INDEX.TSEST.REGION.FREQ, as positions into the dimension lists below.
HEADLINE_YOY_KEY = "0:0:0:0:0"  # measure 3, index 10001, Original
HEADLINE_MOM_KEY = "1:0:0:0:0"  # measure 2, index 10001, Original
TRIMMED_YOY_KEY = "0:1:1:0:0"  # measure 3, index 999902, Seasonally Adjusted

DIMENSIONS = [
    {
        "id": "MEASURE",
        "values": [
            {"id": "3", "name": "Percentage change from previous year"},
            {"id": "2", "name": "Percentage change from previous period"},
            {"id": "1", "name": "Index numbers"},
        ],
    },
    {
        "id": "INDEX",
        "values": [
            {"id": "10001", "name": "All groups CPI"},
            {"id": "999902", "name": "Trimmed Mean"},
            {"id": "30002", "name": "Bread and cereal products"},
        ],
    },
    {
        "id": "TSEST",
        "values": [
            {"id": "10", "name": "Original"},
            {"id": "20", "name": "Seasonally Adjusted"},
        ],
    },
    {"id": "REGION", "values": [{"id": "50", "name": "Australia"}]},
    {"id": "FREQ", "values": [{"id": "M", "name": "Monthly"}]},
]


def sdmx(series: dict[str, dict], periods: list[str], *, plural: bool = True) -> dict:
    """An SDMX-JSON doc shaped like the ABS response."""
    structure = {
        "dimensions": {
            "series": DIMENSIONS,
            "observation": [{"id": "TIME_PERIOD", "values": [{"id": p} for p in periods]}],
        }
    }
    data: dict = {"dataSets": [{"series": series}]}
    data["structures" if plural else "structure"] = [structure] if plural else structure
    return {"data": data}


def all_targets_doc(periods: list[str], *, yoy: float = 3.8) -> dict:
    """A doc carrying all three track_record targets over `periods`."""
    n = len(periods)
    return sdmx(
        {
            HEADLINE_YOY_KEY: {"observations": {str(i): [yoy] for i in range(n)}},
            HEADLINE_MOM_KEY: {"observations": {str(i): [0.3] for i in range(n)}},
            TRIMMED_YOY_KEY: {"observations": {str(i): [3.6] for i in range(n)}},
        },
        periods,
    )
