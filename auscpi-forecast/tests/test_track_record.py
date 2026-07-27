import pytest

from auscpi import track_record
from auscpi.config import settings


@pytest.fixture(autouse=True)
def tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(type(settings), "forecast_log", property(lambda self: tmp_path / "log.csv"))
    yield


def test_months_between():
    assert track_record.months_between("2026-07", "2026-09") == 2
    assert track_record.months_between("2026-11", "2027-02") == 3
    assert track_record.months_between("2026-07", "2026-07") == 0


def test_score_groups_by_horizon():
    """A model good at h=1 and useless at h=6 must not be reported as one number."""
    for h, point in ((1, 0.45), (6, 0.90)):
        track_record.log_forecast(
            track_record.ForecastRecord(
                made_at="",
                reference_month="2026-09",
                horizon_months=h,
                target="headline_mom",
                point=point,
                model="test",
                benchmark_name="random_walk_yoy",
                benchmark_point=0.60,
                actual=0.50,
            )
        )
    rows = track_record.score()
    assert len(rows) == 2
    short, long_ = rows[0], rows[1]
    assert short["horizon_months"] == 1
    assert short["skill"] > 0  # beat the benchmark at h=1
    assert long_["skill"] < 0  # lost to it at h=6
