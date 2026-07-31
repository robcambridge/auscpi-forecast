"""Synthetic NSW rental bond workbooks.

Not a test module. Mirrors the published layout in exactly one place: a title row,
a blank row, the header on the third row, five columns, and a second sheet whose
name varies month to month — which is why the parser selects the data sheet by
position and the fixtures vary the name to keep it honest.

Bedrooms and Weekly Rent are written as TEXT, as published, because both carry "U"
for unknown. A fixture that wrote them as numbers would be easier to read and
would test a file the ABS never publishes.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import date

import openpyxl

HEADERS = ("Lodgement Date", "Postcode", "Dwelling Type", "Bedrooms", "Weekly Rent")

TITLE = "NSW Fair Trading\n\nResidential Rental Bond Lodgements"


def bond_workbook(
    rows: Sequence[tuple],
    *,
    second_sheet: str | None = "Definitions",
    headers: Sequence[str] = HEADERS,
    title: str = TITLE,
) -> bytes:
    """An .xlsx shaped like a published lodgement file."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Jun26 Rental Bond Lodgments"
    sheet.append([title, None, None, None, None])
    sheet.append([None, None, None, None, None])
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    if second_sheet is not None:
        workbook.create_sheet(second_sheet)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def lodgements(
    period: str,
    *,
    dwelling: str = "F",
    bedrooms: object = 2,
    rent: object = 600,
    n: int = 40,
    day: int = 15,
    postcode: int = 2000,
) -> list[tuple]:
    """`n` identical lodgements in `period`, encoded the way the source encodes them."""
    year, month = (int(x) for x in period.split("-"))
    return [(date(year, month, day), postcode, dwelling, str(bedrooms), str(rent))] * n
