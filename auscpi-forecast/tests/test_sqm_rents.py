from __future__ import annotations

import pytest

from auscpi.collectors import registry, sqm_rents
from auscpi.collectors.sqm_rents import SQMRentsCollector, load_postcodes


def test_registered():
    assert registry.get("sqm_rents") is SQMRentsCollector


def test_cadence_is_weekly():
    assert SQMRentsCollector.cadence == "weekly"


def test_disabled_by_default():
    # Ships off: Cloudflare blocks CI's datacentre IP and the daily job would run
    # it too often. It is enabled by hand on a residential weekly runner.
    assert SQMRentsCollector.enabled is False


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


def test_fetch_returns_raw_html_keyed_by_postcode(monkeypatch):
    monkeypatch.setattr(sqm_rents.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sqm_rents, "load_postcodes", lambda: ["2000", "3000"])
    monkeypatch.setattr(
        sqm_rents, "_fetch_postcode", lambda client, pc: f"<html>{pc} var data = []</html>"
    )

    payload, url, n = SQMRentsCollector().fetch()

    assert url == sqm_rents.WEEKLY_RENTS_URL
    assert n == 2
    assert set(payload["pages"]) == {"2000", "3000"}
    # Stored verbatim — the collector does not parse.
    assert payload["pages"]["3000"] == "<html>3000 var data = []</html>"
    assert payload["failed"] == {}


def test_fetch_records_partial_failure_without_raising(monkeypatch):
    monkeypatch.setattr(sqm_rents.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sqm_rents, "load_postcodes", lambda: ["2000", "9999"])

    def fake(client, pc):
        if pc == "9999":
            raise RuntimeError("boom")
        return "<html>ok</html>"

    monkeypatch.setattr(sqm_rents, "_fetch_postcode", fake)

    payload, _url, n = SQMRentsCollector().fetch()

    assert n == 1
    assert set(payload["pages"]) == {"2000"}
    assert "boom" in payload["failed"]["9999"]


def test_fetch_raises_when_every_postcode_fails(monkeypatch):
    monkeypatch.setattr(sqm_rents.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sqm_rents, "load_postcodes", lambda: ["2000", "3000"])

    def boom(client, pc):
        raise RuntimeError("blocked")

    monkeypatch.setattr(sqm_rents, "_fetch_postcode", boom)

    with pytest.raises(RuntimeError):
        SQMRentsCollector().fetch()
