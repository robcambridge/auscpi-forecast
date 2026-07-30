"""Command line interface.

auscpi collect --all          run every enabled collector
auscpi collect fuelcheck      run one
auscpi backfill-bonds         capture the NSW rental bond history
auscpi build                  rebuild data/curated from data/raw
auscpi health                 when did each source last succeed?
auscpi log-forecast ...       append a row to the public track record
auscpi score                  error vs benchmark, for settled forecasts
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table

from auscpi import track_record
from auscpi.collectors import registry
from auscpi.storage import read_manifest

app = typer.Typer(add_completion=False, help="Australian monthly CPI nowcast.")
console = Console()


@app.command()
def collect(
    source: str = typer.Argument(None, help="Collector slug. Omit and use --all for everything."),
    all_: bool = typer.Option(False, "--all", help="Run every enabled collector."),
) -> None:
    if all_:
        targets = [cls for cls in registry.values() if cls.enabled]
    elif source:
        if source not in registry:
            raise typer.BadParameter(f"unknown source {source!r}; have {sorted(registry)}")
        targets = [registry[source]]
    else:
        raise typer.BadParameter("give a source or --all")

    failures = 0
    for cls in targets:
        result = cls().run()
        if result.ok:
            console.print(
                f"[green]ok[/green]  {result.source:20} "
                f"{result.n_records if result.n_records is not None else '?':>7} records  "
                f"{result.seconds:.1f}s"
            )
        else:
            failures += 1
            console.print(f"[red]FAIL[/red] {result.source:20} {result.error}")

    if failures:
        raise typer.Exit(code=1)


@app.command()
def build(
    as_at: str = typer.Option(
        None,
        "--as-at",
        help='Build from the information set as at this instant, e.g. "2026-06-30". '
        "Omitted, the newest snapshot is used.",
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Fail if a source has no snapshot yet, rather than skipping it."
    ),
) -> None:
    """Rebuild data/curated from data/raw. Safe to re-run; output is disposable."""
    from auscpi.build import build_all

    cutoff = None
    if as_at:
        cutoff = datetime.fromisoformat(as_at)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)

    results = build_all(as_at=cutoff, strict=strict)
    if not results:
        console.print(
            "[yellow]nothing to build[/yellow] — no snapshots yet. "
            "Try `auscpi collect abs_cpi_monthly`."
        )
        raise typer.Exit()

    table = Table("source", "rows", "periods", "latest", "vintage (UTC)")
    for r in results:
        table.add_row(
            r.source,
            f"{r.rows:,}",
            str(r.periods),
            r.latest_period,
            r.vintage[:19].replace("T", " "),
        )
    console.print(table)
    for r in results:
        for out in r.outputs:
            console.print(f"  [green]wrote[/green] {out}")


@app.command("backfill-bonds")
def backfill_bonds(
    include_annual: bool = typer.Option(
        False,
        "--include-annual",
        help="Also take the annual compilations. They duplicate the monthlies.",
    ),
    limit: int = typer.Option(None, help="Stop after N files. Useful for a trial run."),
) -> None:
    """Capture the published history of NSW rental bond lodgements.

    Monthly files run back to January 2022 — roughly 55 files at ~675 KB each, so
    expect ~35 MB in data/raw and a slow first run at one file every few seconds.
    Already-captured files are skipped, so this resumes rather than restarting.
    """
    from auscpi.collectors.nsw_rental_bonds import backfill

    written = backfill(include_annual=include_annual, limit=limit)
    if not written:
        console.print("[yellow]nothing to do[/yellow] — every published file is already captured.")
        raise typer.Exit()
    console.print(f"[green]captured[/green] {len(written)} file(s) into data/raw.")
    console.print("[dim]Commit data/raw — it is the provenance layer.[/dim]")


@app.command()
def health() -> None:
    """Last successful fetch per source. A silent scraper is the main failure mode."""
    table = Table("source", "cadence", "last ok (UTC)", "age", "last error")
    now = datetime.now(UTC)
    for slug, cls in sorted(registry.items()):
        entries = read_manifest(slug)
        oks = [e for e in entries if e["status"] == "ok"]
        errs = [e for e in entries if e["status"] == "error"]
        if oks:
            last = datetime.fromisoformat(oks[-1]["fetched_at"])
            age = now - last
            age_s = f"{age.days}d {age.seconds // 3600}h"
            stamp = last.strftime("%Y-%m-%d %H:%M")
        else:
            age_s, stamp = "-", "never"
        table.add_row(slug, cls.cadence, stamp, age_s, (errs[-1]["note"] if errs else ""))
    console.print(table)


@app.command("log-forecast")
def log_forecast(
    reference_month: str = typer.Option(..., help='CPI month being forecast, e.g. "2026-09"'),
    target: str = typer.Option(
        "headline_mom", help="headline_mom | headline_yoy | trimmed_mean_yoy"
    ),
    point: float = typer.Option(..., help="Point forecast, per cent."),
    horizon: int = typer.Option(
        None, help="Months ahead. Omitted, it is derived from today's date."
    ),
    model: str = typer.Option("manual", help="Model name."),
    p10: float = typer.Option(None),
    p90: float = typer.Option(None),
    benchmark_name: str = typer.Option("", help='e.g. "random_walk_yoy"'),
    benchmark_point: float = typer.Option(None),
    note: str = typer.Option(""),
) -> None:
    if horizon is None:
        origin = datetime.now(UTC).strftime("%Y-%m")
        horizon = track_record.months_between(origin, reference_month)

    track_record.log_forecast(
        track_record.ForecastRecord(
            made_at="",
            reference_month=reference_month,
            horizon_months=horizon,
            target=target,
            point=point,
            p10=p10,
            p90=p90,
            model=model,
            benchmark_name=benchmark_name,
            benchmark_point=benchmark_point,
            note=note,
        )
    )
    console.print(
        f"[green]logged[/green] {model} {target} {reference_month} "
        f"(h={horizon}) = {point}. Commit and push it — the push time is the proof."
    )


@app.command("log-path")
def log_path(
    reference_month: str = typer.Option(..., help="First month of the path."),
    points: str = typer.Option(..., help='Comma-separated, e.g. "0.4,0.3,0.5,0.6"'),
    target: str = typer.Option("headline_mom"),
    model: str = typer.Option("manual"),
) -> None:
    """Log a whole forecast path in one go. This is the normal case."""
    origin = datetime.now(UTC).strftime("%Y-%m")
    y, m = (int(x) for x in reference_month.split("-"))
    for i, raw in enumerate(points.split(",")):
        month_index = (m - 1) + i
        ref = f"{y + month_index // 12:04d}-{month_index % 12 + 1:02d}"
        track_record.log_forecast(
            track_record.ForecastRecord(
                made_at="",
                reference_month=ref,
                horizon_months=track_record.months_between(origin, ref),
                target=target,
                point=float(raw.strip()),
                model=model,
            )
        )
    console.print(
        f"[green]logged[/green] path of {len(points.split(','))} months from {reference_month}."
    )


@app.command()
def score() -> None:
    rows = track_record.score()
    if not rows:
        console.print("No settled forecasts yet.")
        raise typer.Exit()
    table = Table("h", "model", "target", "n", "MAE", "bench MAE", "skill")
    for r in rows:
        table.add_row(
            str(r["horizon_months"]),
            str(r["model"]),
            str(r["target"]),
            str(r["n"]),
            f"{r['mae']:.3f}",
            "-" if r["benchmark_mae"] is None else f"{r['benchmark_mae']:.3f}",
            "-" if r["skill"] is None else f"{r['skill']:+.2f}",
        )
    console.print(table)
    console.print(
        "[dim]skill = 1 - MAE/benchmark MAE. Positive is better than the benchmark.\n"
        "Overlapping horizons have correlated errors — n is not effective sample size.[/dim]"
    )


if __name__ == "__main__":
    app()
