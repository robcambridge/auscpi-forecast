from auscpi.collectors import registry
from auscpi.collectors.base import Collector


def test_cadence_tolerance_is_generous_but_finite():
    """One missed run is timing variation; several is a fault."""
    from datetime import timedelta

    from auscpi.collectors.base import overdue_after

    assert overdue_after("daily") == timedelta(days=2)
    assert overdue_after("monthly") == timedelta(days=40)
    assert overdue_after("quarterly") > overdue_after("monthly")
    # An unrecognised cadence must not silently become "never overdue".
    assert overdue_after("fortnightly-ish") == overdue_after("daily")


def test_ruled_out_is_separate_from_merely_unscheduled():
    """`enabled = False` is overloaded; health must not report SQM as overdue.

    Several collectors set enabled=False only because the daily runner would
    re-fetch a monthly file thirty times. SQM is different in kind — its terms
    prohibit collection — and conflating the two would either nag forever about a
    source that must never run, or hide a monthly source that has genuinely stopped.
    """
    from auscpi.collectors import registry
    from auscpi.collectors.abs_cpi import ABSMonthlyCPICollector
    from auscpi.collectors.sqm_rents import SQMRentsCollector

    assert SQMRentsCollector.ruled_out is True
    assert SQMRentsCollector.enabled is False
    # Unscheduled but perfectly collectable.
    assert ABSMonthlyCPICollector.enabled is False
    assert ABSMonthlyCPICollector.ruled_out is False
    assert all(not c.ruled_out for s, c in registry.items() if s != "sqm_rents")


def test_health_flags_an_overdue_source_and_strict_exits_nonzero(tmp_path, monkeypatch):
    """The Phase 1 completion criterion is 'fetched within its cadence'.

    Printing an age and leaving the comparison to the reader cannot answer that,
    which is how a daily collector sat un-run without the tool noticing.
    """
    from datetime import UTC, datetime, timedelta

    from typer.testing import CliRunner

    from auscpi.cli import app
    from auscpi.config import settings
    from auscpi.storage import write_snapshot

    monkeypatch.setattr(settings, "auscpi_data_dir", str(tmp_path / "data"))
    stale = datetime.now(UTC) - timedelta(days=5)
    write_snapshot("fuelcheck", {"x": 1}, url="http://x", fetched_at=stale)

    runner = CliRunner()
    assert "OVERDUE" in runner.invoke(app, ["health"]).stdout
    assert runner.invoke(app, ["health", "--strict"]).exit_code == 1


def test_health_is_quiet_when_a_source_is_fresh(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from typer.testing import CliRunner

    from auscpi.cli import app
    from auscpi.config import settings
    from auscpi.storage import write_snapshot

    monkeypatch.setattr(settings, "auscpi_data_dir", str(tmp_path / "data"))
    write_snapshot("fuelcheck", {"x": 1}, url="http://x", fetched_at=datetime.now(UTC))

    result = CliRunner().invoke(app, ["health"])
    assert "OVERDUE" not in result.stdout
    # sqm_rents has never been collected and never will be; it must not be "never
    # collected" in the failing sense.
    assert "ruled out" in result.stdout


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
