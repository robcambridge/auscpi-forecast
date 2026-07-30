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
- [ ] **NSW rental bond lodgements** — the replacement rent indicator, and the
      input to the main forecasting edge. New-lease rents by postcode, CC-BY.
      No longer the day-one priority: unlike asking rents this backfills to
      Jan 2021, so nothing is lost by building it after the grocery scraper.
- [ ] Grocery basket scraper — fixed ~200 SKUs mapped to expenditure classes
- [x] Confirm the ABS dataflow id, enable the ABS collector — `ABS,CPI,2.0.0`,
      monthly and quarterly national slices, on collect-abs.yml. The old
      `CPI_M` indicator is retired at 2025-09 and must not be used.
- [ ] Backfill FuelCheck history from data.nsw.gov.au archives

**Done when:** `auscpi health` shows every source fetched within its cadence,
three days running, untouched.

## Phase 2 — benchmarks and the first public path (weeks 2–3)

- [x] Naive benchmarks: random walk, seasonal naive, Atkeson–Ohanian, midpoint
- [ ] Parse ABS monthly CPI into an expenditure-class panel
- [ ] Published CPI weights and the expenditure-class taxonomy
- [ ] `config/release_calendar.csv` upkeep — schedule moves to the fourth
      Wednesday from February 2027, do not hardcode it
- [ ] RBA SMP forecasts entered as a benchmark series (manual entry is fine,
      it is four numbers a quarter)
- [ ] **Log the first public forecast path and push it**

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
- [ ] Rents, current-month measurement
- [ ] Electricity and gas from AER determinations and rebate schedules
- [ ] Long tail: seasonal model. Do not gold-plate.

## Phase 4 — h≥1, the actual forecast (weeks 5–9)

This is the project. Everything above exists to make this possible.

- [ ] **Rent roll-through.** Estimate the distribution of lags from SQM asking
      rents to ABS measured rents. The ABS measures the stock, not new leases, so
      today's asking rents constrain measured rents 6–12 months out almost
      mechanically. This is the one component where accuracy improves with
      horizon rather than decaying, and it is the core of the edge.
- [ ] **Fuel forward path** from refined product futures plus AUD forwards plus
      known excise indexation, rather than a statistical model of petrol prices.
- [ ] Tradables: FX pass-through with an estimated lag, off import prices
- [ ] Services: wages (WPI) and unit labour costs
- [ ] Seasonality estimated explicitly per component — at h≥1 you cannot observe
      it, so it has to be modelled
- [ ] Aggregate to a headline path on published weights

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
- [ ] Structured extraction per announcement: expenditure class, direction,
      estimated percentage effect, effective date, population share, confidence
- [ ] Forward calendar feeding directly into the h≥1 component paths
- [ ] **Leakage test:** event features must improve the components they should
      affect and leave the others alone. If they improve unrelated components you
      have a bug or a leak. Not optional.

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
