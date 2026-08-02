"""The fuel component: NSW pump prices to ABS automotive fuel.

THIS IS MEASUREMENT, NOT FORECASTING, AND THE NUMBERS SAY SO. Regressing ABS
automotive fuel (class 40081) monthly growth on the FuelCheck NSW mean price over 41
overlapping months gives

    beta 1.008    alpha -0.061    correlation 0.973    residual sd 1.55pp

against a target whose own monthly standard deviation is 5.96pp. A slope of one and
an intercept of zero is not a fitted relationship, it is an identity with noise on
it: NSW pump prices move one-for-one into the national index. Contrast the rent
roll-through, where beta came out near a half and needed three separate explanations.

WHY THAT MATTERS MORE THAN THE CORRELATION. FuelCheck publishes daily and in real
time; the ABS publishes fuel about four weeks after the month ends. So for any month
that has finished but not printed, this component does not predict the answer — it
already knows it, up to 1.5pp of noise. Fuel is 3.347% of the basket and third of 87
classes by headline leverage, and March 2026 alone moved the headline by 1.10pp.

NATIONAL IS THE RIGHT TARGET, WHICH WAS NOT THE EXPECTED ANSWER. The predictor is
NSW, so Sydney should be the better-matched target, and for rents it was. For fuel it
is the reverse:

    Australia   corr 0.973   beta 1.008   residual sd 1.55pp
    Sydney      corr 0.938   beta 1.103   residual sd 2.63pp
    Perth       corr 0.952   beta 0.965   residual sd 2.00pp
    Melbourne   corr 0.899   beta 0.994   residual sd 3.11pp
    Brisbane    corr 0.888   beta 0.910   residual sd 3.03pp

Sydney runs a pronounced discount cycle whose timing is its own, and the FuelCheck
mean spans the whole state rather than the city, so it already averages metro against
regional. That makes it closer to an eight-city average than to Sydney. Convenient,
because national is what this project actually forecasts — but it is a measured
result, not a design choice, and it should be re-checked as the sample grows.

NO FORWARD PATH BEYOND THE LAST OBSERVATION, DELIBERATELY. Past the newest FuelCheck
month this carries the index flat, which is a random walk in levels and the standard
benchmark for petrol. Refined product futures plus AUD forwards plus known excise
would give a genuine forward path and that is Phase 4; layering a weak price forecast
on top of a strong price measurement would mix the two and make the result impossible
to attribute. `observed_share` on each point says which regime a horizon is in.

WHAT IS STILL WEAK:

  - Unweighted across stations, and the parser's docstring shows this is not
    cosmetic: improving station coverage makes the correlation slightly worse, which
    is the signature of the crude sample accidentally approximating the volume
    weighting the ABS actually uses.
  - U91 only. The ABS class covers petrol, diesel and LPG on expenditure shares, and
    diesel in particular follows a different cycle.
  - 41 overlapping monthly observations. The relationship is strong enough that this
    is less fragile than the rent calibration, but it is not a long sample.
  - The ABS quality-adjusts and this does not, though for a homogeneous product sold
    by the litre there is little to adjust.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from auscpi.parsers.abs_cpi import (
    MEASURE_INDEX_NUMBER,
    REGION_AUSTRALIA,
    TSEST_ORIGINAL,
    series_for,
)

#: ABS expenditure class for Automotive fuel, 3.347% of the basket.
FUEL_INDEX_ID = "40081"

#: Monthly pairs needed before a calibration is worth reporting.
MIN_CALIBRATION_POINTS = 12


def add_months(period: str, n: int) -> str:
    year, month = (int(x) for x in period.split("-"))
    index = (year * 12 + month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@dataclass(frozen=True)
class Calibration:
    """The fitted map from FuelCheck price growth to ABS class growth.

    Expect `beta` near 1 and `alpha` near 0. If a rebuild moves them far from that,
    something has changed in the construction rather than in the world — check the
    parser's step function and carry-in before believing it.
    """

    alpha: float
    beta: float
    n: int
    correlation: float
    residual_sd: float
    region: str
    information_cutoff: str

    def apply(self, fuelcheck_mom: float) -> float:
        return self.alpha + self.beta * fuelcheck_mom


def abs_fuel(panel: pd.DataFrame, *, region: str = REGION_AUSTRALIA) -> pd.Series:
    """The ABS automotive fuel index, Original."""
    return series_for(
        panel, FUEL_INDEX_ID, MEASURE_INDEX_NUMBER, TSEST_ORIGINAL, region=region, name="abs_fuel"
    )


def _mom(series: pd.Series) -> pd.Series:
    return (series / series.shift(1) - 1.0) * 100.0


def calibrate(
    measured: pd.Series, fuelcheck: pd.Series, *, region: str = REGION_AUSTRALIA
) -> Calibration:
    """Fit ABS class growth on FuelCheck price growth, month on month.

    Monthly rather than year-ended, unlike the rent roll-through: the signal here is
    strong enough that no smoothing is needed, and monthly keeps the pairs close to
    independent instead of overlapping elevenfold.

    Only data passed in is used, so an `as_at`-loaded panel keeps a backtest honest.
    """
    pairs = pd.DataFrame({"abs": _mom(measured.dropna()), "fc": _mom(fuelcheck.dropna())}).dropna()
    if len(pairs) < MIN_CALIBRATION_POINTS:
        raise ValueError(
            f"need {MIN_CALIBRATION_POINTS} monthly pairs to calibrate the fuel "
            f"component, have {len(pairs)}"
        )
    if float(pairs["fc"].std()) < 1e-9:
        raise ValueError("FuelCheck growth has no variation; the mapping is not identified")

    beta, alpha = np.polyfit(pairs["fc"], pairs["abs"], 1)
    fitted = alpha + beta * pairs["fc"]
    return Calibration(
        alpha=float(alpha),
        beta=float(beta),
        n=len(pairs),
        correlation=float(pairs["fc"].corr(pairs["abs"])),
        residual_sd=float((pairs["abs"] - fitted).std()),
        region=region,
        information_cutoff=max(str(measured.dropna().index[-1]), str(fuelcheck.dropna().index[-1])),
    )


@dataclass(frozen=True)
class FuelPoint:
    reference_month: str
    horizon_months: int
    #: Year-ended growth of the ABS class, per cent.
    point: float
    #: Carry the last observed year-ended rate flat.
    benchmark_point: float
    #: True when FuelCheck actually observed this month, so the value is measured
    #: rather than carried. False means the index is being held flat.
    measured: bool


def project_index(
    measured: pd.Series, fuelcheck: pd.Series, through: str, *, calibration: Calibration
) -> pd.Series:
    """Extend the ABS index using observed FuelCheck growth, then hold it flat.

    Months where FuelCheck has observed a price but the ABS has not published are the
    whole point — there the index is stepped by the calibrated pump-price movement.
    Past the last FuelCheck month there is no information, so the level is carried,
    which is a random walk and the honest benchmark for petrol.
    """
    levels = {str(k): float(v) for k, v in measured.dropna().items()}
    fc_mom = _mom(fuelcheck.dropna())

    cursor = str(measured.dropna().index[-1])
    while cursor < through:
        cursor = add_months(cursor, 1)
        previous = levels[add_months(cursor, -1)]
        if cursor in fc_mom.index and np.isfinite(fc_mom.loc[cursor]):
            levels[cursor] = previous * (1.0 + calibration.apply(float(fc_mom.loc[cursor])) / 100.0)
        else:
            levels[cursor] = previous
    return pd.Series(levels).sort_index()


def component_path(
    measured: pd.Series,
    fuelcheck: pd.Series,
    *,
    origin: str,
    horizons: range | list[int],
    calibration: Calibration | None = None,
) -> tuple[list[FuelPoint], Calibration]:
    """Year-ended path for the fuel class, for `aggregate.ComponentSwap`."""
    calibration = calibration or calibrate(measured, fuelcheck)
    months = [add_months(origin, h) for h in horizons]
    levels = project_index(measured, fuelcheck, max(months), calibration=calibration)

    observed_fc = set(_mom(fuelcheck.dropna()).dropna().index.astype(str))
    last_abs = str(measured.dropna().index[-1])
    year_ended = (measured.dropna() / measured.dropna().shift(12) - 1.0) * 100.0
    benchmark = float(year_ended.dropna().iloc[-1])

    points: list[FuelPoint] = []
    for horizon, month in zip(horizons, months, strict=True):
        base = add_months(month, -12)
        if month not in levels.index or base not in levels.index:
            continue
        points.append(
            FuelPoint(
                reference_month=month,
                horizon_months=horizon,
                point=round((levels[month] / levels[base] - 1.0) * 100.0, 3),
                benchmark_point=round(benchmark, 3),
                measured=month <= last_abs or month in observed_fc,
            )
        )
    if not points:
        raise ValueError(f"no horizon could be projected from fuel data through {last_abs}")
    return points, calibration


def load_inputs(
    *, as_at: object | None = None, region: str = REGION_AUSTRALIA
) -> tuple[pd.Series, pd.Series]:
    """Both series, routed through the as-at aware build so rule 3 holds."""
    from auscpi.build import load_fuel_series, load_panel

    source = "abs_cpi_monthly" if region == REGION_AUSTRALIA else "abs_cpi_regional"
    measured = abs_fuel(load_panel(source, as_at=as_at), region=region)  # type: ignore[arg-type]
    _, monthly, _, _ = load_fuel_series(as_at=as_at)  # type: ignore[arg-type]
    return measured, monthly.set_index("period")["mean_price"]
