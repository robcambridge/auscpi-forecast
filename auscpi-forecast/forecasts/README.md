# Forecast log

`log.csv` is append-only. One row per forecast **per horizon**, written before
the release it refers to, committed and pushed immediately.

Columns worth understanding:

- `horizon_months` — 0 is a nowcast of the current month, 3 is one quarter out.
  Scoring is always broken out by horizon. A model good at h=1 and useless at
  h=6 must never be reported as a single number.
- `information_cutoff` — the last date of data used. Live, it equals `made_at`.
  In a backtest it does not, and this is the field that makes look-ahead bias
  detectable by someone reading the file rather than trusting you.

**Never edit an existing row** except to fill in `actual` after the print lands,
and do that in a separate commit so the history shows the forecast and the
outcome as two distinct events.

Always log a benchmark alongside. A track record without a benchmark is
unreadable — nobody knows whether 0.3pp of error is good or dreadful.

## Reading the scores honestly

Forecast errors at different horizons for overlapping reference months are
correlated. Twelve months of h=1..12 forecasts is nowhere near 144 independent
observations, and treating it as such will make a coin flip look like skill.

Expect skill to decay with horizon. If the model does not beat a random walk at
h=12, say so in the README rather than quietly dropping the horizon from the
table.
