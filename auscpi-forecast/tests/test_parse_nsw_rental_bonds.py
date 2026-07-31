from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from bond_fixtures import bond_workbook, lodgements

from auscpi.parsers.nsw_rental_bonds import (
    MIN_CELL_LODGEMENTS,
    clean_records,
    file_period,
    index_frame,
    parse_workbook,
    rent_index,
    stratum_medians,
)


def cleaned(rows, period):
    """Parse a synthetic workbook straight through to cleaned records."""
    raw = parse_workbook(bond_workbook(rows))
    return clean_records(raw, period)


def test_parse_reads_the_header_from_the_third_row():
    frame = parse_workbook(bond_workbook(lodgements("2026-06", n=3)))
    assert list(frame.columns) == [
        "lodgement_date",
        "postcode",
        "dwelling_type",
        "bedrooms_raw",
        "weekly_rent_raw",
    ]
    assert len(frame) == 3
    assert frame["lodgement_date"].iloc[0] == pd.Timestamp("2026-06-15")
    assert frame["postcode"].iloc[0] == 2000


@pytest.mark.parametrize("name", ["Definitions", "Definition", "Definitions ", "Sheet2", None])
def test_data_sheet_is_chosen_by_position_not_name(name):
    """A quarter of the published archive names the second sheet 'Sheet2'."""
    blob = bond_workbook(lodgements("2026-06", n=2), second_sheet=name)
    assert len(parse_workbook(blob)) == 2


def test_a_changed_layout_is_refused_rather_than_guessed_at():
    """The cleaning rules below are only meaningful against the known columns."""
    headers = ("Lodgement Date", "Postcode", "Dwelling Type", "Bedrooms", "Rent Per Week")
    with pytest.raises(ValueError, match="unexpected columns"):
        parse_workbook(bond_workbook(lodgements("2026-06", n=2), headers=headers))


def test_unknown_markers_survive_parsing_as_text():
    """'U' must reach the cleaning step to be counted, not vanish into NaN."""
    rows = lodgements("2026-06", rent="U", bedrooms="U", n=2)
    frame = parse_workbook(bond_workbook(rows))
    assert frame["weekly_rent_raw"].iloc[0] == "U"
    assert frame["bedrooms_raw"].iloc[0] == "U"


def test_file_period_is_the_modal_month():
    rows = lodgements("2026-06", n=30) + lodgements("2026-05", n=2)
    assert file_period(parse_workbook(bond_workbook(rows))) == "2026-06"


def test_every_exclusion_is_counted():
    rows = (
        lodgements("2026-06", dwelling="F", bedrooms=2, rent=600, n=10)
        + lodgements("2026-06", dwelling="U", n=3)  # unknown dwelling type
        + lodgements("2026-06", dwelling="O", n=4)  # garages, car spaces, rooms
        + lodgements("2026-06", dwelling="G", n=2)  # undocumented code
        + lodgements("2026-06", bedrooms="U", n=5)  # unknown bedrooms
        + lodgements("2026-06", bedrooms=9, n=6)  # above MAX_BEDROOMS
        + lodgements("2026-06", rent="U", n=7)  # unknown rent
        + lodgements("2026-06", rent=12, n=8)  # a car space, not a dwelling
        + lodgements("2026-06", rent=9000, n=9)  # prestige or a typo
        + lodgements("2026-05", n=1)  # a straggler from another month
    )
    records, rejections = cleaned(rows, "2026-06")

    assert rejections.unknown_dwelling_type == 3
    assert rejections.non_dwelling_type == 6  # O and the undocumented G
    assert rejections.unknown_bedrooms == 5
    assert rejections.bedrooms_out_of_range == 6
    assert rejections.unknown_rent == 7
    assert rejections.rent_out_of_range == 17  # 8 too cheap, 9 too dear
    assert rejections.outside_file_month == 1
    assert len(records) == 10
    assert rejections.total == 45


def test_cleaned_records_are_typed_for_arithmetic():
    records, _ = cleaned(lodgements("2026-06", bedrooms=3, rent=750, n=5), "2026-06")
    assert records["bedrooms"].iloc[0] == 3
    assert records["weekly_rent"].iloc[0] == 750.0
    assert records["period"].iloc[0] == "2026-06"


# --- the index ------------------------------------------------------------


def two_months_of(*, month_a: list[tuple], month_b: list[tuple]) -> pd.DataFrame:
    a, _ = cleaned(month_a, "2026-05")
    b, _ = cleaned(month_b, "2026-06")
    return pd.concat([a, b], ignore_index=True)


def test_a_pure_mix_shift_moves_the_median_but_not_the_index():
    """The whole reason this file exists.

    Both months price a one-bedroom flat at $500 and a three-bedroom house at
    $900. Nothing changes price. The second month simply leases more flats, which
    drags the plain median from $700 to $500 — and a roll-through model fitted on
    that would be fitting composition. The fixed-weight index must not move.
    """
    records = two_months_of(
        month_a=(
            lodgements("2026-05", dwelling="F", bedrooms=1, rent=500, n=100)
            + lodgements("2026-05", dwelling="H", bedrooms=3, rent=900, n=100)
        ),
        month_b=(
            lodgements("2026-06", dwelling="F", bedrooms=1, rent=500, n=180)
            + lodgements("2026-06", dwelling="H", bedrooms=3, rent=900, n=40)
        ),
    )
    frame = index_frame(records)

    assert frame["median_weekly_rent"].tolist() == [700.0, 500.0]
    assert frame["median_mom_pct"].iloc[1] == pytest.approx(-28.57, abs=0.01)
    # Same strata, same prices: the index is flat to floating-point.
    assert frame["index"].iloc[1] == pytest.approx(frame["index"].iloc[0], abs=1e-9)
    assert frame["index_mom_pct"].iloc[1] == pytest.approx(0.0, abs=1e-9)


def test_a_real_price_rise_does_move_the_index():
    """The complement: mix control must not have flattened the signal too."""
    records = two_months_of(
        month_a=(
            lodgements("2026-05", dwelling="F", bedrooms=1, rent=500, n=100)
            + lodgements("2026-05", dwelling="H", bedrooms=3, rent=900, n=100)
        ),
        month_b=(
            lodgements("2026-06", dwelling="F", bedrooms=1, rent=550, n=100)
            + lodgements("2026-06", dwelling="H", bedrooms=3, rent=990, n=100)
        ),
    )
    frame = index_frame(records)
    # Every stratum up 10%, so the index is up 10% whatever the weights are.
    assert frame["index_mom_pct"].iloc[1] == pytest.approx(10.0, abs=1e-6)


def test_a_thin_cell_is_dropped_rather_than_trusted():
    """A median over a handful of leases is arbitrary, not merely imprecise."""
    rows = lodgements("2026-06", dwelling="F", bedrooms=2, rent=600, n=50) + lodgements(
        "2026-06", dwelling="H", bedrooms=4, rent=1200, n=MIN_CELL_LODGEMENTS - 1
    )
    records, _ = cleaned(rows, "2026-06")

    cells = stratum_medians(records)
    assert len(cells) == 1
    assert cells["dwelling_type"].iloc[0] == "F"
    assert index_frame(records)["strata_used"].iloc[0] == 1


def test_the_index_only_ever_looks_backwards_for_its_base():
    """Rule 3 at construction time: weights come from the earliest months, not all.

    With a one-month base window the base level is May alone, so May indexes to
    exactly 100 and June carries the whole move. Were the base taken over the full
    sample, May's value would depend on June.
    """
    records = two_months_of(
        month_a=lodgements("2026-05", dwelling="F", bedrooms=1, rent=500, n=100),
        month_b=lodgements("2026-06", dwelling="F", bedrooms=1, rent=600, n=100),
    )
    frame = rent_index(stratum_medians(records), base_window=1)
    assert frame["index"].iloc[0] == pytest.approx(100.0)
    assert frame["index"].iloc[1] == pytest.approx(120.0)


def test_an_empty_history_yields_an_empty_index_rather_than_raising():
    """`build_all` runs before every source has data; a stub must not be fatal."""
    empty = pd.DataFrame(columns=["period", "dwelling_type", "bedrooms", "median_rent", "n"])
    assert rent_index(empty).empty


def test_year_ended_appears_once_there_are_thirteen_months():
    frames = []
    for i in range(13):
        period = f"2025-{i + 1:02d}" if i < 12 else "2026-01"
        rent = 500 * (1.01**i)
        records, _ = cleaned(
            lodgements(period, dwelling="F", bedrooms=1, rent=round(rent, 2), n=50), period
        )
        frames.append(records)
    frame = index_frame(pd.concat(frames, ignore_index=True))

    assert frame["index_yoy_pct"].iloc[:12].isna().all()
    # Twelve compounded 1% steps.
    assert frame["index_yoy_pct"].iloc[12] == pytest.approx(((1.01**12) - 1) * 100, abs=0.01)


def test_period_end_is_the_last_day_of_the_month():
    records, _ = cleaned(lodgements("2026-06", n=50), "2026-06")
    assert index_frame(records)["period_end"].iloc[0] == date(2026, 6, 30)
