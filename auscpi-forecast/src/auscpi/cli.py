"""Command line interface.

auscpi collect --all          run every enabled collector
auscpi collect fuelcheck      run one
auscpi backfill-bonds         capture the NSW rental bond history
auscpi build                  rebuild data/curated from data/raw
auscpi forecast --log         produce and log a naive h=0..12 path
auscpi fill-actual            fill `actual` for months the ABS has published
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
        # What a source discarded is part of the result, not a debug detail: a
        # cleaning rule that starts dropping twice as much should be visible here.
        if r.note:
            console.print(f"  [dim]{r.note}[/dim]")


@app.command("backfill-fuel")
def backfill_fuel(
    since: str = typer.Option(None, help='Earliest month to capture, "YYYY-MM". Omit for all.'),
    until: str = typer.Option(None, help='Latest month to capture, "YYYY-MM". Omit for all.'),
    limit: int = typer.Option(None, help="Stop after N files. Useful for a trial run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be captured."),
) -> None:
    """Capture NSW's published monthly FuelCheck price-history files.

    The live API returns prices right now and nothing else, so this is the only route
    to fuel history. The full archive runs to roughly 400 MB back to 2016 and
    data/raw is tracked in git, so bound it with --since/--until rather than taking
    the lot by reflex. Already-captured files are skipped, so it resumes.
    """
    from auscpi.collectors.fuelcheck import (
        backfill,
        discover_archive_files,
        fetch_archive_index,
    )

    if dry_run:
        files = [
            f
            for f in discover_archive_files(fetch_archive_index())
            if (since is None or f.period >= since) and (until is None or f.period <= until)
        ]
        if limit:
            files = files[:limit]
        known = sum(f.size or 0 for f in files)
        table = Table("period", "format", "MB", "name")
        for f in sorted(files, key=lambda f: f.period):
            table.add_row(f.period, f.fmt, f"{(f.size or 0) / 1024 / 1024:.1f}", f.name[:48])
        console.print(table)
        console.print(f"{len(files)} file(s), {known / 1024 / 1024:.1f} MB. Nothing downloaded.")
        raise typer.Exit()

    written = backfill(since=since, until=until, limit=limit)
    if not written:
        console.print("[yellow]nothing to do[/yellow] — every file in range is already captured.")
        raise typer.Exit()
    console.print(f"[green]captured[/green] {len(written)} file(s) into data/raw.")
    console.print("[dim]Commit data/raw — it is the provenance layer.[/dim]")


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
def rents(
    horizons: int = typer.Option(12, help="Longest horizon. The path runs h=0..N."),
    roll_through: int = typer.Option(
        None, help="Months for the stock to roll through. Structural; default 12."
    ),
    as_at: str = typer.Option(None, "--as-at", help="Use the vintage as at this instant."),
    region: str = typer.Option(
        "australia",
        help="Target city. National is the product; a city is a diagnostic on the pass-through.",
    ),
    show_backtest: bool = typer.Option(
        False, "--backtest", help="Score the roll-through against carrying rents flat, by horizon."
    ),
) -> None:
    """Project ABS measured rents from new leases already signed.

    Not logged to the public track record: this component does not have
    demonstrated skill on the sample available. See auscpi/rents.py.
    """
    from auscpi.rents import REGIONS, ROLL_THROUGH_MONTHS, backtest, load_inputs, rent_path

    if region.lower() not in REGIONS:
        raise typer.BadParameter(f"unknown region {region!r}; have {sorted(REGIONS)}")
    region_code = REGIONS[region.lower()]

    cutoff = None
    if as_at:
        cutoff = datetime.fromisoformat(as_at)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)

    console.print("[dim]re-parsing bond snapshots from data/raw, this takes a minute…[/dim]")
    try:
        measured, new_lease = load_inputs(as_at=cutoff, region=region_code)
    except FileNotFoundError as exc:
        console.print(
            f"[red]no data[/red] {exc}\n"
            "Capital-city rents come from abs_cpi_regional; run "
            "`auscpi collect abs_cpi_regional` first."
        )
        raise typer.Exit(code=1) from exc
    # h=0 is the first month of measured rents the ABS has NOT published, matching
    # the nowcast-as-h=0 convention in forecast.py.
    from auscpi.rents import add_months

    origin = add_months(str(measured.dropna().index[-1]), 1)
    try:
        points, calibration = rent_path(
            measured,
            new_lease,
            origin=origin,
            horizons=list(range(horizons + 1)),
            roll_through_months=roll_through or ROLL_THROUGH_MONTHS,
        )
    except ValueError as exc:
        console.print(f"[red]cannot project rents[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table("h", "month", "measured y/y", "benchmark", "window observed")
    for p in points:
        table.add_row(
            str(p.horizon_months),
            p.reference_month,
            f"{p.point:+.2f}",
            f"{p.benchmark_point:+.2f}",
            f"{p.observed_share:.0%}",
        )
    console.print(table)
    console.print(
        f"{region.lower()} · roll-through {calibration.roll_through_months}m · "
        f"beta {calibration.beta:.3f} alpha {calibration.alpha:+.3f} on "
        f"{calibration.n} overlapping year-ended pairs · cutoff {calibration.information_cutoff}"
    )
    console.print(
        "[yellow]not logged[/yellow] — skill is not established on this sample. "
        "See the module docstring before quoting these numbers."
    )

    if show_backtest:
        results = backtest(
            measured, new_lease, roll_through_months=roll_through or ROLL_THROUGH_MONTHS
        )
        if not results:
            console.print("[yellow]not enough history to score anything yet[/yellow]")
            return
        scores = Table("h", "MAE", "benchmark MAE", "skill", "n")
        for r in results:
            scores.add_row(
                str(r.horizon_months),
                f"{r.mae:.3f}",
                f"{r.benchmark_mae:.3f}",
                f"{r.skill:+.3f}",
                str(r.n),
            )
        console.print(scores)
        console.print(
            "[dim]n counts forecasts, not independent observations: origins overlap and "
            "year-ended windows overlap elevenfold.[/dim]"
        )


@app.command()
def leverage(
    top: int = typer.Option(15, help="How many classes to show."),
    as_at: str = typer.Option(None, "--as-at", help="Use the vintage as at this instant."),
) -> None:
    """Rank expenditure classes by how much headline movement they can produce.

    Where to spend modelling effort, measured rather than guessed. Leverage is
    weight x monthly standard deviation, in headline percentage points.
    """
    from auscpi.aggregate import class_leverage, load_weights
    from auscpi.build import load_panel

    cutoff = None
    if as_at:
        cutoff = datetime.fromisoformat(as_at)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)

    try:
        panel = load_panel("abs_cpi_monthly", as_at=cutoff)
        weights = load_weights()
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]no data[/red] {exc}")
        raise typer.Exit(code=1) from exc

    ranked = class_leverage(panel, weights)
    if not ranked:
        console.print("[yellow]no class has enough history yet[/yellow]")
        raise typer.Exit()

    table = Table("#", "class", "weight %", "monthly sd", "leverage pp", "months")
    for i, c in enumerate(ranked[:top], start=1):
        table.add_row(
            str(i),
            f"{c.label[:38]} ({c.index_id})",
            f"{c.weight:.3f}",
            f"{c.monthly_sd:.2f}",
            f"{c.leverage_pp:.3f}",
            str(c.months),
        )
    console.print(table)

    median = sorted(c.leverage_pp for c in ranked)[len(ranked) // 2]
    console.print(f"median leverage across {len(ranked)} classes: {median:.3f}pp")
    console.print(
        "[dim]Leverage is how much a class MOVES, not how much of that is "
        "anticipable. Read it as where the movement lives, then ask separately "
        "whether any of it is forecastable.[/dim]"
    )


@app.command()
def components(
    horizons: int = typer.Option(12, help="Longest horizon. The path runs h=0..N."),
    as_at: str = typer.Option(None, "--as-at", help="Use the vintage as at this instant."),
) -> None:
    """Show what swapping component models into the headline path would do.

    Not wired into `auscpi forecast` or the public log. On current evidence the only
    component that exists — rents — moves the headline by less than the 0.1pp step
    the ABS rounds to. See auscpi/aggregate.py.
    """
    from auscpi.aggregate import (
        ComponentSwap,
        administered_swaps,
        component_baseline,
        load_weights,
        swap_components,
    )
    from auscpi.build import load_panel
    from auscpi.forecast import forecast_path
    from auscpi.rents import RENTS_INDEX_ID, load_inputs, rent_path

    cutoff = None
    if as_at:
        cutoff = datetime.fromisoformat(as_at)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)

    span = list(range(horizons + 1))
    try:
        path = forecast_path("headline_yoy", horizons=span, as_at=cutoff)
        weights = load_weights()
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]cannot build the headline path[/red] {exc}")
        raise typer.Exit(code=1) from exc

    headline = {r.reference_month: r.point for r in path.records}
    origin = path.records[0].reference_month

    console.print("[dim]re-parsing bond snapshots from data/raw, this takes a minute…[/dim]")
    measured, new_lease = load_inputs(as_at=cutoff)
    points, _ = rent_path(measured, new_lease, origin=origin, horizons=span)

    panel = load_panel("abs_cpi_monthly", as_at=cutoff)
    swaps = [
        ComponentSwap(
            index_id=RENTS_INDEX_ID,
            label="Rents",
            baseline=component_baseline(panel, RENTS_INDEX_ID, list(headline)),
            override={p.reference_month: p.point for p in points},
        )
    ]

    # Fuel: the strongest component in the repo, and the only one that measures its
    # class rather than modelling it. Skipped rather than fatal when the archive has
    # not been captured, so `auscpi components` still works on a fresh clone.
    try:
        from auscpi.fuel import FUEL_INDEX_ID
        from auscpi.fuel import component_path as fuel_path
        from auscpi.fuel import load_inputs as fuel_inputs

        fuel_measured, fuel_prices = fuel_inputs(as_at=cutoff)
        fuel_points, fuel_cal = fuel_path(
            fuel_measured, fuel_prices, origin=origin, horizons=span
        )
        swaps.append(
            ComponentSwap(
                index_id=FUEL_INDEX_ID,
                label="Automotive fuel",
                baseline=component_baseline(panel, FUEL_INDEX_ID, list(headline)),
                override={p.reference_month: p.point for p in fuel_points},
            )
        )
        console.print(
            f"[dim]fuel calibration: beta {fuel_cal.beta:.3f} alpha {fuel_cal.alpha:+.3f} "
            f"corr {fuel_cal.correlation:.3f} on {fuel_cal.n} months; "
            f"{sum(1 for p in fuel_points if p.measured)} of {len(fuel_points)} horizons "
            f"measured rather than carried[/dim]"
        )
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[yellow]fuel component unavailable[/yellow] {exc}")
    # The administered calendar, with the leakage guard on the forecast's own cutoff
    # rather than today: an event announced later must not reach a backtest.
    calendar_cutoff = (cutoff.date() if cutoff else datetime.now(UTC).date())
    swaps += [
        s
        for s in administered_swaps(panel, list(headline), information_cutoff=calendar_cutoff)
        if s.index_id != RENTS_INDEX_ID  # rents already has a component model above
    ]
    adjusted, contributions = swap_components(headline, swaps, weights)

    by_month: dict[str, list] = {}
    for c in contributions:
        by_month.setdefault(c.reference_month, []).append(c)

    table = Table("h", "month", "headline", "adjusted", "total effect pp", "movers")
    for record in path.records:
        month = record.reference_month
        here = by_month.get(month, [])
        material = [c for c in here if abs(c.effect_pp) >= 0.005]
        table.add_row(
            str(record.horizon_months),
            month,
            f"{record.point:+.2f}",
            f"{adjusted[month]:+.2f}",
            f"{sum(c.effect_pp for c in here):+.3f}",
            ", ".join(f"{c.label} {c.effect_pp:+.3f}" for c in material) or "-",
        )
    console.print(table)

    for index_id in sorted({c.index_id for c in contributions}):
        cs = [c for c in contributions if c.index_id == index_id]
        largest = max(abs(c.effect_pp) for c in cs)
        console.print(
            f"  {cs[0].label}: weight {cs[0].weight:.3f}% · largest effect {largest:.3f}pp"
        )
    overall = max((abs(c.effect_pp) for c in contributions), default=0.0)
    if overall < 0.1:
        console.print(
            "[yellow]every component is below the 0.1pp step the ABS rounds to[/yellow] — "
            "nothing here can move a published headline figure."
        )


@app.command()
def forecast(
    target: str = typer.Option(
        None, help="One target, or omitted for every target this vintage supports."
    ),
    model: str = typer.Option(None, help="Path rule. Omitted, the target's default is used."),
    benchmark: str = typer.Option(None, help="Benchmark rule. Must differ from the model."),
    horizons: int = typer.Option(12, help="Longest horizon. The path runs h=0..N."),
    as_at: str = typer.Option(
        None, "--as-at", help="Backtest against the vintage at this instant."
    ),
    log: bool = typer.Option(False, "--log", help="Append the path to forecasts/log.csv."),
) -> None:
    """Produce a forecast path. Naive v0 — see auscpi/forecast.py on what is weak.

    Without --log this only prints, so it is safe to inspect before committing to
    the public track record.
    """
    from auscpi.forecast import forecast_all, forecast_path

    cutoff = None
    if as_at:
        cutoff = datetime.fromisoformat(as_at)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)

    span = range(horizons + 1)
    try:
        paths = (
            [forecast_path(target, model=model, benchmark=benchmark, horizons=span, as_at=cutoff)]
            if target
            else forecast_all(horizons=span, as_at=cutoff)
        )
    except FileNotFoundError as exc:
        console.print(f"[red]no data[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if not paths:
        console.print("[yellow]no target could be forecast from this vintage[/yellow]")
        raise typer.Exit(code=1)

    for path in paths:
        table = Table(
            "h", "month", "point", f"benchmark ({path.benchmark})", title=f"{path.target}"
        )
        for r in path.records:
            table.add_row(
                str(r.horizon_months),
                r.reference_month,
                f"{r.point:+.2f}",
                "-" if r.benchmark_point is None else f"{r.benchmark_point:+.2f}",
            )
        console.print(table)
        version = path.records[0].model_version if path.records else "?"
        console.print(
            f"[dim]model {path.model} {version} · origin {path.origin} · "
            f"information cutoff {path.information_cutoff}[/dim]"
        )

    if not log:
        console.print("\n[yellow]not logged[/yellow] — re-run with --log to append it.")
        raise typer.Exit()

    n = 0
    for path in paths:
        for record in path.records:
            track_record.log_forecast(record)
            n += 1
    console.print(f"\n[green]logged[/green] {n} rows to forecasts/log.csv.")
    console.print("[dim]Commit and push it — the push time is the proof.[/dim]")


@app.command("fill-actual")
def fill_actual(
    as_at: str = typer.Option(None, "--as-at", help="Use the vintage at this instant."),
) -> None:
    """Fill `actual` for reference months the ABS has now published.

    The only edit permitted to forecasts/log.csv. Rows with an actual already set
    are never rewritten, so this is safe to run after every release.
    """
    from auscpi.build import load_panel
    from auscpi.parsers.abs_cpi import TARGETS, target_series

    cutoff = None
    if as_at:
        cutoff = datetime.fromisoformat(as_at)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)

    try:
        panel = load_panel("abs_cpi_monthly", as_at=cutoff)
    except FileNotFoundError as exc:
        console.print(f"[red]no data[/red] {exc}")
        raise typer.Exit(code=1) from exc

    actuals: dict[tuple[str, str], float] = {}
    for name in TARGETS:
        try:
            series = target_series(panel, name).dropna()
        except ValueError:
            continue
        for period, value in series.items():
            actuals[(name, str(period))] = float(value)

    result = track_record.fill_actuals(actuals)
    if not result.rows:
        console.print("[yellow]no forecast log yet[/yellow] — run `auscpi forecast --log` first.")
        raise typer.Exit()

    console.print(
        f"[green]filled[/green] {result.filled}   "
        f"[dim]already set {result.already_set} · not yet released {result.unavailable} · "
        f"rows {result.rows}[/dim]"
    )
    if result.filled:
        console.print("[dim]Commit it, then `auscpi score`.[/dim]")


@app.command()
def health(
    strict: bool = typer.Option(
        False, "--strict", help="Exit non-zero if any source is overdue. For CI and cron."
    ),
) -> None:
    """Is every source being fetched within its cadence? A silent collector is the
    main failure mode, so this compares each age against its cadence rather than
    printing ages and leaving the arithmetic to the reader.

    `--strict` makes it usable as a monitoring step: a scheduled job that runs this
    fails loudly when collection has stopped, which is the only way an unattended
    pipeline gets noticed before the gap is months wide.
    """
    from auscpi.collectors.base import is_backfill, overdue_after

    table = Table("source", "cadence", "last scheduled (UTC)", "age", "status", "last error")
    now = datetime.now(UTC)
    overdue: list[str] = []
    never: list[str] = []

    for slug, cls in sorted(registry.items()):
        entries = read_manifest(slug)
        errs = [e for e in entries if e["status"] == "error"]
        # Backfills are excluded on purpose: they stamp fetched_at = now and would
        # otherwise report a stalled collector as freshly collected. See is_backfill.
        oks = [e for e in entries if e["status"] == "ok" and not is_backfill(e)]
        backfilled = any(e["status"] == "ok" and is_backfill(e) for e in entries)
        tolerance = overdue_after(cls.cadence)

        if getattr(cls, "ruled_out", False):
            status, age_s, stamp = "[dim]ruled out[/dim]", "-", "never"
        elif not oks:
            age_s, stamp = "-", "never"
            if backfilled:
                # Data exists, but nothing has ever collected it on a schedule.
                status = "[red]backfill only[/red]"
                never.append(f"{slug} (backfill only)")
            else:
                status = "[red]never collected[/red]"
                never.append(slug)
        else:
            last = datetime.fromisoformat(oks[-1]["fetched_at"])
            age = now - last
            age_s = f"{age.days}d {age.seconds // 3600}h"
            stamp = last.strftime("%Y-%m-%d %H:%M")
            if age > tolerance:
                status = f"[red]OVERDUE (>{tolerance.days}d)[/red]"
                overdue.append(f"{slug} ({age.days}d, {cls.cadence})")
            else:
                status = "[green]ok[/green]"

        table.add_row(slug, cls.cadence, stamp, age_s, status, (errs[-1]["note"] if errs else ""))

    console.print(table)

    if never:
        console.print(f"[red]no scheduled collection:[/red] {', '.join(never)}")
        console.print(
            "[dim]'backfill only' means the data is there but nothing has ever "
            "collected it on a schedule — history captured by hand, pipeline not "
            "running.[/dim]"
        )
    if overdue:
        console.print(f"[red]overdue:[/red] {', '.join(overdue)}")
    if not overdue and not never:
        console.print("[green]every scheduled source is within its cadence.[/green]")
    if strict and (overdue or never):
        raise typer.Exit(code=1)


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
