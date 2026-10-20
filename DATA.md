# Data

This file explains **Blue Vector's** suggested data sources.

> **`DATA.md` is tracked in git. The `data/` folder is not.** Clone the repo, then populate `data/` locally. Blue Vector is designed to avoid raw AIS bulk data entirely, but the rule still holds: never commit data.

## Suggested sources (starting point)

This table is the source register, ordered by necessity. The first seven columns are pre-filled. **Members fill in the blank columns for their own rows in Week 1.** `n/a` cells are not used. Background on each source is in [Source notes](#source-notes) below.

| Priority | Source | Owner (subteam) | Origin / URL | Access method | License | Sensitivity | Exact fields confirmed | Coverage (regions, years, vessel types) | Update frequency and lag | Quota / API key | Check result (pass / fail) | Pull request | Notes (members) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 · Essential | IMF PortWatch | A | https://portwatch.imf.org/ | Free download / API | IMF data terms: free with attribution; commercial reuse needs permission (verify PortWatch page-specific terms) | None | Ports: `date`, `portid`, `portname`, `country`, `ISO3`, `portcalls_container`, `portcalls_dry_bulk`, `portcalls_general_cargo`, `portcalls_roro`, `portcalls_tanker`, `import_container`, `import_dry_bulk`, `import_general_cargo`, `import_roro`, `import_tanker`, `export_container`, `export_dry_bulk`, `export_general_cargo`, `export_roro`, `export_tanker`. Chokepoints: `date`, `portid`, `portname`, `n_container`, `n_dry_bulk`, `n_general_cargo`, `n_roro`, `n_tanker`, `n_total`, `capacity_tanker`, `capacity` | Global PortWatch coverage; daily observations since 2019 (current service also includes 2026); container, dry bulk, general cargo, Ro-Ro, tankers; coverage varies by port and AIS reporting | Daily observations; published/updated approximately weekly; recent lag varies, so record last available date when pulled | No key for checked public ArcGIS REST layer; max 1,000 rows per query page; rate limits not confirmed | pass: Suez Canal `n_tanker` = 20 and Cape of Good Hope `n_tanker` = 15 on 2026-10-11 in daily chokepoint query | TBD: enter PR number after opening | Valerie’s original port fields retained and extended to export/chokepoint fields. `capacity_tanker` estimates combined cargo-carrying capacity in tonnes, not maximum throughput. Imports/exports are modeled estimates, not customs measurements; tanker is not crude-only. |
| 2 · Essential | UN Comtrade | B | https://comtrade.un.org/ | REST API (free API key) or `pip install comtradeapicall` | Attribution — free tier | None | `reporterCode`; `reporterDesc`; `partnerCode`; `partnerDesc`; `partner2Code`; `partner2Desc`; `flowCode`; `period`; `clCode`; `cmdCode`; `netWgt`; `primaryValue`. Additional sea-only filter: `motCode`. | Global (~200 countries); 1962-current; NA; gap: Not all countries report their trade statistics every year, and so reporting for a country may be incomplete across years. | Continuously processed as countries submit monthly and annual data; no fixed refresh schedule.  | Yes and needs to be regenerated periodically; 100,000 records/request; 500 calls/day; 1 call/second  | PASS (with limitations): Contains monthly or annual level crude oil trade records with required reporter, partner, trade flow, net weight, trade value, and sea-transport fields; The field for reporter may not accurately represent all countries engaged in imports & exports, and were missing large importers/exporters like the U.S., China, and Saudi Arabia that were recorded in the field for partners. | #[TBD]: PR per subteam | Limitations: In the 2019–2025 sea-transport dataset, only 61 countries appear as reporters, compared with 150 unique trading partners. Thus, reporting-country coverage does not represent all countries engaged in crude oil trade. UN notes that imports reported by one country do not coincide with exports reported by its trading partner. All trades may not be reported by countries due to confidentiality; `comtradeapicall` python package is available for convinient API calls; License: UN Comtrade data are copywrited by the UN, provided for internal use only, and may not be re-disseminated in any form without UNSD permission. Formula: Barrel weights `ok`; Oil demand: `[TBD]: Based on PortWatch data`|
| 3 · Essential | EIA Open Data (Brent, WTI) | C | https://www.eia.gov/opendata/ | REST API (free API key) | US government data, free to use (confirm terms) | None | Date; Europe Brent Spot Price FOB (Dollars per Barrel); Sourcekey: RBRTE ; Chokepoints: Location; 2020; 2021; 2022; 2023; 2024; 1H25; unit: million barrels per day (b/d) | Europe Brent benchmark; daily observations from May 20, 1987 through October 6, 2026; not vessel-specific. Chokepoint estimates cover 2020–2024 annually and the first half of 2025; Panama Canal figures use fiscal years. | Weekly release; the October 7 release’s newest observation was October 6 (one-day reporting lag); next release scheduled October 15. | Free API key required for API requests; no key required for XLS or bulk downloads. | Pass. 1,966 daily observations from 2019-01-02 through 2026-10-06; latest observation is recent. Eleven 5-day gaps fall around Easter/Christmas periods; no longer gaps found. Chokepoint check: Pass. EIA’s Table 1 lists Strait of Hormuz 2024 oil flows as 20.7 million barrels per day; this matches the table.| #3 | EIA’s XLS workbook lists Thomson Reuters as the underlying source. ; Barrels from tons — ok: UN Statistics Division gives 7.32 barrels per metric tonne for average crude oil; 7.3 is a rounded approximation. ; Weekly Brent — ok: arithmetic mean of observed daily Brent prices in each calendar week; dates with no observation are excluded, not treated as zero. ; Crude share — needs change: EIA’s chokepoint flow includes petroleum products, so dividing it by tanker volume does not give a crude-only share; flag for PM review. |
| 4 · Essential | searoute | D | https://pypi.org/project/searoute/ | `pip install` | Apache-2.0 (package, per the installed v1.6.0 metadata; subteam D listed MIT, so please confirm). Network data credited to Eurostat's Searoute: TBD: confirm its license | None | Function `searoute.searoute(origin, destination, units, speed_knot, restrictions, return_passages)`. Inputs are `[lon, lat]` pairs. Output is a GeoJSON Feature: `geometry` (a `LineString`) and properties `length`, `units`, `duration_hours` (and `traversed_passages` when `return_passages=True`) | Global shortest route over a built-in marine network (credited to Eurostat's Searoute library), between any two `[lon, lat]` points; not tied to vessel types. Passages that can be avoided: `babalmandab`, `bosporus`, `gibraltar`, `suez`, `panama`, `ormuz`, `northwest`, `malacca`, `sunda`, `chili`, `south_africa`. Built for visualization, not ship routing | Static package routing network; updated via library releases (v1.6.0 released 2026-05-17) | No key and no quota: runs locally (`pip install searoute`) | pass. (1) Subteam D computed distances and route geometry for Rotterdam, Singapore, and Houston routes (distances to be added). (2) Independent test on v1.6.0: Shanghai to Rotterdam via Suez 10,514 nm vs. published ~10,525 and ~10,600 (within ~1%); with Suez closed (`restrictions=['northwest','suez']`) 13,479 nm vs. published ~13,800 to 13,843 (about 2.5% lower). Also Rotterdam to Singapore 8,365 nm, Houston to Rotterdam 5,031 nm, Ras Tanura to Ningbo 5,844 nm (not compared with published values) | TBD: PR not open yet | Default `speed_knot` is 24, so `duration_hours` assumes 24 knots; pass `speed_knot=12` to `14` to match our minimum-transit-days assumption. Suez can be closed with `restrictions`, which the scenario engine needs. Published comparison values: Ostrum/SEA-DISTANCES and a Texas A&M (Rodrigue) route map. Subteam D to add: the distances from their three tests, and formula checks for minimum transit days, observed transit days, and delay. |
| 5 · Recommended | Global Fishing Watch | D | https://globalfishingwatch.org/data-download/ (API: https://globalfishingwatch.org/our-apis/) | Registered download / API token | CC-BY-NC (research) | Low (vessel identifiers only) | Events API v3 port-visit events (`public-global-port-visits-events:latest`): `id`, `type` (`port_visit`), `start`, `end`, `position` (`lat`, `lon`), `regions`, `vessel` (includes vessel `id`), `confidence` (levels 1–4, port visits only), plus port and anchorage entries. Subteam D reported these fields from their pull: `mmsi`, `imo`, `shipname`, `vessel_class`, `port_name`, `port_country`. TBD: copy exact names from a real API response (the docs describe nested `vessel` and `position` objects) | Global port visits and events for all vessel types (tankers, cargo, fishing, etc.); AIS-based. Tanker pull tested by subteam D (details TBD). AIS-gap events are a separate dataset (`public-global-gaps-events:latest`), not a field of port visits. Date range: TBD | Reported by subteam D as daily ingestion with a standard AIS processing lag. TBD: confirm on GFW's pages and record the actual lag | Free registration; API token required (GFW API Portal); non-commercial license. Rate limits: TBD: read GFW's API Terms of Use and record them | pass (per subteam D): port-visit records and timestamps retrieved for named tankers. TBD: add which tankers, how many visits, and a few lines of output | TBD: PR not open yet | Subteam D reports verifying tanker port visits and AIS gap fields. `confidence` levels run 1–4 for port visits; the gfwr examples use 3–4 to reduce false visits. MMSI/IMO and ship class may come from the Vessels API, so copy exact names from a real response. Subteam D to add: tankers tested, number of visits, sample output. |
| 6 · Recommended | NGA World Port Index | A | https://msi.nga.mil/Publications/WPI | Public download | US government, public (confirm) | None | `World Port Index Number`, `Main Port Name`, `Alternate Port Name`, `UN/LOCODE`, `Country Code`, `Latitude`, `Longitude`, `Channel Depth (m)`, and maximum vessel length, beam and draft in meters (modern NGA CSV; headings vary by edition and the hosted feature service uses snake_case field names) | Worldwide port reference data, not a traffic time series; some ports appear as terminal-level records (for example Singapore as Keppel - (East Singapore)); edition year not yet verified | Periodically revised reference dataset; current edition date and lag TBD: not yet confirmed on the NGA page | No API key for downloads; the hosted copy tested needed no key; download quota TBD | pass with caveat: Rotterdam (WPI 31140, NL RTM) and Houston (WPI 9240, US HOU) found; Singapore found only as terminal records, so PortWatch-to-WPI matching needs location and name normalization | TBD: enter PR number after opening | Test used a hosted WPI copy, not a direct NGA download; verify the NGA edition and the completeness of depth and vessel-dimension fields before production use. |
| 7 · Optional | World Bank Commodity Prices | C | https://www.worldbank.org/en/research/commodity-markets | Monthly XLS | CC BY; attribute the World Bank | None | File CMO-Historical-Data-Monthly.xlsx, sheet "Monthly Prices"; date format 2026M09; columns "Crude oil, average", "Crude oil, Brent", "Crude oil, Dubai", "Crude oil, WTI"; units ($/bbl) | Global monthly nominal USD prices, 1960 to present; Brent, Dubai and WTI in $/bbl; early Brent rows are blank, not relevant for 2019+; not vessel-specific | Monthly, first few business days of the month; Oct 2026 edition updated Oct 2, 2026, newest row 2026M09 (monthly average); next update Nov 3, 2026 | No key, no quota; direct XLS download | Pass: Brent, Dubai and WTI monthly in $/bbl are present; newest row 2026M09 has Brent 116.8, Dubai 93.3, WTI 96.9 | #3 | Headers checked against the downloaded XLS. Optional cross-check vs EIA monthly Brent not run. Listed as Optional in this table but Required in the first-deliverable doc, so filled in. |
| 8 · Optional | Natural Earth (coastlines, borders) | Unassigned | https://www.naturalearthdata.com/ | Public download | Public domain | None |  |  |  |  |  |  |  |
| 9 · Optional | JODI-Oil | B | https://www.jodidata.org/ | Public download | TBD: Terms not found on site | None | API: `country`, `product`, `flow`, `unit`, `date` (yyyy-MM), `quantity`, `quality`; CSV download: `REF_AREA`, `ENERGY_PRODUCT`, `FLOW_BREAKDOWN`, `UNIT_MEASURE`, `TIME_PERIOD`, `OBS_VALUE`, `ASSESSMENT_CODE`; codes: `CRUDEOIL`, `TOTEXPSB` (exports), `TOTIMPSB` (imports), `KBD` | 2019-01 to 2025-12, all months, ~118 countries/yr; only 55 report non-zero exports (AE, IN, IR, LY, JP, KR zero throughout; RU, IQ, AE, IN absent 2025-02 to 2025-12) | TBD: refresh schedule not stated in the API guide; data is monthly; newest month is 2026-07 in the CSV download (about 2–3 months behind) | Free JODI website account required (no separate key). Token valid for 1 hour, 100 requests per minute per user. Queries over about 6 months timed out, so pull one month per request | pass: API returns monthly crude exports and imports (KBD) for 2019-01 to 2025-12; SA, US, CN, NO complete. Caveat: RU, IQ, AE, IN have gaps | TBD: PR not open yet | API returns `0`, not `-`, for unreported data, so zeros are not real exports (only 55 of 118 countries report non-zero). `CONVBBL` is a barrels-per-kt factor (SA 7.323 bbl/t), not a flow. |
| 10 · Redundant | FRED (Brent mirror) | — | https://fred.stlouisfed.org/ | REST API (free API key) | Free (confirm series terms) | None | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| 11 · Dropped | MarineCadastre (US AIS) | — | https://marinecadastre.gov/ais/ | Free bulk download by year/month | Public | None | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

### Priority guide

- **Essential (1–4):** the minimum to build the graph, flow model, validation, and price model.
- **Recommended (5–6):** deliver the delay, transit-time, and port-matching attributes in the full scope.
- **Optional (7–9):** convenience or cross-checks; the first sources to cut under time pressure.
- **Redundant and Dropped:** kept in the register so the decision is auditable.

In Week 1, vet Essential sources first. PortWatch and Comtrade are single points of failure: if either fails its checks, raise it to the PM immediately.

### How to fill in the table

1. Find your rows using the **Owner** column (see [`TEAMS.md`](TEAMS.md)).
2. Fill every blank cell in those rows, using the column guide below. If a value is unknown, write `TBD: <reason>`.
3. Update the table through a pull request (a git merge request) into the integration branch, following [`CONTRIBUTING.md`](CONTRIBUTING.md). Put the pull request number in the **Pull request** column.
4. Never commit data. Commit `DATA.md` and your scripts only.

### Column guide

| Column | What to write | Format example (placeholders) |
|---|---|---|
| Exact fields confirmed | Exact field names from the source's data dictionary for the metrics we need (see [Exact metrics](#exact-metrics-to-pull-from-each-source)) | `[exact_field_name]; [exact_field_name]` |
| Coverage | Regions, years, vessel types included, and known gaps | `[regions]; [years]; [vessel types]; gap: [describe]` |
| Update frequency and lag | How often the source refreshes and how stale the latest value is | `[how often]; about [N] days behind` |
| Quota / API key | Registration, key, and rate limits | `[key required?]; [N] requests per day`, or `TBD: waiting on token approval` |
| Check result (pass / fail) | The result of your Week 1 check, plus one line of why | `pass: [one line why]` |
| Pull request | Pull request number | `#[number]` |
| Notes (members) | Anything surprising: gaps, quirks, license caveats | Free text |

### Source notes

- **IMF PortWatch** (Essential): **Backbone of the project.** AIS-derived daily port calls and import/export volume estimates by vessel type, plus daily chokepoint transit calls and trade estimates (~28 chokepoints; verify the current list, including Suez, Bab-el-Mandeb, and Cape of Good Hope). Updated weekly (verify). Volumes are estimates, not measurements. **Without it there is no project.**
- **UN Comtrade** (Essential): Bilateral crude oil flows (HS 2709) by country. Anchors the origin-destination demand matrix. Months of lag; mirror mismatches between exporter and importer reports are common. Without it, the OD matrix falls back to PortWatch port volumes alone and loses its country-to-country structure.
- **EIA Open Data (Brent, WTI)** (Essential): Daily Brent price: the target of the oil price model. Also publishes chokepoint oil-flow figures used to calibrate the crude share. Replaces FRED as the single Brent source.
- **searoute** (Essential): Sea distances and route geometry between nodes. Spot-check against published port-to-port distances. Without it the team must build its own sea routing.
- **Global Fishing Watch** (Recommended): Port-visit events for all vessel types, including tankers, with vessel identity. Used for ship-level tracing, port-to-port transit times, and delay validation (a **sample**, not the full fleet). Registration required; check API quotas. Non-commercial: attribute properly and don't ship it in a commercial product. Without it, transit days and delay become `modeled` only and one validation channel is lost; the core model still runs.
- **NGA World Port Index** (Recommended): Port locations and attributes to name and place port nodes, and to match ports across sources. Check which fields (e.g., channel depth) are available. If PortWatch already carries port locations, the main loss without it is the vessel-size-limit attribute.
- **World Bank Commodity Prices** (Optional): Monthly crude price history (Brent, Dubai, WTI) as a cross-check on Brent. Adds little once daily EIA data is in place.
- **Natural Earth (coastlines, borders)** (Optional): Custom base layers for the map. Kepler.gl and PyDeck already include basemaps, so adopt only if custom layers are wanted.
- **JODI-Oil** (Optional): Second source to cross-check Comtrade oil trade totals. Adopt only if Comtrade proves gappy for key oil exporters. Verify license before adopting.
- **FRED (Brent mirror)** (Redundant): Mirror of the EIA daily Brent series. **Do not adopt; use EIA.**
- **MarineCadastre (US AIS)** (Dropped): **Dropped.** US waters only and very large; nothing in the core plan needs it. Reconsider only as an optional extension for detailed validation around US Gulf Coast crude export ports.

## How to think about using each source

- **PortWatch is the backbone, so there is no raw AIS to process.** It already aggregates AIS into daily counts. Chokepoint transit counts are the most observation-like numbers in the project; volume figures are model estimates.
- **"Tanker" is not "crude."** PortWatch tanker volumes include refined products and chemicals. Document the crude-share assumption you use and keep it explicit and adjustable.
- **Observed vs. modeled.** Real data exists only at chokepoints, ports, and a sample of tracked ships. Every dynamic attribute carries a `source` field (`observed` or `modeled`), and the dashboard shows it. Never present modeled values as measurements.
- **Spoofing / noise.** Ship-level data from Global Fishing Watch has gaps, spoofed signals, and "dark" tankers that switch AIS off. Filter port visits by confidence, and don't chase every anomaly. Test it on a set of known tankers before relying on it.
- **Comtrade lag and mismatches.** Trade data lags by months, and exporter and importer reports of the same flow often disagree. Any real-time claim is unsupportable, so frame the economic model as counterfactual, not forecast.
- **License.** Global Fishing Watch data is CC-BY-NC: attribute properly and don't ship it in a commercial product. PortWatch requires attribution and permission for commercial reuse.

Choosing and vetting a source is a **judgment call** — surface it to the PM rather than deciding a major data direction alone.

## Source vetting checklist

Run this before writing any pipeline for a new source, and record the answers in the register above:

- [ ] **License** — what is allowed (research, redistribution, commercial)? Any attribution text required?
- [ ] **Sensitivity** — any personal data, restricted data, or permission issues?
- [ ] **Access** — registration, API key, rate limits, and quotas?
- [ ] **Coverage** — which regions, vessel types, years, and ports are included? Any known gaps?
- [ ] **Lag and update frequency** — how stale can the latest value be?
- [ ] **Quality check** — one small test against a known answer (see the Week 1 checks in [`DELIVERABLES.md`](DELIVERABLES.md)).
- [ ] **Reproducible** — a script in the repo can fetch it from scratch.

## What each source feeds

| Attribute / output | Primary source | Real-world check |
|---|---|---|
| Port ranking and node volumes | PortWatch | Cross-check against an independent published port ranking (TBD) |
| Chokepoint transit counts by vessel type | PortWatch | Compare with published canal authority statistics and Cape of Good Hope counts |
| Estimated oil volume | PortWatch trade estimates × crude-share assumption | EIA published chokepoint oil-flow figures; Comtrade / JODI-Oil at country level |
| Origin-destination (OD) oil matrix | Comtrade (HS 2709) + PortWatch port totals | Exporter vs. importer mirror check; JODI-Oil / EIA |
| Transit days and delay | Global Fishing Watch port-visit sequences (sample) | Known-tanker test set; compare against a distance ÷ speed baseline |
| Length, geometry, minimum transit days | searoute, NGA World Port Index | Spot-check against published port-to-port distances |
| Brent price (target variable) | EIA | Held-out backtest versus a random-walk benchmark |
| Shipping cost per barrel | Computed from network output + documented tanker daily-cost assumption | EIA / IMF commentary on tanker rate changes (no free freight index available) |

## Exact metrics to pull from each source

Field names below are descriptive. Confirm the exact names against each source's data dictionary during Week 1, and record them in the register.

| Source | Metrics needed | Used for |
|---|---|---|
| PortWatch (ports) | Date; port ID, name, country; port calls by vessel type (tanker, container, dry bulk, general cargo, Ro-Ro); estimated import tons and export tons by vessel type | Port ranking, node attributes, OD matrix totals |
| PortWatch (chokepoints) | Date; chokepoint ID, name, location; transit calls by vessel type (all ships, including tankers); estimated trade volume by vessel type if provided. Priority chokepoints: Suez, Bab-el-Mandeb, Cape of Good Hope, Gibraltar, Hormuz, Malacca, Panama (confirm availability) | Edge transit counts and volumes, validation targets |
| Global Fishing Watch | Vessel ID (MMSI / IMO) and vessel class; port-visit start and end timestamps; port visited (name, country, coordinates); visit confidence level; optional AIS-gap events | Port-to-port transit days, delay, sampled transit counts |
| UN Comtrade | Reporter, partner, flow direction, period (year / month), commodity HS 2709, net weight (kg), trade value (USD). Pull both exporter-reported and importer-reported flows | OD matrix, mirror-mismatch check |
| EIA | Daily Brent spot price (USD per barrel); EIA published chokepoint oil flows (million barrels per day) | Price target, oil-volume cross-check |
| World Bank (optional) | Monthly Brent, Dubai, and WTI prices | Price calibration cross-check |
| NGA World Port Index | Port name, UN/LOCODE if present, latitude / longitude, country, channel depth and max vessel size if available | Port naming, matching, size limits |
| searoute | Sea distance (nm) and route geometry between each node pair | Edge length and geometry |
| Natural Earth (optional) | Coastline and border layers | Map base layers |
| JODI-Oil (optional) | Monthly crude exports and imports by country | Comtrade cross-check |

## Derived metrics and how they are computed

Every assumption listed here must be written down with its value and source in Week 1, and kept adjustable in the code.

| Derived metric | How it is computed |
|---|---|
| Port selection | Per port, sum estimated import + export tons over a reference window (e.g., 2019–2025), for all vessel types and for tankers only. Take the top ~60 by total and the top ~40 by tanker tons, de-duplicate, then fill to 100 with the next best by combined rank. Coverage = selected tons ÷ global PortWatch tons, reported for total and tanker volume and by region. |
| Port crosswalk | Match PortWatch, NGA World Port Index, and Global Fishing Watch ports by UN/LOCODE where available; otherwise by nearest coordinates within a set radius plus name similarity. Manually review the final 100. |
| Minimum transit days | Length (nm) ÷ (assumed speed in knots × 24). Default tanker service speed is a documented, adjustable assumption (e.g., 12–14 knots). |
| Capacity proxy | A high percentile (e.g., 95th–99th) of weekly transit count or volume for an edge or node in a pre-disruption baseline window. Percentile and window are documented. |
| Utilization | Weekly count (or volume) ÷ capacity proxy. |
| Anomaly vs. seasonal baseline | z-score: (weekly value − mean for the same week-of-year across baseline years) ÷ standard deviation for that week-of-year. |
| Average vessel size | **Needs change (interpretation):** for chokepoint tanker data use `capacity_tanker` (estimated aggregate carrying-capacity tonnes) ÷ `n_tanker` (transit count), giving **estimated carrying-capacity tonnes per transit**, not measured vessel dimensions or actual loaded cargo. For other vessel classes, use the matching `capacity_*` and `n_*` fields; if the count is zero the ratio is undefined. |
| Estimated oil volume | PortWatch tanker trade estimate × crude share, converted to barrels (about 7.3 barrels per tonne; documented assumption). The share is calibrated per chokepoint against EIA published oil flows where available, with a documented default elsewhere. Tanker volumes include products and chemicals, so the share is never assumed to be 1. |
| Observed route transit days | From Global Fishing Watch: start of port visit B minus end of port visit A for consecutive visits by the same vessel. Aggregated by port pair and week using the median across sampled vessels. |
| Delay | Median voyage days for the week minus median voyage days for the same port pair in the pre-disruption baseline window. Also reported against minimum transit days. Baseline-relative because ships often slow-steam. |
| OD oil matrix | Comtrade country-to-country crude flows (kg converted to barrels). Each exporter's flow is split across its ports by share of the country's PortWatch tanker export tons, and each importer's by share of tanker import tons. Iterative proportional fitting then forces port totals to match PortWatch marginals (scaled by the crude share). |
| Transit count between any two nodes | Observed sample from Global Fishing Watch voyages; the full matrix comes from assigning the OD matrix over the graph. Tagged `observed` or `modeled`. |
| Scenario tanker transits (per edge) | Scenario oil volume (barrels) ÷ average cargo per tanker transit on that edge. Average cargo comes from baseline data (observed volume ÷ observed count at chokepoints; a documented assumption elsewhere) and respects vessel-size limits, such as laden very large crude carriers not transiting Suez. |
| Edge delay (congestion) | Free-flow time × (1 + α × (flow ÷ capacity)^β) − free-flow time, a standard congestion curve. α and β are calibrated on the 2023–24 Red Sea observations. Route delay is the sum of edge delays. |
| Scenario utilization and anomaly | The utilization and anomaly formulas above, applied to scenario flow. Every dynamic tanker metric is therefore a model output on every edge, for baseline and scenario. v1 models tankers only; other vessel types are observed at chokepoints but not predicted. |
| Shipping cost per barrel | (extra voyage days × assumed tanker daily cost ÷ barrels per cargo) + change in canal toll per barrel. Daily cost and cargo size are documented as low / base / high assumptions from public sources. |
| Price-model indices | (1) Suez tanker share = Suez ÷ (Suez + Cape of Good Hope) tanker transits. (2) Barrel-weighted average voyage days = Σ(flow × days) ÷ Σ flow. (3) Rerouted ton-miles = Σ over edges (change in flow × length). Defined identically for history and for scenarios. Edge-level GNN predictions are aggregated into these indices (plus low-dimensional GNN summaries) as price-model features. |
| GNN training set | Two labeled parts. **Simulated:** for each edge closed or throttled, the flow model's per-edge metrics for baseline and scenario (tagged `simulated`). **Real:** PortWatch weekly series at chokepoint and port nodes from 2019 to present, including the 2023–24 Red Sea diversions (tagged `observed`). Time-based splits; Ever Given, Panama, and some chokepoints are held out for evaluation. |
| Brent target | Weekly average of daily Brent, modeled as log return or level. Benchmark: random walk. |

## Ports & traffic (Subteam A) — Deliverable 1 source and formula checks (2026-10-15)

This section documents only Subteam A's work and does not modify the other subteams' source rows. Checks were carried out against publicly accessible ArcGIS services on 2026-10-15. The examples below are real source records where noted; the arithmetic-only illustrations are **not** source measurements.

### Source check 1: IMF PortWatch — pass

- The Daily Ports Data service exposes `date`, `portid`, `portname`, `country`, port-call counts, and separate `import_*` and `export_*` fields for container, dry bulk, general cargo, Ro-Ro and tanker traffic. The data are daily observations; trade tonnages are estimates.
- The Daily Chokepoints Data service exposes `date`, `portid`, `portname`, `n_tanker` (tanker transit count) and other vessel-class counts. A direct date-and-name query returned **Suez Canal: 20 tanker transits on 2026-10-11** and **Cape of Good Hope: 15 tanker transits on 2026-10-11**. This is a valid field-and-sample check, not a historical trend analysis.
- Each public ArcGIS layer advertises a maximum query page size of **1,000 records**; full downloads require pagination. No API key was needed for these checks, but production quotas remain to be verified.
- The `capacity_tanker` chokepoint field is **estimated carrying-capacity tonnage of transiting tankers**, not a physically available weekly number-of-vessels capacity.

Sources: [PortWatch ports schema](https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Ports_Data/FeatureServer/0); [PortWatch chokepoints schema and query](https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0).

### Source check 2: NGA World Port Index — pass (with matching caveat)

- The WPI reference data contain port names, identifiers, country information, location, and physical characteristics. The modern CSV uses column headings such as `World Port Index Number`, `Main Port Name`, `UN/LOCODE`, `Latitude`, `Longitude`, `Channel Depth (m)`, and maximum vessel dimensions in meters; the hosted feature service exposes snake_case fields and geometric coordinates.
- A hosted WPI query returned **Rotterdam (WPI 31140; UN/LOCODE NL RTM)** and **Houston (WPI 9240; US HOU)**. **Singapore** appears in a terminal-level record **Keppel - (East Singapore) (WPI 50000; SG KEP)**. This confirms presence but also shows why PortWatch-to-WPI matching needs location/name normalization rather than name equality alone.
- The test used a **hosted WPI copy**, not a direct download from NGA. The NGA file edition and completeness of individual dimensions must still be verified before production use.

Sources: [Official NGA WPI](https://msi.nga.mil/Publications/WPI); [hosted modern WPI schema](https://arcgis.netl.doe.gov/server/rest/services/Hosted/CCS_EJ_SJ_Data/FeatureServer/17).

### Formula review (five required)

| Formula | Result | Check and required fields |
|---|---|---|
| Port ranking | **ok** | Group by `portid`; sum all five ship-type `import_* + export_*` tonnage fields across 2019–2025 for overall rank, and `import_tanker + export_tanker` for tanker-only rank. Combine the top ~60 overall and ~40 tanker ports, deduplicate and fill to ~100. Required fields exist in the PortWatch ports schema. The 100-port ranking itself has not yet been computed. |
| Capacity proxy | **ok** | Sum daily `n_tanker` per chokepoint into weekly transit counts, then use the 95th–99th percentile of weekly counts in a documented pre-disruption baseline (e.g. Jan 2019–Oct 2023). Do not confuse `capacity_tanker` (tonnes) with the derived count-per-week capacity proxy. No full historical percentile has yet been calculated. |
| Utilization | **ok** | `weekly tanker count / historical weekly capacity proxy`; illustrative arithmetic: 80 / 100 = 0.80 (dimensionless). It can exceed 1 because a historical percentile is not a physical maximum. |
| Anomaly | **ok** | `z = (weekly count - baseline mean for that week of year) / baseline standard deviation for that week of year`; illustrative arithmetic: (80 - 100) / 10 = -2.0. If baseline standard deviation is zero or insufficient past weeks exist, mark as undefined; do not divide by zero. |
| Average vessel size | **needs change: label as an estimated carrying-capacity proxy, not observed vessel size or loaded cargo** | For chokepoints, `capacity_tanker / n_tanker` gives estimated tanker carrying-capacity tonnes per transit. Illustrative arithmetic: 240,000 t / 3 = 80,000 t/transit. Actual loaded tonnes and ship dimensions cannot be inferred from that ratio alone; if `n_tanker` is zero, mark undefined. This interpretation is reflected in the corrected Derived metrics row below. |

**Limitations:** Formula test numbers above are illustrative, not observed values. Source spot checks passed, but full 2019–2025 rankings, historical capacity percentiles, update-lag measurements, and complete PortWatch-to-WPI matching have not been performed. Confirm the official WPI edition and source-specific reuse terms before treating these sources as production inputs.

## Local layout convention

```
data/
├── raw/          # PortWatch, Comtrade, EIA pulls and cached API responses as downloaded — NEVER commit
├── interim/      # port rankings, cleaned time series, GFW voyage tables, OD intermediates
└── processed/    # graph node + edge tables, OD matrices, scenario outputs (Parquet)
```

Because `data/` isn't in git, the **pipeline that fetches and builds these folders** is what must be committed and reproducible — not the data itself. Store tables as Parquet. Request only the ports, chokepoints, and date range you need (roughly 2019 to present), and cache every API response so reruns don't spend your quota.
