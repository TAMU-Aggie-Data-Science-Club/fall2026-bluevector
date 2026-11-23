# Blue Vector — Technical Specification (v1)

**Status:** audited 22 November 2026. This specification retains historical design/derivation sections; current GNN/price methods and results are in [MODEL_METHODOLOGY.md](MODEL_METHODOLOGY.md), and the [Hormuz readiness audit](MODEL_METHODOLOGY.md#15-methodology-audit-and-hormuz-readiness) takes precedence over presentation ambitions. A named source check or historical derivation is not a new validation of current outputs.

**How to read the status labels**

| Label | Meaning |
|---|---|
| **Built** | Code exists, has been run, and its output is in `config/` or `data/`. |
| **Planned** | The contract and method are fixed in `deliverables/DELIVERABLE5.md`; code is not written yet. |
| **Contract** | A table schema or file other parts depend on. Changing it breaks downstream work. |

**Three rules the whole project follows**

1. Every dynamic value says where it came from: `observed`, `modeled`, `simulated` or `gnn`.
2. One time step everywhere: one week. daily PortWatch inputs are aggregated to weekly chokepoint counts, so the model and
   the data it is scored against use the same clock.
3. Never commit `data/`. Commit `config/` and scripts only.

---

## 0. The system in one page

**The question.** If a canal is closed or throttled, where does crude oil go, how much cannot move,
how much longer does the trip take, and what does that cost per barrel?

**The chain.**

```
PortWatch ─┐
Comtrade ──┼→ ports → graph → OD matrix → flow model → validation
EIA ───────┤        (142 nodes)  (5,380 pairs)  (Dijkstra+BPR)      │
searoute ──┘                                                       ├→ GNN scenarios → GNN
                                                                   └→ price model → dashboard
```

| # | Stage | Input | Output | Technology | Evidence |
|---|---|---|---|---|---|
| 1 | Sources | APIs and downloads | raw tables | requests, pandas | source checks in `DATA.md` |
| 2 | Ports and graph | PortWatch, searoute, NGA | `config/nodes.csv`, `config/edges.csv` | pandas, searoute, NetworkX | 33 graph checks, 32 pass |
| 3 | OD matrix | Comtrade, PortWatch, EIA | `data/interim/od_matrix.csv` | pandas, IPF | 5,380 pairs, 7.552 G bbl/yr |
| 4 | Flow model | graph + OD + parameters | `data/processed/scenario_edge_flows.parquet` | NetworkX, NumPy | retained H1 share target unmet; calibration metadata is historical |
| 5 | Validation | PortWatch events, EIA voyage benchmark | `validation_report.csv`, `corridor_delays_validation.csv` | pandas | 3 held-out events |
| 6 | GNN | 337 simulated scenarios + observed weeks | `gnn_edge_predictions.parquet` (experimental surrogate), separate temporal forecasts | plain PyTorch | real-only and mixed backtests; closure feasibility not established |
| 7 | Price | edge outputs + Brent | `price_backtest_report.csv` | NumPy Ridge | 2.6% RMSE gain vs random walk; statistically uncertain |
| 8 | Dashboard | graph + scenario outputs | Streamlit app (**built, partial**) | Streamlit, PyDeck, Kepler.gl | manual review |

**Scale.** 142 nodes (100 ports, 42 junctions), 279 edges (10 chokepoints, 69 lanes, 200 connectors),
5,380 port pairs, 144,826,807 barrels of crude per week in the baseline.

---

## 1. Sources

### 1.1 What each source provides

| Source | Exact fields used | Feeds | Access and technology |
|---|---|---|---|
| IMF PortWatch — Daily Ports | `date`, `portid`, `portname`, `country`, `ISO3`, `import_tanker`, `export_tanker`, `portcalls_tanker`, `import_*` / `export_*` for container, dry bulk, general cargo, RoRo | port ranking, port totals, node attributes | ArcGIS REST, paginated at 1,000 rows, no key; `requests` |
| IMF PortWatch — Daily Chokepoints | `date`, `portid`, `portname`, `n_tanker`, `capacity_tanker`, `n_total` | capacity proxy, utilization, anomaly, validation targets | same service family, no key |
| UN Comtrade | `reporterCode`, `partnerCode`, `flowCode`, `period`, `cmdCode` (2709), `netWgt`, `motCode` | country-to-country crude flows | bounded cached REST requests, free key, 500 calls/day |
| EIA | daily Brent spot; chokepoint oil flow in million barrels per day | crude-share calibration, price target | REST or XLS, free key |
| NGA World Port Index | port name, UN/LOCODE, lat/lon, depth, max vessel dimensions | port matching, size limits | public download |
| searoute | `length`, `geometry`, `traversed_passages` | edge length, geometry, passage checks | local package, no key |
| World Bank | monthly Brent, Dubai, WTI | price cross-check | XLS |
| Global Fishing Watch | port-visit events, vessel id, timestamps | transit-time validation (**not used**: no file in `data/`) | API token |

### 1.2 Traceability: source field → final artifact

| Source field | Becomes | Script | Lands in |
|---|---|---|---|
| `import_tanker`, `export_tanker` (ports) | `import_tanker_tons`, `export_tanker_tons`, `tanker_role` | `ports/select_ports.py` | `config/nodes.csv` |
| all `import_*` + `export_*` (ports) | `tons_total`, `tons_tanker`, `share_of_global_*` | `ports/select_ports.py` | `config/nodes.csv` |
| `n_tanker`, `capacity_tanker` (chokepoints) | `capacity_proxy`, `weekly_tanker_transits`, `utilization`, `anomaly` | `graph/build_edge_attributes.py` | `config/edges.csv` |
| `netWgt` (Comtrade 2709) | `barrels_per_year` by country pair | `od_matrix/od_country_flows.py` | `data/interim/country_flows.csv` |
| port totals | export/import shares per country | `od_matrix/od_port_shares.py` | `data/interim/port_shares.csv` |
| EIA flow ÷ PortWatch tanker volume | `crude_share` (diagnostic) | `od_matrix/od_crude_share.py` | `data/interim/crude_share.csv` |
| Comtrade ÷ PortWatch (global) | `global_default` crude share 0.53 (agreed run assumption) | `od_matrix/od_crude_share.py` | `data/interim/crude_share.csv` |
| country flows + shares | OD pairs in barrels per year | `od_matrix/od_matrix.py` | `data/interim/od_matrix.csv` |
| OD + graph + parameters | edge flows, transits, delay, cost | `flow_model/*` | `data/processed/*` |
| PortWatch event weeks | observed targets | `validation/validation_targets.py` | `data/processed/validation_targets.csv` |

### 1.3 Source limits that shape the design

- PortWatch volumes are **model estimates**, not customs measurements, and `tanker` includes refined
  products and chemicals. Hence a separate crude-share step (§3.2).
- Comtrade reporters are incomplete: in the 2019–2025 sea-transport pull only 61 countries report,
  against 150 unique partners, and mirror reports disagree. The OD matrix therefore uses
  importer-reported flows where available.
- EIA chokepoint flows include products and, at Suez, the SUMED pipeline. They are not a crude-only
  tanker count.
- PortWatch counts **all tankers**; the model routes **crude only**. Validation compares percentage
  shifts, never absolute levels (§5.2).

---

## 2. Ports and graph

### 2.1 Port selection — Built

| Step | Rule |
|---|---|
| Pool | every PortWatch port, minus exclusions; terminals merged through `member_portids` |
| Rank | `rank_total` by all-type tons; `rank_tanker` by tanker tons |
| Select | strongest ports on each rank, de-duplicated, then filled to 100 by `combined_rank` |
| Manual | ports may be forced in with `selected_by` recording why |
| Window | daily PortWatch series summed over 2019–2025 |

**Coverage result** (`data/interim/ports_selected_coverage.csv`)

| Region | Ports | Share of total tons | Share of tanker tons |
|---|---|---|---|
| Global | 100 | 39.11 % | 58.72 % |
| Asia & Pacific | 46 | 42.18 % | 64.31 % |
| Europe | 30 | 44.89 % | 60.68 % |
| North America | 15 | 42.90 % | 59.42 % |
| South America | 6 | 13.57 % | 37.64 % |
| Africa | 3 | 6.43 % | 20.44 % |

Why 100 ports and not 300: every additional port adds connectors and OD pairs, and the 100 selected
already carry 58.7 % of world tanker tonnage. The coverage number is the honest statement of what the
graph represents.

### 2.2 Node schema — Built (`config/nodes.csv`, 142 rows, 28 columns)

| Field | Meaning / derivation |
|---|---|
| `node_id` | `port_<portid>` or `J_<name>` |
| `node_type` | `port` (100) or `junction` (42) |
| `name`, `country`, `country_iso3`, `continent` | labels |
| `lat`, `lon` | coordinates; junctions sit in water |
| `portwatch_portid` | join key to PortWatch |
| `wpi_index`, `unlocode` | NGA crosswalk fields (left empty where unmatched) |
| `rank_total`, `rank_tanker` | rank by all-type and tanker tons |
| `tons_total`, `tons_tanker` | summed PortWatch tons, 2019–2025 |
| `tanker_share` | `tons_tanker / tons_total` |
| `combined_rank`, `selected_by` | selection bookkeeping |
| `member_portids` | terminal IDs merged into this port |
| `share_of_global_total`, `share_of_global_tanker` | port tons ÷ global PortWatch tons |
| `import_tanker_tons`, `export_tanker_tons` | summed over member terminals |
| `tanker_role` | `net_importer` if imports > exports, else `net_exporter` (59 / 41 ports) |
| `source_period` | `2019-2025` — the window the sums cover (empty on junctions) |

Junctions carry only `node_id`, `name`, `lat`, `lon`, `country_iso3`, `portwatch_portid`. They exist
where lanes meet (e.g. Gulf of Aden) and hold no flow attributes of their own.

### 2.3 Edge schema — Built (`config/edges.csv`, 279 rows, 32 columns)

**Counts.** 10 chokepoints (`source = observed`), 69 lanes and 200 connectors (`source = modeled`).

**Static fields**

| Field | Meaning / derivation |
|---|---|
| `edge_id`, `from_node`, `to_node`, `from_lat/lon`, `to_lat/lon` | identity and endpoints |
| `edge_type` | `chokepoint`, `lane` or `connector` |
| `chokepoint_portid` | PortWatch id for the 10 chokepoints |
| `alternative_group` | passages that substitute for this one (`asia_europe`, `indian_pacific`, `atlantic_pacific`, `none`) |
| `searoute_passage` | the passage searoute says this edge traverses |
| `traversed_passages` | passages along the stored geometry |
| `length_nm` | searoute distance at 13 kn |
| `from_snap_nm`, `to_snap_nm` | distance from the node to the snapped route endpoint |
| `min_transit_days` | `length_nm / (14 × 24)` — fastest documented speed |
| `toll_usd_per_transit`, `toll_usd_per_bbl` | published tariffs; per-bbl = per-transit ÷ barrels per ship |
| `speed_knot`, `typical_transit_days` | reference values behind the length |
| `connector_kind` | how a port connector was attached (`auto`, `manual`) |
| `route_segments_json`, `geometry_geojson` | water-routed geometry for the map |

**Dynamic fields.** Every chokepoint number starts from `capacity_tanker`, which is not a deadweight
ceiling: PortWatch defines it as *total trade volume in metric tons of all tankers transiting that
day*, estimated as each vessel's payload share × its deadweight. The word "capacity" is the DWT
basis, not the semantics.

| Field | Derivation |
|---|---|
| `weekly_tanker_transits` | Σ daily `n_tanker` over a Friday-ending week |
| `capacity_tanker_tonnes` (upstream) | Σ daily `capacity_tanker` |
| `avg_vessel_size_tonnes` | `capacity_tanker_tonnes / n_tanker` — estimated carrying-capacity tonnes per tanker transit; neither measured loaded cargo nor vessel dimensions |
| `est_oil_volume` | `capacity_tanker × 7.3 × crude_share` |
| `capacity_proxy` | 95th percentile of weekly tanker transits, 2019-01-01 → 2023-10-31 |
| `utilization` | `weekly_tanker_transits / capacity_proxy`; may exceed 1 |
| `anomaly` | `(week − mean same ISO week 2019–22) / std same ISO week`; undefined when std = 0 |
| `latest_*` | most recent week's transits, utilization and anomaly |
| `source` | `observed` for chokepoints, `modeled` for lanes and connectors |

**Key equations**

```
min_transit_days   = length_nm / (speed_knots × 24)              # 13 kn sailing, 14 kn floor
capacity_proxy     = P95(weekly_tanker_transits)                  # 2019-01-01..2023-10-31
utilization        = weekly_tanker_transits / capacity_proxy
est_oil_volume     = capacity_tanker_tonnes × 7.3 × crude_share
anomaly            = (x − mean_same_week(x, 2019..2022)) / std_same_week(x, 2019..2022)
toll_usd_per_bbl   = toll_usd_per_transit / cost_barrels_per_cargo   # 1,000,000 bbl Suezmax
```

### 2.4 Connector rule — Built

A port is a start or end point, never a shortcut. Connectors are split during solving into
`port → junction` (leaving) and `junction → port` (arriving), so a route cannot enter Rotterdam and
leave from another berth. Construction rules:

- at most 3 connectors per port (actual: max 3, average 2.0);
- connect to the nearest junctions, keeping only links that cut at least 5 % off the existing path
  (all-pairs Dijkstra over junctions);
- an automatic connector may not cross a chokepoint;
- ports sitting on a chokepoint get a manual connector instead.

### 2.5 Graph validation — Built

33 checks, **32 PASS, 1 WARN**, run by `scripts/graph/graph_validation.py` with NetworkX, searoute
and `global_land_mask`. Output: `config/validation_results.csv`.

| Group | Checks | Result |
|---|---|---|
| Identity and schema | unique node/edge ids, endpoints exist, no self-loops, no duplicate pairs, positive lengths, coordinates match | 8 PASS |
| Snapping | endpoints within 100 nm | **WARN** — 8 edges: E004, E007, E014, E017, E024, E028, E029, E030 |
| Connectivity | junction graph connected; full graph connected (142 nodes, 279 edges) | 2 PASS |
| Passage correctness | chokepoint `searoute_passage` = `traversed_passages`; non-chokepoint edges cross no passage; the `none` labels are exactly Hormuz, Bosporus, Gibraltar | 3 PASS |
| Alternatives | every group has a second passage: Suez detour 12,468 nm vs 319 direct; Bab-el-Mandeb 12,517 vs 270; Panama 10,599 vs 154; Malacca 1,917 vs 698; Sunda 2,286 vs 330; Magellan 10,179 vs 574; plus the three expected absences (Hormuz, Bosporus, Gibraltar) | 10 PASS |
| Geometry | 79 core edges within 5 % of a fresh searoute; 21 junction pairs within 1.15× of searoute; all junctions in water | 3 PASS |
| Ports | every port has ≥1 connector; ≤3 connectors; no automatic connector crosses a chokepoint | 3 PASS |
| Groups | `asia_europe` = Suez, Bab-el-Mandeb, Cape; `indian_pacific` = Malacca, Sunda; `atlantic_pacific` = Panama, Magellan | 3 PASS |

Hormuz, Bosporus and Gibraltar correctly have **no practical alternative**; the check asserts the
absence rather than pretending a detour exists.

The WARN is a real but bounded defect: eight long edges have route endpoints more than 100 nm from
their node coordinates. It does not disconnect the graph or change any flow result, and it is
reported rather than suppressed.

---

## 3. Demand: OD matrix and crude share

### 3.1 Country flows

Comtrade crude (HS 2709) for 2019–2022, converted from kg to barrels (7.3 bbl/t), filtered to sea
transport where `motCode` allows. Where a country reports imports but not exports, the importer's
figure is used and the mirror gap is recorded in `source_method` (`all_mode_proxy`,
`all_minus_pipeline`, `mirror`).

Barrels per year per country pair is the only thing this stage produces. Prices and values are
discarded; the model routes physical barrels.

### 3.2 Crude share: the correction that made the OD matrix valid

**Historical derivation:** the numerical 0.8511/0.556 calculations below explain earlier revisions, not a new estimate in this run. The executed OD run fixes the approved port-universe share at 0.53. Current per-chokepoint estimates are separate, including Panama 0.058; the 0.48–0.93 range excludes Panama.

**First attempt — per chokepoint.**

```
crude_share = EIA oil flow (mbd) / (mean daily capacity_tanker × 7.3 / 1e6)
```

| Chokepoint | EIA (mbd) | PortWatch tanker (mbd) | Ratio |
|---|---|---|---|
| Malacca | 22.75 | 21.80 | 1.044 |
| Hormuz | 20.20 | 16.80 | 1.202 |
| Suez | 6.03 | 5.89 | 1.023 |
| Bab-el-Mandeb | 6.43 | 6.27 | 1.025 |
| Cape of Good Hope | 7.18 | 6.35 | 1.131 |

A share above 1 is impossible for a cargo-to-cargo comparison, and `capacity_tanker` is a cargo
estimate (PortWatch: "total trade volume (in metric tons) of all tankers transiting"; method =
estimated payload share × deadweight). So the excess is a **method residual**, not a
capacity-versus-cargo mismatch:

1. Ballast legs count as transits while contributing little or no estimated cargo, which pulls mean
tonnage per transit down.
2. EIA reports oil (crude + products); PortWatch reports **all** tanker liquids, a superset. Like for
like, EIA should be the *smaller* number, so a ratio above 1 means the PortWatch estimate reads
low — 2 % at Suez, 20 % at Hormuz.
3. At Suez, EIA includes the **SUMED pipeline**, which is not ship traffic.
4. EIA editions mix annual and semi-annual figures.

**Second attempt — one global ratio.**

```
crude_share = Σ Comtrade crude barrels / (Σ PortWatch export tanker tons × 7.3)
            = 33.902 / 39.834
            = 0.8511
```

The 0.8511 is not usable either: it loads **all** of each country's crude onto the selected
ports, because `port_shares.csv` normalises shares to sum to 1 *within the selection*. Single
countries expose it — India came out at 5.36× its own import tanker tonnage, the UAE at 1.54×,
and Britain at 1.32×. A share above 1 for one country is impossible, so the defect is the
normalisation, not the crude estimate.

**Final (V1) — two universes, two shares.** The two numbers that were competing were never
a measuring the same population.

| Universe | Formula | Value |
|---|---|---|
| Chokepoint edges (`est_oil_volume`) | EIA **crude** mbd ÷ PortWatch tanker mbd, per chokepoint, mean 2020–22 | 0.48–0.93, median 0.73 |
| OD matrix (`global_default`) | Comtrade crude reaching the 100 ports ÷ their tanker tonnage | **0.556** (V1 agreed at ≈0.53) |

Port-side derivation, step by step:

```
w_c         = country c's tanker tons at the 100 selected ports / its tanker tons at all 1,955 ports
crude_calls = Σ_c (crude exports_c + crude imports_c) × w_c          = 43.36 mbd
base        = (selected export + import tanker tons) × 7.3           = 77.94 mbd
share       = 43.36 / 77.94                                          = 0.5563
```

Both sides count a voyage twice (load and discharge), so the ratio is consistent. `w_c` is what
keeps crude that loads at a port we did not select out of the numerator — the assumption the
0.8511 version silently made and the 0.556 version does not. Coverage of world tanker tonnage is
57.8 %, so a share of 0.556 says crude reaches these ports slightly more than proportionally.

Why 0.70 must not be used here: crude makes roughly 1.3 chokepoint transits per tonne against
0.5 for products, so chokepoint tonnage is about twice as crude-rich as port tonnage. Putting
0.70 into the OD gives 27.3 mbd — 82 % of world crude on 58.7 % of world tanker tonnage. The
0.70 family belongs only on the chokepoint edges.

| Chokepoint | EIA crude 2022 (mbd) | EIA total 2022 (mbd) | crude ÷ PortWatch tonne, 2020–22 |
|---|---|---|---|
| Malacca | 16.3 | 23.0 | 0.73 |
| Hormuz | 16.2 | 21.9 | 0.85 |
| Suez | 3.6 | 7.3 | 0.48 |
| Bab el-Mandeb | 4.0 | 8.0 | 0.50 |
| Cape of Good Hope | 4.9 | 6.1 | 0.93 |
| Panama (fiscal year) | 0.1 | 2.2 | 0.06 |

This is used in the export/import targets and in `est_oil_volume`. It is a single documented number,
adjustable in one place, instead of five contradictory ones.

**What it still gets wrong.** It is an *oil* share, not a crude-only share; it sits above the
0.60–0.70 prior the team expected; and it is a group decision, not a measurement. The per-chokepoint
rows remain in `data/interim/crude_share.csv` as diagnostics, flagged with a warning when above 1.

### 3.3 Port totals and shares

Window: 2019-01-01 → 2023-10-31 = **1,765 daily observations = 4.835 years**.

```
port_total_tons_yr  = Σ daily tanker tons / 4.835
port_total_bbl_yr   = port_total_tons_yr × 7.3
export_share        = port export tons / Σ country export tons     # sums to 1 per country
import_share        = port import tons / Σ country import tons
```

Shares are computed separately for exports and imports. 82 country-direction groups were checked:
77 sum to 1.0; the 5 that sum to 0 are import groups with no reported sea imports (COL, EGY, KWT,
NGA, QAT). Those countries cannot receive an OD allocation, which is a data limit, not a bug.

### 3.4 Seed and IPF

```
seed[i,j] = country_flow[exporter, importer] × export_share[i] × import_share[j]
```

Seeds are zero when Comtrade reports no flow; the fit never invents trade between a pair that did not
trade. Targets are first scaled so total exports equal total imports (their mean).

```
repeat:
  F[i,j] ← F[i,j] × export_target[i] / Σ_j F[i,j]      # row step
  F[i,j] ← F[i,j] × import_target[j] / Σ_i F[i,j]      # column step
until max relative gap < 1e-6, or 1000 passes
```

**Result.** 5,380 port pairs, 100 ports, **7.91 billion barrels of crude per year** =
144,826,807 barrels per week.

**Checks** (`od_matrix/od_checks.py`): schema, no NaN, no negatives, no duplicate pairs, no
self-pairs, no domestic pairs, row and column sums within 0.01 %, and share sums of 1. All pass.

Why IPF rather than a gravity model: the marginals (port totals, country flows) are measured, and IPF
is the standard way to satisfy both while staying as close as possible to the observed seed.

---

## 4. Flow model

### 4.1 What it is for

One solver does three jobs: it produces dashboard numbers, it generates the GNN's training scenarios,
and it is the baseline the GNN must beat.

### 4.2 Parameters (`config/flow_params.json`)

| Parameter | Retained value | Interpretation |
|---|---|---|
| `speed_knots` | 13 | sailing speed assumption |
| `alpha`, `beta` | 0.12, 3.0 | historical congestion calibration settings |
| `daily_tanker_rate` | $50,000 | base; low $35k, high $75k |
| `avg_barrels_per_transit` | 1,000,000 | fallback, not all observed cargo sizes |
| `red_sea_throttle` | 0.85 | requested fraction of baseline throughput retained |
| `red_sea_war_risk_per_bbl` | 1.25 | risk parameter scaled by lost throughput fraction |
| `risk_passages` | Suez and Bab-el-Mandeb | both applicable under disruption |
| `max_util_for_delay` | 5 | BPR-utilization cap |
| `over_capacity_delay_days_per_unit` | 1 | excess queue slope |
| `n_congestion_iters` | 10 | loading passes |

Calibration diagnostic fields in the JSON describe an earlier fit, not the newly rebuilt inputs.
The current config has no `awrp_per_bbl` or `cost_barrels_per_cargo` parameter.

### 4.3 One solve, in order

1. Convert OD barrels/year to barrels/week by 52.1429.
2. Split port origins/destinations so routes cannot pass through an intermediate port. Sea corridors have arcs in both directions with shared physical edge IDs.
3. Compute sailing days = length/(13 * 24), transits = volume/edge cargo, and utilization = transits/capacity. Delay = service_days * 0.12 * min(utilization,5)^3 + max(0,utilization-1), with one service day at chokepoints and free sailing days elsewhere.
4. Arc cost = (sailing + delay) * daily_rate / edge_cargo + toll + applicable risk_parameter * (1 - requested_throughput_fraction). Primary cargo is estimated carrying-capacity tonnes * 7.33; the alternative oil/transit fallback alone is clipped. These are not observed loaded crude or enforced vessel dimensions.
5. Route one tenth of each OD demand by Dijkstra, updating costs between passes. Closed edges are absent. Throttled edges already at 98% saturation are removed before later passes; an increment can overshoot the ceiling. Open-edge capacity proxies remain soft congestion references.
6. Record any demand with no route as stranded. Partial throttles can strand demand too: this audit's 50% Hormuz case strands 10.61% of modeled demand.

This is an approximate incremental assignment, not exact capacitated minimum-cost flow or a guarantee of an equilibrium optimum. The formerly described soft-queue-only implementation is not the current code.

### 4.4 What a throttle means

Requested q is the fraction of baseline crude throughput retained. Capacity is q * baseline modeled transits when q < 1 and baseline flow is positive; for zero baseline flow it falls back to q * open capacity. Open capacity is the historical tanker proxy multiplied by the configured corridor crude share. The reported throttle_factor is capacity/open-capacity, clipped to [0,1], not always q. At Suez, q = 0.85 gives about 26.47 slots from a 31.139-transit baseline, versus about 91 open slots. Do not apply an all-tanker proxy directly to crude flow or call modeled baseline traffic measured throughput.

### 4.5 Output contract — 22 columns

| Group | Columns |
|---|---|
| Identity | `scenario_id`, `edge_id`, `from_node`, `to_node`, `edge_type`, `chokepoint_portid`, `searoute_passage` |
| Geometry | `length_nm` |
| State | `throttle_factor`, `oil_volume_bbl`, `tanker_transits`, `capacity_weekly`, `utilization`, `free_flow_days`, `delay_days`, `anomaly_z`, `cost_per_barrel`, `stranded_barrels` |
| Change | `delta_volume_bbl`, `delta_transits`, `share_rerouted` |
| Provenance | `source` |

`share_rerouted = max(0, −delta_volume / baseline_volume)` — loss, not churn. `anomaly_z` is computed
only at chokepoints, by scaling the model's ratio to that chokepoint's observed pre-crisis mean;
comparing crude transits directly with an all-tanker mean would build a permanent false anomaly.

### 4.6 Scenarios

Throttles come from the event, never from the answer being scored.

| Scenario | Setting | Reason |
|---|---|---|
| `baseline` | 1.0 | nothing closed |
| `red_sea_2024` (both windows) | Suez and Bab-el-Mandeb 0.85; $1.25/bbl war-risk premium | fitted on Dec 2023–Feb 2024 only; the canal stayed open, so the 15 % cut is the model's stand-in for a gap it cannot otherwise reproduce |
| `ever_given_2021` | Suez 1/7 ≈ 0.14 | canal blocked 6 of 7 days in the peak week |
| `panama_drought_2023` | Panama 24/36 ≈ 0.67 | daily booking slots cut from ~36 to 24 |

| Scenario | Suez transits/wk | Suez share | Stranded (bbl/wk) |
|---|---|---|---|
| baseline | 31.139 | 0.5835 | 0 |
| Red Sea (H1 and H2 are the same run) | 21.724 | 0.4407 | 0 |
| Ever Given 2021 | 3.779 | 0.0875 | 0 |
| Panama drought 2023 | 31.188 | 0.5839 | 0 |

H1 and H2 are one row because one throttle serves both windows — the model has no way to say that
March–June behaved differently from December–February, and the fitted share sits between the two
observed values rather than matching either.

Only a full closure strands anything. Panama barely moves Suez, which is correct: a canal on the
other side of the world should not change Red Sea traffic. Its effect is local: the Panama edge
drops 19.5 → 15.3 transits (−21 %). At baseline that edge is already at 325 % of its observed crude
capacity, because the OD routes ~19.5M bbl/wk of crude through a corridor that carries 0.94M — the
queue figure there is an artefact of over-routing, not congestion (see FLOW_MODEL §8).

Every scenario has all 279 edges, so `scenario_edge_flows.parquet` has 1,116 rows.

### 4.7 Criticality

Every chokepoint and the 15 busiest lanes closed one at a time (`edge_criticality.csv`).

| Edge | Stranded share | Extra ton-miles | Extra days | Note |
|---|---|---|---|---|
| Hormuz | 21.19 % | 0 % | 0 | true single point of failure |
| Gibraltar | 0 % | +19.07 % | +3.43 | worst rerouting burden without stranding |
| Bab-el-Mandeb | 0 % | +10.37 % | +1.81 | Cape detour |
| Suez | 0 % | +9.40 % | +1.70 | Cape detour |
| Panama | 0 % | +9.27 % | +1.59 | |
| Bosporus | 2.38 % | 0 % | 0 | no sea alternative |

Lane closures are ranked separately (`rank_scope = lane_sensitivity`), because a 1,000 nm lane is one
edge and its natural detour runs through a port, which the port rule forbids. They are reported but
never ranked against physical chokepoints.

---

## 5. Validation

### 5.1 Calibration design — Built

The grid function minimizes H1 (December 2023–February 2024) absolute Suez/Cape percentage-change errors plus Suez-share error over alpha, beta, throttle and risk. The retained config's H1 share error is 0.1017, outside its 0.05 target. It was not recalibrated on the rebuilt OD matrix in this audit. Current raw crude share is 0.4407 versus observed all-tanker shares 0.5412 H1 and 0.4167 H2; these raw-share comparisons themselves mix populations.

Previous retention discussion considered H2/Ever Given behavior, so the final historical settings are retrospectively assessed, not selected in a pristine untouched-test protocol. A 2026 study must freeze all settings and calibration before its evaluation boundary. The price-feature mapping now applies crude percentage responses to observed tanker baselines before forming shares.

### 5.2 Why validation compares shifts, not levels

The model counts crude; PortWatch counts all tankers. A direct level comparison would show a
permanent ~2.5× error that says nothing about the model. So:

1. take the observed pre-event level;
2. apply the model's percentage change;
3. compare that implied level with the observed crisis level.

Metrics below are absolute errors in implied observed all-tanker levels after relative-response mapping; they are not raw crude-versus-tanker count errors.

### 5.3 Held-out events

| Event | MAE (transits/wk) | RMSE | MAPE | Direction match | Mean gap (pp) |
|---|---|---|---|---|---|
| Red Sea, Mar–Jun 2024 | 22.160 | 30.1153 | 0.1772 | 0.8 | 18.44 |
| Ever Given peak week | 23.628 | 31.7936 | 0.2342 | 0.8 | 20.91 |
| Panama drought | 16.233 | 23.0396 | 0.1118 | 0.7 | 13.00 |

RMSE above MAE indicates uneven error magnitudes; consult per-corridor details before claiming a particular concentration pattern.
`validation_event_details.csv` names them.

### 5.4 Voyage-time check

**Historical diagnostic, not ship-level validation:** despite its filename the checker uses a published benchmark, not GFW observations. Its undirected graph can traverse intermediate ports that the main router forbids. Old corridor figures below have not been freshly reproduced and do not certify routed-OD delays.

Benchmark: Persian Gulf → Rotterdam, 19 days via Suez and ~35 via the Cape (EIA, Feb 2024, laden
Suezmax at 14 kn).

| Corridor | Model baseline | Model crisis | Extra | Benchmark extra | Error |
|---|---|---|---|---|---|
| Ras Tanura → Rotterdam | 21.19 d | 38.04 d | 16.85 d | 16.0 d | **0.85 d** |
| Ras Tanura → Ningbo (control) | 19.18 d | 19.18 d | 0.00 d | 0 | 0.00 |
| Houston → Rotterdam (control) | 16.45 d | 16.45 d | 0.00 d | 0 | 0.00 |

Only the *extra* time is scored: absolute levels differ by ~2 days because the graph sails at 13 kn
and the benchmark at 14 kn. The controls catch the failure mode where Red Sea traffic leaks into
unrelated lanes.

### 5.5 What validation does not prove

- One premium per window cannot represent a crisis whose intensity changed weekly.
- The model under-predicts where diverted traffic lands (Cape +37.7 % vs +68.8 % in H1, +65.0 % vs
  +123.2 % in H2).
- Ever Given overshoots the Cape (+79 % modelled vs +5.7 % observed) because the throttle lasts a
  full week while the canal reopened after six days, and the model has no time dimension.
- A throttled edge is removed at its ceiling, so the model cannot show queueing at an open chokepoint.
- Panama sends almost nothing to the Cape, where reality added 51 %.

---

## 6. GNN — Implemented research prototype

### 6.1 Purpose and honest claim

The temporal GNN forecasts next-week observed all-tanker counts. The separately exported simulator surrogate approximates flow scenarios and is experimental. They are different tasks; no demonstrated latency or generalization claim should be inferred from a proposed architecture.

### 6.2 Dataset

337 simulator scenarios contain 94,023 edge rows. The new trainer holds out all 20 compound cases and splits 317 single/baseline cases into 254 train and 63 validation cases. Temporal training uses six node and fourteen edge slots, including three strictly lagged observed transit summaries; solver OD and current tariffs are excluded from real-only inputs. Feature meanings differ between pretraining and temporal mode. The old G1 bundle excludes event dates but includes later history, so use `train_gnn.py` for chronological tests.

### 6.3 Model

Plain PyTorch, three bidirectional endpoint-aggregation layers and 32 hidden units in the executed trainer. Four nonnegative edge heads and an eight-coordinate pooled graph summary. Closed-edge volume/transits are masked to zero. The embedding projection itself has no separate training loss.

### 6.4 Training and outputs

Huber loss on scaled targets; only observed transits contribute to real-data loss. No signed conservation loss is applied to combined two-way edge labels. Seeds 7/19/43 are averaged, with validation early stopping. The 22-column `gnn_edge_predictions.parquet` is simulator-only, before temporal fine-tuning. `gnn_temporal_predictions.parquet` and `gnn_price_embeddings.parquet` are separate products.

### 6.5 Evaluation

Real-only and mixed forecast MAPE across 440 pairs is 12.18% and 12.77%. Mixed RMSE improves on persistence for Ever Given/Panama, but both GNN modes lose on Red Sea. Baselines are persistence and seasonal, not ARIMA/LSTM. Synthetic transits have 17.10% WAPE on held-out compounds; that average hides a major Hormuz feasibility failure: full closure strands only about 0.29 million bbl/week by the surrogate connector residual, versus 30.75 million in the router. Zero Hormuz flow is enforced by a mask. Do not present the surrogate as physically reliable.

---

## 7. Price model — Implemented observational regression

### 7.1 Shipping cost per barrel

`edge_cost = (free_days + delay_days) * charter_rate / edge_cargo + toll`. Cargo comes from volume/transits where active. Baseline toll recovery subtracts both sailing and delay. Fleet cost sums edge flow times edge cost and divides by served barrels (half the connector-volume sum). $35k/$50k/$75k rates are assumptions, not statistical confidence intervals. The explicit routing war-risk penalty is not added as a separate measured freight charge.

### 7.2 Three indices, one function

Observed history supplies Suez/(Suez+Cape) tanker share. Historical global route days, rerouted work and freight costs remain missing. Scenario voyage days use the served-barrel denominator; absolute changed edge barrel-miles are an activity index, not net extra OD distance. Scenario shares map crude percentage responses onto observed tanker baselines before taking their ratio.

### 7.3 Brent model

NumPy Ridge predicts a correction to lagged price from lagged price, price-average gap, lagged Suez share and lagged share change. Optional frozen GNN summaries add eight features. Historical route costs and stranded supply are not regressors. There is no causal freight/supply-risk decomposition. Residual calibration replaces planned quantile regression.

### 7.4 Backtest

204 common fit weeks through December 2022, 43 calibration weeks in January–October 2023, 35 test weeks November 2023–June 2024. Network RMSE $2.400 vs random walk $2.463; improvement 2.6%, with a bootstrap difference interval crossing zero. Current-vintage scenario calibration includes 2026 and cannot be reused as untouched Hormuz holdout calibration. The price model has no direct Hormuz or supply-loss input; a closure's small indirect price change is not a meaningful supply-shock estimate.

---

## 8. Dashboard — Built, partial

### 8.1 What exists (`app/dashboard.py`)

| Feature | State |
|---|---|
| 142 nodes, 279 water-routed edges, antimeridian split | built |
| PyDeck map by default; Kepler.gl toggle with preconfigured layers | built |
| 9 chokepoint throttle sliders + 6 presets | built |
| Live call to `scenario_engine.run_scenario` when `od_matrix.csv` exists; analytical fallback otherwise | built |
| KPIs: chokepoint transits, rerouted mb/d, extra days, Suez share, stranded volume | built |
| Chokepoint table: throttle, transits, delta, utilization, free-flow and delay days | built |
| Edge colouring: red when throttled, gold when utilization > 0.95, blue lanes | built (chokepoints only) |
| Remove any port or edge with instant re-solve | **not built** (chokepoint sliders only) |
| Week timelapse 2019 → 2026 | **not built** |
| Observed / modeled / GNN source toggle | **not built** |
| Price panel ($/bbl, Brent) | **not built** (Deliverable 5) |

### 8.2 Known mismatches to fix before presenting

| Item | Current | Calibrated / source of truth |
|---|---|---|
| Red Sea preset | `suez = 0.35`, `babalmandab = 0.35` | `suez = 0.85`, `babalmandab = 0.85`, $1.25/bbl war-risk premium (`red_sea_throttle`, `red_sea_war_risk_per_bbl`) |
| Default BPR sliders | α 0.18, β 3.8 | α 0.12, β 3.0 |
| Baseline Suez share shown in the KPI delta | hardcoded 0.611 | current raw crude share 0.5835; observed-equivalent share is a different population |
| Slider semantics | "0 = closed, 1 = open" | same fraction-of-throughput meaning; the presets are what is wrong |

The dashboard intentionally shows physical metrics only (Deliverables 3–4). Price belongs to
Deliverable 5 and should not be faked in the UI.

### 8.3 Target behaviour (the presentation vision)

1. Click any port or edge, not only nine chokepoints.
2. Remove or throttle it; the network re-solves in under ~2 s.
3. Edge colour = congestion, with a legend.
4. Week slider to replay observed history and scenario weeks.
5. Source toggle, with a visible `observed` / `modeled` badge on every layer.
6. Right panel: Brent impact with interval, cost per bbl, rerouted ton-miles, Suez share.

---

## 9. Real-world closures

Only three events can be checked against data; everything else is a what-if and must be labelled so.

| Event | Date | Mechanism | Modelled as | Observed | Model |
|---|---|---|---|---|---|
| Red Sea / Houthi diversions | Dec 2023 – 2024 | war-risk rerouting | Suez and Bab-el-Mandeb 0.85, $1.25/bbl war-risk | Suez −29.5 % (H1) / −43.5 % (H2); Cape +69 % then +123 %; share 0.541 / 0.417 | Suez −30.3 %; share 0.4395 (one run for both windows) |
| Ever Given | 23–29 Mar 2021 | channel blocked | Suez 1/7 | Suez −76 % in the peak week, Cape +5.7 % | Suez −87.9 %; Cape +77.0 % |
| Panama drought | 2023 – 2024 | draft and slot limits | Panama 24/36 = 0.67 | canal −25.8 %; Cape +51.4 %; slots 36 → 24/day | Panama edge −21.4 %; no Cape response |

**What-if set** (no observed event, always labelled): Hormuz, Malacca, Bab-el-Mandeb alone, Gibraltar,
Bosporus, Cape of Good Hope, Sunda, Magellan, and compound cases.

Hormuz is the one closure that strands rather than reroutes: 21.19 % of crude demand, which is the
model's check that a true single point of failure behaves like one.

---

## 10. Limits

- Retained H1 calibration misses its share target; historical model selection was retrospective.
- A constant Red Sea setting cannot reproduce all phases or independently validate unobserved diverted flows.
- Demand is static; no new pipeline export reallocation, inventories, production substitution, or price elasticity is represented. Hormuz's 21.23% stranding is a share of modeled demand, not world supply.
- Panama baseline utilization is 3.2666 and delay 5.8731 days; these expose assumptions, not measured queues. Vessel-size and port-congestion restrictions remain incomplete.
- Coarse lane closures can exaggerate detours, so they remain separate sensitivity rows.
- No GFW voyage sample was reproduced; the corridor diagnostic has a routing-rule mismatch.
- The surrogate fails Hormuz cargo feasibility even on a pretraining case. Its zero closure flow is a mask, not a learned proof.
- The price model does not identify a supply-risk premium; its 2026 scenario interval calibration is not a fresh Hormuz holdout.
- Dashboard defaults/presets do not match retained model settings; GNN and price outputs are not integrated into its physical-only view.

See [the complete dated audit](MODEL_METHODOLOGY.md#15-methodology-audit-and-hormuz-readiness).

## 11. Presentation plan

The interface panels below are presentation ambitions, not a claim of completed integration. Use the current audited scorecards and state the scenario/forecast distinction.

Two flowcharts, two panels. This is the order that makes the dependencies visible: data →
representation → demand → physics → evidence → learning → money → product.

### 11.1 Flowchart A — "Data → graph → demand → physics"

| Panel | Answers | Show | Numbers to quote | Artifact |
|---|---|---|---|---|
| A1 Sources | Where does every number come from? | 6 source cards, one field list each | 39.1 % of tons, 58.7 % of tanker tons covered | `DATA.md`, `ports_selected_coverage.csv` |
| A2 Graph | What is the network? | 142 nodes / 279 edges diagram, port + edge attribute cards | 10 chokepoints, 69 lanes, 200 connectors | `config/nodes.csv`, `config/edges.csv` |
| A3 Attribute equations | How is each attribute computed? | equation per field, with its source column | `capacity_proxy` = P95; `min_transit_days` = length/(14×24) | `edges.csv` |
| A4 Graph checks | Can the graph be trusted? | 33 checks, 32 pass, 1 warn | 8 long-snap edges | `config/validation_results.csv` |
| A5 OD matrix | Who sends crude to whom? | port total → share → seed → IPF | 5,380 pairs, 7.552 G bbl/yr, gap < 1e-6 | `data/interim/od_matrix.csv` |
| A6 Crude share | Which population does the share describe? | historical correction, current explicit assumption | port share 0.53; per-chokepoint values differ | `data/interim/crude_share.csv` |
| A7 Flow model | How does a closure propagate? | 6-step solve diagram, cost equation | 144,826,807 bbl/wk; 0 stranded | `FLOW_MODEL.md` |
| A8 Validation | How wrong is it? | dated event scorecard and audit limitations | flow MAE 23.628 / 16.233 / 22.160; H1 calibration target unmet | `validation_report.csv` |

### 11.2 Flowchart B — "Learning → money → product"

| Panel | Answers | Show | Numbers to quote | Artifact |
|---|---|---|---|---|
| B1 Training data | What does the GNN learn from? | 337 scenarios, grid diagram, row count | 94,023 rows, 4 targets | `gnn_simulated_scenarios.parquet` |
| B2 GNN | Why a graph network? | 3 message-passing layers, two heads | 6 node / 14 edge features, 8-dim embedding | `DELIVERABLE5.md` §5 |
| B3 GNN scoring | Does it beat the solver? | conditional solver versus temporal GNN caveat; persistence/seasonal baselines | same metrics as A8; synthetic vs held-out | `gnn_evaluation_report.csv` |
| B4 Price | How does the network become money? | cost formula, 3 rates, 3 indices | $35k / $50k / $75k per day | `network_price_features.parquet` |
| B5 Price test | Does it beat a random walk? | 4 models, 2023-11 → 2024-06 | RMSE, MAE, direction, coverage | `price_backtest_report.csv` |

### 11.3 Panel C — Real-world closures

| Item | Content |
|---|---|
| Timeline | 2021 Ever Given → 2023–24 Panama → 2023–24 Red Sea (focal) |
| For each event | mechanism, throttle and why, observed change, modelled change |
| What-ifs | Hormuz, Malacca, Gibraltar, Bosporus, Cape, Sunda, Magellan, compounds — labelled what-if |
| Headline | Hormuz strands 21.19 %; Suez reroutes 100 % |

### 11.4 Panel D — Dashboard

| Item | Content |
|---|---|
| Map | 279 edges, port and junction points, edge colour by congestion |
| Control | select any port or edge, close or throttle, instant re-solve (target < 2 s) |
| Time | week slider 2019 → 2026 |
| Provenance | observed / modeled / gnn toggle with badges |
| Economics | Brent with interval, cost per bbl, rerouted ton-miles, Suez share |
| Honest status | what exists today vs what is planned (§8.1) |

### 11.5 One-line story per stage

1. **Sources** — six public datasets, no raw AIS.
2. **Graph** — 100 ports and 42 junctions; every chokepoint is one edge, so any edge can be closed.
3. **OD** — country trade split to ports, fitted to PortWatch marginals.
4. **Physics** — dollars per barrel, ten loading passes, BPR congestion, strand what cannot move.
5. **Validation** — fitted on H1, scored on H2 and two more events; every error is published.
6. **GNN** — separate simulator imitation and real temporal forecasting; disclose Hormuz feasibility failure.
7. **Price** — separate freight calculations from lagged observed-share forecasting; disclose uncertain skill and unidentifiable causal effects.
8. **Product** — a map where a user closes an edge and sees the world re-route.

---

## 12. Artifact map and run order

### 12.1 Files that matter

| Path | Contents |
|---|---|
| `config/nodes.csv` | 142 nodes, 28 columns |
| `config/edges.csv` | 279 edges, 32 columns |
| `config/edges_core.csv` | 79 lanes + chokepoints (no connectors), 29 columns |
| `config/flow_params.json` | calibrated parameters and fit diagnostics |
| `config/validation_results.csv` | 33 graph checks |
| `data/interim/od_matrix.csv` | 5,380 pairs, barrels per year |
| `data/interim/crude_share.csv` | global default + 6 reference chokepoint ratios |
| `data/processed/scenario_edge_flows.parquet` | 4 scenarios × 279 edges |
| `data/processed/gnn_simulated_scenarios.parquet` | 337 × 279 = 94,023 rows |
| `data/processed/validation_targets.csv` | observed event windows |
| `data/processed/validation_report.csv` | event scorecard |
| `data/processed/corridor_delays_validation.csv` | voyage-time check |
| `data/processed/edge_criticality.csv` | one-at-a-time closures |
| `app/dashboard.py` | Streamlit + PyDeck/Kepler dashboard |

### 12.2 Run order

```
python scripts/data_pull/pull_ports_static.py
python scripts/data_pull/pull_port_totals.py
python scripts/data_pull/pull_portwatch_transits_2019_2025.py
python scripts/data_pull/pull_chokepoints.py
python scripts/data_pull/pull_chokepoints_static.py
python scripts/data_pull/comtrade_script.py
python scripts/data_pull/eia_brent.py
python scripts/data_pull/eia_chokepoints.py

python scripts/ports/country_codes.py
python scripts/ports/select_ports.py

python scripts/graph/build_nodes.py
python scripts/graph/build_edge_attributes.py
python scripts/graph/build_connector_edges.py
python scripts/graph/graph_validation.py

python scripts/od_matrix/od_country_flows.py
python scripts/od_matrix/od_port_shares.py
python scripts/od_matrix/od_crude_share.py
python scripts/od_matrix/od_matrix.py
python scripts/od_matrix/od_checks.py

python scripts/validation/validation_targets.py
python scripts/validation/calibrate_flow.py
python scripts/flow_model/flow_solver.py
python scripts/flow_model/edge_metrics.py
python scripts/flow_model/scenario_engine.py
python scripts/flow_model/gnn_sim_generator.py --workers N
python scripts/validation/validate_events.py
python scripts/validation/validate_gfw_delays.py

.venv/bin/streamlit run app/dashboard.py
```

Order matters: each stage reads what the previous one wrote. `config/flow_params.json` is the only
model output kept in version control, so both halves of Deliverable 5 work from the same numbers.

### 12.3 Sanity checks after any change

- routed + stranded barrels = OD total;
- baseline stranded = 0;
- no negative volumes;
- a closed edge carries zero flow;
- Raw crude Suez share near 0.5835 baseline / 0.4407 Red Sea in this run; observed-equivalent price shares differ.

---

## 13. Glossary

| Term | Meaning |
|---|---|
| Node / edge | A place / a sea route between two places |
| Chokepoint | A narrow passage modelled as one edge |
| OD pair | One origin port and one destination port |
| Baseline | The world with nothing closed |
| Scenario | The same world with one or more edges throttled |
| Throttle | Fraction of pre-event crude throughput still available |
| Stranded | Demand that cannot reach its destination, in barrels per week |
| BPR | Standard congestion formula; delay grows with the cube of utilization |
| IPF / RAS | Iterative proportional fitting: rescale rows then columns until both match targets |
| Holdout | Real events kept out of fitting so the score is honest |
| MAE / RMSE / MAPE | Average absolute error / big-miss-weighted error / error as a share |
| Direction match | Share of corridors where the model moved the right way |
| Message passing | A GNN layer where each node and edge updates from its neighbours |
| Embedding | A short vector summarising the whole graph for another model |
| Quantile regression | Predicting a range rather than a single number |
| Interval coverage | Share of actual values inside the predicted range; 0.9 is the target for a 90 % interval |
