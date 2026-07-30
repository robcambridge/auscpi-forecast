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


def _select_snapshot(source: str, as_at: datetime | None) -> dict[str, Any]:
    """The newest ok snapshot for `source`, respecting `as_at` if given."""
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
    return max(entries, key=lambda m: m["fetched_at"])


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
    return results
