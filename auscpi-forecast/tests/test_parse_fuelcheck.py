from __future__ import annotations

import io

import openpyxl
import pandas as pd
import pytest

from auscpi.parsers.fuelcheck import (
    EXPECTED_COLUMNS,
    clean_events,
    closing_prices,
    daily_prices,
    monthly_prices,
    parse_price_events,
)

ROWS = [
    ("Metro St Marys", "516 GREAT WESTERN HWY", "ST MARYS", 2760, "Metro", "U91",
     "2026-06-01 00:58:18", 170.5),
    ("Metro St Marys", "516 GREAT WESTERN HWY", "ST MARYS", 2760, "Metro", "E10",
     "2026-06-01 00:58:18", 168.5),
    ("Astron Yagoona", "45 ROOKWOOD RD", "YAGOONA", 2199, "ASTRON", "U91",
     "2026-06-02 09:00:00", 180.0),
]


def xlsx(rows=ROWS, *, title_rows: int = 0, headers=EXPECTED_COLUMNS) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "FC-R07-Price History Checks"
    for _ in range(title_rows):
        sheet.append(["Price History Checks"] + [None] * 7)
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def csv_bytes(rows=ROWS, *, bom: bool = True) -> bytes:
    lines = [",".join(EXPECTED_COLUMNS)]
    lines += [",".join(str(c) for c in row) for row in rows]
    text = "\n".join(lines)
    return ("﻿" + text).encode("utf-8") if bom else text.encode("utf-8")


@pytest.mark.parametrize("title_rows", [0, 2])
def test_the_header_is_located_not_assumed(title_rows):
    """24 archive files put the header first; ten carry a title row and a blank."""
    frame = parse_price_events(xlsx(title_rows=title_rows))
    assert list(frame.columns) == list(EXPECTED_COLUMNS)
    assert len(frame) == len(ROWS)


def test_csv_with_a_byte_order_mark_is_read():
    frame = parse_price_events(csv_bytes())
    assert list(frame.columns) == list(EXPECTED_COLUMNS)
    assert len(frame) == len(ROWS)


def test_a_file_without_the_expected_columns_is_refused():
    headers = ("Station", "Addr", "Suburb", "Postcode", "Brand", "Fuel", "When", "Cents")
    with pytest.raises(ValueError, match="no header row containing"):
        parse_price_events(xlsx(headers=headers))


# --- the date trap --------------------------------------------------------


DAY_FIRST = [
    ("A", "1 X ST", "S", 2000, "B", "U91", "01/09/2023 12:04:31 AM", 190.0),
    ("A", "1 X ST", "S", 2000, "B", "U91", "13/09/2023 12:04:45 AM", 191.0),
    ("A", "1 X ST", "S", 2000, "B", "U91", "30/09/2023 11:00:00 PM", 192.0),
]


def test_day_first_timestamps_are_not_silently_mis_dated():
    """Three of 42 archive files use Australian day-first slashes.

    Left to infer, pandas reads the opening rows as month-first: 01/09/2023 becomes
    9 January, every row with a day of 12 or lower is quietly wrong, and only days 13
    and up fail outright. The quiet 30% is the dangerous part, so this pins that all
    three rows land in September 2023.
    """
    events, rejections = clean_events(parse_price_events(csv_bytes(DAY_FIRST)))

    assert rejections.unparseable_timestamp == 0
    months = pd.to_datetime(events["ts"]).dt.strftime("%Y-%m").unique().tolist()
    assert months == ["2023-09"], months
    assert sorted(pd.to_datetime(events["ts"]).dt.day.tolist()) == [1, 13, 30]


def test_iso_timestamps_still_parse():
    events, rejections = clean_events(parse_price_events(csv_bytes()))
    assert rejections.unparseable_timestamp == 0
    assert pd.to_datetime(events["ts"]).dt.strftime("%Y-%m").unique().tolist() == ["2026-06"]


def test_cleaning_counts_what_it_drops():
    rows = list(ROWS) + [
        ("Bad", "1 Y ST", "S", 2000, "B", "U91", "not a date", 170.0),
        ("Bad", "1 Y ST", "S", 2000, "B", "U91", "2026-06-03 10:00:00", 9.0),  # out of range
        ("Bad", "1 Y ST", "S", 2000, "B", "XYZ", "2026-06-03 10:00:00", 170.0),  # unknown fuel
    ]
    _, rejections = clean_events(parse_price_events(csv_bytes(rows)))
    assert rejections.unparseable_timestamp == 1
    assert rejections.price_out_of_range == 1
    assert rejections.unknown_fuel_code == 1


# --- the step function ----------------------------------------------------


def stepped_rows():
    """One station holding a price, another re-pricing constantly and cheaply."""
    rows = [("Steady", "1 A ST", "S", 2000, "B", "U91", "2026-06-01 06:00:00", 200.0)]
    for day in range(1, 11):
        rows.append(
            ("Churner", "2 B ST", "S", 2000, "B", "U91", f"2026-06-{day:02d} 06:00:00", 100.0)
        )
    return rows


def test_averaging_events_would_weight_stations_by_how_often_they_re_price():
    """The bias this module exists to avoid, on numbers small enough to check.

    Two stations, one at 200 posting once and one at 100 posting ten times. The true
    cross-sectional mean is 150 every day. Averaging the event rows gives 109.
    """
    events, _ = clean_events(parse_price_events(csv_bytes(stepped_rows())))
    assert events["price"].mean() == pytest.approx(109.09, abs=0.01)

    daily = daily_prices(events, fuel="U91")
    assert daily["mean_price"].tolist() == pytest.approx([150.0] * len(daily))


def test_a_price_holds_until_the_station_posts_again():
    rows = [
        ("A", "1 A ST", "S", 2000, "B", "U91", "2026-06-01 06:00:00", 100.0),
        ("A", "1 A ST", "S", 2000, "B", "U91", "2026-06-04 06:00:00", 200.0),
    ]
    events, _ = clean_events(parse_price_events(csv_bytes(rows)))
    daily = daily_prices(events, fuel="U91").set_index("date")["mean_price"]
    assert daily.iloc[0] == pytest.approx(100.0)
    assert daily.iloc[-1] == pytest.approx(200.0)
    assert len(daily) == 2  # only days with a posting become columns


def test_carry_in_covers_stations_that_have_not_posted_yet():
    """Without it, day one is assembled from the few stations that happened to post.

    In March 2026 that was 205 of 2,041 stations, and a biased 205 — a station posts
    precisely when it has just moved its price.
    """
    rows = [("Late", "9 Z ST", "S", 2000, "B", "U91", "2026-06-20 06:00:00", 300.0)]
    events, _ = clean_events(parse_price_events(csv_bytes(rows)))

    without = daily_prices(events, fuel="U91")
    assert without["stations"].iloc[0] == 1

    carry = pd.Series({"Early|1 A ST": 100.0, "Late|9 Z ST": 290.0})
    with_carry = daily_prices(events, fuel="U91", carry_in=carry)
    assert with_carry["stations"].iloc[0] == 2
    assert with_carry["mean_price"].iloc[0] == pytest.approx(200.0)


def test_closing_prices_are_what_the_next_month_carries_in():
    rows = [
        ("A", "1 A ST", "S", 2000, "B", "U91", "2026-06-01 06:00:00", 100.0),
        ("A", "1 A ST", "S", 2000, "B", "U91", "2026-06-28 06:00:00", 150.0),
    ]
    events, _ = clean_events(parse_price_events(csv_bytes(rows)))
    closing = closing_prices(events, fuel="U91")
    assert closing.loc["A|1 A ST"] == pytest.approx(150.0)


def test_only_the_requested_fuel_is_used():
    events, _ = clean_events(parse_price_events(csv_bytes()))
    assert daily_prices(events, fuel="E10")["mean_price"].iloc[0] == pytest.approx(168.5)
    assert daily_prices(events, fuel="LPG").empty


def test_monthly_is_the_mean_of_daily_means_not_of_events():
    """Every day counts once, so a volatile week cannot outvote a quiet one."""
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-01", "2026-06-02", "2026-07-01"]).date,
            "fuel": "U91",
            "mean_price": [100.0, 200.0, 400.0],
            "stations": [10, 10, 10],
        }
    )
    monthly = monthly_prices(daily).set_index("period")
    assert monthly.loc["2026-06", "mean_price"] == pytest.approx(150.0)
    assert monthly.loc["2026-06", "days"] == 2
    assert monthly.loc["2026-07", "mom_pct"] == pytest.approx((400 / 150 - 1) * 100)


def test_an_empty_frame_does_not_raise():
    """build_all runs against half-built histories."""
    assert monthly_prices(pd.DataFrame()).empty
