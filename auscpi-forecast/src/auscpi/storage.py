"""The provenance layer.

Design rule, and the one that matters most in this project:

    data/raw/  is immutable. Every collector run writes a new timestamped,
               gzipped snapshot of exactly what the source returned, plus a
               sidecar manifest recording when and how it was fetched.

    data/curated/  is derived. It can be deleted and rebuilt from raw at any
                   time. Nothing of value lives here.

The reason is look-ahead bias. A nowcast is only credible if you can prove that
the inputs were available at the time you claim. Storing the raw response with a
fetch timestamp is what lets you reconstruct the information set as at any past
date. Overwriting a file in place destroys that, permanently and silently.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auscpi.config import settings


@dataclass(frozen=True)
class Snapshot:
    """Metadata for one raw fetch. Written alongside the payload."""

    source: str
    fetched_at: str  # ISO 8601, UTC, when the request returned
    url: str
    status: str  # "ok" | "error"
    payload_path: str
    sha256: str
    n_records: int | None = None
    note: str = ""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def snapshot_path(source: str, fetched_at: datetime, ext: str = "json.gz") -> Path:
    """data/raw/<source>/<YYYY>/<MM>/<source>_<YYYYMMDDTHHMMSSZ>.<ext>"""
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    return (
        settings.raw_dir
        / source
        / f"{fetched_at:%Y}"
        / f"{fetched_at:%m}"
        / f"{source}_{stamp}.{ext}"
    )


def write_snapshot(
    source: str,
    payload: Any,
    url: str,
    *,
    fetched_at: datetime | None = None,
    n_records: int | None = None,
    note: str = "",
    status: str = "ok",
) -> Snapshot:
    """Persist one raw fetch. Returns the manifest entry.

    `payload` may be a dict/list (serialised as JSON) or raw bytes/str.
    """
    fetched_at = fetched_at or _utc_now()

    if isinstance(payload, (dict, list)):
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        ext = "json.gz"
    elif isinstance(payload, str):
        blob = payload.encode()
        ext = "txt.gz"
    else:
        blob = payload
        ext = "bin.gz"

    path = snapshot_path(source, fetched_at, ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing snapshot: {path}")

    with gzip.open(path, "wb") as fh:
        fh.write(blob)

    snap = Snapshot(
        source=source,
        fetched_at=fetched_at.isoformat(),
        url=url,
        status=status,
        payload_path=str(path.relative_to(settings.data_dir.parent)),
        sha256=hashlib.sha256(blob).hexdigest(),
        n_records=n_records,
        note=note,
    )
    _append_manifest(source, snap)
    return snap


def _append_manifest(source: str, snap: Snapshot) -> None:
    """One JSONL manifest per source. Append-only; this is the audit trail."""
    manifest = settings.raw_dir / source / "_manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(snap), ensure_ascii=False) + "\n")


def read_manifest(source: str) -> list[dict[str, Any]]:
    manifest = settings.raw_dir / source / "_manifest.jsonl"
    if not manifest.exists():
        return []
    with manifest.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_snapshot(path: str | Path) -> Any:
    path = Path(path)
    if not path.is_absolute():
        path = settings.data_dir.parent / path
    with gzip.open(path, "rb") as fh:
        blob = fh.read()
    if path.name.endswith(".json.gz"):
        return json.loads(blob)
    return blob


def snapshots_as_at(source: str, as_at: datetime) -> list[dict[str, Any]]:
    """Every snapshot fetched at or before `as_at`.

    This is the function that makes a real-time backtest possible: it returns
    the information set that actually existed at a past moment, rather than
    today's revised view of it.
    """
    cutoff = as_at.isoformat()
    return [m for m in read_manifest(source) if m["fetched_at"] <= cutoff and m["status"] == "ok"]
