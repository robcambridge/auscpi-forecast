from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sdmx_fixtures import all_targets_doc

from auscpi.build import build_abs_cpi, build_all
from auscpi.storage import write_snapshot


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from auscpi.config import settings

    monkeypatch.setattr(settings, "auscpi_data_dir", str(tmp_path / "data"))
    return tmp_path / "data"


def doc(periods: list[str], yoy: float = 3.8) -> dict:
    return all_targets_doc(periods, yoy=yoy)


def test_build_writes_panel_and_targets(data_dir):
    write_snapshot("abs_cpi_monthly", doc(["2026-05", "2026-06"]), url="http://x")

    result = build_abs_cpi("abs_cpi_monthly")

    # 7 series (3 targets, their m/m and index companions, and the seasonally
    # adjusted headline counterpart) x 2 periods.
    assert result.rows == 14
    assert result.periods == 2
    assert result.latest_period == "2026-06"
    assert (data_dir / "curated" / "abs_cpi_monthly.parquet").exists()
    assert (data_dir / "curated" / "abs_cpi_monthly_targets.csv").exists()


def test_build_reports_the_vintage_it_used(data_dir):
    write_snapshot("abs_cpi_monthly", doc(["2026-06"]), url="http://x")
    result = build_abs_cpi("abs_cpi_monthly")
    # The fetched_at of the snapshot, so a curated file is always traceable to one.
    assert result.vintage.startswith(str(datetime.now(UTC).year))


def test_build_is_idempotent(data_dir):
    write_snapshot("abs_cpi_monthly", doc(["2026-06"]), url="http://x")
    first = build_abs_cpi("abs_cpi_monthly")
    second = build_abs_cpi("abs_cpi_monthly")
    assert (first.rows, first.latest_period) == (second.rows, second.latest_period)


def test_build_raises_when_nothing_collected(data_dir):
    with pytest.raises(FileNotFoundError, match="no successful abs_cpi_monthly snapshot"):
        build_abs_cpi("abs_cpi_monthly")


def test_build_uses_the_newest_snapshot(data_dir):
    write_snapshot(
        "abs_cpi_monthly",
        doc(["2026-05"]),
        url="http://old",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    write_snapshot(
        "abs_cpi_monthly",
        doc(["2026-05", "2026-06"]),
        url="http://new",
        fetched_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert build_abs_cpi("abs_cpi_monthly").latest_period == "2026-06"


def test_as_at_excludes_later_vintages(data_dir):
    """Rule 3: a backtest must see only what had been fetched by then."""
    write_snapshot(
        "abs_cpi_monthly",
        doc(["2026-05"]),
        url="http://old",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    write_snapshot(
        "abs_cpi_monthly",
        doc(["2026-05", "2026-06"]),
        url="http://new",
        fetched_at=datetime(2026, 7, 1, tzinfo=UTC),
    )

    result = build_abs_cpi("abs_cpi_monthly", as_at=datetime(2026, 6, 1, tzinfo=UTC))
    # June's print did not exist on 1 June; using it would be look-ahead.
    assert result.latest_period == "2026-05"


def test_as_at_before_any_snapshot_raises(data_dir):
    write_snapshot(
        "abs_cpi_monthly",
        doc(["2026-06"]),
        url="http://x",
        fetched_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    with pytest.raises(FileNotFoundError, match="as at"):
        build_abs_cpi("abs_cpi_monthly", as_at=datetime(2026, 1, 1, tzinfo=UTC))


def test_error_snapshots_are_not_built(data_dir):
    write_snapshot("abs_cpi_monthly", {"error": "boom"}, url="", status="error")
    with pytest.raises(FileNotFoundError):
        build_abs_cpi("abs_cpi_monthly")


def bonds(period: str, *, rent: int = 600, n: int = 50) -> bytes:
    from bond_fixtures import bond_workbook, lodgements

    return bond_workbook(lodgements(period, rent=rent, n=n))


def test_bond_build_stacks_every_snapshot_not_just_the_newest(data_dir):
    """One bond snapshot is one MONTH, not one vintage of the whole history."""
    from auscpi.build import build_nsw_rental_bonds

    # Distinct fetched_at: the snapshot path is second-resolution, and the real
    # collector never writes two within one second.
    write_snapshot(
        "nsw_rental_bonds",
        bonds("2026-05"),
        url="http://may",
        fetched_at=datetime(2026, 6, 10, tzinfo=UTC),
    )
    write_snapshot(
        "nsw_rental_bonds",
        bonds("2026-06"),
        url="http://june",
        fetched_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    result = build_nsw_rental_bonds()
    assert result.periods == 2
    assert result.rows == 100
    assert result.latest_period == "2026-06"
    assert (data_dir / "curated" / "nsw_rental_bonds_index.csv").exists()


def test_a_month_collected_twice_is_not_double_counted(data_dir):
    """The published file for a month can be reissued, and the workflow recaptures it."""
    import pandas as pd

    from auscpi.build import build_nsw_rental_bonds

    write_snapshot(
        "nsw_rental_bonds",
        bonds("2026-06", rent=500),
        url="http://first",
        fetched_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    write_snapshot(
        "nsw_rental_bonds",
        bonds("2026-06", rent=600),
        url="http://reissued",
        fetched_at=datetime(2026, 7, 20, tzinfo=UTC),
    )

    result = build_nsw_rental_bonds()
    assert result.rows == 50, "the month was counted twice"
    index = pd.read_csv(data_dir / "curated" / "nsw_rental_bonds_index.csv")
    assert index["median_weekly_rent"].iloc[0] == 600, "the reissued file should win"


def test_bond_build_respects_as_at(data_dir):
    """Rule 3, on a source whose history is assembled from many snapshots."""
    from auscpi.build import build_nsw_rental_bonds

    write_snapshot(
        "nsw_rental_bonds",
        bonds("2026-05"),
        url="http://may",
        fetched_at=datetime(2026, 6, 10, tzinfo=UTC),
    )
    write_snapshot(
        "nsw_rental_bonds",
        bonds("2026-06"),
        url="http://june",
        fetched_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    result = build_nsw_rental_bonds(as_at=datetime(2026, 6, 30, tzinfo=UTC))
    assert result.latest_period == "2026-05"
    assert result.periods == 1


def test_bond_build_reports_what_it_discarded(data_dir):
    """The cleaning rules drop real published rows, so the count has to surface."""
    from bond_fixtures import bond_workbook, lodgements

    from auscpi.build import build_nsw_rental_bonds

    rows = lodgements("2026-06", n=50) + lodgements("2026-06", dwelling="O", n=7)
    write_snapshot("nsw_rental_bonds", bond_workbook(rows), url="http://x")

    result = build_nsw_rental_bonds()
    assert result.rows == 50
    assert "dropped 7 rows" in result.note
    assert "non_dwelling_type" in result.note


def test_build_all_skips_uncollected_sources(data_dir):
    write_snapshot("abs_cpi_monthly", doc(["2026-06"]), url="http://x")
    results = build_all()
    assert [r.source for r in results] == ["abs_cpi_monthly"]


def test_build_all_strict_raises_on_a_missing_source(data_dir):
    write_snapshot("abs_cpi_monthly", doc(["2026-06"]), url="http://x")
    with pytest.raises(FileNotFoundError):
        build_all(strict=True)


def test_build_all_empty_when_nothing_collected(data_dir):
    assert build_all() == []
