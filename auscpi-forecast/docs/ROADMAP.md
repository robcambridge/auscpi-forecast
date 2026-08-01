# Roadmap

Ordered by dependency, not by interest. The tempting order — model first,
plumbing later — produces a project that demos once and then rots.

Two constraints drive the ordering:

1. **Scraped data cannot be backfilled.** Anything that must accumulate in real
   time starts in week one, even crudely.
2. **A forecast needs a nowcast underneath it.** A September forecast built on a
   wrong estimate of July is wrong for reasons that have nothing to do with
   forecasting. h=0 comes first, but it is scaffolding, not the product.

---

## Phase 1 — collection (week 1)

Only goal: data starts accumulating today.

- [x] Provenance storage with as-at reconstruction
- [x] Collector framework, health check, scheduled runs
- [x] FuelCheck daily collector
- [ ] FuelCheck API key, repo secrets, confirm the Action runs
- [x] ~~**SQM weekly asking rents**~~ — ruled out, their Terms of Service prohibit
      automated access (docs/DATA_SOURCES.md, "Sources ruled out")
- [x] **NSW rental bond lodgements** — the replacement rent indicator, and the
      input to the rent roll-through. (Described here as "the main forecasting edge"
      before it was measured; at 6.613% of the basket one component cannot be — see
      the aggregation item in Phase 4.) New-lease rents by postcode, CC-BY.
      History captured 2026-07-31: 54 monthly workbooks, 2022-01 to 2026-06,
      1,465,157 lodgements, ~37 MB in `data/raw`. Monthly files run back to
      2022-01, not 2021-01 as first recorded here.
- [ ] Grocery basket scraper — fixed ~200 SKUs mapped to expenditure classes
- [x] Confirm the ABS dataflow id, enable the ABS collector — `ABS,CPI,2.0.0`,
      monthly and quarterly national slices, on collect-abs.yml. The old
      `CPI_M` indicator is retired at 2025-09 and must not be used.
- [ ] Backfill FuelCheck history from data.nsw.gov.au archives

**Done when:** `auscpi health` shows every source fetched within its cadence,
three days running, untouched.

## Phase 2 — benchmarks and the first public path (weeks 2–3)

- [x] Naive benchmarks: random walk, seasonal naive, Atkeson–Ohanian, midpoint
- [x] Parse ABS monthly CPI into an expenditure-class panel — `auscpi build`
      writes `data/curated/abs_cpi_{monthly,quarterly}.parquet` plus a targets
      CSV. Pass `--as-at` for a real-time information set (rule 3).
      Sample reality check: only ~26 monthly m/m observations and ~15 year-ended
      exist, which is the binding constraint on any monthly model.
- [x] Published CPI weights and the expenditure-class taxonomy — `ABS,CPI_WEIGHTS,1.0.0`
      plus its hierarchical codelist, collected together. 87 expenditure classes
      summing to 100, validated on read. Rents 6.613%, automotive fuel 3.347%,
      food group 17.439% at the 2024-Q4 reweight.
- [ ] `config/release_calendar.csv` upkeep — schedule moves to the fourth
      Wednesday from February 2027, do not hardcode it
- [ ] RBA SMP forecasts entered as a benchmark series (manual entry is fine,
      it is four numbers a quarter)
- [x] **Log the first public forecast path and push it** — done 2026-07-30, 39 rows
      at `539ae70`, and a second path logged 2026-08-01 at `b6625e1` once the m/m
      target was derived from the same projected index. 78 rows total. The
      2026-08-01 path has origin 2026-08, so the new m/m model has NO forecast for
      reference month 2026-07 and will not be scored on the 2026-08-26 print — its
      first scoreable print is 2026-09-30. The machinery is done
      (`auscpi forecast --log`, then `auscpi fill-actual` after each print, then
      `auscpi score`). All three targets project the index so base effects are
      exact, and both headline targets read the *same* projection — year-ended as
      `level(m)/level(m-12)`, monthly as `level(m)/level(m-1)` — so they cannot
      contradict each other the way `v0-naive` did. `v2-seasonal-index` for
      headline, `v1-index-projection` for the trimmed mean; see
      forecasts/README.md. Remaining step is a human one: run it, commit, push.

Log a path before the next release even though the model is bad. A track record
starting at n=1 with a weak model beats one starting at n=0 with a good one, and
for a forecast the gap widens faster, because you need many more settled
observations before skill is statistically visible.

## Phase 3 — h=0, the initialisation (weeks 3–5)

The volatile components, nowcast from observed prices. ~35–40% of the basket and
much more of the month-to-month variance.

- [ ] Fuel from FuelCheck. Near-deterministic. Volume-weight NSW stations, then
      estimate the NSW-to-national gap.
- [ ] Food and non-alcoholic beverages from the scraped basket (~17%)
- [x] **Rents, current-month measurement** — `auscpi build` writes
      `data/curated/nsw_rental_bonds.parquet` (1,341,330 cleaned lodgements) and
      `nsw_rental_bonds_index.csv`, a FIXED-WEIGHT index of stratum medians rather
      than the plain median NSW Fair Trading publishes. The plain median is
      contaminated by which dwellings happened to be leased that month and is
      carried alongside only so the contamination stays visible; the two m/m
      series differ by 0.86pp on average across the sample. See
      `parsers/nsw_rental_bonds.py`. Still NSW-only and not yet split Sydney from
      the rest of the state — that correspondence is an ABS product to read, not
      a postcode range to guess.
- [ ] Electricity and gas from AER determinations and rebate schedules
- [ ] Long tail: seasonal model. Do not gold-plate.

## Phase 4 — h≥1, the actual forecast (weeks 5–9)

This is the project. Everything above exists to make this possible.

- [x] **Rent roll-through — built, skill NOT established.** `auscpi rents`, in
      `src/auscpi/rents.py`. The stock is modelled as a 12-month moving average of
      the new-lease flow, so most of any horizon under a year is already-signed
      leases; `observed_share` on each point reports how much. K=12 is structural
      (lease length), not tuned — see the module docstring for why the correlation
      peak at 15–17 was not followed.

      **The honest result, and it is not the one the roadmap assumed.** On every
      point the sample can evaluate, the model beats carrying rents flat by 21%,
      with skill rising by horizon exactly as the mechanism predicts (−1.10 at h=1,
      +0.56 at h=12). On the 45 points that every candidate K can evaluate, all of
      that disappears — K=12 scores +0.009 and every other K loses. Those are
      different periods, not different models: the common sample sits in the recent
      calm stretch where a flat carry is very hard to beat. With at most 19 origins
      and elevenfold-overlapping windows this sample cannot settle it. Re-run
      `auscpi rents --backtest` after each release; that verdict is meant to be
      revisited, not trusted.

      Nothing from this is logged to `forecasts/log.csv`.

- [x] **Measure how much of the pass-through gap is geography.** Done 2026-07-31,
      and it needed no postcode work at all — the blocker was on the ABS side, not
      the bond side. `abs_cpi_regional` collects rents by capital city (56 series,
      ~39 KB, not the 8,055-series all-regions payload), and `auscpi rents --region
      sydney` re-fits against the geographically matched target.

      β rises from 0.478 to 0.667, closing about a third of the distance to full
      pass-through. It is an amplitude effect, not a level one: Sydney and national
      rents grew almost identically (5.58 vs 5.39 %/yr), but the national series
      averages eight cities whose cycles are out of phase and so swings less. β by
      city — Sydney 0.667, Perth 0.500, Australia 0.478, Brisbane 0.439, Melbourne
      0.426 — puts Sydney top, which is the sanity check worth having: a NSW
      predictor mapping most strongly to Sydney is what a real signal looks like.

      Sydney's *skill* is nonetheless worse (+0.05 at h=6 against +0.24 national).
      On 8–14 points that settles nothing, but it is the wrong direction.

- [ ] **A Sydney-only bond index**, if it is still worth it. NSW lodgements are
      Sydney-dominated but not Sydney-only, so this would narrow the remaining gap.
      It needs the ABS postcode correspondence read rather than postcode ranges
      guessed at, which is real work for a residual share of a partial explanation.
      Do the CRA item first — it is cheaper and probably larger.
- [x] **Confirm the Commonwealth Rent Assistance treatment.** Done 2026-08-01, and
      the answer is that it cannot be corrected for on this sample. Confirmed: CPI
      rents are net of CRA, maximum rates rose 15% from 20 September 2023 and 10%
      from 20 September 2024 above routine indexation, so reference months 2023-09
      to 2025-08 are affected. Published wedge where the ABS quantifies it is
      +0.2 to +0.3pp (December 2025, January 2026); by June 2026 it is no longer
      reported. Peak-period magnitude is not published anywhere.

      Dropping the affected months leaves 10 of 31 calibration pairs and sends β to
      0.366 nationally and −0.098 for Sydney. A negative pass-through is noise, not
      a result, so no adjustment is applied. What remains is a directional warning
      that cannot be sized: β is fitted on a depressed window and forecasts a
      period where the depression has ended, so the projection is biased **low**.

      There is no ex-CRA series in the API — the counterfactual exists only in
      release text — so reconstructing it is document extraction. That is Phase 5,
      which makes CRA an argument for bringing Phase 5 forward rather than a
      separate task.
- [ ] **Fuel forward path** from refined product futures plus AUD forwards plus
      known excise indexation, rather than a statistical model of petrol prices.
- [ ] Tradables: FX pass-through with an estimated lag, off import prices
- [ ] Services: wages (WPI) and unit labour costs
- [ ] Seasonality estimated explicitly per component — at h≥1 you cannot observe
      it, so it has to be modelled
- [x] **Aggregate to a headline path on published weights** — `auscpi components`,
      in `src/auscpi/aggregate.py`. A component swap nets its view against what the
      top-down rule already implied for that class and weights the difference, so a
      component that merely agrees changes nothing. Contributions stay attributable
      per class.

      **The result reframes what a single component can be worth.** Swapping the rent
      roll-through into the headline moves it by at most **0.072pp** at h=12 and
      0.02–0.05pp across most of the path — under the 0.1pp step the ABS rounds its
      published rates to. The arithmetic is not subtle: rents are 6.613% of the
      basket and the two rent views differ by at most ~1.1pp.

      So no single component at this weight can move a published headline figure
      unless it disagrees with the naive baseline by several points. The edge has to
      be the combination — and components that disagree sharply are worth more than
      components that are merely well modelled. Not wired into `auscpi forecast` or
      the log: a swap below the rounding step would change the logged model without
      changing any published number.

## Phase 5 — the administered price calendar (weeks 9–11)

Where the frontier model earns its place. Framed as a feature, not a research
question.

The forecast framing is what makes this worth building. In a nowcast, a policy
price change that took effect on 1 July shows up in scraped data by 5 July, so
document extraction adds almost nothing. In a forecast made in June for
September, reading the determination is the *only* way to know. No time-series
model and no price scraper can see it.

- [ ] Corpus: Commonwealth and state budget papers, MYEFO, ministerial releases,
      AER and IPART determinations, the annual private health insurance premium
      round, tobacco excise indexation, PBS co-payments, childcare subsidy
      changes, state energy rebates
- [x] **The schema, the store and the leakage guard** — `src/auscpi/administered.py`
      and `config/administered_prices.csv`. An event carries expenditure class,
      announced date, effective month, announced per cent, pass-through, confidence
      and a required source URL. `override_path` turns events into a year-ended path
      for the class, netting the announced movement against what the projection
      already assumed for that month, so an event that matches expectations changes
      nothing. That plugs straight into `aggregate.ComponentSwap`.

      **`announced_date` is separate from `effective_month` and `visible_at` is the
      only supported selector.** Filtering on the effective month would let a
      February forecast use a March announcement — rule 3 for documents, and easier
      to get wrong here than with a time series because the effective date is the
      memorable one.

      **Two things this measured.** The announced number is not the CPI effect: the
      2025 and 2026 private health insurance rounds announced 3.73% and 4.41% and
      class 40091 printed 2.38% and 3.52%, so pass-through was 0.64 and 0.80 — it
      differs by a quarter between two consecutive events and cannot be assumed to
      be 1. And the magnitude justifies the phase: 5.032% of the basket moving 3.5%
      is ~0.18pp on the headline from one event, about two and a half times the
      entire rent roll-through, public two months before the reference month began.

- [ ] Corpus and extraction, per announcement. The schema above is the target.
      Highest-value classes not yet covered: electricity (40055, 1.835%, state
      rebates have swung it violently), tobacco excise (40090, 1.874%, indexed
      twice yearly on a known schedule), pharmaceutical products (40094, 0.954%)
      and child care (115498, 0.741%)
- [x] **Forward calendar feeding directly into the h≥1 component paths** —
      `aggregate.administered_swaps` builds component swaps from the calendar and
      `auscpi components` shows them beside the rent model. `information_cutoff` is
      a required argument threaded to `visible_at`, so an event announced after the
      forecast cannot reach it.

      Measured with `administered.event_value` on class 40091, forecasting from data
      published before the 17 February 2026 announcement and scoring April–June:
      no calendar 1.026, pass-through 0.64 → **0.704**, pass-through 1.00 → 0.906.
      0.64 is the previous round's realised ratio and the only one knowable at the
      time, so that is out of sample: a 31% error reduction, and a calibrated
      pass-through captures about two and a half times the benefit of taking the
      announcement at face value.

      Caveat kept with the number: the ranking of the face-value line is sensitive to
      where the data is truncated — cutting at end-February instead makes the
      baseline better and pushes face-value to *worse* than no calendar. One event
      cannot settle that. The robust claim is only that a calibrated pass-through
      beats face value in both cuts.

- [x] **Leakage test** — two properties, both in `tests/test_aggregate.py`. An event
      announced after the information cutoff produces no swap at all. An event moves
      only the class it names, and contributes exactly zero in months before its
      effective month. Neither is a smoke test: filtering on effective month instead
      of announced date would pass every other test in this repo.

## Phase 6 — density and attribution (weeks 11–13)

- [ ] Quantile regression on component errors, **estimated separately by
      horizon** — the error distribution at h=1 and h=6 are different objects
- [ ] Calibration monitoring per horizon. This is the product. An uncalibrated
      forecast is worse than none, because it invites confident wrong sizing.
- [ ] Attribution: what is the path, how did it move since the last update, and
      which components moved it

## Phase 7 — delivery (weeks 13–16)

- [ ] Static dashboard with the fan chart (GitHub Pages is enough)
- [ ] CSV/JSON endpoint
- [ ] Daily high-frequency index from scraped data, validated against each print
      — this accumulates evidence far faster than twelve prints a year, which
      matters more for a forecast than a nowcast because the settled-observation
      count is the binding constraint on demonstrating skill

## A note on how long the track record takes

This is the real cost of forecasting over nowcasting and it should be planned
for rather than discovered.

A nowcast track record becomes convincing quickly: six months of beating
consensus by a consistent margin is a decent sample, because nowcast errors are
small and the signal-to-noise is high.

A forecast track record at h=3 is close to noise after six months. Demonstrating
statistical skill takes years, and overlapping horizons have correlated errors,
so twelve months of h=1..12 forecasts is nowhere near 144 independent
observations.

Two mitigations, both worth doing:

- Report h=0 alongside the path. It settles monthly and demonstrates competence
  fast, even though it is not the product.
- Build the daily high-frequency index in Phase 7 early if you can. It validates
  against every print and accumulates evidence continuously.

## Explicitly out of scope

Deep learning on the macro series. Regime-switching ensembles. Multi-agent
architectures. A model zoo. News sentiment. The trading-reaction regression as a
subsystem — one chart on the dashboard, not a module.
