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
- `model_version` — what produced the row. `v2-seasonal-index` for both headline
  targets, `v1-index-projection` for the trimmed mean. The two headline targets
  share a version on purpose: they are one projected index read two ways, and
  versioning them apart would suggest they could move independently. Rows written
  before 2026-07-31 carry `v0-naive`, when `headline_mom` was a separate rule.

**Never edit an existing row** except to fill in `actual` after the print lands,
and do that in a separate commit so the history shows the forecast and the
outcome as two distinct events.

### The same reference month appears many times, and that is the point

Each run logs a full path, so a reference month is forecast again at a shorter
horizon every time. Reference month 2026-08 is h=1 in the path made 2026-07-30 and
h=0 in the one made 2026-08-01. Scoring buckets by (model, target, horizon), so
these land in different buckets and nothing is double-counted.

But read `information_cutoff` before treating them as two observations. Those two
rows share a cutoff of 2026-06, because no CPI printed between the two runs — so
they are the *same* forecast re-stamped at a shorter horizon, not an updated one.
A path logged when the information set has not moved adds a row without adding
information. This matters when counting: it inflates `n` in the scorecard while
the effective sample stands still, on top of the overlapping-horizon problem
described at the bottom of this file.

The rows are kept rather than avoided, because the alternative — deciding after
the fact which logged forecasts "counted" — is exactly the discretion this file
exists to remove.

Always log a benchmark alongside. A track record without a benchmark is
unreadable — nobody knows whether 0.3pp of error is good or dreadful.

## The current model is naive, and that is on purpose

Logged now rather than when it is good, because a track record starting at n=1
with a weak model beats one starting at n=0 with a good one.

| Target | Model | Benchmark |
|---|---|---|
| `headline_mom` | `seasonal_index_mom` — the projected index read as `level(m) / level(m-1)` | `seasonal_naive` — same calendar month, most recent year it was observed |
| `headline_yoy` | `seasonal_index_projection` — the same projected index read as the year-ended ratio | `random_walk` — carry the last year-ended rate |
| `trimmed_mean_yoy` | `index_projection` — as above without the seasonal step, the series being already adjusted | `random_walk` |

The model and the benchmark are always different rules; pairing a rule against
itself would report skill of exactly zero forever and look like diligence.

### The headline targets are one forecast, not two

They were not always. Until 2026-07-31 `headline_mom` was `seasonal_naive` and
`headline_yoy` was the index projection — two rules over the same index, which
duly contradicted each other. For July 2026 they said **+1.30%** and an implied
**+0.70%**, and both numbers went into the log with nothing to tell a reader which
one the model believed.

Now the m/m path is read off the same projected level series as the year-ended
path, so the arithmetic ties: compound the m/m path across the twelve months of an
annual window and the year-ended point for that window comes back out. Measured on
the live vintage, h=0..11 compounds to 3.7267 against a logged 3.727 at h=11 —
the gap is the third decimal place the log rounds to.

Two consequences worth stating plainly:

- **`seasonal_naive` moved to the benchmark slot.** It is what this replaced, so
  the logged skill number answers the question the change actually raises rather
  than a more flattering one.
- **Coherence is not accuracy.** Three targets driven by one projection can now be
  wrong together, and one bad trend estimate moves all of them the same way. What
  it buys is that an error is attributable to the projection instead of to an
  unexplained disagreement between rules.

The forecast is not rounded to match the published m/m, which the ABS gives to one
decimal place. That leaves up to 0.05pp of scored error at h=0 that is measurement
rather than model — a floor on measured accuracy, and negligible beside the errors
at any longer horizon. Rounding to close it would hide the model's actual view to
improve the scorecard.

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

  It is not hypothetical: the live h=0 point for July 2026 is +0.70%, which is
  trend plus the recurring July factor and nothing else. If July 2026 carries an
  administered movement like July 2025's, the forecast is light by most of a
  point, and it will be the calendar rather than the model that could have known.
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
