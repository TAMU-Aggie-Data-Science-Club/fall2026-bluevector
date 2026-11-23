# The Flow Model: How It Works and What It Gets Wrong

This is the reference for the crude-oil flow model in `scripts/flow_model/` and the checks in
`scripts/validation/`. It covers why the model exists, what it takes in, the equations it solves,
how the parameters were fitted, and how it performs against real disruptions.

Read [`deliverables/DELIVERABLE4.md`](deliverables/DELIVERABLE4.md) for the assignment itself and
the shared column contract. This file documents what we actually built.

> **Audit, 2026-11-22:** current rebuilt-demand figures below supersede earlier examples. Existing calibration metadata is historical, not a fresh fit to this OD matrix. See [Hormuz readiness](MODEL_METHODOLOGY.md#15-methodology-audit-and-hormuz-readiness) before using scenario outputs as real-world predictions.

## 1. Why it exists

The dashboard has to answer a concrete question: if a canal is closed or throttled, where does the
crude oil go, how much of it cannot move at all, how much longer does the trip take, and what does
that cost per barrel?

There is no dataset that answers that, because most of those situations have not happened. So we
route demand over the network ourselves. The same model does three jobs:

1. it produces the baseline and scenario numbers the dashboard shows;
2. it generates the simulated disruptions that train the GNN in Deliverable 5;
3. it is the baseline the GNN has to beat, and the thing we score against real events.

## 2. What goes in

| Input | Where | What it is |
|---|---|---|
| `config/nodes.csv` | Deliverable 3 | 100 ports plus 42 open-sea junction nodes |
| `config/edges.csv` | Deliverable 3 | 279 edges: 10 chokepoints, 69 lanes, 200 port connectors. Each carries length, a capacity proxy, a toll, and an estimated carrying-capacity proxy |
| `data/interim/od_matrix.csv` | Deliverable 3 | Port-to-port crude oil demand in barrels per year, from Comtrade scaled to PortWatch port totals |
| `config/flow_params.json` | Deliverable 4 | Speed, congestion curve, ship economics, and calibrated Red Sea parameters |

Everything runs on a **one-week time step**, so demand is converted once, at load:

```
barrels_per_week = barrels_per_year / 52.1429
```

PortWatch inputs are daily and are aggregated into weekly counts; this aligns the model and validation clock.

## 3. How one solve works

**Step 1 — build a directed graph.** Lanes and chokepoints are traversable in both directions.
Port connectors are split into a `port → junction` arc (usable when leaving a port) and a
`junction → port` arc (usable when arriving). That split enforces the port rule: a ship may start
or end at a port, but may never cut through one as a shortcut. Without it, the router happily
"saves" thousands of miles by entering Rotterdam and popping out somewhere else.

**Step 2 — give every arc a cost in dollars per barrel.** Ships compare routes on generalized cost,
not on distance, because a short congested canal can be worse than a long quiet detour.

```
free_flow_days  = length_nm / (speed_knots * 24)
transits        = barrels_per_week / barrels_per_ship
utilization     = transits / capacity_weekly
delay_days      = service_days * alpha * min(utilization, 5) ** beta + over_cap_days * max(0, utilization - 1)
cost_per_barrel = (free_flow_days + delay_days) * daily_tanker_rate / barrels_per_ship
                + toll_usd_per_bbl
                + war_risk_per_bbl * (1 - throughput_factor)
```

`service_days` is the time a ship nominally spends being served at that edge: one day at a
chokepoint (queue and convoy), and the sailing time itself on open water. The chokepoint base is the
queue time, not the length of the segment, because a 400 nm lane has a 1.3 day base: congestion
priced against sailing time is nearly free, and routes then never avoid a busy corridor.

`barrels_per_ship` uses `avg_vessel_size_tonnes * 7.33` when that carrying-capacity proxy exists. Only the alternative oil-volume/transit fallback is clipped to 250k–2.2M barrels; otherwise the default is one million. The primary proxy is neither observed vessel dimensions nor measured loaded crude, and physical vessel-size restrictions are incomplete.

**Step 3 — load demand in ten passes.** Cheapest paths come from Dijkstra's algorithm, one
single-source run per origin port that returns the best path to every destination at once (NetworkX
`single_source_dijkstra`). Every arc weight is a cost in dollars per barrel, so it is non-negative,
which is Dijkstra's only precondition. A single shortest-path assignment would drop every barrel on
one route and ignore congestion entirely, so each pass routes one tenth of each origin-destination
pair, then recomputes costs from the flows so far: the standard incremental-loading,
Frank-Wolfe-style approximation of user equilibrium. Ten passes is enough for routes to settle
without spending minutes per scenario.

Capacity is not enforced inside the optimization; there is no min-cost-flow solve. It enters through
this loading procedure and through arc removal in step 4, so the result is an approximate
equilibrium, not the exact optimum of a capacitated flow problem.

**Step 4 — saturate throttled edges.** An edge that has been throttled and is already at 98 % of its
available throughput is removed from the graph for the remaining passes. The extra cargo then has to
reroute or strand, which is the point of closing a canal. Unthrottled lanes keep only the soft
congestion curve, because their capacity proxy is a 95–99th percentile of historical transits, not a
physical wall; ships really do surge past it, as the Cape did in 2024.

**Step 5 — record.** Demand that finds no path at all is counted as **stranded**. Closing Hormuz
completely strands about 21.2% of this modeled network's crude demand, which is the model's check that a true
single point of failure behaves like one.

A solved network routes 144,826,807 barrels per week across 5,380 port pairs in the baseline, with
zero stranded barrels and no negative flows.

## 4. What a throttle means

This is the decision that makes or breaks every scenario.

A throttle is a **fraction of pre-event crude throughput still available**. 0.0 is closed, 0.65
means 65 % of the edge's pre-event crude still passes. Capacity follows from that:

```
capacity_weekly = throttle * baseline_transits(edge)          when throttle < 1
capacity_weekly = capacity_proxy * crude_share                when open
throttle_factor = capacity_weekly / (capacity_proxy * crude_share)   (reported, 0..1)
```

Open capacity uses PortWatch's proxy on the corridor crude basis: Suez's 187.5 tanker slots become about 91 crude-equivalent slots under the configured edge attributes. Capacity and flow are
both crude, and capacity stays an observation — nothing in it is derived from what the model routes.
(It used to be re-based by the model's own first-pass output, which made utilisation identically
`observed ÷ proxy` and moved whenever the solver changed.)

A throttle is applied to modeled baseline crude throughput rather than to that capacity, because the proxy
counts **all tankers** while the pulled crude flow is about 32 transits per week. A throttle applied
to the proxy leaves a ceiling far above the crude that actually uses the edge, so the cut does not
bind: halving 91 still leaves 45 slots against 32 transits of demand.

The reported `throttle_factor` is converted back onto the Deliverable 3 basis so the column keeps
the units the shared contract defines. At Suez, 0.85 × 31.139 ≈ 26.47 crude-equivalent slots/week, reported as `throttle_factor` ≈ 0.291 of the 91-slot open proxy. Actual solved traffic can be below that ceiling.

Edges that carried no crude at baseline fall back to `throttle * capacity_weekly`, so a throttled
but unused edge is never reported as closed while still carrying flow.

## 5. Per-edge outputs

For every edge and every scenario, `compute_edge_metrics` produces the contract columns, including
tanker transits, utilization, delay, cost per barrel, an anomaly z-score against the pre-November
2023 weekly mean at chokepoints, and the change against baseline. Two details worth knowing:

- **`share_rerouted`** is the fraction of an edge's baseline volume that left it, floored at zero:
  `max(0, -delta_volume / baseline_volume)`. It measures loss, not churn.
- **`anomaly_z`** is computed at chokepoints only, by scaling the model's ratio to that chokepoint's
  observed pre-crisis mean. Comparing modeled crude transits directly with an all-tanker mean would
  build a permanent false anomaly into the table.

## 6. Scenarios come from the events, not from the answers

The scenario engine does not tune itself to the numbers it will later be scored against. Each
historical scenario is read off the event:

| Scenario | Throttle | Why that number |
|---|---|---|
| `red_sea_2024` | Suez and Bab-el-Mandeb at 0.85; war-risk $1.25/bbl | calibrated on Dec 2023–Feb 2024 (section 7) |
| `ever_given_2021` | Suez at 1/7 ≈ 0.14 | the canal was blocked 6 of the 7 days in the peak week |
| `panama_drought_2023` | Panama at 24/36 ≈ 0.67 | the ACP cut daily booking slots from about 36 to 24 |

Results against the baseline:

| Scenario | Suez transits | Suez/(Suez+Cape) share | Stranded (bbl/wk) |
|---|---|---|---|
| baseline | 31.139 | 0.5835 | 0 |
| Red Sea 2024 | 21.724 | 0.4407 | 0 |
| Ever Given 2021 | 3.779 | 0.0875 | 0 |
| Panama drought 2023 | 31.188 | 0.5839 | 0 |

The Panama scenario barely moves Suez, under the current static routing assumptions; this is not a universal claim about global fleet responses. Its effect is on the Panama edge itself (19.5 → 15.3 transits,
about −21 %), an edge that already sits at 325 % of its observed crude capacity at baseline — the
router sends crude through Panama that a vessel-size limit would forbid (section 8).

## 7. Calibration

Four parameters are fitted: the congestion curve `alpha` and `beta`, the Red Sea throughput
retained (`red_sea_throttle`), and the war-risk surcharge per barrel (`red_sea_war_risk_per_bbl`).
The search is a grid over alpha (0.10–0.15), beta (3.0–4.0), throttle (0.45–0.95), and surcharge
($0.00–$2.50/bbl) minimizing:

```
loss = |suez_pct_model - suez_pct_observed| / 100
     + |cape_pct_model - cape_pct_observed| / 100
     + |suez_share_model - suez_share_observed|
```

**The split matters more than the grid.** Parameters are fitted on December 2023–February 2024 (H1)
only. March–June 2024 (H2) is never used to choose anything, so scoring it is a real out-of-sample
test rather than a fitted number dressed up as validation.

Retained settings are alpha 0.12, beta 3.0, throttle 0.85 and war-risk $1.25/bbl. The `config/flow_params.json` loss/share fields describe an earlier calibration vintage (H1 share error 0.1017, target unmet), not newly reproduced fit metrics. Current rebuilt output has crude Suez share 0.4407 versus observed 0.5412 in H1 and 0.4167 in H2. Those raw crude/all-tanker share comparisons also mix populations; the newer price feature path maps percentage responses onto observed tanker baselines before forming a share.

The grid function selects on H1, but earlier parameter-retention notes explicitly considered Ever Given/H2 performance when retaining settings. The historical cases are consequently retrospective assessments, not a pristine untouched final test. A new crisis evaluation must freeze settings before its test dates. No recalibration was performed during this audit.

## 8. What is validated, and how

The model counts **crude only**. PortWatch counts **all tankers**. Comparing the two levels directly
would produce a permanent error of roughly 2.5×, so validation compares percentage shifts: the
model's change is applied to the observed pre-event level, and that implied level is compared with
the observed crisis level. The reported transit errors are absolute errors in the implied all-tanker level after this relative-response mapping; they are not direct errors between raw crude and all-tanker counts.

The intended assessment windows, subject to the retrospective-selection qualification above, are: Red Sea H2, the Ever Given peak week (23–29 March
2021), and the Panama drought. All 10 priority chokepoints are evaluated across all events.

| Event | MAE (transits/wk) | RMSE | MAPE | Direction match | Mean gap (pct points) |
|---|---|---|---|---|---|
| Red Sea H2 | 22.160 | 30.1153 | 0.1772 | 0.8 | 18.44 |
| Ever Given peak | 23.628 | 31.7936 | 0.2342 | 0.8 | 20.91 |
| Panama drought | 16.233 | 23.0396 | 0.1118 | 0.7 | 13.00 |

Every single event meets the acceptance criterion of **MAE < 25 transits off reality**, and MAPE
averages 17.44% across the three event-average MAPE values; this is not a universal accuracy score.

A baseline limitation is Panama utilization of 3.2666 and modeled delay of 5.8731 days. That exposes imperfect OD/cargo-size/capacity assumptions and incomplete physical vessel restrictions; the data do not establish that real ships experienced this queue.

### Voyage time check

`validate_gfw_delays.py` compares an assumed full Red Sea closure with a published corridor benchmark and structural controls. Despite its filename, it does not read real GFW voyage observations. It builds an undirected graph without the router's split-port restriction, so it can use paths the shipping router forbids. No fresh corridor result was reproduced in this audit. Earlier corridor numbers are historical diagnostics, not independent ship-level validation or proof of routed-OD delay accuracy.

## 9. Current errors and limits

Read these as the model's honest boundaries.

- **Red Sea intensity changes over time.** One throttle/surcharge does not reproduce every phase; current H1 share calibration misses its target. All-tanker versus crude populations add interpretation limits.
- **Displaced traffic remains imperfect.** Event-average transit errors do not establish correct rerouting, voyage delays or oil volumes on unobserved edges.
- **Ever Given is represented by one blocked week.** It cannot capture the exact shutdown/reopening chronology or backlog dynamics.
- **Hormuz has no modeled sea bypass.** Closure strands 30.75 million bbl/week (21.23% of the modeled demand), not 21.23% of world supply. New pipeline-export reallocation, inventories, production substitution and demand response are absent. The GNN surrogate severely fails this stress case; see the dated audit.
- **Lane closures are a graph-resolution artifact.** A 1,000 nm lane is a single edge, and the short
  natural detour around it runs through a port, which the port rule forbids. Closing such a lane
  therefore reports a large detour. Lane closures are kept in
  `data/processed/edge_criticality.csv` but labelled `rank_scope = lane_sensitivity` and sorted
  below the physical chokepoints instead of competing with Hormuz and Suez.
- **Capacity is statistical.** The proxy comes from historical percentiles, which is why only
  throttled edges are removed between loading increments at a saturation threshold; a pass can overshoot capacity. A real canal also has a physical draft limit that the graph
  represents only through an estimated carrying-capacity proxys.
- **Demand is static.** No price elasticity, no substitution between origins, no newbuilding
  response. Every scenario reroutes a fixed weekly demand.
- **Port congestion is not modeled.** Ports are endpoints with no queue of their own.

## 10. Running it

From the repository root, in this order (later scripts read what earlier ones write):

```bash
python scripts/validation/validation_targets.py    # observed targets, incl. H1/H2 split
python scripts/validation/calibrate_flow.py        # OPTIONAL NEW FIT: overwrites retained config; not reproduction of fixed results
python scripts/flow_model/flow_solver.py           # baseline raw flows
python scripts/flow_model/edge_metrics.py          # baseline contract table
python scripts/flow_model/scenario_engine.py       # historical events + criticality ranking
python scripts/flow_model/gnn_sim_generator.py     # simulated training scenarios
python scripts/validation/validate_events.py       # scorecard
python scripts/validation/validate_gfw_delays.py   # voyage-time check
```

Run time depends on input size, machine and worker count; earlier timings are not guaranteed. Reproducing the fixed snapshot should preserve config and skip recalibration. All outputs land in `data/`, which is never committed.
`config/flow_params.json` is the one output that belongs in version control, so both groups work from
the same numbers.

Sanity checks: routed plus stranded barrels equal the OD total (144,826,807 bbl/wk), baseline
stranded is zero, no volume is negative, a closed edge carries no flow, and all three validation
events achieve MAE < 25 transits/wk.
