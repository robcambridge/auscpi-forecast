from __future__ import annotations

import pytest

from auscpi.collectors import registry, sqm_rents
from auscpi.collectors.sqm_rents import SQMRentsCollector, load_postcodes


def test_registered():
    assert registry.get("sqm_rents") is SQMRentsCollector


def test_cadence_is_weekly():
    assert SQMRentsCollector.cadence == "weekly"


def test_disabled_because_terms_prohibit_automated_access():
    assert SQMRentsCollector.enabled is False


def test_fetch_refuses():
    with pytest.raises(RuntimeError) as exc:
        SQMRentsCollector().fetch()
    # The message has to carry the reason and the alternative; a bare raise here
    # would just get "fixed" by whoever trips over it next.
    message = str(exc.value)
    assert "Terms of Service" in message
    assert "terms-of-service" in message
    assert "Rental Bond" in message


def test_fetch_refuses_before_opening_a_connection(monkeypatch):
    """The guard must not depend on the network being unreachable."""

    def explode(*args, **kwargs):
        raise AssertionError("sqm_rents attempted a network connection")

    monkeypatch.setattr(sqm_rents.httpx, "Client", explode)
    with pytest.raises(RuntimeError):
        SQMRentsCollector().fetch()


def test_enabling_it_alone_does_not_start_scraping(monkeypatch, tmp_path):
    """Flipping `enabled` is not sufficient — fetch() still refuses.

    This is the failure mode the guard exists for: someone reads "disabled by
    default", flips the flag, and silently breaches the source's terms.
    """
    from auscpi.config import settings

    monkeypatch.setattr(settings, "auscpi_data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(SQMRentsCollector, "enabled", True)

    result = SQMRentsCollector().run()

    assert result.ok is False
    assert "Terms of Service" in result.error


def test_default_basket_keeps_leading_zero():
    # Darwin is 0800; were postcodes ever ints the leading zero would vanish.
    assert "0800" in sqm_rents.DEFAULT_POSTCODES
    assert all(isinstance(pc, str) for pc in sqm_rents.DEFAULT_POSTCODES)


def test_load_postcodes_reads_file_and_skips_noise(tmp_path, monkeypatch):
    f = tmp_path / "pc.csv"
    f.write_text("# a comment, with a comma\npostcode\n2000\n\n0800\n", encoding="utf-8")
    monkeypatch.setattr(sqm_rents, "POSTCODE_FILE", f)
    assert load_postcodes() == ["2000", "0800"]


def test_load_postcodes_falls_back_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sqm_rents, "POSTCODE_FILE", tmp_path / "nope.csv")
    assert load_postcodes() == list(sqm_rents.DEFAULT_POSTCODES)
