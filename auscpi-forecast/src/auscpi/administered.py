"""The administered price calendar: announced price changes, before they print.

WHY THIS IS THE HIGHEST-VALUE COMPONENT, AND IT IS NOW MEASURED RATHER THAN ASSERTED.
Aggregation (see aggregate.py) showed that a component only moves the headline if it
disagrees with the naive baseline by a lot: the rent roll-through, at 6.613% of the
basket, shifts the headline by at most 0.072pp — under the 0.1pp step the ABS rounds
to. Administered prices are the opposite case. Private health insurance premiums rose
an announced 4.41% average on 1 April 2026, and expenditure class 40091 "Medical and
hospital services" — 5.032% of the basket — printed +3.52% that month. That is about
0.18pp on the headline from ONE event, roughly two and a half times the entire rent
model, and it was public two months before the reference month began.

That is the whole thesis of the forecast framing. In a nowcast a 1 April change shows
up in scraped prices by 5 April and reading the announcement adds nothing. Forecasting
April from February, the announcement is the only way to know.

THE ANNOUNCED NUMBER IS NOT THE CPI EFFECT, AND ASSUMING IT IS WOULD BE WRONG BY A
THIRD. An expenditure class is broader than the administered item inside it: 40091
also carries doctor and hospital fees that do not move on 1 April. Realised class
movement against the announced average:

    1 April 2025   announced 3.73%   class printed 2.38%   pass-through 0.64
    1 April 2026   announced 4.41%   class printed 3.52%   pass-through 0.80

So `passthrough` is a required field, not an optional refinement. Two observations
cannot pin it — the two above differ by a quarter — which is why it is stored per
event with a confidence rather than fitted, and why `estimate_passthrough` exists to
be re-run as events accumulate rather than to be trusted now.

ANNOUNCED DATE IS THE LEAKAGE GUARD AND IS NOT OPTIONAL. An event may only inform a
forecast whose information cutoff is on or after the date the announcement was public.
This is CLAUDE.md rule 3 applied to documents rather than data: a backtest that lets
February's forecast use an announcement made in March is exactly the look-ahead this
project exists to make impossible, and it is far easier to commit here than with a
time series, because the effective date is the memorable one and the announced date
is the one that matters. `visible_at` is the only supported way to select events.

WHAT IS DELIBERATELY NOT HERE YET. The document corpus and the extraction layer.
This is the schema, the store, the leakage guard and the arithmetic that turns an
event into a component override — the thing extraction must produce and the thing a
forecast can consume. Seeding it by hand first means the pipeline is testable end to
end before any model is pointed at a PDF, and it means the extraction layer has a
target to be checked against rather than a blank page.

WHAT IS STILL WEAK:

  - Two pass-through observations, from one event type. Nothing here is estimated in
    any statistical sense.
  - One event type seeded. The classes that matter and are not yet covered are
    electricity (40055, 1.835%, and state rebates have swung it violently), tobacco
    excise (40090, 1.874%, indexed twice yearly on a known schedule), pharmaceutical
    products (40094) and child care (115498).
  - An event is treated as a one-month level shift. Some administered changes phase
    in, and a phased change modelled as a step is wrong in both directions in
    consecutive months.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from auscpi.config import REPO_ROOT
from auscpi.forecast import History, add_months, project_levels
from auscpi.periods import period_end

CALENDAR_PATH = REPO_ROOT / "config" / "administered_prices.csv"

#: Confidence levels, coarse on purpose. A finer scale would imply a precision the
#: underlying judgement does not have.
CONFIDENCES = ("announced", "scheduled", "estimated")


@dataclass(frozen=True)
class AdministeredEvent:
    """One announced price change, mapped to an expenditure class.

    `announced_pct` is what the announcement said. `passthrough` is the share of it
    that reaches the class, because the class is broader than the item. The product
    is the class-level effect, and keeping them separate means a revised pass-through
    does not require re-reading the announcement.
    """

    index_id: str
    label: str
    #: When the announcement became public. The leakage guard reads this, not
    #: `effective_month`.
    announced_date: date
    #: Reference month the change first affects, "YYYY-MM".
    effective_month: str
    announced_pct: float
    passthrough: float
    confidence: str
    source_url: str
    note: str = ""

    @property
    def class_effect_pct(self) -> float:
        """The one-month movement this implies for the expenditure class."""
        return self.announced_pct * self.passthrough

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCES:
            raise ValueError(f"confidence {self.confidence!r} not one of {CONFIDENCES}")
        if not self.source_url:
            raise ValueError(
                f"{self.label!r} has no source_url; an unsourced administered event "
                "cannot be checked and must not be forecast from"
            )
        # An announcement made after the month began was not available to forecast it.
        # Stored anyway for pass-through estimation, but flagged so it cannot be
        # mistaken for a forecastable event.
        if self.announced_date > period_end(self.effective_month):
            raise ValueError(
                f"{self.label!r} was announced {self.announced_date} which is after "
                f"{self.effective_month} ended; it cannot be an administered forecast input"
            )


def load_events(path: Path | None = None) -> list[AdministeredEvent]:
    """Read the calendar. Absent file is empty, not an error — it is hand-maintained."""
    path = path or CALENDAR_PATH
    if not path.exists():
        return []
    events: list[AdministeredEvent] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("index_id") or "").strip():
                continue
            events.append(
                AdministeredEvent(
                    index_id=row["index_id"].strip(),
                    label=row["label"].strip(),
                    announced_date=date.fromisoformat(row["announced_date"].strip()),
                    effective_month=row["effective_month"].strip(),
                    announced_pct=float(row["announced_pct"]),
                    passthrough=float(row["passthrough"]),
                    confidence=row["confidence"].strip(),
                    source_url=row["source_url"].strip(),
                    note=(row.get("note") or "").strip(),
                )
            )
    return sorted(events, key=lambda e: (e.effective_month, e.index_id))


def visible_at(
    events: list[AdministeredEvent], information_cutoff: date
) -> list[AdministeredEvent]:
    """Events whose announcement was public by `information_cutoff`.

    The ONLY supported way to select events for a forecast. Filtering on
    `effective_month` instead is the mistake this function exists to prevent: it
    silently lets a backtest use announcements that had not been made.
    """
    return [e for e in events if e.announced_date <= information_cutoff]


def for_class(events: list[AdministeredEvent], index_id: str) -> list[AdministeredEvent]:
    return [e for e in events if e.index_id == index_id]


def override_path(
    hist: History,
    events: list[AdministeredEvent],
    months: list[str],
    *,
    seasonal: bool = True,
) -> dict[str, float]:
    """The class's year-ended path with announced changes applied to its index.

    NETTED, NOT ADDED. The baseline projection already assumes the class drifts up in
    the effective month — at trend, with the published seasonal shape. Applying the
    announced movement on top would count that drift twice, so what is applied is the
    INCREMENT over what the projection already assumed:

        increment = (1 + announced x passthrough) / (1 + baseline movement) - 1

    An event whose effect matches what the projection already expected therefore
    changes nothing, which is the same discipline aggregate.py applies one level up.

    The shift is permanent from the effective month, because a price level that steps
    up stays up; it leaves the year-ended rate twelve months later, which the ratio
    handles by itself.
    """
    horizon_end = max(months, key=period_end)
    levels = project_levels(hist, horizon_end, seasonal=seasonal)

    adjusted = dict(levels)
    for event in sorted(events, key=lambda e: e.effective_month):
        effective = event.effective_month
        previous = add_months(effective, -1)
        if effective not in adjusted or previous not in adjusted:
            continue  # outside the projected span; nothing to apply it to
        baseline_move = adjusted[effective] / adjusted[previous] - 1.0
        increment = (1.0 + event.class_effect_pct / 100.0) / (1.0 + baseline_move) - 1.0
        for month in adjusted:
            if period_end(month) >= period_end(effective):
                adjusted[month] *= 1.0 + increment

    out: dict[str, float] = {}
    for month in months:
        base = add_months(month, -12)
        if month in adjusted and base in adjusted:
            out[month] = (adjusted[month] / adjusted[base] - 1.0) * 100.0
    return out


def estimate_passthrough(
    panel: pd.DataFrame, events: list[AdministeredEvent]
) -> list[tuple[AdministeredEvent, float]]:
    """Realised class movement divided by the announced movement, per past event.

    Re-run as events accumulate. Do not read two observations as an estimate — the
    two private health insurance rounds on record differ by a quarter, and there is
    no reason to think the next one falls between them.
    """
    from auscpi.parsers.abs_cpi import MEASURE_INDEX_NUMBER, TSEST_ORIGINAL, series_for

    out: list[tuple[AdministeredEvent, float]] = []
    for event in events:
        try:
            level = series_for(
                panel, event.index_id, MEASURE_INDEX_NUMBER, TSEST_ORIGINAL, name="lvl"
            ).dropna()
        except ValueError:
            continue
        previous = add_months(event.effective_month, -1)
        if event.effective_month not in level.index or previous not in level.index:
            continue
        realised = (
            float(level.loc[event.effective_month]) / float(level.loc[previous]) - 1.0
        ) * 100.0
        if event.announced_pct:
            out.append((event, realised / event.announced_pct))
    return out
