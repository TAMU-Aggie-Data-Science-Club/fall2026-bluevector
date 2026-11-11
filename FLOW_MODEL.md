# The Flow Model: How It Works and What It Gets Wrong

This is the reference for the crude-oil flow model in `scripts/flow_model/` and the checks in
`scripts/validation/`. It covers why the model exists, what it takes in, the equations it solves,
how the parameters were fitted, and how far off it currently is against real disruptions.

Read [`deliverables/DELIVERABLE4.md`](deliverables/DELIVERABLE4.md) for the assignment itself and
the shared column contract. This file documents what we actually built.

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
| `config/edges.csv` | Deliverable 3 | 279 edges: 10 chokepoints, 69 lanes, 200 port connectors. Each carries length, a capacity proxy, a toll, and the observed vessel size |
| `data/interim/od_matrix.csv` | Deliverable 3 | Port-to-port crude oil demand in barrels per year, from Comtrade scaled to PortWatch port totals |
| `config/flow_params.json` | Deliverable 4 | Speed, congestion curve, ship economics, the calibrated Red Sea parameters |

Everything runs on a **one-week time step**, so demand is converted once, at load:

```
barrels_per_week = barrels_per_year / 52.1429
```

Weekly units are what PortWatch reports for chokepoints, so the model and the data we validate
against are in the same clock.

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
delay_days      = service_days * alpha * min(utilization, 5) ** beta
cost_per_barrel = (free_flow_days + delay_days) * daily_tanker_rate / barrels_per_ship
                + toll_usd_per_bbl
                + war_risk_per_bbl * (1 - throttle)
```

`service_days` is the time a ship nominally spends being served at that edge: one day at a
chokepoint (queue and convoy), and the sailing time itself on open water. The chokepoint base is the
queue time, not the length of the segment, because a 400 nm lane has a 1.3 day base: congestion
priced against sailing time is nearly free, and routes then never avoid a busy corridor.

`barrels_per_ship` is taken from Deliverable 3 where the chokepoint has an observed vessel size
(estimated oil volume divided by weekly tanker transits, clipped to 250k–2.2M barrels), and one
million barrels otherwise.

**Step 3 — load demand in ten passes.** A single shortest-path assignment would drop every barrel
on one route and ignore congestion entirely. Instead each pass routes one tenth of each
origin-destination pair along current cheapest paths, then recomputes costs from the flows so far.
Ten passes is enough for routes to settle without spending minutes per scenario.

**Step 4 — saturate throttled edges.** An edge that has been throttled and is already at 98 % of its
available throughput is removed from the graph for the remaining passes. The extra cargo then has to
reroute or strand, which is the point of closing a canal. Unthrottled lanes keep only the soft
congestion curve, because their capacity proxy is a 95–99th percentile of historical transits, not a
physical wall; ships really do surge past it, as the Cape did in 2024.

**Step 5 — record.** Demand that finds no path at all is counted as **stranded**. Closing Hormuz
completely strands about 21 % of global crude demand, which is the model's check that a true
single point of failure behaves like one.

A solved network routes 232,164,410 barrels per week in the baseline, with zero stranded barrels
and no negative flows.

## 4. What a throttle means

This is the decision that makes or breaks every scenario.

A throttle is a **fraction of pre-event crude throughput still available**. 0.0 is closed, 0.65
means 65 % of the edge's pre-event crude still passes. Capacity follows from that:

```
capacity_weekly = throttle * baseline_transits(edge)          when throttle < 1
capacity_weekly = capacity_proxy                              when open
throttle_factor = capacity_weekly / capacity_proxy            (reported, 0..1)
```

Capacity is built from measured crude throughput rather than from `capacity_proxy`, because the
proxy counts **all tankers** (187 transit slots per week at Suez) while the modeled **crude** flow is
only about 47 transits per week. A throttle applied to the proxy leaves a ceiling far above the
crude that actually uses the edge, so the cut does not bind: halving the proxy still leaves 93 slots
against 47 transits of demand.

The reported `throttle_factor` is converted back onto the Deliverable 3 basis so the column keeps
the units the shared contract defines. At Suez, a throttle of 0.65 leaves 0.65 × 47.4 ≈ 30.8
transits per week, which is reported as `throttle_factor` ≈ 0.16 of the 187.5 proxy.

Edges that carried no crude at baseline fall back to `throttle * capacity_proxy`, so a throttled but
unused edge is never reported as closed while still carrying flow.

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
| `red_sea_2024` | Suez and Bab-el-Mandeb at 0.65 | calibrated on Dec 2023–Feb 2024 (section 7) |
| `ever_given_2021` | Suez at 1/7 ≈ 0.14 | the canal was blocked 6 of the 7 days in the peak week |
| `panama_drought_2023` | Panama at 24/36 ≈ 0.67 | the ACP cut daily booking slots from about 36 to 24 |

Results against the baseline:

| Scenario | Suez transits | Suez/(Suez+Cape) share | Stranded (bbl/wk) |
|---|---|---|---|
| baseline | 47.4 | 0.7254 | 0 |
| Red Sea 2024 | 30.6 | 0.5415 | 820,704 |
| Ever Given 2021 | 6.8 | 0.1630 | 0 |
| Panama drought 2023 | 47.5 | 0.7256 | 0 |

The Panama scenario barely moves Suez, which is correct: a canal on the other side of the world
should not change Red Sea traffic. Its effect is on the Panama edge itself (69.4 → 48.6 transits,
about −30 %).

## 7. Calibration

Four parameters are fitted: the congestion curve `alpha` and `beta`, the Red Sea throughput
retained, and the war-risk surcharge per barrel. The search is a grid (alpha 0.10–0.15, beta 3–4,
throttle 0.45–0.85, surcharge $0–1/bbl) minimizing:

```
loss = |suez_pct_model - suez_pct_observed| / 100
     + |cape_pct_model - cape_pct_observed| / 100
     + |suez_share_model - suez_share_observed|
```

**The split matters more than the grid.** Parameters are fitted on December 2023–February 2024 (H1)
only. March–June 2024 (H2) is never used to choose anything, so scoring it is a real out-of-sample
test rather than a fitted number dressed up as validation.

Fitted values: alpha 0.10, beta 3.0, Red Sea throttle 0.65, war-risk $0.25/bbl. On H1 that gives a
Suez share of 0.5415 against 0.5412 observed (error 0.0003, inside the 5 % target). On held-out H2
the same parameters give 0.5415 against 0.4167 observed, an error of 0.125.

The war-risk surcharge deserves a caveat: the grid allowed up to $1/bbl and the fit chose a low
value. Transit counts alone identify it weakly, because a surcharge and a throughput cut both push
traffic the same way.

## 8. What is validated, and how

The model counts **crude only**. PortWatch counts **all tankers**. Comparing the two levels directly
would produce a permanent error of roughly 2.5×, so validation compares percentage shifts: the
model's change is applied to the observed pre-event level, and that implied level is compared with
the observed crisis level. Every reported error is therefore about direction and relative size, not
about absolute transit counts.

Scored events are held out from calibration: Red Sea H2, the Ever Given peak week (23–29 March
2021), and the Panama drought.

| Event | MAE (transits/wk) | RMSE | MAPE | Direction match | Mean gap (pct points) |
|---|---|---|---|---|---|
| Red Sea H2 | 18.7 | 24.3 | 0.143 | 0.8 | 15.1 |
| Ever Given peak | 21.0 | 29.8 | 0.227 | 0.7 | 21.6 |
| Panama drought | 17.5 | 23.5 | 0.273 | 0.6 | 35.6 |

RMSE sits well above MAE in all three, which says the error is concentrated in a few corridors
rather than spread evenly. The per-corridor detail in
`data/processed/validation_event_details.csv` shows where.

### Voyage time check

One corridor has a published benchmark: Persian Gulf to Rotterdam, 19 days via Suez and about 35
days via the Cape (EIA, February 2024, laden Suezmax at 14 knots). The model gives 21.2 and 38.0
days, so the extra time is 16.85 days against 16.0, an error of 0.85 days. Absolute levels differ
by about 2 days because the graph uses a 13-knot fleet speed, which is why only the extra time is
scored. Two structural controls (Gulf to Asia, Houston to Rotterdam) must show zero extra time, and
do; they catch the failure mode where Red Sea traffic leaks into unrelated lanes.

## 9. Current errors and limits

Read these as the model's honest boundaries.

- **The Red Sea intensity is one number, and the crisis got worse over time.** A single calibrated
  throttle reproduces the H1 share almost exactly and misses H2 by 0.125, because diversions deepened
  through the spring. A time-varying throttle would fit better and is not implemented.
- **The model under-predicts the Cape surge.** In H1 it predicts +44 % Cape transits against +69 %
  observed; in H2, +44 % against +123 %. Part of that is the crude-only basis, part is that real
  operators redeployed more capacity than a cost-minimizing router does.
- **Ever Given overshoots.** The canal is throttled to one seventh for a whole week, while the real
  blockage cleared after six days, so the modeled drop (−86 % Suez) is deeper than observed (−76 %).
- **Displaced traffic is the weak spot, not the disrupted canal.** Under the Panama drought the model
  gets the canal right (−30 % vs −26 %) but sends almost nothing to the Cape, where reality added
  51 %. It also over-predicts Chile traffic on a very small base (6.8 → 26.7 modeled transits vs 9.7
  observed).
- **Lane closures are a graph-resolution artifact.** A 1,000 nm lane is a single edge, and the short
  natural detour around it runs through a port, which the port rule forbids. Closing such a lane
  therefore reports a large detour. Lane closures are kept in
  `data/processed/edge_criticality.csv` but labelled `rank_scope = lane_sensitivity` and sorted
  below the physical chokepoints instead of competing with Hormuz and Suez.
- **Capacity is statistical.** The proxy comes from historical percentiles, which is why only
  throttled edges get a hard ceiling. A real canal also has a physical draft limit that the graph
  represents only through the observed vessel sizes.
- **Demand is static.** No price elasticity, no substitution between origins, no newbuilding
  response. Every scenario reroutes a fixed weekly demand.
- **Port congestion is not modeled.** Ports are endpoints with no queue of their own.

## 10. Running it

From the repository root, in this order (later scripts read what earlier ones write):

```bash
python scripts/validation/validation_targets.py    # observed targets, incl. H1/H2 split
python scripts/validation/calibrate_flow.py        # writes config/flow_params.json
python scripts/flow_model/flow_solver.py           # baseline raw flows
python scripts/flow_model/edge_metrics.py          # baseline contract table
python scripts/flow_model/scenario_engine.py       # three events + criticality ranking
python scripts/flow_model/gnn_sim_generator.py     # 337 training scenarios (--workers N)
python scripts/validation/validate_events.py       # scorecard
python scripts/validation/validate_gfw_delays.py   # voyage-time check
```

Calibration takes about a minute; the scenario engine about twenty seconds; the GNN generator about
four minutes on eight workers and around forty on one. All outputs land in `data/`, which is never
committed. `config/flow_params.json` is the one output that belongs in version control, so both
groups work from the same numbers.

Sanity checks worth running after any change: routed plus stranded barrels equal the OD total,
baseline stranded is zero, no volume is negative, a closed edge carries no flow, and the Suez share
still lands near the calibrated value.
