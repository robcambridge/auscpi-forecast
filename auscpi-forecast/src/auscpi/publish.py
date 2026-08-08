"""Publish the forecast: a machine-readable endpoint and a static fan chart.

Phase 7. Everything before this is only legible to someone with the repository
checked out and a Python environment, which is nobody. A forecast nobody can read is
indistinguishable from no forecast.

TWO ARTIFACTS, DELIBERATELY SEPARATE.

  data/published/forecast.{json,csv}   the endpoint. Tracked in git, so
                                       raw.githubusercontent.com serves it with no
                                       hosting, no build and no dependency on Pages
                                       staying enabled.
  site/index.html                       the page. Self-contained: inline SVG and
                                       inline CSS, no scripts, no fonts, no CDN.

The page is generated rather than committed, because a committed HTML file rots
against the data it describes. The endpoint IS committed, because its git history is
the same kind of evidence forecasts/log.csv is: a timestamped record that the numbers
said what they said before the outcome was known.

WHAT THE CHART SHOWS AND WHAT IT REFUSES TO SHOW. The fan is p10..p90 where the error
sample supports it and stops where it does not — around h=6, because fourteen usable
origins leave two errors at h=12. A fan that tapered smoothly to the end of the
horizon would look better and would be a lie about the sample. The band measures
dispersion around the point, not total error; the bias is stated on the page rather
than folded into the fan, for the reasons in uncertainty.py.

NO JAVASCRIPT, NO EXTERNAL REQUESTS. A dashboard that breaks when a CDN moves is a
dashboard that breaks. Inline SVG renders in any browser, prints, and survives being
saved to disk or pasted into an email.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auscpi.forecast import Path as ForecastPath

#: Chart geometry, in SVG user units.
WIDTH, HEIGHT = 720, 260
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 48, 16, 16, 34

TARGET_TITLES = {
    "headline_mom": "Headline CPI, month on month",
    "headline_yoy": "Headline CPI, year ended",
    "trimmed_mean_yoy": "Trimmed mean, year ended",
}

#: Plain-English gloss for each rule. A reader arriving at this page cold should not
#: have to decode an identifier like `seasonal_index_projection` to know what produced
#: the line they are looking at.
MODEL_NOTES = {
    "seasonal_index_projection": (
        "projects the seasonally adjusted index forward, then puts the published "
        "seasonal pattern back on"
    ),
    "seasonal_index_mom": "the same projected index, read month to month",
    "index_projection": "projects the index forward; this series is already seasonally adjusted",
    "seasonal_naive": "repeats the same calendar month a year earlier",
    "random_walk": "carries the last published year-ended rate forward unchanged",
    "mean_mom": "the average monthly movement over the past year",
    "atkeson_ohanian": "the average of the last twelve months",
    "target_midpoint": "the midpoint of the Reserve Bank's 2–3% target band",
}


def _describe(rule: str) -> str:
    note = MODEL_NOTES.get(rule)
    return f"{rule} &mdash; {note}" if note else rule


def _nice_bounds(values: list[float]) -> tuple[float, float]:
    """Axis bounds with a little air, and never a zero-height chart."""
    low, high = min(values), max(values)
    if high - low < 1e-9:
        low, high = low - 0.5, high + 0.5
    margin = (high - low) * 0.15
    return low - margin, high + margin


def _svg(path: ForecastPath) -> str:
    """One fan chart, as inline SVG.

    Drawn by hand rather than with a plotting library: the output has to be a single
    self-contained file, and a chart this simple does not justify a dependency that
    would then have to be installed wherever the page is built.
    """
    records = path.records
    if not records:
        return ""

    values = [r.point for r in records]
    values += [r.benchmark_point for r in records if r.benchmark_point is not None]
    values += [r.p10 for r in records if r.p10 is not None]
    values += [r.p90 for r in records if r.p90 is not None]
    low, high = _nice_bounds([float(v) for v in values])

    inner_w = WIDTH - PAD_LEFT - PAD_RIGHT
    inner_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    n = max(len(records) - 1, 1)

    def x(i: int) -> float:
        return PAD_LEFT + inner_w * i / n

    def y(value: float) -> float:
        return PAD_TOP + inner_h * (high - value) / (high - low)

    parts: list[str] = [
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" width="100%" role="img" '
        f'aria-label="{TARGET_TITLES.get(path.target, path.target)} forecast">'
    ]

    # Horizontal gridlines and the y axis.
    steps = 4
    for k in range(steps + 1):
        value = low + (high - low) * k / steps
        yy = y(value)
        parts.append(
            f'<line x1="{PAD_LEFT}" y1="{yy:.1f}" x2="{WIDTH - PAD_RIGHT}" y2="{yy:.1f}" '
            f'class="grid"/>'
            f'<text x="{PAD_LEFT - 6}" y="{yy + 3:.1f}" class="ytick">{value:+.1f}</text>'
        )

    # The fan, over the leading run of horizons that actually have a band. It stops
    # where the sample stops rather than tapering to the end of the path.
    banded = [
        (i, r) for i, r in enumerate(records) if r.p10 is not None and r.p90 is not None
    ]
    if len(banded) > 1:
        top = " ".join(f"{x(i):.1f},{y(float(r.p90)):.1f}" for i, r in banded)
        bottom = " ".join(
            f"{x(i):.1f},{y(float(r.p10)):.1f}" for i, r in reversed(banded)
        )
        parts.append(f'<polygon points="{top} {bottom}" class="fan"/>')

    def polyline(getter, cls: str) -> None:
        pts = [
            f"{x(i):.1f},{y(float(getter(r))):.1f}"
            for i, r in enumerate(records)
            if getter(r) is not None
        ]
        if len(pts) > 1:
            parts.append(f'<polyline points="{" ".join(pts)}" class="{cls}"/>')

    polyline(lambda r: r.benchmark_point, "bench")
    polyline(lambda r: r.point, "point")

    # X labels: first, middle, last, so the axis stays readable at this width. The
    # outer two are anchored inwards — centring them puts half the label past the
    # edge of the viewBox, which clips the last month.
    last = len(records) - 1
    for i in sorted({0, last // 2, last}):
        anchor = "start" if i == 0 else "end" if i == last else "middle"
        parts.append(
            f'<text x="{x(i):.1f}" y="{HEIGHT - 12}" class="xtick" '
            f'text-anchor="{anchor}">{records[i].reference_month}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1rem 4rem; font:16px/1.55 -apple-system,BlinkMacSystemFont,
  "Segoe UI",Roboto,Helvetica,Arial,sans-serif; max-width:820px; margin-inline:auto;
  color:#16181d; background:#fff; }
h1 { font-size:1.6rem; margin:0 0 .25rem; letter-spacing:-.01em; }
h2 { font-size:1.05rem; margin:2.5rem 0 .35rem; }
.sub { color:#5c626e; margin:0 0 2rem; }
.meta { color:#5c626e; font-size:.85rem; margin:.2rem 0 1rem; line-height:1.5; }
.method { border-top:1px solid #e6e8ec; border-bottom:1px solid #e6e8ec;
  padding:.5rem 0 1.4rem; margin:0 0 1rem; }
.method h2 { font-size:.95rem; text-transform:uppercase; letter-spacing:.06em;
  color:#5c626e; margin:1.4rem 0 .8rem; }
.method p { font-size:.94rem; margin:.7rem 0; }
figure { margin:0 0 .5rem; }
svg { display:block; }
.grid { stroke:#e6e8ec; stroke-width:1; }
.ytick { fill:#8a909c; font-size:11px; text-anchor:end; }
.xtick { fill:#8a909c; font-size:11px; text-anchor:middle; }
.fan { fill:#2f6feb; opacity:.16; }
.point { fill:none; stroke:#2f6feb; stroke-width:2.5; stroke-linejoin:round; }
.bench { fill:none; stroke:#9aa1ad; stroke-width:1.5; stroke-dasharray:5 4; }
.key { display:flex; gap:1.25rem; flex-wrap:wrap; font-size:.85rem; color:#5c626e;
  margin:.1rem 0 0; }
.key i { display:inline-block; width:18px; height:3px; vertical-align:middle;
  margin-right:.4rem; }
.k-point i { background:#2f6feb; }
.k-bench i { background:#9aa1ad; }
.k-fan i { background:#2f6feb; opacity:.3; height:10px; }
table { border-collapse:collapse; width:100%; font-size:.88rem; margin-top:.5rem; }
th,td { text-align:right; padding:.3rem .5rem; border-bottom:1px solid #e6e8ec; }
th:first-child,td:first-child { text-align:left; }
th { color:#5c626e; font-weight:600; }
.caveat { background:#fff8e6; border:1px solid #f0dfae; border-radius:8px;
  padding:.85rem 1rem; margin:2rem 0 0; font-size:.9rem; }
.caveat p { margin:.4rem 0; }
footer { margin-top:3rem; color:#8a909c; font-size:.82rem; }
a { color:#2f6feb; }
@media (prefers-color-scheme: dark) {
  body { color:#e6e8ec; background:#12141a; }
  .sub,.meta,.ytick,.xtick,th,footer,.method h2 { color:#98a0ae; }
  .grid,th,td,.method { border-color:#252932; stroke:#252932; }
  .caveat { background:#241f10; border-color:#4a3d18; }
}
"""


def _table(path: ForecastPath) -> str:
    head = (
        "<tr><th>h</th><th>month</th><th>point</th><th>p10</th><th>p90</th>"
        "<th>benchmark</th></tr>"
    )
    rows = []
    for r in path.records:
        band = (
            (f"{r.p10:+.2f}", f"{r.p90:+.2f}")
            if r.p10 is not None and r.p90 is not None
            else ("&mdash;", "&mdash;")
        )
        bench = "&mdash;" if r.benchmark_point is None else f"{r.benchmark_point:+.2f}"
        rows.append(
            f"<tr><td>{r.horizon_months}</td><td>{r.reference_month}</td>"
            f"<td>{r.point:+.2f}</td><td>{band[0]}</td><td>{band[1]}</td>"
            f"<td>{bench}</td></tr>"
        )
    return f"<table>{head}{''.join(rows)}</table>"


def render_dashboard(paths: list[ForecastPath], *, generated: str | None = None) -> str:
    """The whole page, self-contained."""
    stamp = generated or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    cutoff = paths[0].information_cutoff if paths else "—"

    blocks = []
    for path in paths:
        title = TARGET_TITLES.get(path.target, path.target)
        blocks.append(
            f"<h2>{title}</h2>"
            f'<p class="meta"><strong>Model:</strong> {_describe(path.model)}.<br>'
            f"<strong>Benchmark:</strong> {_describe(path.benchmark)}.<br>"
            f"Forecast made in {path.origin}.</p>"
            f"<figure>{_svg(path)}</figure>"
            '<p class="key">'
            '<span class="k-point"><i></i>forecast</span>'
            '<span class="k-bench"><i></i>benchmark</span>'
            '<span class="k-fan"><i></i>p10&ndash;p90, where estimable</span>'
            "</p>"
            f"{_table(path)}"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Australian CPI forecast</title>
<style>{STYLE}</style></head><body>
<h1>Australian CPI forecast</h1>
<p class="sub">A path from h=0 to h=12, updated after each ABS release.
Information cutoff <strong>{cutoff}</strong>. Generated {stamp}.</p>

<section class="method">
<h2>How this is made</h2>
<p>Each chart is a <em>path</em>: a forecast for the current month (h=0) and for each of the
twelve months after it, rather than a single number. Everything is built from the ABS
Consumer Price Index; <strong>information cutoff</strong> above is the most recent month
of published data used, so anything after it is genuinely forecast.</p>

<p>The method projects the underlying <strong>index level</strong> forward and reads the
rate off that, instead of forecasting the published rate directly. This matters because a
year-ended rate compares two index levels twelve months apart, so most of it is already
published: forecasting July from June data, eleven of the twelve monthly movements are
history and only one is unknown. Those known movements drop out of the twelve-month
comparison on a fixed, knowable schedule &mdash; a <em>base effect</em> &mdash; and simply
carrying the last published rate forward would throw all of it away.</p>

<p>The share that is genuinely forecast grows with horizon, so the model decays into a
naive rule at the long end rather than claiming skill it does not have. Every point is
shown against a <strong>benchmark</strong>: a deliberately simple rule that is hard to beat.
Without one, an error of a few tenths means nothing &mdash; nobody can tell whether that is
good or dreadful.</p>
</section>
{"".join(blocks)}
<div class="caveat">
<p><strong>Read this before using the numbers.</strong></p>
<p>The models are naive by design and were published before they were good, because a
track record starting at n=1 with a weak model beats one starting at n=0 with a good
one. No claim of skill is made.</p>
<p>The fan stops around h=6. It is not tapering off the end of the chart &mdash; the
error sample runs out. Fourteen usable origins leave two errors at h=12, and a band
drawn from two numbers would be decoration.</p>
<p>The band measures dispersion around the point, not total error. The year-ended
models read <em>low</em>, increasingly with horizon (about &minus;0.65pp at h=6 for
headline). That bias is deliberately not folded into the fan and not corrected, since
fourteen overlapping origins over one inflation episode cannot establish it.</p>
<p>Forecast errors at different horizons for overlapping months are correlated.
Twelve months of h=1..12 forecasts is nowhere near 144 independent observations.</p>
</div>
<footer>Every forecast is committed before the release it refers to; the git history of
<code>forecasts/log.csv</code> is the evidence. Machine-readable copies of this path are
at <code>data/published/forecast.json</code> and <code>forecast.csv</code>.</footer>
</body></html>
"""


def write_endpoint(paths: list[ForecastPath], outdir: Path) -> list[Path]:
    """The machine-readable artifacts, as JSON and CSV."""
    outdir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(UTC).isoformat()

    payload = {
        "generated_at": generated,
        "information_cutoff": paths[0].information_cutoff if paths else None,
        "paths": [
            {
                "target": p.target,
                "model": p.model,
                "benchmark": p.benchmark,
                "origin": p.origin,
                "information_cutoff": p.information_cutoff,
                "records": [asdict(r) for r in p.records],
            }
            for p in paths
        ],
    }
    json_path = outdir / "forecast.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    csv_path = outdir / "forecast.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["target", "model", "benchmark", "origin", "information_cutoff",
             "reference_month", "horizon_months", "point", "p10", "p25", "p75", "p90",
             "benchmark_point"]
        )
        for p in paths:
            for r in p.records:
                writer.writerow(
                    [p.target, p.model, p.benchmark, p.origin, p.information_cutoff,
                     r.reference_month, r.horizon_months, r.point, r.p10, r.p25,
                     r.p75, r.p90, r.benchmark_point]
                )
    return [json_path, csv_path]


def write_site(paths: list[ForecastPath], outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    page = outdir / "index.html"
    page.write_text(render_dashboard(paths), encoding="utf-8")
    return page
