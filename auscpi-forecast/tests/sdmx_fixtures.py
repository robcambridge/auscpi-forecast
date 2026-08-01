"""Shared SDMX-JSON fixtures.

Not a test module. Kept separate so the parser, build and forecast tests do not
import each other, and so the fake payload mirrors the real ABS encoding in
exactly one place: dimension order MEASURE.INDEX.TSEST.REGION.FREQ, series keyed
by dimension position, observations keyed by period position with the datum at
element zero.

The index levels are compounded from the monthly rates rather than invented
independently, because the real series satisfy that identity to within rounding
and the index-projection model depends on it. A fixture where they disagreed
would pass tests the real data would fail.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

# MEASURE.INDEX.TSEST.REGION.FREQ, as positions into the dimension lists below.
# MEASURE: 0 -> "3" year-ended, 1 -> "2" month-on-month, 2 -> "1" index numbers.
# INDEX:   0 -> 10001 All groups,  1 -> 999902 Trimmed Mean.
# TSEST:   0 -> "10" Original,     1 -> "20" Seasonally Adjusted.
HEADLINE_YOY_KEY = "0:0:0:0:0"
HEADLINE_MOM_KEY = "1:0:0:0:0"
HEADLINE_LEVEL_KEY = "2:0:0:0:0"
TRIMMED_YOY_KEY = "0:1:1:0:0"
TRIMMED_MOM_KEY = "1:1:1:0:0"
TRIMMED_LEVEL_KEY = "2:1:1:0:0"
# 999901 "All groups CPI, seasonally adjusted" — INDEX position 3, TSEST position 1.
HEADLINE_SA_LEVEL_KEY = "2:3:1:0:0"
# 30014 Rents — INDEX position 4. REGION position 1 is Sydney, for the regional
# slice the rent roll-through reads. Both are appended to the dimension lists below
# so every key above keeps its meaning.
RENTS_LEVEL_KEY = "2:4:0:0:0"
RENTS_SYDNEY_LEVEL_KEY = "2:4:0:1:0"

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
            {"id": "999901", "name": "All groups CPI, seasonally adjusted"},
            {"id": "30014", "name": "Rents"},
        ],
    },
    {
        "id": "TSEST",
        "values": [
            {"id": "10", "name": "Original"},
            {"id": "20", "name": "Seasonally Adjusted"},
        ],
    },
    {
        "id": "REGION",
        "values": [{"id": "50", "name": "Australia"}, {"id": "1", "name": "Sydney"}],
    },
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


def observations(values: Sequence[float | None]) -> dict:
    return {"observations": {str(i): [v] for i, v in enumerate(values)}}


def compound(rates: Sequence[float], start: float = 100.0) -> list[float]:
    """Index levels implied by a monthly rate path, first level = `start`."""
    levels = [start]
    for rate in rates[1:]:
        levels.append(levels[-1] * (1.0 + rate / 100.0))
    return levels


def all_targets_doc(
    periods: list[str],
    *,
    yoy: float = 3.8,
    trimmed_yoy: float = 3.6,
    mom: Callable[[int], float] | None = None,
    trimmed_mom: Callable[[int], float] | None = None,
    sa_factors: dict[int, float] | None = None,
) -> dict:
    """A doc carrying all three targets plus their m/m and index companions.

    `mom` maps an observation position to a monthly rate; the default is flat.
    Pass a varying one where a rule must be shown to respond to seasonality.

    `sa_factors` maps a calendar month to an Original/SeasonallyAdjusted ratio, and
    the 999901 adjusted level is emitted as Original divided by it. The default of
    1.0 everywhere makes the adjusted series identical to the Original, so the
    seasonal projection reduces exactly to the flat one — which is what a test
    wanting no seasonality should see.
    """
    n = len(periods)
    mom_fn = mom or (lambda _i: 0.3)
    trimmed_fn = trimmed_mom or (lambda _i: 0.25)
    factors = sa_factors or {}

    headline_rates = [mom_fn(i) for i in range(n)]
    trimmed_rates = [trimmed_fn(i) for i in range(n)]
    headline_levels = compound(headline_rates)

    sa_levels = [
        level / factors.get(int(period.split("-")[1]), 1.0)
        for level, period in zip(headline_levels, periods, strict=True)
    ]

    return sdmx(
        {
            HEADLINE_YOY_KEY: observations([yoy] * n),
            HEADLINE_MOM_KEY: observations(headline_rates),
            HEADLINE_LEVEL_KEY: observations(headline_levels),
            HEADLINE_SA_LEVEL_KEY: observations(sa_levels),
            TRIMMED_YOY_KEY: observations([trimmed_yoy] * n),
            TRIMMED_MOM_KEY: observations(trimmed_rates),
            TRIMMED_LEVEL_KEY: observations(compound(trimmed_rates)),
        },
        periods,
    )
