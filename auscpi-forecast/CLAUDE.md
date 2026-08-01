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

Collectors: `fuelcheck` (daily, key works, runs in Actions), `abs_cpi_monthly`,
`abs_cpi_quarterly`, `abs_cpi_weights` (all on collect-abs.yml, 1st of month),
`nsw_rental_bonds` (monthly workflow; history backfilled 2026-07-31, 54 files,
2022-01 to 2026-06, ~37 MB).

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

In rough value order from here: split Sydney from the rest of NSW in the bond index
(the tractable part of β, needs the ABS postcode correspondence); confirm the CRA
treatment and effective dates against the ABS release notes; the administered-price
calendar (Phase 5, the only thing that can resolve the year-specific 1 July swing,
which is worth more than any driver refinement); then quantiles by horizon.

**Not started and possibly blocked:** the grocery basket (~17% of the basket)
needs Coles/Woolworths terms checked first, and on the SQM precedent may fail the
same test. Check before writing code, not after.
