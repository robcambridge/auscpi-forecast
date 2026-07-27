from auscpi.collectors import registry
from auscpi.collectors.base import Collector


def test_registry_populated():
    assert "fuelcheck" in registry


def test_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    from auscpi.config import settings

    monkeypatch.setattr(settings, "auscpi_data_dir", str(tmp_path / "data"))

    class Broken(Collector):
        source = "broken_test"

        def fetch(self):
            raise ValueError("boom")

    result = Broken().run()
    assert result.ok is False
    assert "boom" in result.error
