from __future__ import annotations

import json
import re

import pytest

from auscpi.forecast import Path as ForecastPath
from auscpi.publish import render_dashboard, write_endpoint, write_site
from auscpi.track_record import ForecastRecord


def record(h: int, point: float, *, banded: bool = True) -> ForecastRecord:
    return ForecastRecord(
        made_at="2026-08-02T00:00:00+00:00",
        reference_month=f"2026-{8 + h:02d}" if h < 5 else f"2027-{h - 4:02d}",
        horizon_months=h,
        target="headline_yoy",
        point=point,
        information_cutoff="2026-06",
        p10=point - 0.5 if banded else None,
        p25=point - 0.2 if banded else None,
        p75=point + 0.2 if banded else None,
        p90=point + 0.5 if banded else None,
        benchmark_point=3.8,
    )


def path_with(records) -> ForecastPath:
    return ForecastPath(
        target="headline_yoy",
        model="seasonal_index_projection",
        benchmark="random_walk",
        origin="2026-08",
        information_cutoff="2026-06",
        records=records,
    )


MIXED = path_with(
    [record(h, 3.2 + 0.05 * h) for h in range(4)]
    + [record(h, 3.2 + 0.05 * h, banded=False) for h in range(4, 8)]
)


def test_the_page_is_self_contained():
    """No CDN, no fonts, no scripts. A dashboard that breaks when a host moves is a
    dashboard that breaks, and this one has to survive being saved to disk."""
    html = render_dashboard([MIXED])
    assert "<script" not in html.lower()
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', html, re.I)
    assert "<style>" in html


def test_the_fan_stops_where_the_bands_stop():
    """It must not taper to the end of the horizon: the sample runs out, and drawing
    through that would be a lie about how much is known."""
    html = render_dashboard([MIXED])
    polygon = re.search(r'<polygon points="([^"]+)" class="fan"', html)
    assert polygon, "a path with bands should render a fan"
    # Four banded horizons, traced out along the top and back along the bottom.
    assert len(polygon.group(1).split()) == 8


def test_no_fan_when_nothing_is_estimable():
    bare = path_with([record(h, 3.2, banded=False) for h in range(6)])
    assert 'class="fan"' not in render_dashboard([bare])


def test_unbanded_horizons_are_dashes_in_the_table_not_blanks():
    html = render_dashboard([MIXED])
    assert "&mdash;" in html


def test_the_page_names_the_model_class_before_the_first_chart():
    """A reader should not have to infer the model class from an identifier."""
    html = render_dashboard([MIXED])
    assert html.index("random walk with drift") < html.index("<svg")
    assert "seasonally adjusted index" in html


def test_model_names_carry_a_specification_not_just_an_identifier():
    html = render_dashboard([MIXED])
    assert "seasonal_index_projection" in html, "the code's own name should still appear"
    assert "random walk with drift on the seasonally adjusted index" in html
    assert "no change from the last published year-ended rate" in html


def test_an_unglossed_rule_still_renders():
    """A new rule must not break the page before someone writes its note."""
    from auscpi.publish import _describe

    assert _describe("some_new_rule") == "some_new_rule"


def test_the_page_states_why_the_fan_stops():
    """The blank half of the chart is the honest part and needs saying in words."""
    html = render_dashboard([MIXED]).lower()
    assert "sample" in html
    assert "dispersion" in html
    assert "no claim of skill" in html


def test_the_outer_axis_labels_are_anchored_inwards():
    """Centring them puts half the text past the viewBox and clips the last month."""
    html = render_dashboard([MIXED])
    anchors = re.findall(r'class="xtick" text-anchor="(\w+)"', html)
    assert anchors[0] == "start"
    assert anchors[-1] == "end"


def test_an_empty_path_does_not_crash_the_renderer():
    assert "<html" in render_dashboard([path_with([])])


def test_the_chart_survives_a_flat_path():
    """A zero-height axis would divide by zero and produce an unrenderable chart."""
    flat = path_with([record(h, 3.0) for h in range(4)])
    for r in flat.records:
        r.p10 = r.p25 = r.p75 = r.p90 = 3.0
        r.benchmark_point = 3.0
    html = render_dashboard([flat])
    assert "nan" not in html.lower()
    assert "<svg" in html


# --- the endpoint ---------------------------------------------------------


def test_endpoint_writes_json_and_csv(tmp_path):
    written = write_endpoint([MIXED], tmp_path)
    assert {p.name for p in written} == {"forecast.json", "forecast.csv"}

    payload = json.loads((tmp_path / "forecast.json").read_text(encoding="utf-8"))
    assert payload["information_cutoff"] == "2026-06"
    assert payload["paths"][0]["target"] == "headline_yoy"
    assert len(payload["paths"][0]["records"]) == len(MIXED.records)


def test_endpoint_csv_carries_the_bands_and_the_cutoff(tmp_path):
    write_endpoint([MIXED], tmp_path)
    lines = (tmp_path / "forecast.csv").read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    assert {"p10", "p90", "information_cutoff", "benchmark_point"} <= set(header)
    assert len(lines) == len(MIXED.records) + 1


def test_endpoint_json_round_trips_a_missing_band(tmp_path):
    """Nulls must survive as nulls rather than becoming zero."""
    write_endpoint([MIXED], tmp_path)
    payload = json.loads((tmp_path / "forecast.json").read_text(encoding="utf-8"))
    tail = payload["paths"][0]["records"][-1]
    assert tail["p10"] is None
    assert tail["p90"] is None


def test_write_site_creates_the_directory(tmp_path):
    page = write_site([MIXED], tmp_path / "nested" / "site")
    assert page.exists()
    assert page.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_every_target_gets_its_own_section():
    other = path_with([record(h, 0.3) for h in range(4)])
    other.target = "headline_mom"
    html = render_dashboard([MIXED, other])
    # Counted by chart rather than by <h2>, since the method section has one too.
    assert html.count("<figure>") == 2
    assert "Headline CPI, year ended" in html
    assert "Headline CPI, month on month" in html


@pytest.mark.parametrize("target", ["headline_mom", "headline_yoy", "trimmed_mean_yoy"])
def test_every_logged_target_has_a_readable_title(target):
    from auscpi.publish import TARGET_TITLES

    assert target in TARGET_TITLES
