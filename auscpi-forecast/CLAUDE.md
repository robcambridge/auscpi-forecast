# Instructions for Claude Code

Read this before changing anything.

## What this project is

A production **forecast** of Australian inflation — a path from h=0 to h=12, not
a single number, and not a nowcast. The nowcast survives only as h=0, the
initialisation of the path.

Not a research paper, not a notebook. The differentiator is that the pipeline
runs unattended and does not break, not that the model is clever. A slightly
worse model that is always current beats a better model that needs someone to
run a notebook.

## Hard rules

0. **Do not run `git commit`, `git push`, `git checkout`, or any other git
   command that changes state.** The user reviews and commits through GitHub
   Desktop. Make the file changes, tell the user what you changed and suggest a
   commit message, and stop there. `git status` and `git log` are fine.

1. **Never write to `data/raw/` except by appending a new snapshot.** No edits,
   no deletions, no overwrites. It is the provenance layer and it cannot be
   rebuilt. `storage.write_snapshot` already refuses to overwrite; do not add a
   force flag.

2. **Collectors do not parse.** `fetch()` returns the source's response
   essentially untouched. All parsing happens in the build step reading from
   `data/raw`. This is so that when a parser turns out to be wrong — and one
   will — history can be reprocessed instead of lost.

3. **No look-ahead, ever.** Any backtest must go through
   `storage.snapshots_as_at()`. If you find yourself reading the latest file
   directly inside evaluation code, stop.

4. **Never edit an existing row in `forecasts/log.csv`** except to fill in
   `actual` after a release. That file's git history is the credibility of the
   whole project.

5. **Every forecast carries a horizon.** `ForecastRecord.horizon_months` is
   not optional and scoring is always broken out by horizon. A model that is
   good at h=1 and useless at h=6 must never be reported as a single number.

6. **Do not hardcode the release schedule.** Reference month N is released on the
   last Wednesday of month N+1, but this changes to the fourth Wednesday from
   February 2027. Read `config/release_calendar.csv`.

7. **Secrets come from `.env` via `auscpi.config.settings`.** Never inline a key,
   never commit one, never print one.

## Things that look like good ideas and are not

Do not add, and push back if asked to:

- LSTMs, transformers, or any deep learning on the macro series. ~26 monthly
  observations. These underperform, professionals know they underperform, and
  shipping them signals inexperience.
- Regime-switching ensembles and multi-agent architectures. Same reason.
- A model zoo. Every model in the repo must be either a benchmark or in
  production.
- Sentiment scores from news. The LLM layer here reads primary policy documents
  and extracts quantified scheduled price changes with dates and effective dates.
  In a forecast that is a real information channel no time-series model and no
  price scraper can see. Sentiment is not.
- Claims of skill at long horizons. If the model does not beat a random walk at
  h=12, the README says so. Do not quietly drop the losing horizons from the
  scorecard.

## Conventions

- Python 3.11+, `from __future__ import annotations`, full type hints.
- `httpx` not `requests`; `typer` for CLI; `pydantic-settings` for config.
- `ruff check` and `pytest` must pass before any commit.
- New collector: subclass `Collector`, set `source`, implement `fetch()`,
  import it in `collectors/__init__.py`. It registers itself.
- Comments explain *why*, not *what*. If a choice is non-obvious to a reader who
  knows Python but not Australian CPI methodology, say why.

## Where to look first

- `src/auscpi/storage.py` — the provenance model, read this before anything else
- `src/auscpi/collectors/base.py` — the collector contract
- `src/auscpi/forecast.py` — the models, and an honest list of what is weak
- `src/auscpi/parsers/abs_cpi.py` — the SDMX flattener and the verified target triples
- `docs/DATA_SOURCES.md` — every source, access method, backfillable or not
- `docs/ROADMAP.md` — build order and what depends on what
- `forecasts/README.md` — what the published model is and where it fails

## Running it locally

There is no venv by default and the package is not pip-installed. To run the
suite or the CLI:

```
python -m venv .venv
.venv/Scripts/python.exe -m pip install pytest ruff httpx tenacity pydantic pydantic-settings pandas pyarrow typer rich pyyaml
.venv/Scripts/python.exe -m ruff check src tests
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q tests
PYTHONPATH=src .venv/Scripts/python.exe -c "from auscpi.cli import app; app()" health
```

`PYTHONPATH` separates with `;` on Windows, but one entry is enough. To exercise a
collector or the build without touching the real `data/`, set
`AUSCPI_DATA_DIR` to a throwaway directory — do that for any experiment, since
`data/raw` is append-only provenance.

## State of play — read this before planning anything

Last updated 2026-07-30. `docs/ROADMAP.md` has the per-item detail and is kept
ticked off; this is the summary.

**Working end to end.** `auscpi collect` → `auscpi build` → `auscpi forecast --log`
→ `auscpi fill-actual` → `auscpi score`. The first public path is logged and
pushed: 39 rows, h=0..12, three targets, information cutoff 2026-06.

**No workflow had ever run, and the cause was file placement (fixed 2026-08-01).**
The git root is ONE LEVEL ABOVE this project, and `.github/workflows/` lived inside
`auscpi-forecast/`. GitHub only scans `.github/workflows/` at the repository root, so
it had never seen a workflow: empty Actions tab, no schedules, no runs, and every
snapshot in `data/raw` produced by hand. `ci.yml` had never run either, so `ruff` and
`pytest` were never enforced on a push.

`.github/` now sits at the repo root and every job carries
`defaults.run.working-directory: auscpi-forecast`. **Keep it there** — anything added
under `auscpi-forecast/.github/` is invisible to GitHub and will silently never run.

`auscpi health` now compares each age against its cadence rather than printing ages
(a stalled daily source used to look identical to a healthy monthly one), `--strict`
exits non-zero, and `collect.yml` runs it after the commit step.

**Health deliberately ignores backfill snapshots.** They stamp `fetched_at = now`, so
capturing the FuelCheck archive flipped fuelcheck from OVERDUE to "ok, 0d 0h" while
the daily collector still had not run — green *because* of an unrelated action, and
self-concealing, since more history makes a dead pipeline look healthier. Cadence is
assessed on scheduled runs only. A source with data but no scheduled run reports
**backfill only**, which is currently true of `nsw_rental_bonds` as well as fuelcheck.
Any new backfill must note itself with a `backfill`/`archive` prefix or it will
re-open this hole.

Collectors: `fuelcheck` (daily, **not currently collecting — see above**),
`abs_cpi_monthly`,
`abs_cpi_quarterly`, `abs_cpi_weights`, `abs_cpi_regional` (all on collect-abs.yml,
1st of month), `nsw_rental_bonds` (monthly workflow; history backfilled 2026-07-31,
54 files, 2022-01 to 2026-06, ~37 MB).

**Two traps that already bit once. Do not rediscover them:**

1. `ABS,CPI_M,*` is the RETIRED monthly indicator, frozen at 2025-09. The live
   dataflow is `ABS,CPI,2.0.0`. Collectors refuse a stale series for this reason.
2. SQM Research must not be scraped — their Terms of Service prohibit it even
   though robots.txt allows it. `collectors/sqm_rents.py` is hard-disabled and
   raises. See `docs/DATA_SOURCES.md`, "Sources ruled out". NSW rental bond
   lodgements replaced it and are a better series anyway.

**The binding constraint is sample size, not code.** The monthly CPI gives ~27
index observations and ~15 year-ended. Nothing estimated here is statistically
meaningful yet, and no claim of skill should be made. This is why the roadmap says
log a path now rather than when the model is good — the track record clock is the
one thing that cannot be accelerated later.

**Fixed 2026-07-31: the targets no longer contradict each other.** `headline_mom`
was seasonal naive while `headline_yoy` projected the index, and for July 2026 they
said +1.30% and +0.70%. The m/m path is now `seasonal_index_mom`, read off the same
projected levels as the year-ended rules (`level(m)/level(m-1)`), so compounding the
m/m path across an annual window returns that window's year-ended point — 3.7267
against 3.727 on the live vintage. `seasonal_naive` became the benchmark for
`headline_mom`, since it is what the change replaced. Both headline targets log as
`v2-seasonal-index`. The rows already in `log.csv` keep the old pairing and
`v0-naive`; they are history, not a mistake to correct (rule 4).

**Rents are measured, 2026-07-31.** `auscpi build` now writes
`data/curated/nsw_rental_bonds_index.csv` from 1.34M cleaned lodgements. The index
is FIXED-WEIGHT over strata (dwelling type x bedrooms), not the plain median NSW
Fair Trading publishes — the plain median moves with whatever mix happened to be
leased, and the two m/m series differ by 0.86pp on average, so a roll-through model
fitted on the raw median would be fitting composition. Read the module docstring in
`parsers/nsw_rental_bonds.py` before changing any threshold in it; every exclusion
is counted and surfaced in `BuildResult.note` on purpose.

**Rent roll-through built 2026-07-31, and it does NOT have demonstrated skill.**
`auscpi rents` / `src/auscpi/rents.py`. The stock is a 12-month moving average of the
new-lease flow, K=12 structural rather than tuned. Read the module docstring before
touching it — the short version is that the model beats a flat carry by 21% overall
with skill rising by horizon exactly as the mechanism predicts, and that entire
result vanishes on the subsample where every candidate K is comparable. Those are
different periods, not different models, and 19 origins cannot tell them apart.
Nothing from it is logged. `auscpi rents --backtest` re-runs the verdict; it is meant
to be revisited after each release.

The parameter doing the work is β≈0.48: new-lease rents move about twice as much as
measured ones. At least three things are inside it — NSW vs national, CRA netting,
and partial pass-through within the stock — and separating them is what would turn β
from a fudge factor into a forecast.

**Geography measured 2026-07-31, and it is about a third of β.** New collector
`abs_cpi_regional` (rents by capital city, 56 series, ~39 KB, on collect-abs.yml).
Re-fitting against Sydney rents raises β from 0.478 to 0.667. It is an amplitude
effect, not a level one — Sydney and national rents grew almost identically, but the
national series averages eight out-of-phase city cycles and so swings less. β is
highest for Sydney of the five cities tried, which is the sanity check worth having.
Sydney's *skill* is nonetheless worse, on a sample too small to mean much either way.
National stays the default: it is what the project forecasts, and a city is a
diagnostic.

Two ABS gotchas found while doing it: `30014` and `115522` are both published as
"Rents" and are byte-identical, and the regional slice's national series matches the
`abs_cpi_monthly` one exactly across all 48 months, which is the cross-check that
says the new collector is wired up right.

**CRA checked 2026-08-01: real, dated, and NOT correctable on this sample.** CPI
rents are net of Commonwealth Rent Assistance; maximum rates rose 15% from
20 September 2023 and 10% from 20 September 2024, so reference months 2023-09 to
2025-08 are affected — most of the calibration window. Dropping them leaves 10 of 31
pairs and sends β to 0.366 nationally and −0.098 for Sydney, which is noise rather
than a finding, so no adjustment is applied. The residual: β is fitted on a depressed
window and forecasts a period where the depression has ended, so **the rent
projection is biased low by an amount nobody can currently size**. There is no ex-CRA
series in the API, only release commentary, so fixing this is document extraction.

That is now the second independent argument for Phase 5 — the first was the
year-specific 1 July swing. Phase 5 is looking less like a later refinement and more
like the thing two separate dead ends both point at.

**Aggregation built 2026-08-01, and it resized the whole thesis.** `auscpi components`
/ `src/auscpi/aggregate.py` swaps a component's view into the headline, netting it
against what the top-down rule already implied for that class and weighting the
difference. Swapping in the rent roll-through moves the headline by at most **0.072pp**
— under the 0.1pp step the ABS rounds to. Rents are 6.613% of the basket and the two
rent views differ by at most ~1.1pp; 0.066 x 1.1 is 0.07. Not wired into `auscpi
forecast` or the log, because a swap below the rounding step changes the logged model
without changing any published number.

The consequence to carry forward: **no single component at this weight can move a
published headline figure.** The edge has to be the combination, and a component that
disagrees sharply with the naive baseline is worth more than one that is merely well
modelled. That is an argument for prioritising breadth of coverage and for Phase 5,
where administered changes can disagree with the baseline by a lot — the July 2025
decomposition put ~0.90pp of the index on that year's own administered movement.

**Administered price calendar started 2026-08-01, and it vindicates the phase.**
`src/auscpi/administered.py` + `config/administered_prices.csv`: the schema, the store,
the leakage guard and the arithmetic turning an event into a component override. Not
the corpus or the extraction layer — those are next, and this is what they must
produce.

Two measured findings worth carrying:

1. **One administered event beats the whole rent model.** The 1 April 2026 private
   health insurance round moved class 40091 (5.032% of the basket) by 3.52%, about
   0.18pp on the headline — ~2.5x the entire rent roll-through — and was public two
   months before the reference month began.
2. **The announced number is not the CPI effect.** Announced 3.73% and 4.41% in 2025
   and 2026; the class printed 2.38% and 3.52%. Pass-through 0.64 then 0.80, because
   the class also holds doctor and hospital fees that do not move on 1 April. It
   differs by a quarter between consecutive events, so it is stored per event with a
   confidence rather than fitted.

**`announced_date` is separate from `effective_month` on purpose and `visible_at` is
the only supported selector.** Filtering on the effective month lets a February
forecast use a March announcement. That is rule 3 for documents and it is easier to
get wrong here than with a time series, because the effective date is the memorable
one.

**Calendar wired into the component path 2026-08-01, with the leakage test done.**
`aggregate.administered_swaps` takes a required `information_cutoff` threaded to
`visible_at`; `auscpi components` shows calendar events beside the rent model. Two
leakage properties are tested: an event announced after the cutoff produces no swap,
and an event moves only the class it names and contributes zero before its effective
month. Filtering on effective month instead of announced date would pass every other
test in this repo, which is why those two exist.

`administered.event_value` scores an event against what printed: class 40091 from
data published before the 17 February announcement gives no calendar 1.026,
pass-through 0.64 → 0.704, pass-through 1.00 → 0.906. A calibrated pass-through is
worth about 2.5x face value. **Do not quote a stronger version of this.** An earlier
scratch run truncating at end-February showed face-value doing worse than no calendar
at all; that ranking does not survive the shipped truncation, and one event cannot
settle it. The robust claim is only that calibrated beats face value.

Also note the stored 0.80 for the 2026 round was read off the outcome, so scoring
with it returns a meaningless 0.039 — always pass the previous round's ratio.

**The component order was wrong and 2026-08-01 measured it.** `auscpi leverage` ranks
classes by weight x monthly sd — how much headline movement each can produce. Median
across 87 classes is 0.008pp. Top: international holiday travel 0.343pp, domestic
holiday travel 0.201pp, automotive fuel 0.199pp, electricity 0.113pp, medical and
hospital services 0.057pp. **Rents is not in the top twelve**, which is consistent with
the whole rent model moving the headline by at most 0.072pp.

Three consequences:

- **Fuel is the best-value component in the plan** — third by leverage, max observed
  contribution 1.098pp, the one high-leverage class that is genuinely anticipable
  (futures + AUD forwards + known excise), and its collector already runs daily.
- **Holiday travel is the largest source of headline movement and is nowhere in the
  roadmap.** Combined 0.54pp. Not administered, probably not pre-determined, but
  strongly seasonal.
- **Leverage is not skill.** It says where the movement lives, not what can be
  anticipated. Use it to choose which classes are worth asking the skill question
  about, not as a claim that any of them are forecastable.

**No electricity calendar entry, deliberately.** The 2026-27 DMO was decided May 2026,
effective 1 July 2026 — a live case since July had not printed. But the determination
is a range across regions and customer types (−3.4% to −7.2% NSW/SEQ, +1.4% SA) for
standing-offer customers only, and there is no defensible route to a single class
effect without inventing it. It is also the wrong driver: electricity's biggest moves
are +22.3% Nov 2024, +18.5% Jan 2026, −14.6% Aug 2024 — rebate timing, not July. The
corpus to build is the rebate instalment schedules.

**Fuel history captured 2026-08-01.** `auscpi backfill-fuel --since 2023-01`: 42
monthly price-history files, ~186 MB, from the data.nsw CKAN dataset `fuel-check`.
The live API returns prices *right now* only, so the archive is the sole route to
history. The full archive is 119 files back to 2016 (~400 MB); the range is bounded
because `data/raw` is tracked in git, and the backfill resumes if widened.

Two things found doing it, both in docs/DATA_SOURCES.md:

- **The archive is CC BY-SA, not CC-BY**, which this repo had recorded. Share-Alike
  carries obligations for derived works; worth understanding before redistributing
  anything built on it. NSW rental bonds are separately CC-BY and unaffected.
- **The published titles mix full and abbreviated month names** and vary the prefix
  ("June 2026", "Feb 2024", "Service Station & Price History Sep 2019"). Matching
  only full names skipped 23 of 119 files including ten consecutive months of 2024 —
  a backfill that quietly captures two thirds of the archive.

In rough value order from here: the fuel component itself (parse the archive into a
volume-weighted NSW price series, then the NSW-to-national gap — Phase 3 nowcast
before the Phase 4 forward path); the electricity rebate schedule as the first real
extraction corpus; explicit seasonality for the holiday travel classes; then quantiles
by horizon.

**Log caught up 2026-08-01.** `log.csv` is now 78 rows: the original 39 at `539ae70`
plus 39 at `b6625e1` carrying `seasonal_index_mom`. Two things to know when reading
it. The 2026-08-01 path has origin 2026-08, so the new m/m model has no forecast for
reference month 2026-07 and will not be scored on the 2026-08-26 print — its first
scoreable print is 2026-09-30, and the old `seasonal_naive` row (+1.30) is what that
print scores. And because no CPI printed between the two runs, both share
`information_cutoff` 2026-06, so the y/y rows are the same forecast re-stamped a
horizon shorter rather than an updated one. See forecasts/README.md.

**Not started and possibly blocked:** the grocery basket (~17% of the basket)
needs Coles/Woolworths terms checked first, and on the SQM precedent may fail the
same test. Check before writing code, not after.
