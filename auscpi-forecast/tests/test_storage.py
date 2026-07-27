from datetime import UTC, datetime

import pytest

from auscpi import storage
from auscpi.config import settings


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auscpi_data_dir", str(tmp_path / "data"))
    yield


def test_snapshot_roundtrip():
    snap = storage.write_snapshot("demo", {"a": 1}, url="https://example.test", n_records=1)
    assert snap.status == "ok"
    assert storage.load_snapshot(snap.payload_path) == {"a": 1}


def test_snapshots_are_immutable():
    t = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    storage.write_snapshot("demo", {"a": 1}, url="u", fetched_at=t)
    with pytest.raises(FileExistsError):
        storage.write_snapshot("demo", {"a": 2}, url="u", fetched_at=t)


def test_as_at_excludes_the_future():
    """The whole point: a backtest must not see data fetched after the cutoff."""
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 6, 1, tzinfo=UTC)
    storage.write_snapshot("demo", {"v": "early"}, url="u", fetched_at=early)
    storage.write_snapshot("demo", {"v": "late"}, url="u", fetched_at=late)

    visible = storage.snapshots_as_at("demo", datetime(2026, 3, 1, tzinfo=UTC))
    assert len(visible) == 1
    assert storage.load_snapshot(visible[0]["payload_path"]) == {"v": "early"}
