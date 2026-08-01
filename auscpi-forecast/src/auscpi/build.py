"""Rebuild data/curated from data/raw.

Everything here is derived and disposable. Delete data/curated and run this again
and you must get the same result — if you ever cannot, something has leaked state
and the provenance guarantee is broken.

LOOK-AHEAD. `as_at` is the whole reason this is a separate step. Passing it routes
snapshot selection through storage.snapshots_as_at(), which returns only what had
actually been fetched by that moment, so a backtest sees the information set that
existed rather than today's revised view of it. Reading the newest file directly
inside evaluation code is the mistake this exists to prevent (CLAUDE.md rule 3).

Vintages matter for the CPI specifically: the ABS revises seasonally adjusted
series, and the quarterly index was re-referenced in December 2025. Each snapshot
is one vintage; the build always reads exactly one, never a blend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from auscpi.config import settings
from auscpi.parsers.abs_cpi import parse_sdmx_json, targets_frame
from auscpi.periods import period_end
from auscpi.storage import load_snapshot, read_manifest, snapshots_as_at

#: Raw source slug -> curated basename.
ABS_SOURCES = {
    "abs_cpi_monthly": "abs_cpi_monthly",
    "abs_cpi_quarterly": "abs_cpi_quarterly",
}


@dataclass
class BuildResult:
    source: str
    rows: int
    periods: int
    latest_period: str
    outputs: list[str]
    vintage: str  # fetched_at of the snapshot used
    #: Free text for anything a caller should see without reading the outputs —
    #: the bond build reports what its cleaning rules discarded.
    note: str = ""


def _select_snapshots(source: str, as_at: datetime | None) -> list[dict[str, Any]]:
    """Every ok snapshot for `source`, respecting `as_at` if given.

    Sources whose history accumulates one snapshot per reference month — the bond
    lodgements — need all of them, not the newest. Routing both cases through the
    same `as_at` filter keeps rule 3 enforced identically for each.
    """
    entries = (
        snapshots_as_at(source, as_at)
        if as_at is not None
        else [m for m in read_manifest(source) if m["status"] == "ok"]
    )
    if not entries:
        when = f" as at {as_at.isoformat()}" if as_at else ""
        raise FileNotFoundError(
            f"no successful {source} snapshot{when}. Run `auscpi collect {source}` first."
        )
    return entries


def _select_snapshot(source: str, as_at: datetime | None) -> dict[str, Any]:
    """The newest ok snapshot for `source`, respecting `as_at` if given."""
    return max(_select_snapshots(source, as_at), key=lambda m: m["fetched_at"])


def load_panel(source: str, *, as_at: datetime | None = None) -> pd.DataFrame:
    """Parse a raw snapshot straight into a panel, without touching data/curated.

    Modelling code reads through here rather than off the curated parquet, for two
    reasons: `as_at` keeps rule 3 enforceable at the point of use, and a forecast
    can never accidentally be built on a curated file left over from an older
    vintage. data/curated is for humans and inspection.
    """
    entry = _select_snapshot(source, as_at)
    panel = parse_sdmx_json(load_snapshot(entry["payload_path"]))
    if panel.empty:
        raise ValueError(f"{source} snapshot parsed to zero rows: {entry['payload_path']}")
    return panel


def _write(frame: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False)
    return str(path.relative_to(settings.data_dir.parent))


def build_abs_cpi(source: str, *, as_at: datetime | None = None) -> BuildResult:
    """Parse one ABS CPI snapshot into a panel plus a targets table."""
    manifest_entry = _select_snapshot(source, as_at)
    doc = load_snapshot(manifest_entry["payload_path"])

    panel = parse_sdmx_json(doc)
    if panel.empty:
        raise ValueError(f"{source} snapshot parsed to zero rows: {manifest_entry['payload_path']}")

    basename = ABS_SOURCES.get(source, source)
    outputs = [_write(panel, settings.curated_dir / f"{basename}.parquet")]

    targets = targets_frame(panel)
    if not targets.empty:
        outputs.append(_write(targets, settings.curated_dir / f"{basename}_targets.csv"))

    latest = panel.loc[panel["period_end"].idxmax(), "period"]
    return BuildResult(
        source=source,
        rows=len(panel),
        periods=panel["period"].nunique(),
        latest_period=str(latest),
        outputs=outputs,
        vintage=manifest_entry["fetched_at"],
    )


def build_abs_cpi_weights(*, as_at: datetime | None = None) -> BuildResult:
    """Parse the weights snapshot into a levelled panel plus the class weights.

    The expenditure-class CSV is the one downstream code should read: it is the
    level that sums to 100, and `weights_at` refuses to emit it otherwise.
    """
    from auscpi.parsers.abs_cpi_weights import weights_at, weights_panel

    source = "abs_cpi_weights"
    manifest_entry = _select_snapshot(source, as_at)
    payload = load_snapshot(manifest_entry["payload_path"])

    panel = weights_panel(payload)
    classes = weights_at(payload)

    outputs = [_write(panel, settings.curated_dir / f"{source}.parquet")]
    outputs.append(
        _write(
            classes.rename("weight").rename_axis("index_id").reset_index(),
            settings.curated_dir / f"{source}_expenditure_classes.csv",
        )
    )

    latest = panel.loc[panel["period_end"].idxmax(), "period"]
    return BuildResult(
        source=source,
        rows=len(panel),
        periods=panel["period"].nunique(),
        latest_period=str(latest),
        outputs=outputs,
        vintage=manifest_entry["fetched_at"],
    )


def load_bond_records(*, as_at: datetime | None = None) -> tuple[pd.DataFrame, Any, str]:
    """Cleaned bond lodgements from raw, without touching data/curated.

    The bond counterpart of `load_panel`, and it exists for the same reason:
    modelling code must be able to reconstruct a past information set without
    depending on a curated file left over from another vintage, and without a read
    quietly writing.

    Unlike the ABS sources, one snapshot here is one MONTH rather than one vintage
    of everything, so this reads every snapshot and stacks them. Two consequences
    the ABS path never has to think about:

      - Collecting the same month twice must not double-count it. The published
        file for a month can be reissued, and the monthly workflow will happily
        capture it again. Months are therefore keyed on the date found in the DATA,
        and the newest snapshot wins — filename and note are navigation metadata
        and neither is reliable enough to key on.
      - Parsing is per-file, so a single corrupt or re-shaped workbook fails the
        whole build. It is allowed to: a rent index silently missing March is worse
        than one that refuses to build, and the exception names the file.

    Returns the records, the tally of what was discarded, and the newest vintage.
    """
    from auscpi.parsers.nsw_rental_bonds import (
        Rejections,
        clean_records,
        file_period,
        parse_workbook,
    )

    source = "nsw_rental_bonds"
    # Newest snapshot first, so the first sighting of a month is the one to keep.
    newest_first = sorted(
        _select_snapshots(source, as_at), key=lambda m: m["fetched_at"], reverse=True
    )
    frames: list[pd.DataFrame] = []
    rejected = Rejections()
    seen: set[str] = set()

    for entry in newest_first:
        try:
            raw = parse_workbook(load_snapshot(entry["payload_path"]))
            period = file_period(raw)
        except Exception as exc:  # noqa: BLE001 - the path is what makes it actionable
            raise ValueError(f"failed to parse {entry['payload_path']}: {exc}") from exc
        if period in seen:
            continue
        seen.add(period)
        records, counts = clean_records(raw, period)
        frames.append(records)
        rejected = rejected + counts

    panel = pd.concat(frames, ignore_index=True).sort_values(["period", "lodgement_date"])
    if panel.empty:
        raise ValueError(f"{source} snapshots parsed to zero usable rows")
    return panel, rejected, newest_first[0]["fetched_at"]


def build_nsw_rental_bonds(*, as_at: datetime | None = None) -> BuildResult:
    """Write the bond lodgement history plus its mix-controlled index to curated."""
    from auscpi.parsers.nsw_rental_bonds import index_frame

    source = "nsw_rental_bonds"
    panel, rejected, vintage = load_bond_records(as_at=as_at)
    seen = set(panel["period"].unique())

    index = index_frame(panel)

    outputs = [_write(panel, settings.curated_dir / f"{source}.parquet")]
    outputs.append(_write(index, settings.curated_dir / f"{source}_index.csv"))

    return BuildResult(
        source=source,
        rows=len(panel),
        periods=panel["period"].nunique(),
        latest_period=str(max(seen, key=period_end)),
        outputs=outputs,
        vintage=vintage,
        note=f"dropped {rejected.total:,} rows: {rejected.as_dict()}",
    )


def build_all(*, as_at: datetime | None = None, strict: bool = False) -> list[BuildResult]:
    """Build every source that has a snapshot.

    Sources not collected yet are skipped rather than fatal, so `auscpi build`
    stays useful while the pipeline is still being filled in. `strict=True` turns
    a missing source back into an error for CI.
    """
    results: list[BuildResult] = []
    for source in ABS_SOURCES:
        try:
            results.append(build_abs_cpi(source, as_at=as_at))
        except FileNotFoundError:
            if strict:
                raise
    try:
        results.append(build_abs_cpi_weights(as_at=as_at))
    except FileNotFoundError:
        if strict:
            raise
    try:
        results.append(build_nsw_rental_bonds(as_at=as_at))
    except FileNotFoundError:
        if strict:
            raise
    return results
