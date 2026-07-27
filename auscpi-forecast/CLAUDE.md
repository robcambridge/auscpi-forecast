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
- `docs/DATA_SOURCES.md` — every source, access method, backfillable or not
- `docs/ROADMAP.md` — build order and what depends on what

## Current priority

Phase 1 in `docs/ROADMAP.md`. The single most time-critical item is getting the
daily collectors running, because scraped data cannot be backfilled and every
day not collecting is a day permanently missing from the sample. Asking rents
matter most: they are the input to the rent roll-through model, which is the
project's main forecasting edge.
