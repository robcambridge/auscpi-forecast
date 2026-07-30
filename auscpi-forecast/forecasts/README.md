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
| `headline_yoy` | `index_projection` — project the index, then take the year-ended ratio | `random_walk` — carry the last year-ended rate |
| `trimmed_mean_yoy` | `index_projection` | `random_walk` |

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

Known weaknesses, so nobody has to discover them:

- **The driver is a flat 12-month mean**, so the path converges on the annualised
  recent mean at long horizons — Atkeson–Ohanian by another route. Any value over
  that benchmark is concentrated at short horizons where the base dominates, which
  is the honest place to expect it.
- **It is weakest exactly where Australia needs it most.** Administered prices
  reset on 1 July, and the July 2025 index rose 1.31% against a 0.31% average. A
  path made now therefore marks July 2026 down about a point on the base effect,
  when much of that rise is an annual reset likely to recur. The truth sits
  between this projection and the flat carry. Closing that gap needs the
  administered-price calendar, not a better driver.
- **The sample is ~27 monthly index observations.** Nothing here is statistically
  meaningful, and no claim of skill should be made from it.
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
