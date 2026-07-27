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
| SQM Research | Weekly asking rents by postcode | HTML | Asking rents lead *measured* rents, which the ABS collects on the stock, not on new leases. The lag structure is the modelling problem. |
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

## Access notes

**NSW FuelCheck.** Free registration at <https://api.nsw.gov.au/Product/Index/22>.
OAuth2 client credentials against `api.onegov.nsw.gov.au`; tokens are
short-lived so mint one per run. The all-prices endpoint returns every
prescribed fuel at 2,500+ NSW stations.

**ABS Data API.** `https://data.api.abs.gov.au/rest/`. Confirm the dataflow
identifier for the Monthly CPI publication against
`https://data.api.abs.gov.au/rest/dataflow` before writing the parser — the
identifier in `collectors/abs_cpi.py` is a placeholder and the collector is
disabled until it is verified.

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
