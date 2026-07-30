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

    assert result.rows == 6  # 3 series x 2 periods
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
