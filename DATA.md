# Data

This file explains **Blue Vector's** suggested data sources.

> **`DATA.md` is tracked in git. The `data/` folder is not.** Clone the repo, then populate `data/` locally. Blue Vector is designed to avoid raw AIS bulk data entirely, but the rule still holds: never commit data.

## Suggested sources (starting point)

This table is the source register, ordered by necessity. The first seven columns are pre-filled. **Members fill in the blank columns for their own rows in Week 1.** `n/a` cells are not used. Background on each source is in [Source notes](#source-notes) below.

| Priority | Source | Owner (subteam) | Origin / URL | Access method | License | Sensitivity | Exact fields confirmed | Coverage (regions, years, vessel types) | Update frequency and lag | Quota / API key | Check result (pass / fail) | Pull request | Notes (members) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 · Essential | IMF PortWatch | A | https://portwatch.imf.org/ | Free download / API | IMF data terms: free with attribution; commercial reuse needs permission (verify PortWatch page-specific terms) | None |  |  |  |  |  |  |  |
| 2 · Essential | UN Comtrade | B | https://comtrade.un.org/ | REST API (free API key) | Attribution — free tier | None |  |  |  |  |  |  |  |
| 3 · Essential | EIA Open Data (Brent, WTI) | C | https://www.eia.gov/opendata/ | REST API (free API key) | US government data, free to use (confirm terms) | None |  |  |  |  |  |  |  |
| 4 · Essential | searoute | D | https://pypi.org/project/searoute/ | `pip install` | TBD (check the package and its bundled network data) | None |  |  |  |  |  |  |  |
| 5 · Recommended | Global Fishing Watch | D | https://globalfishingwatch.org/data-download/ (API: https://globalfishingwatch.org/our-apis/) | Registered download / API token | CC-BY-NC (research) | Low (vessel identifiers only) |  |  |  |  |  |  |  |
| 6 · Recommended | NGA World Port Index | A | https://msi.nga.mil/Publications/WPI | Public download | US government, public (confirm) | None |  |  |  |  |  |  |  |
| 7 · Optional | World Bank Commodity Prices | C | https://www.worldbank.org/en/research/commodity-markets | Monthly XLS | Public (confirm terms) | None |  |  |  |  |  |  |  |
| 8 · Optional | Natural Earth (coastlines, borders) | Unassigned | https://www.naturalearthdata.com/ | Public download | Public domain | None |  |  |  |  |  |  |  |
| 9 · Optional | JODI-Oil | B | https://www.jodidata.org/ | Public download | TBD | None |  |  |  |  |  |  |  |
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
| Average vessel size | Estimated trade volume (tons) ÷ transit count, by vessel type. |
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

## Local layout convention

```
data/
├── raw/          # PortWatch, Comtrade, EIA pulls and cached API responses as downloaded — NEVER commit
├── interim/      # port rankings, cleaned time series, GFW voyage tables, OD intermediates
└── processed/    # graph node + edge tables, OD matrices, scenario outputs (Parquet)
```

Because `data/` isn't in git, the **pipeline that fetches and builds these folders** is what must be committed and reproducible — not the data itself. Store tables as Parquet. Request only the ports, chokepoints, and date range you need (roughly 2019 to present), and cache every API response so reruns don't spend your quota.