# auscpi — a live forecast of Australian inflation

A component-level, bottom-up **forecast** of the Australian Consumer Price Index,
producing a full path from the current month out to twelve months ahead, with a
public timestamped track record scored by horizon.

In November 2025 the ABS replaced the quarterly CPI with a complete Monthly CPI,
and headline inflation from that series became the target for monetary policy.
Every rates desk and bank economics team in the country is rebuilding its
inflation forecasting around a series with barely two years of history. This is
one attempt at that, built in the open.

---

## Forecast, not nowcast

The distinction is the whole design.

A **nowcast** estimates a month whose prices have already been set — you are
measuring something that happened, ahead of the ABS aggregating it. It is
mostly a data problem and the errors are small.

A **forecast** projects months that have not happened. It is harder, the errors
are larger, and it is the only one of the two a desk can position on, because by
the time a print is a week away the consensus has converged and there is nothing
left to trade.

This project forecasts. The nowcast survives as **h=0**, the initialisation of
the path, because a forecast of September built on a wrong estimate of July is
wrong for a reason that has nothing to do with forecasting.

Output is a path, not a number:

```
made 2026-07-27, information cutoff 2026-07-27

  h=0   2026-07   m/m  +0.35%   [+0.28, +0.44]
  h=1   2026-08   m/m  +0.21%   [+0.06, +0.38]
  h=2   2026-09   m/m  +0.48%   [+0.19, +0.79]
  ...
  h=12  2027-07   y/y   3.1%    [ 1.9,   4.4 ]
```

## Where a forecast can actually have an edge

Three components of the Australian CPI are unusually **pre-determined** — much
of their future value is knowable today, from information a time-series model
cannot see. That is the entire thesis.

**Rents (~6%).** The ABS measures rents on the *stock* of dwellings, not on new
leases. A change in asking rents today reaches the measured index gradually over
the following year as existing leases roll. Today's asking rents therefore
mechanically constrain measured rents six to twelve months out. This is the
strongest single argument for the forecast framing over the nowcast one, because
the advantage grows with horizon rather than shrinking.

**Administered and policy-set prices.** Australia's CPI has an unusually high
administered share, and those changes are announced in text, with quantified
effects and effective dates, months before they appear in the data. Electricity
determinations, the annual health insurance premium round, tobacco excise
indexation, PBS co-payments, state rebate schemes. A model cannot see them; a
document reader can. Note this is the reverse of the nowcast case, where a
policy change would have shown up in scraped prices anyway — **the forecast
framing is what makes the document-extraction layer worth building.**

**Fuel (~3.5%).** Not pre-determined, but market-implied: refined product
futures plus AUD forwards plus known excise gives a forward path that is better
than any statistical model of retail petrol prices.

## What this is not

Not an attempt to beat a bank's inflation team on the headline number. They have
business liaison, proprietary data, and twenty years of institutional memory.

And an honest expectation, stated up front: **the edge decays with horizon.** At
h=1 this should be good. At h=12 it will not beat a random walk, and the track
record will say so. A model that is candid about where it stops adding value is
more persuasive than one claiming uniform superiority, which nobody believes.

## Benchmarks

Forecast benchmarks are much harder than nowcast benchmarks. These are the ones
that matter, in ascending order of difficulty:

| Benchmark | Why it is there |
|---|---|
| Seasonal naive m/m | Strong for a CPI with heavy seasonality and annual resets |
| Random walk on y/y | Embarrassingly hard to beat past h=3 |
| Atkeson–Ohanian | Twelve-month average; beat Phillips curves for decades |
| RBA target midpoint | Near-unbeatable at long horizons if the RBA is credible |
| **RBA Statement on Monetary Policy** | Free, public, quarterly. The only one that means anything. |

Beating the SMP at one to two quarters is a real claim. Beating a random walk is
table stakes.

---

## Track record

`forecasts/log.csv` is append-only. Every row was committed to this public
repository **before** the corresponding ABS release. The git history is the audit
trail; nothing is backfilled.

| h | Model | Target | n | MAE | Benchmark MAE | Skill |
|---|---|---|---|---|---|---|
| _first entries pending_ | | | | | | |

Regenerate with `auscpi score`.

---

## Architecture

```
data/raw/         immutable, append-only, committed
                     |
                     v
data/curated/     derived, rebuildable, gitignored
                     |
                     v
h=0 component nowcast  <-- scraped fuel, groceries, asking rents
                     |
                     v
h>=1 projection        <-- rent roll-through, futures curves,
                     |     announced policy calendar, wages, FX pass-through
                     v
aggregate on published CPI weights
                     |
                     v
quantile regression by horizon -> fan
                     |
                     v
forecasts/log.csv  timestamped public record, scored by horizon
```

`data/raw` is immutable and committed because a forecast is only credible if you
can prove the inputs existed when you claim. `storage.snapshots_as_at()`
reconstructs the information set as at any past moment, which is what makes an
honest backtest possible. Overwriting a file in place destroys that, silently.

## Quick start

See [`docs/SETUP.md`](docs/SETUP.md) for a step-by-step walkthrough using GitHub
Desktop and the Claude desktop app, with no terminal required.

## Commands

| Command | Does |
|---|---|
| `auscpi collect --all` | Run every collector, snapshot to `data/raw` |
| `auscpi health` | Last successful fetch per source |
| `auscpi log-forecast ...` | Append one forecast at one horizon |
| `auscpi log-path ...` | Append a whole path in one go |
| `auscpi score` | MAE and skill vs benchmark, broken out by horizon |

## Status

- [x] Provenance storage with as-at reconstruction
- [x] Collector framework, health checks, scheduled collection
- [x] NSW FuelCheck daily collector
- [x] Horizon-aware track record and scoring
- [x] Naive benchmarks
- [ ] ABS Monthly CPI collector (dataflow id needs confirming)
- [ ] SQM asking rents — **start immediately, cannot be backfilled**
- [ ] Grocery basket scraper — **start immediately, cannot be backfilled**
- [ ] CPI weights and expenditure-class taxonomy
- [ ] RBA SMP forecasts as a benchmark series
- [ ] Rent roll-through model — the core forecasting edge
- [ ] Fuel forward curve
- [ ] Administered price calendar (document extraction)
- [ ] Density forecasts by horizon
- [ ] Attribution engine
- [ ] Dashboard

## Data sources and licensing

See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md). Retailer data is collected at
low frequency, respects `robots.txt`, and is never republished at product level —
only derived aggregate indices. ABS and RBA material is used under CC-BY.

## Licence

MIT.
