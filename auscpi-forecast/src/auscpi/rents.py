"""The rent roll-through: NSW new-lease rents to ABS measured rents.

THE MECHANISM IS CLOSE TO ARITHMETIC, WHICH IS WHY IT IS WORTH BUILDING. A bond is
lodged when a tenancy starts, so the bond index prices the FLOW of new leases. The
ABS prices the STOCK of occupied dwellings, and a sitting tenant's rent does not
move until their lease is reset. If a fraction 1/K of the stock re-leases each
month, the stock index is a K-month moving average of the flow:

    measured(t) ~ mean( new_lease(t-K+1) .. new_lease(t) )

Most of that average is therefore already observed at any horizon shorter than K.
Forecasting measured rents six months out, half the window is history. That is the
same structure as the base-effect argument in forecast.py, and it is why this is
the one component whose accuracy decays slowly with horizon instead of fast.

WHAT IS STRUCTURAL AND WHAT IS FITTED.

  K = ROLL_THROUGH_MONTHS is structural, not tuned. A standard NSW residential
  lease is six or twelve months and many roll to periodic after it, so the
  effective re-lease interval is at least twelve. K=12 is also the only window that
  does not lose to a random walk on the fair comparison below. Two other signals
  argued for 15-17 — the year-ended correlation peaks there, and that is where the
  smoothed series matches measured-rent volatility — but both are computed on two
  strongly trending series over four years, which inflates them, and the backtest
  contradicts them. Structure wins over a correlation peak on 40 observations.

  alpha and beta ARE fitted, on year-ended pairs, and beta is doing most of the
  work. It comes out near 0.5: smoothed new-lease rents ran 8.1%/yr against
  measured 5.4%/yr, over a year-ended range of 4.5-14.0% against 3.5-7.7%. New-lease
  rents move roughly twice as much as measured ones, so the pass-through is a half,
  not a one, and at least three things are inside that number:

    - NSW against national. The bond data is NSW; expenditure class 30014 is
      Australia. Sydney ran hotter than the national average over this sample.
    - Commonwealth Rent Assistance. The ABS measures rent net of CRA, so the
      increases legislated over this period damp measured rent inflation relative
      to gross market rents. Worth confirming against the ABS release notes and
      the effective dates before this model is relied on, because it is a level
      shift on specific months rather than a constant wedge, and beta is currently
      absorbing it as if it were constant.
    - Partial pass-through within the stock. Even a lease that resets often resets
      below market, because re-letting to a sitting tenant avoids a vacancy.

  Splitting those apart is what would turn beta from a fudge factor into a
  forecast. Sydney-versus-national is the tractable one and needs the ABS postcode
  correspondence; see parsers/nsw_rental_bonds.py.

WHAT THE EVIDENCE ACTUALLY SHOWS, WHICH IS LESS THAN THE MECHANISM PROMISES.
Measured in a pseudo-real-time backtest, calibrating only on data available at each
origin:

  - On every point the sample can evaluate (162 forecasts from 19 origins), K=12
    beats carrying measured rents flat by 21% on mean absolute error, and the skill
    rises with horizon exactly as the mechanism predicts: -1.10 at h=1, +0.24 at
    h=6, +0.56 at h=12. It loses at short horizons, which is expected and correct —
    nothing beats a random walk on a slow series one month out.
  - On the 45 points that every candidate K can evaluate, ALL of that disappears.
    K=12 scores +0.009, a dead heat, and every other K loses.

The two samples are different periods, not different models. The common sample sits
in the recent stretch where rent inflation had stabilised and carrying flat is very
hard to beat; the fuller sample includes the sharp deceleration where it is not.
So the mechanism is sound and the skill is unproven, and with at most 19 origins and
heavily overlapping year-ended windows this sample cannot settle it. NOTHING HERE IS
LOGGED TO THE PUBLIC TRACK RECORD YET, and no claim of skill should be made from it.

Overlapping year-ended windows also make the calibration residuals strongly
autocorrelated, so the fit's standard errors would be meaningless and none are
reported. beta is a point estimate on ~31 effective observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from auscpi.parsers.abs_cpi import (
    MEASURE_INDEX_NUMBER,
    TSEST_ORIGINAL,
    series_for,
)

#: Months a lease takes to roll through the stock. Structural — see the module
#: docstring on why this is not tuned to the backtest.
ROLL_THROUGH_MONTHS = 12

#: ABS expenditure class for Rents, 6.613% of the basket at the 2024-Q4 reweight.
RENTS_INDEX_ID = "30014"

#: Year-ended pairs needed before a calibration is allowed to be reported. Twelve
#: is already thin; below it the fit is not worth the arithmetic.
MIN_CALIBRATION_POINTS = 12


def add_months(period: str, n: int) -> str:
    year, month = (int(x) for x in period.split("-"))
    index = (year * 12 + month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@dataclass(frozen=True)
class Calibration:
    """The fitted map from smoothed new-lease growth to measured rent growth.

    `n` is the number of year-ended pairs, NOT independent observations — they
    overlap by eleven months. Treat it as a sample-size warning, not a sample size.
    """

    alpha: float
    beta: float
    n: int
    roll_through_months: int
    information_cutoff: str

    def apply(self, new_lease_yoy: float) -> float:
        return self.alpha + self.beta * new_lease_yoy


def measured_rents(panel: pd.DataFrame) -> pd.Series:
    """The ABS measured rent index, Original, national.

    Original rather than seasonally adjusted: everything below is compared
    year-ended, which removes the annual pattern without depending on the ABS's
    adjustment of a single expenditure class.
    """
    return series_for(
        panel, RENTS_INDEX_ID, MEASURE_INDEX_NUMBER, TSEST_ORIGINAL, name="measured_rents"
    )


def smoothed_new_lease(
    new_lease: pd.Series, *, roll_through_months: int = ROLL_THROUGH_MONTHS
) -> pd.Series:
    """The stock implied by the flow: a K-month moving average of new-lease rents."""
    return new_lease.dropna().rolling(roll_through_months).mean().dropna()


def _year_ended(series: pd.Series) -> pd.Series:
    return (series / series.shift(12) - 1.0) * 100.0


def calibrate(
    measured: pd.Series,
    new_lease: pd.Series,
    *,
    roll_through_months: int = ROLL_THROUGH_MONTHS,
) -> Calibration:
    """Fit measured rent growth on smoothed new-lease growth, year-ended.

    Year-ended rather than monthly because the monthly new-lease series is far too
    noisy to regress on: month-on-month correlations between the two series at
    every lag from 0 to 18 sit between -0.21 and +0.33 with no peak, which is what
    fitting noise looks like. The moving average and the annual difference are both
    there to average that away.

    Only data passed in is used, so threading an `as_at`-loaded panel through here
    keeps the calibration honest for a backtest (CLAUDE.md rule 3).
    """
    smooth = smoothed_new_lease(new_lease, roll_through_months=roll_through_months)
    pairs = pd.DataFrame(
        {"measured": _year_ended(measured.dropna()), "new_lease": _year_ended(smooth)}
    ).dropna()

    if len(pairs) < MIN_CALIBRATION_POINTS:
        raise ValueError(
            f"need {MIN_CALIBRATION_POINTS} year-ended pairs to calibrate the roll-through, "
            f"have {len(pairs)}. The bond index must lead measured rents by "
            f"{roll_through_months} months before the first pair exists"
        )

    # A new-lease path growing at a constant rate has a CONSTANT year-ended rate, so
    # the regressor carries no variation and beta is not identified. numpy answers
    # that with an SVD convergence error several frames down; say what is actually
    # wrong instead, because this is a real case — a flat or perfectly smooth bond
    # index would land here.
    spread = float(pairs["new_lease"].std())
    if not np.isfinite(spread) or spread < 1e-9:
        raise ValueError(
            "smoothed new-lease growth has no variation over the calibration window, "
            "so the pass-through is not identified. Nothing can be fitted from a "
            "constant regressor"
        )

    beta, alpha = np.polyfit(pairs["new_lease"], pairs["measured"], 1)
    cutoff = max(str(measured.dropna().index[-1]), str(smooth.index[-1]))
    return Calibration(
        alpha=float(alpha),
        beta=float(beta),
        n=len(pairs),
        roll_through_months=roll_through_months,
        information_cutoff=cutoff,
    )


def project_new_lease(new_lease: pd.Series, through: str) -> pd.Series:
    """Extend the new-lease index to `through` by carrying the last level flat.

    Flat is the conservative choice and it is deliberate. The roll-through's claim
    is that ALREADY OBSERVED new leases determine measured rents some months out;
    layering a forecast of new-lease rents on top would mix that claim with a much
    weaker one and make the result impossible to attribute. At horizons below K the
    carried months are a minority of the window anyway.
    """
    observed = new_lease.dropna()
    out = {str(k): float(v) for k, v in observed.items()}
    last = str(observed.index[-1])
    cursor = last
    while cursor < through:
        cursor = add_months(cursor, 1)
        out[cursor] = out[last]
    return pd.Series(out).sort_index()


@dataclass(frozen=True)
class RentPoint:
    reference_month: str
    horizon_months: int
    #: Year-ended measured rent inflation, per cent.
    point: float
    #: Carry the last observed measured year-ended rate flat.
    benchmark_point: float
    #: Share of the K-month window already observed. 1.0 means the forecast is
    #: arithmetic on published data; 0.0 means every month of it is carried.
    observed_share: float


def rent_path(
    measured: pd.Series,
    new_lease: pd.Series,
    *,
    origin: str,
    horizons: range | list[int],
    roll_through_months: int = ROLL_THROUGH_MONTHS,
    calibration: Calibration | None = None,
) -> tuple[list[RentPoint], Calibration]:
    """Project measured rent inflation from new leases already signed.

    `observed_share` on each point is the honest headline: it says how much of the
    answer is arithmetic on published data rather than the flat carry. It falls
    linearly to zero at h=K, which is where this stops being a roll-through and
    becomes an assumption.

    Pass `calibration` to hold the fitted pass-through fixed while the flow varies —
    a backtest sweeping origins should fit once per origin rather than once per
    horizon, and isolating the mechanism from the fit is the only way to see which
    of the two moved an answer.
    """
    if calibration is None:
        calibration = calibrate(measured, new_lease, roll_through_months=roll_through_months)
    elif calibration.roll_through_months != roll_through_months:
        raise ValueError(
            f"calibration was fitted with K={calibration.roll_through_months} but the path "
            f"asks for K={roll_through_months}; the pass-through is specific to the window"
        )

    months = [add_months(origin, h) for h in horizons]
    horizon_end = max(months)
    projected = project_new_lease(new_lease, horizon_end)
    smooth = smoothed_new_lease(projected, roll_through_months=roll_through_months)
    smooth_yoy = _year_ended(smooth)

    measured_obs = measured.dropna()
    last_measured = str(measured_obs.index[-1])
    benchmark = float(_year_ended(measured_obs).dropna().iloc[-1])
    last_new_lease = str(new_lease.dropna().index[-1])

    points: list[RentPoint] = []
    for horizon, month in zip(horizons, months, strict=True):
        if month not in smooth_yoy.index:
            continue
        # Months of the K-window that were signed on or before the last observation.
        carried = sum(
            1
            for j in range(roll_through_months)
            if add_months(month, -j) > last_new_lease
        )
        points.append(
            RentPoint(
                reference_month=month,
                horizon_months=horizon,
                point=round(calibration.apply(float(smooth_yoy.loc[month])), 3),
                benchmark_point=round(benchmark, 3),
                observed_share=round(1.0 - carried / roll_through_months, 3),
            )
        )

    if not points:
        raise ValueError(
            f"no horizon could be projected from measured rents through {last_measured}"
        )
    return points, calibration


@dataclass(frozen=True)
class BacktestResult:
    horizon_months: int
    n: int
    mae: float
    benchmark_mae: float
    skill: float  # 1 - mae/benchmark_mae; positive means better than carrying flat


def backtest(
    measured: pd.Series,
    new_lease: pd.Series,
    *,
    roll_through_months: int = ROLL_THROUGH_MONTHS,
    horizons: range | list[int] | None = None,
) -> list[BacktestResult]:
    """Pseudo-real-time skill against carrying measured rents flat, by horizon.

    The numbers quoted in the module docstring come from here, which is the point:
    a backtest is a claim, and a claim nobody can re-run is not evidence. Re-running
    it after every release is how the "skill not established" verdict above gets
    revisited.

    Each origin recalibrates on data available AT THAT ORIGIN and on nothing later,
    so the fitted pass-through never sees its own evaluation period. `n` counts
    forecasts, not independent observations — origins overlap and year-ended windows
    overlap elevenfold, so a difference in MAE here is worth much less than the same
    difference on independent data.

    Comparing across `roll_through_months` needs care and the honest answer changed
    when it was done properly: a larger K needs more lead-in, so each K is evaluable
    on a different and later set of origins. Score them on the intersection or the
    comparison measures the period rather than the model.
    """
    # h=0 is excluded by default: it is the month the ABS is about to publish, and
    # scoring a nowcast beside forecasts would flatter the aggregate.
    span = list(range(1, 13)) if horizons is None else list(horizons)
    periods = [str(p) for p in measured.dropna().index]
    rows: list[dict[str, float | int]] = []

    for origin in periods:
        measured_then = measured.dropna().loc[:origin]
        new_lease_then = new_lease.dropna().loc[:origin]
        if len(new_lease_then) < roll_through_months:
            continue
        try:
            fit = calibrate(
                measured_then, new_lease_then, roll_through_months=roll_through_months
            )
        except ValueError:
            continue  # not enough history yet at this origin

        try:
            points, _ = rent_path(
                measured_then,
                new_lease_then,
                origin=origin,
                horizons=span,
                roll_through_months=roll_through_months,
                calibration=fit,
            )
        except ValueError:
            continue

        for point in points:
            target = point.reference_month
            base = add_months(target, -12)
            if target not in measured.index or base not in measured.index:
                continue
            actual_level, base_level = measured.get(target), measured.get(base)
            if pd.isna(actual_level) or pd.isna(base_level):
                continue
            actual = (float(actual_level) / float(base_level) - 1.0) * 100.0
            rows.append(
                {
                    "h": point.horizon_months,
                    "model": abs(point.point - actual),
                    "bench": abs(point.benchmark_point - actual),
                }
            )

    if not rows:
        return []

    frame = pd.DataFrame(rows)
    out: list[BacktestResult] = []
    for horizon, group in frame.groupby("h"):
        mae = float(group["model"].mean())
        bench = float(group["bench"].mean())
        out.append(
            BacktestResult(
                horizon_months=int(horizon),
                n=len(group),
                mae=round(mae, 4),
                benchmark_mae=round(bench, 4),
                skill=round(1.0 - mae / bench, 3) if bench else float("nan"),
            )
        )
    return out


def load_inputs(*, as_at: datetime | None = None) -> tuple[pd.Series, pd.Series]:
    """Both series, routed through the as-at aware build so rule 3 holds.

    Slow on purpose: the bond side re-parses every monthly workbook rather than
    reading data/curated, because a curated file left over from another vintage is
    exactly the look-ahead this project exists to prevent. A backtest is a batch
    job and can afford it.
    """
    from auscpi.build import load_bond_records, load_panel
    from auscpi.parsers.nsw_rental_bonds import index_frame

    measured = measured_rents(load_panel("abs_cpi_monthly", as_at=as_at))
    records, _, _ = load_bond_records(as_at=as_at)
    new_lease = index_frame(records).set_index("period")["index"]
    return measured, new_lease
