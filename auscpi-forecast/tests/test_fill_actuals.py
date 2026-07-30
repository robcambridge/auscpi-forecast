from __future__ import annotations

import csv
from datetime import date

import pytest

from auscpi import release_calendar, track_record
from auscpi.config import settings


@pytest.fixture(autouse=True)
def tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(type(settings), "forecast_log", property(lambda self: tmp_path / "log.csv"))
    return tmp_path / "log.csv"


def log_one(reference_month: str, target: str = "headline_mom", **kw) -> None:
    track_record.log_forecast(
        track_record.ForecastRecord(
            made_at="",
            reference_month=reference_month,
            horizon_months=kw.pop("horizon", 1),
            target=target,
            point=kw.pop("point", 0.4),
            **kw,
        )
    )


def read_rows(path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_fills_only_matching_rows(tmp_log):
    log_one("2026-07")
    log_one("2026-08")

    result = track_record.fill_actuals({("headline_mom", "2026-07"): 0.5})

    assert (result.filled, result.unavailable) == (1, 1)
    rows = {r["reference_month"]: r["actual"] for r in read_rows(tmp_log)}
    assert rows["2026-07"] == "0.5"
    assert rows["2026-08"] == ""


def test_matches_on_target_as_well_as_month(tmp_log):
    log_one("2026-07", target="headline_mom")
    log_one("2026-07", target="headline_yoy")

    track_record.fill_actuals({("headline_yoy", "2026-07"): 3.8})

    rows = {r["target"]: r["actual"] for r in read_rows(tmp_log)}
    assert rows["headline_yoy"] == "3.8"
    assert rows["headline_mom"] == ""


def test_never_overwrites_an_actual_already_set(tmp_log):
    """Rule 4: revising a logged actual would rewrite history in the model's favour."""
    log_one("2026-07", actual=0.5)

    result = track_record.fill_actuals({("headline_mom", "2026-07"): 9.9})

    assert (result.filled, result.already_set) == (0, 1)
    assert read_rows(tmp_log)[0]["actual"] == "0.5"


def test_leaves_every_other_field_untouched(tmp_log):
    log_one("2026-07", model="atkeson_ohanian", benchmark_name="random_walk", benchmark_point=0.31)
    before = read_rows(tmp_log)[0]

    track_record.fill_actuals({("headline_mom", "2026-07"): 0.5})
    after = read_rows(tmp_log)[0]

    for field, value in before.items():
        if field != "actual":
            assert after[field] == value, field


def test_preserves_the_files_own_header_order(tmp_log):
    log_one("2026-07")
    with tmp_log.open(encoding="utf-8") as fh:
        header_before = fh.readline().strip()

    track_record.fill_actuals({("headline_mom", "2026-07"): 0.5})

    with tmp_log.open(encoding="utf-8") as fh:
        assert fh.readline().strip() == header_before


def test_is_idempotent(tmp_log):
    log_one("2026-07")
    first = track_record.fill_actuals({("headline_mom", "2026-07"): 0.5})
    second = track_record.fill_actuals({("headline_mom", "2026-07"): 0.5})

    assert first.filled == 1
    assert (second.filled, second.already_set) == (0, 1)
    assert len(read_rows(tmp_log)) == 1


def test_no_log_file_is_not_an_error(tmp_log):
    result = track_record.fill_actuals({("headline_mom", "2026-07"): 0.5})
    assert result == track_record.FillResult(filled=0, already_set=0, unavailable=0, rows=0)


def test_no_temp_file_left_behind(tmp_log):
    log_one("2026-07")
    track_record.fill_actuals({("headline_mom", "2026-07"): 0.5})
    assert not list(tmp_log.parent.glob("*.tmp"))


def test_filling_makes_the_row_scoreable(tmp_log):
    """The whole point: score() ignores rows without an actual."""
    log_one("2026-07", benchmark_name="random_walk", benchmark_point=0.30)
    assert track_record.score() == []

    track_record.fill_actuals({("headline_mom", "2026-07"): 0.50})
    rows = track_record.score()

    assert len(rows) == 1
    assert rows[0]["n"] == 1
    assert rows[0]["mae"] == pytest.approx(0.10, abs=1e-9)
    # Benchmark was further out (0.20), so skill is positive.
    assert rows[0]["skill"] > 0


# --- release calendar (rule 6) ---


def test_calendar_reads_the_shipped_file():
    cal = release_calendar.load()
    assert cal["2026-06"] == date(2026, 7, 29)
    # The reason not to hardcode "last Wednesday": this one is neither.
    assert cal["2026-11"] == date(2027, 1, 6)


def test_is_released_distinguishes_unknown_from_not_yet():
    assert release_calendar.is_released("2026-06", date(2026, 7, 30)) is True
    assert release_calendar.is_released("2026-09", date(2026, 7, 30)) is False
    # Beyond the calendar: unknown, and must not read as "not released".
    assert release_calendar.is_released("2030-01", date(2026, 7, 30)) is None


def test_missing_calendar_file_is_empty_not_fatal(tmp_path):
    assert release_calendar.load(tmp_path / "absent.csv") == {}
    assert release_calendar.release_date("2026-06", tmp_path / "absent.csv") is None
