# Forecast log

`log.csv` is append-only. One row per forecast **per horizon**, written before
the release it refers to, committed and pushed immediately.

Columns worth understanding:

- `horizon_months` — 0 is a nowcast of the current month, 3 is one quarter out.
  Scoring is always broken out by horizon. A model good at h=1 and useless at
  h=6 must never be reported as a single number.
- `information_cutoff` — the last data used. Rows written by `auscpi forecast`
  put the newest **observed reference month** here (`2026-06`), not a wall-clock
  stamp, because that is what "last date of data used" actually means for a
  monthly series and a timestamp would only repeat `made_at`. Rows entered by
  hand through `auscpi log-forecast` fall back to `made_at`. Either way this is
  the field that makes look-ahead detectable by someone reading the file rather
  than trusting you: a backtest claiming h=1 while cutting off after the print
  is visible here.
- `model_version` — `v0-naive` for everything the current tooling produces.

**Never edit an existing row** except to fill in `actual` after the print lands,
and do that in a separate commit so the history shows the forecast and the
outcome as two distinct events.

Always log a benchmark alongside. A track record without a benchmark is
unreadable — nobody knows whether 0.3pp of error is good or dreadful.

## The current model is naive, and that is on purpose

`v0-naive`. Logged now rather than when it is good, because a track record
starting at n=1 with a weak model beats one starting at n=0 with a good one.

| Target | Model | Benchmark |
|---|---|---|
| `headline_mom` | `seasonal_naive` — same calendar month, most recent year it was observed | `mean_mom` — 12-month mean |
| `headline_yoy` | `seasonal_index_projection` — project the adjusted index, restore the published seasonal shape, take the year-ended ratio | `random_walk` — carry the last year-ended rate |
| `trimmed_mean_yoy` | `index_projection` — as above without the seasonal step, the series being already adjusted | `random_walk` |

The model and the benchmark are always different rules; pairing a rule against
itself would report skill of exactly zero forever and look like diligence.

### Why the year-ended paths project the index

A year-ended rate is a ratio of two index levels twelve months apart, so most of
it is already observed. On data through June 2026, the July 2026 rate needs
eleven monthly movements the ABS has published and one that has not happened.
Those known movements roll out of the annual window on a fixed schedule that is
knowable today, and carrying the last rate flat throws all of it away.

The arithmetic is verified rather than assumed: recomputing year-ended rates from
the published index reproduces the published rates to within 0.048pp, half the
0.1 step the ABS rounds to.

### Where the seasonality comes from

Not fitted here. The ABS publishes both an Original headline index (`10001`) and
its own seasonally adjusted version (`999901`), so the ratio between them *is* a
seasonal factor, estimated by the ABS with far more information than 27
observations could support. It is stable: per-month spreads across the sample run
0.00002–0.0005. April sits +0.57% above June, December +0.49%, July +0.41%,
November −0.22%.

The projection therefore grows the *adjusted* index at a flat trend — that series
has no seasonality left to get wrong — and multiplies the published factor back on.
Compounding a flat rate on the Original series instead would silently give every
future month average seasonality.

Known weaknesses, so nobody has to discover them:

- **The driver is a flat trend**, so the path converges on the annualised recent
  trend at long horizons — Atkeson–Ohanian by another route. The seasonal
  correction cancels there too, appearing in both numerator and denominator once
  the base is itself projected: live, it moves h=0 by +0.40pp and h=12 by
  −0.03pp. Any value over the benchmark is concentrated at short horizons where
  the base is observed.
- **The year-specific part of 1 July is untouched, and it is large.** Decomposing
  July 2025: the Original index rose 1.31%, of which ~0.41pp is the recurring
  July factor and ~0.90pp was that year's own administered movement. July 2024
  was adjusted −0.16%. So the non-seasonal July component swung more than a point
  between consecutive years, and no seasonal factor can know the direction. Only
  the determinations can — announced months ahead with quantified effects and
  dates. That gap is worth more than any further refinement here.
- **The sample is ~27 monthly index observations**, so each seasonal factor rests
  on about two. Nothing here is statistically meaningful, and no claim of skill
  should be made from it.
- **No uncertainty bands.** Quantiles by horizon come later.

## Workflow

```
auscpi forecast                 # print the path, log nothing
auscpi forecast --log           # append it, then commit and push
auscpi fill-actual              # after a print lands
auscpi score                    # error vs benchmark, by horizon
```

`fill-actual` only ever writes `actual`, never touches a row that already has
one, and reuses the file's own header — so it satisfies the rule above by
construction. Commit it separately from the forecast, so the history shows the
forecast and the outcome as two distinct events.

## Reading the scores honestly

Forecast errors at different horizons for overlapping reference months are
correlated. Twelve months of h=1..12 forecasts is nowhere near 144 independent
observations, and treating it as such will make a coin flip look like skill.

Expect skill to decay with horizon. If the model does not beat a random walk at
h=12, say so in the README rather than quietly dropping the horizon from the
table.
