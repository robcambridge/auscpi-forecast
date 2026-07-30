# Data sources

The column that matters most is **Backfillable**. Sources that are backfillable
can wait; sources that are not must start collecting immediately, because every
day not collected is a day permanently absent from your sample.

## Backfillable — can be assembled retrospectively, no rush

| Source | What | Access | Licence |
|---|---|---|---|
| ABS Consumer Price Index, Australia | Monthly CPI, expenditure class level, from April 2024 | ABS Data API (SDMX) + XLSX downloads | CC-BY |
| ABS quarterly CPI | Decades of quarterly history | ABS Data API | CC-BY |
| RBA statistical tables | Cash rate, market rates, inflation expectations | CSV/XLSX | CC-BY |
| NSW FuelCheck **archives** | Historical station-level prices | data.nsw.gov.au dataset files | CC-BY |
| NSW Rental Bond Lodgements | Monthly **new-lease** rents by postcode: weekly rent, dwelling type, bedrooms | NSW Fair Trading .xlsx, monthly, back to Jan 2021 | CC-BY |
| AER Default Market Offer | Regulated electricity price determinations | PDF/HTML, published in advance | CC-BY |
| IPART determinations | NSW regulated prices (water, transport) | PDF/HTML | CC-BY |
| Commonwealth Budget / MYEFO | Fiscal measures with quantified price effects | PDF | CC-BY |
| Private health insurance premium round | Annual approved premium changes, announced ahead of 1 April | Dept of Health media release | CC-BY |
| Tobacco excise indexation | Scheduled, indexed to AWOTE, dates known in advance | ATO | CC-BY |
| RBA Statement on Monetary Policy | Official forecasts, useful as a benchmark | HTML/PDF | CC-BY |

### Note on ABS index reference periods

The quarterly CPI was re-referenced in the December 2025 release from
2011-12 = 100.0 to **September month 2025 = 100.00**, published to two decimal
places. Mixing pre- and post-re-referencing vintages without applying the
conversion factors produces silently wrong index levels. Conversion factors are
series-specific and unrounded — recompute rather than reusing a published
rounded factor.

## NOT backfillable — start collecting on day one

| Source | What | Access | Note |
|---|---|---|---|
| ~~SQM Research~~ | ~~Weekly asking rents by postcode~~ | **RULED OUT — terms prohibit automated access.** See "Sources ruled out" below. Replaced by NSW Rental Bond Lodgements, above. |  |
| Coles / Woolworths | Grocery shelf prices, fixed SKU basket | HTML | Food & non-alcoholic beverages is ~17% of the basket. Keep the basket small and fixed; a changing basket is not an index. |
| Airline fares | Domestic route fares | HTML | Travel is wildly volatile and seasonal. |
| Live FuelCheck | Daily station prices | api.nsw.gov.au | Also backfillable via archives, so this one is belt and braces. |

Wayback Machine coverage of retail price pages is thin and inconsistent. Do not
plan around it.

## Scraping conduct

This repository is public and carries your name, so the conduct matters as much
as the code.

- Honour `robots.txt`. Check it before writing any scraper, not after.
- One request every few seconds at most. There is no reason to go faster; you
  need a daily snapshot of a few hundred SKUs, not a crawl.
- Identify the client honestly in the User-Agent, with a contact address.
- Never republish product-level retailer data. Publish only derived aggregate
  indices. This is both the courteous position and the defensible one.
- If a site's terms prohibit automated access, do not scrape it. Say so in this
  file and use a different indicator. A missing component is recoverable; a
  cease-and-desist attached to your name is less so.

## Sources ruled out

### SQM Research — asking rents. Do not scrape. (checked 2026-07-28)

`robots.txt` permits it; the **Terms of Service do not**, and the terms are the
binding document. Three separate clauses at
<https://sqmresearch.com.au/terms-of-service>:

> (5) you will not access the Services through automated or non-human means,
> whether through a bot, script or otherwise

> Engage in any automated use of the system, such as using scripts to send
> comments or messages, or using any data mining, robots, or similar data
> gathering and extraction tools.

> Except as may be the result of standard search engine or Internet browser
> usage, use, launch, develop, or distribute any automated system, including
> without limitation, any spider, robot, cheat utility, scraper, or offline
> reader that accesses the Services

The access licence they grant is "personal, non-commercial use or internal
business purpose only" and is expressly conditional on compliance with that
Prohibited Activities section, so scraping voids the licence rather than merely
breaching a guideline. Reports also may not be redistributed without prior
written consent.

For the record, because it is easy to talk yourself into the opposite: `robots.txt`
has `User-agent: *` → `Allow: /` with no `Crawl-delay`, and named AI crawlers
(ClaudeBot, GPTBot, CCBot, Google-Extended, …) each `Disallow: /`. An honest
non-browser client is in fact served HTTP 200. **None of that grants permission.**
robots.txt is a crawler convention; the ToS is the contract, and where they
disagree the ToS wins.

`collectors/sqm_rents.py` exists but is hard-disabled and raises if invoked. The
only legitimate routes are a data licence purchased from SQM, or their prior
written consent. Until then it stays off.

### The replacement: NSW Rental Bond Lodgements

Better suited to this project than SQM was, not merely a consolation:

- Bonds are lodged **at the start of a tenancy**, so this measures new-lease
  rents directly — the same lead-over-measured-rents property that made asking
  rents worth having, and the input the roll-through model actually wants.
- Transacted rents on signed leases, not advertised asking prices, so it has no
  listing or withdrawal bias.
- CC-BY, from the same data.nsw.gov.au portal already used for FuelCheck
  archives, so the licensing question is settled and identical.
- **Backfillable to January 2021**, which removes the "collect today or lose it
  forever" urgency that made SQM the Phase 1 priority in the first place.

Honest trade-offs: NSW only, where SQM was national — but the project already
takes a NSW-only fuel source and estimates the NSW-to-national gap, so the
pattern exists. Monthly rather than weekly, and published with a processing lag,
so it is less timely; against that, new-lease rents lead measured rents by
6–12 months, which dwarfs the difference between a weekly and a monthly
observation. Victoria's DFFH quarterly rental report is the obvious second
jurisdiction if national coverage matters later.

## Access notes

**NSW FuelCheck.** Free registration at <https://api.nsw.gov.au/Product/Index/22>.
OAuth2 client credentials against `api.onegov.nsw.gov.au`; tokens are
short-lived so mint one per run. The all-prices endpoint returns every
prescribed fuel at 2,500+ NSW stations.

**NSW rental bond lodgements.** No key, no auth, CC-BY. Index page:
<https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data>.
Monthly `.xlsx`, record-level — one row per lodgement, columns Lodgement Date,
Postcode, Dwelling Type, Bedrooms, Weekly Rent (~25k rows, ~675 KB). Annual
compilations are the same rows concatenated, so take one or the other, never
both.

The filenames cannot be templated. Real examples, all of them lodgement files:
`rentalbond_lodgements_june_2026.xlsx`, `rental-bond-lodgement-data-july-2025.xlsx`,
`rentalbond_lodgements_september25.xlsx`, `RentalBond_Lodgements_December_2023.xlsx`,
`rentalbond_lodgements_june_2025_0.xlsx`. The containing directory does not match
the data month either — `/2024-05/` holds March, February and January 2024 plus
December 2023. So the collector discovers links from the index page and reads the
period back off the filename. Refunds and holdings are separate series published
on the same page; match on "lodge" to avoid them.

Run monthly with `auscpi collect nsw_rental_bonds`; capture the history to
January 2022 with `auscpi backfill-bonds` (~35 MB, one-time).

**ABS Data API.** `https://data.api.abs.gov.au/rest/`. No key, CC-BY.

Dataflow identifier **confirmed 2026-07-30: `ABS,CPI,2.0.0`** carries both
monthly and quarterly. `GET /rest/dataflow/ABS` lists four CPI flows — `CPI`
2.0.0, `CPI_M` 1.2.0, `CPI_Q` 1.0.0, `CPI_WEIGHTS` 1.0.0.

**Do not use `CPI_M`.** It is the retired monthly *indicator*: its last
observation is 2025-09, it has 39 index values rather than 166, and it is no
longer updated. Version 1.0.0 of it — the old placeholder in this repo — does not
exist at all and 404s with "Could not find Dataflow and/or DSD". A resolvable but
frozen dataflow is the more dangerous of the two failures, because the pipeline
stays green while the target series stops moving, so `abs_cpi.py` refuses any
slice whose newest observation is older than its staleness limit.

Key order is `MEASURE.INDEX.TSEST.REGION.FREQ`; an empty slot is a wildcard:

| Slice | Key | Series | Gzipped | Coverage |
|---|---|---|---|---|
| National monthly | `...50.M` | 1,191 | ~148 KB | 106 periods, 2017-09 → 2026-06 |
| National quarterly | `...50.Q` | 396 | ~172 KB | 312 periods, 1948-Q3 → 2026-Q2 |
| All regions monthly | `....M` | 8,055 | ~937 KB | six times the weight, not collected |

Useful ids: MEASURE `1` index numbers, `2` change on previous period, `3` change
on previous year; INDEX `10001` All groups CPI, `999902` Trimmed Mean,
`999901` All groups seasonally adjusted, `104122` excluding volatile items.
REGION `50` is Australia; `1`–`8` are the capitals.

**ABS CPI weights.** `ABS,CPI_WEIGHTS,1.0.0`, key `..50.Q` — dimension order is
`MEASURE.INDEX.REGION.FREQ`, with **no TSEST**, unlike the price dataflow. Whole
dataflow is ~31 KB gzipped. Reweighted annually; periods present are 2018-Q3
through **2024-Q4**, so a ~19-month-old weight set is normal, not stale.

Measure `1` is the percentage contribution to All groups — the basket share, and
the one to use. Measure `2` is a capital-city share that is uniformly 100 at the
national level; `3` is a points contribution on a different base.

**The trap: every hierarchy level is in there at once, and each sums to 100.**

| Level | Codes | Sums to |
|---|---|---|
| All groups | 1 | 100 |
| Group | 11 | 100 |
| Sub-group | 33 | 100 |
| Expenditure class | 87 | 100 |
| **Total if summed blindly** | **132** | **400** |

The level cannot be read off the code — `20001` Food is a *group*, `30002` Bread
and cereal products a *sub-group*, `126670` Insurance and financial services a
*group* again. It comes from the `CL_CPI_WEIGHTS_INDEX` codelist, which is
hierarchical (`40005` Bread → `30002` → `20001` → `10001`); depth from the root is
the level. The collector captures that codelist in the same snapshot, because a
weight is uninterpretable without the taxonomy that shipped with it, and
`weights_at()` refuses any selection that does not sum to 100.

Published weights at 2024-Q4, for the components this project models:

| Component | Code | Weight |
|---|---|---|
| Housing (group) | `20003` | 21.385% |
| Food and non-alcoholic beverages (group) | `20001` | 17.439% |
| New dwelling purchase | `97559` | 7.593% |
| **Rents** | `30014` | **6.613%** |
| **Automotive fuel** | `40081` | **3.347%** |

All 132 weight codes appear among the 166 `INDEX` codes in the monthly price
dataflow, so weights join the price panel with no orphans.

---

## Forecast-specific sources

A nowcast needs current prices. A forecast needs the things that determine
*future* prices. These are additional to everything above.

| Source | What | Why a forecast needs it | Access |
|---|---|---|---|
| Refined product futures (Singapore MOPS 95) | Forward path of petrol input cost | Retail petrol ≈ product price / FX + excise + GST + margin. The curve gives a market-implied fuel path. | Vendor, or a free proxy via Brent futures + crack spread |
| AUD/USD forwards | Forward FX path | Fuel and all tradables are priced in USD upstream | RBA statistical tables, market data |
| ABS Wage Price Index | Quarterly wages | Principal driver of services inflation at h≥3 | ABS Data API |
| ABS Import Price Index | Imported goods costs | Pass-through to tradables runs 2–4 quarters | ABS Data API |
| Indexed bond breakevens | Market-implied inflation | Both a benchmark and a signal | RBA / market data |
| RBA Statement on Monetary Policy | Official forecast path | The benchmark that actually matters | rba.gov.au, quarterly |
| Excise indexation calendar | Fuel and tobacco excise, indexed to CPI/AWOTE | Dates and magnitudes known in advance | ATO |

## Why the pre-determined components carry the forecast

Three parts of the basket are unusually knowable in advance:

**Rents.** The ABS measures rents on the stock of dwellings, not on new leases.
When market rents move, the measured index only follows as existing leases roll
over, which takes roughly a year. So today's asking rents constrain measured
rents six to twelve months out almost mechanically. The modelling problem is
estimating the roll-through distribution, not predicting rents. This is the one
place where forecast accuracy *improves* relative to a naive model as the
horizon lengthens, which is the opposite of everything else.

**Administered prices.** Announced in documents, with quantified effects and
effective dates, months ahead. Electricity determinations land in May for a
1 July start. The private health insurance premium round is announced around
February for 1 April. Tobacco excise indexation dates are statutory.

**Fuel.** Not pre-determined, but market-implied from the futures curve.

Together these are roughly a fifth of the basket and a much larger share of the
forecastable variance. Everything else is genuinely hard, and the honest position
is that the long tail gets a seasonal model with wide intervals.
