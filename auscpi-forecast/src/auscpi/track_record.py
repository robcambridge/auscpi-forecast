"""The public track record.

Forecasts, not nowcasts. Every row records the HORIZON as well as the target,
because a forecast that is right at h=1 and useless at h=6 is a different object
from one that is mediocre throughout, and a track record that doesn't
distinguish them tells you nothing.

Horizon convention:

    h = 0   the reference month is the current or just-ended month. The prices
            already happened; you are estimating, not predicting. Retained as
            the initialisation of the forecast path, not as the product.
    h = 1   next month
    h = 3   one quarter out — the horizon a rates desk actually positions on
    h = 12  a year out, where you will not beat a random walk and should say so

Why the git history matters: a backtest is a claim, and everyone knows
look-ahead bias hides in backtests. A forecast committed to a public repository
before the print is evidence. Log before every release, from the first one, even
when the model is embarrassing, and always log a benchmark beside it — a track
record without a benchmark is unreadable, because nobody knows whether 0.3pp of
error is good or dreadful.
"""

from __future__ import annotations

import csv
import subprocess
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime

from auscpi.config import settings


def months_between(origin: str, reference: str) -> int:
    """Horizon in months. Both arguments are "YYYY-MM"."""
    oy, om = (int(x) for x in origin.split("-"))
    ry, rm = (int(x) for x in reference.split("-"))
    return (ry - oy) * 12 + (rm - om)


@dataclass
class ForecastRecord:
    made_at: str  # ISO 8601 UTC, when the forecast was produced
    reference_month: str  # "2026-09", the CPI month being forecast
    horizon_months: int  # 0 = nowcast, 1 = next month, 3 = one quarter out
    target: str  # headline_mom | headline_yoy | trimmed_mean_yoy
    point: float
    information_cutoff: str = ""  # last date of data used; equals made_at live,
    # differs in a backtest — this is the field that
    # makes look-ahead detectable
    p10: float | None = None
    p25: float | None = None
    p75: float | None = None
    p90: float | None = None
    model: str = "unknown"
    model_version: str = ""
    git_sha: str = ""
    benchmark_name: str = ""
    benchmark_point: float | None = None
    actual: float | None = None  # filled in, separately, after the release
    note: str = ""


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


def log_forecast(record: ForecastRecord) -> None:
    """Append one forecast. Never rewrite an earlier row except to fill `actual`."""
    now = datetime.now(UTC)
    if not record.made_at:
        record.made_at = now.isoformat()
    if not record.information_cutoff:
        record.information_cutoff = record.made_at
    if not record.git_sha:
        record.git_sha = _git_sha()
    if record.horizon_months is None:
        origin = date.fromisoformat(record.made_at[:10]).strftime("%Y-%m")
        record.horizon_months = months_between(origin, record.reference_month)

    path = settings.forecast_log
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [f.name for f in fields(ForecastRecord)]
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        if new:
            writer.writeheader()
        writer.writerow(asdict(record))


def score(by_horizon: bool = True) -> list[dict[str, object]]:
    """Error vs benchmark for every settled forecast, grouped by horizon.

    Note when you read these: forecasts made in different months for overlapping
    reference periods have correlated errors. Twelve horizons over twelve months
    is not 144 independent observations, and treating it as such will make a
    coin flip look like skill.
    """
    path = settings.forecast_log
    if not path.exists():
        return []

    buckets: dict[tuple[str, str, int], list[tuple[float, float | None]]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not row.get("actual"):
                continue
            actual = float(row["actual"])
            err = abs(float(row["point"]) - actual)
            bench = row.get("benchmark_point")
            bench_err = abs(float(bench) - actual) if bench else None
            key = (
                row["model"],
                row["target"],
                int(row["horizon_months"]) if by_horizon else -1,
            )
            buckets.setdefault(key, []).append((err, bench_err))

    out: list[dict[str, object]] = []
    for (model, target, h), errs in sorted(buckets.items(), key=lambda kv: (kv[0][2], kv[0][0])):
        mae = sum(e for e, _ in errs) / len(errs)
        benched = [(e, b) for e, b in errs if b is not None]
        bench_mae = sum(b for _, b in benched) / len(benched) if benched else None
        out.append(
            {
                "model": model,
                "target": target,
                "horizon_months": h,
                "n": len(errs),
                "mae": round(mae, 4),
                "benchmark_mae": round(bench_mae, 4) if bench_mae is not None else None,
                "skill": round(1 - mae / bench_mae, 3) if bench_mae else None,
            }
        )
    return out
