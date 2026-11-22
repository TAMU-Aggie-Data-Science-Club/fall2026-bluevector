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
                + 10 * max(0, utilization - 1)      # queue above capacity
cost_per_barrel = (free_flow_days + delay_days) * daily_tanker_rate / barrels_per_ship
                + toll_usd_per_bbl
                + awrp_per_bbl                      # flat, listed areas only
```

`service_days` is the time a ship nominally spends being served at that edge: one day at a
chokepoint (queue and convoy), and the sailing time itself on open water. The chokepoint base is the
queue time, not the length of the segment, because a 400 nm lane has a 1.3 day base: congestion
priced against sailing time is nearly free, and routes then never avoid a busy corridor.

`barrels_per_ship` is taken from Deliverable 3 where the chokepoint has an observed vessel size
(estimated oil volume divided by weekly tanker transits, clipped to 250k–2.2M barrels), and one
million barrels otherwise.

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

**Step 4 — charge for excess instead of refusing it.** A throttle lowers an edge's capacity; it does
not delete the edge. Traffic above capacity keeps moving and pays the queue term, ten days per unit
of excess utilisation, so a throttled canal is expensive rather than impossible. Only a closed edge
(`throttle = 0`, capacity 0) leaves the graph, which is what makes Hormuz strand 21 % of crude.
Unthrottled lanes use only the soft congestion curve, because their capacity proxy is a 95–99th
percentile of historical transits, not a physical wall; ships really do surge past it, as the Cape
did in 2024.

Removing a throttled edge at 98 % of its capacity — what the model used to do — made utilisation a
dead column: nothing could pass 1.0, so `alpha` and `beta` changed no output under any scenario. With
the ceiling priced, the Ever Given week runs Suez at 204 % of capacity with 10.8 days of queueing.
The ten-days-per-unit slope is the model's own assumption, not a measurement; see section 9.

**Step 5 — record.** Demand that finds no path at all is counted as **stranded**. Closing Hormuz
completely strands about 21 % of global crude demand, which is the model's check that a true
single point of failure behaves like one.

A solved network routes 151,748,398 barrels per week across 5,380 port pairs in the baseline, with
zero stranded barrels and no negative flows.

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
only about 32 transits per week. A throttle applied to the proxy leaves a ceiling far above the
crude that actually uses the edge, so the cut does not bind: halving the proxy still leaves 93 slots
against 32 transits of demand.

The reported `throttle_factor` is converted back onto the Deliverable 3 basis so the column keeps
the units the shared contract defines. At Suez, a throttle of 0.65 leaves 0.65 × 32.3 ≈ 21.0
transits per week, which is reported as `throttle_factor` ≈ 0.11 of the 187.5 proxy.

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

| Scenario | Setting | Why that number |
|---|---|---|
| `red_sea_2023h1` (Dec–Feb) / `red_sea_2024` (Mar–Jun) | no throttle; $0.50 / $1.00 per bbl war-risk premium at Bab-el-Mandeb | Suez stayed open, so the diversion is a price response; the premium is a sourced insurance rate (section 7) |
| `ever_given_2021` | Suez at 1/7 ≈ 0.14 | the canal was blocked 6 of the 7 days in the peak week |
| `panama_drought_2023` | Panama at 24/36 ≈ 0.67 | the ACP cut daily booking slots from about 36 to 24 |

Results against the baseline:

| Scenario | Suez transits | Suez/(Suez+Cape) share | Stranded (bbl/wk) |
|---|---|---|---|
| baseline | 32.3 | 0.7136 | 0 |
| Red Sea H1 | 22.3 | 0.5557 | 0 |
| Red Sea H2 | 10.0 | 0.3279 | 0 |
| Ever Given 2021 | 9.4 | 0.2883 | 0 |
| Panama drought 2023 | 32.3 | 0.7136 | 0 |

Only a full closure strands anything: a throttle prices the excess instead of refusing it. The Panama
scenario barely moves Suez, which is correct — a canal on the other side of the world should not
change Red Sea traffic. Its effect is local: Panama 62.2 → 54.5 transits, about −12 %, at 131 % of
capacity and 3.1 days of queueing.

## 7. Calibration

Nothing about the crisis itself is fitted any more. The congestion curve `alpha`/`beta` is searched
over a small grid (0.10–0.15 × 3–4), but the Red Sea throughput cut is gone — Suez stayed open
through 2024, so treating the diversion as a capacity loss would replay the observation inside the
constraint meant to predict it — and the war-risk premium is a sourced insurance rate, not a
parameter. The objective, still evaluated on H1 only, is:

```
loss = |suez_pct_model - suez_pct_observed| / 100
     + |cape_pct_model - cape_pct_observed| / 100
     + |suez_share_model - suez_share_observed|
```

**The split matters more than the grid.** Parameters are fitted on December 2023–February 2024 (H1)
only. March–June 2024 (H2) is never used to choose anything, so scoring it is a real out-of-sample
test rather than a fitted number dressed up as validation.

Values: alpha 0.10, beta 3.0 (all six grid points give identical Red Sea results; under Ever Given
they move the Suez drop by about one point, −70.8 → −71.6 %, because the over-capacity queue carries
most of the delay), no Red Sea throttle, and a sourced premium of $0.50/bbl for H1
and $1.00/bbl for H2 charged once at Bab-el-Mandeb. On H1 that predicts a Suez share of 0.5557
against 0.5412 observed (error 0.0145, inside the 5 % target). On held-out H2 the same structure
with the H2 premium predicts 0.3279 against 0.4167, an error of 0.089.

The premium is now the only thing moving traffic, and it does most of the job where it is sourced
tightly. On H1 a $0.50/bbl surcharge at one gate predicts a −30.9 % Suez drop against −29.5 %
observed: 1.4 percentage points, from an input that owes nothing to the transit counts. H2 shows the
limit of one number per window: the ~1 % January peak applied across March–June predicts −67.7 %
against −43.5 %, because rates averaged below their peak over those four months.

What no input fixes is the Cape. The model moves about 0.5 Cape transits per Suez transit lost
(+37.7 % against −30.9 %); the observation is 2.34 (+68.8 % against −29.5 %). More premium only
drives Suez past the observation without buying the Cape response, which points at the corridor's
absorption — its re-based capacity of 16.8 transits/week against a 548k-bbl parcel — not at a
parameter.

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
| Red Sea H2 | 32.5 | 41.1 | 0.207 | 0.6 | 19.3 |
| Ever Given peak | 18.2 | 26.2 | 0.186 | 0.7 | 18.9 |
| Panama drought | 17.2 | 23.2 | 0.142 | 0.5 | 16.2 |

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

- **The Red Sea intensity is one number per window, and the crisis got worse over time.** One sourced
  premium reproduces the H1 share to 0.015 and misses H2 by 0.089, because the ~1 % January rate
  averaged below its peak across March–June. A time-varying premium would fit better and is not
  implemented.
- **The model under-predicts the Cape surge.** In H1 it predicts +37.7 % Cape transits against +68.8 %
  observed; in H2, +65.0 % against +123.2 %. Part of that is the crude-only basis, part is that real
  operators redeployed more capacity than a cost-minimizing router does.
- **Ever Given overshoots the Cape.** The canal is throttled to one seventh for a whole week while the
  real blockage cleared in six days, so the model sends +79 % of traffic round the Cape against
  +5.7 % observed. A six-day blockage is absorbed by waiting, and the model has no time dimension: it
  turns a week of queueing into a 16,000 nm detour.
- **Displaced traffic is the weak spot, not the disrupted canal.** Under the Panama drought the model
  halves the observed cut (−12.4 % against −25.8 %) and sends nothing to the Cape, where reality
  added 51.4 %. Letting the canal run at 131 % of capacity with 3.1 days of queueing is too
  permissive for a slot cut that lasted months.
- **Lane closures are a graph-resolution artifact.** A 1,000 nm lane is a single edge, and the short
  natural detour around it runs through a port, which the port rule forbids. Closing such a lane
  therefore reports a large detour. Lane closures are kept in
  `data/processed/edge_criticality.csv` but labelled `rank_scope = lane_sensitivity` and sorted
  below the physical chokepoints instead of competing with Hormuz and Suez.
- **Above capacity, the delay is invented.** Utilisation may pass 1.0 because a chokepoint queues
  rather than refusing traffic, but the slope is ten days per unit of excess — a number with no
  source. At Suez under Ever Given it carries 10.4 of the 11.3 delay days, which is why `alpha` and
  `beta` barely move the answer. A real queue diverges as utilisation approaches 1; a straight line
  does not.
- **Capacity is statistical.** The proxy comes from historical percentiles, so capacity binds only
  through the queue price, never as a physical wall. A real canal also has a draft limit that the
  graph represents only through the observed vessel sizes.
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
python scripts/flow_model/scenario_engine.py       # four events + criticality ranking
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
