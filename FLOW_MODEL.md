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
| `config/flow_params.json` | Deliverable 4 | Speed, congestion curve, ship economics, and the sourced Red Sea premium |

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
delay_days      = free_flow_days * alpha * min(utilization, 5) ** beta
cost_per_barrel = (free_flow_days + delay_days) * daily_tanker_rate / cost_barrels_per_cargo
                + toll_usd_per_bbl
                + awrp_per_bbl                      # flat, listed areas only
```

That is DELIVERABLE4's formula with nothing added. `alpha` 0.15 and `beta` 4.0 are the curve's
published defaults, not fitted. `capacity_weekly` is PortWatch's `capacity_proxy` on every edge — the
95th percentile of that corridor's weekly tanker transits, 2019–2023 — used as observed, and never
rescaled to the model's own output.

`barrels_per_ship` is taken from Deliverable 3 where the chokepoint has an observed vessel size
(estimated oil volume divided by weekly tanker transits, clipped to 250k–2.2M barrels), and one
million barrels otherwise. It sets **how many ships** an edge carries, not the price of a barrel:
the two dollar terms divide by `cost_barrels_per_cargo` instead (1,000,000 bbl, the Suezmax the
model prices). Section 7 explains why those are two different questions.

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

**Step 4 — a throttle is a wall, not a price.** A throttle lowers an edge's capacity to that fraction
of the transits it carried at baseline. Once flow on it reaches the ceiling it leaves the graph for
the remaining passes, so the surplus reroutes or strands rather than queueing for free. Only a closed
edge (`throttle = 0`) strands traffic outright, which is what makes Hormuz strand 21 % of crude. The
BPR term is the only congestion price, and at observed utilisation (0.33 at Suez, 0.06 at the Cape)
it is small: congestion is not what decides a route in this network.

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
| `red_sea_2023h1` (Dec–Feb) / `red_sea_2024` (Mar–Jun) | no throttle; $0.41 / $0.83 per bbl war-risk premium at Bab-el-Mandeb | Suez stayed open, so the diversion is a price response; the premium is a sourced insurance rate (section 7) |
| `ever_given_2021` | Suez at 1/7 ≈ 0.14 | the canal was blocked 6 of the 7 days in the peak week |
| `panama_drought_2023` | Panama at 24/36 ≈ 0.67 | the ACP cut daily booking slots from about 36 to 24 |

Results against the baseline:

| Scenario | Suez transits | Suez/(Suez+Cape) share | Stranded (bbl/wk) |
|---|---|---|---|
| baseline | 61.5 | 0.9153 | 0 |
| Red Sea H1 | 20.7 | 0.5155 | 0 |
| Red Sea H2 | 9.3 | 0.2894 | 0 |
| Ever Given 2021 | 14.1 | 0.3735 | 0 |
| Panama drought 2023 | 61.6 | 0.9153 | 0 |

Only a full closure strands anything: a throttle prices the excess instead of refusing it. The Panama
scenario barely moves Suez, which is correct — a canal on the other side of the world should not
change Red Sea traffic. Its effect is local: Panama 62.2 → 54.5 transits, about −12 %, at 131 % of
capacity and 3.1 days of queueing.

## 7. Cost basis, and what is fitted

Every cost input is a published number converted on one vessel: a Suezmax carrying 1,000,000 bbl, an
$82.8M hull, a $45,800/day time charter (Frontline's 2024 Q1 Suezmax TCE). `cost_barrels_per_cargo`
is that divisor. It is deliberately separate from `barrels_per_ship` — the observed cargo per transit
that sets how many ships an edge carries — because charging a per-transit tariff against the
observed all-tanker average puts a Suezmax toll on a ship a third its size. At Suez that average is
347,000 bbl, so the model used to divide a tariff converted on 1,000,000 by 347,000: $0.45/bbl of
toll against $0.15/bbl of sailing on the same edge. Converted consistently, the tariff is ten times
the sailing cost of the passage it applies to.

Tolls are the tariff **in force in each window**, not one figure for all history, at SCNT 78,500 and
PC/UMS 65,000, one way: Suez $444,200 for 2023 (the 1 Jan 2023 schedule plus the 25 % laden tanker
surcharge that has applied since 1 April 2023) and $510,700 from 15 January 2024; Panama $384,500 in
2023 ($300,000 fixed plus $1.30 per PC/UMS ton) and $446,250 in 2024; Bosporus $42,914 one way at
gold-franc $4.42 and 50,000 NT. The Ever Given window uses the 2020 Suez schedule, $305,000 laden,
with no surcharge. The premium is 0.5 % of hull value for H1 and 1 % for H2, which is $0.41 and $0.83
per barrel on the same divisor. Sources, and the values still unconfirmed, are in
[`DATA.md`](DATA.md).

**Nothing is fitted to the transit counts.** `alpha`/`beta` are grid-searched by
`scripts/validation/calibrate_flow.py` in two windows: the Red Sea H1 price response (0.10–0.15 × 3–4)
and the Ever Given capacity event (0.15–1.00 × 3–8), because a price-only window never lifts
utilisation far enough for the curve to act. Both grids are flat against the DELIVERABLE4 defaults —
no point improves the loss by a hundredth — so the config keeps 0.15 and 4.0, and the run records the
grids as unidentified instead of promoting whichever point sorts first.

The Ever Given miss that remains is not congestion. The model drops Suez 77.2 % against 43.7 %
observed and raises the Cape 313.7 % against 5.0 %, and no α/β tested moves either by more than a
tenth of a point. The blockade forces the Suez fall; the Cape rise is the all-or-nothing reroute.

No Red Sea throttle is used: Suez stayed open, so the diversion is a price response, and treating it
as a capacity loss would replay the observation inside the constraint.

What the model then does against observation, on definitions that can be recomputed from
`data/processed/chokepoint_weekly_attributes.csv`:

| Window | Model share | Observed share | Error |
|---|---|---|---|
| Baseline (to Oct 2023) | 0.9153 | 0.6279 | +0.287 |
| Red Sea H1 | 0.5155 | 0.5604 | −0.045 |
| Red Sea H2 (held out) | 0.2894 | 0.4175 | −0.128 |

### Why the share is a sensitivity, not a validation

Two things set that share: what each route costs, which is now sourced, and **how readily traffic
substitutes between routes**, which is not a price and has no data source. Operators hold contracts,
charter commitments and sailing schedules; this router holds none, so it over-reacts. That is why the
old fit worked: dividing per-transit dollars by the observed all-tanker cargo silently tuned the
substitution response. Moving to a consistent basis, without touching any premium, moves the H1 share
from 0.5557 to 0.5273 and the baseline from 0.7136 to 0.9153.

Three consistent variants, none reproducing all three targets:

| Cost convention | Baseline | H1 | H2 |
|---|---|---|---|
| tolls on the observed 347k cargo | 0.1320 | 0.1320 | 0.1320 |
| one-way laden dues, shipped | 0.9153 | 0.5273 | 0.3108 |
| dues charged both ways | 0.6565 | 0.2768 | 0.2239 |
| observed | 0.6279 | 0.5604 | 0.4175 |

Charging dues both ways, which is what a round voyage actually pays, fixes the baseline to within
3 points and halves the crisis response. One-way dues fit H1 and leave the baseline 29 points high.
The trade is the missing stickiness term: the shipped convention is the one whose costs are right for
a laden transit, and the share is reported as an upper bound on rerouting rather than a calibrated
output.

**The largest error is the baseline, and the metric that exposes it is crude volume**, which nothing
checked before. The model routes the right volume through Suez (21.3M bbl/wk against 21.6M observed)
and far too little around the Cape (3.1M against 39.7M), so 0.87 of its Suez-plus-Cape barrels go
through Suez against 0.35 observed.

Capacity is not the cause, and neither is a fitted constant. With every edge at its observed capacity
the baseline delay is a rounding error — 0.02 days at Suez, 0.000 at the Cape, at utilisation 0.33
and 0.06 — so the congestion curve barely participates. What sets the split is route cost: the Suez
passage costs $0.491/bbl, of which $0.444 is the toll, against about $0.43/bbl of charter for the
9.3-day Cape detour. An all-or-nothing shortest path hands the entire split to that margin, which is
why the baseline share is the largest error in the model.

The observation it is scored against is not the same quantity. PortWatch counts every tanker,
including ballast, and reports deadweight capacity; the model counts laden crude between 100 ports.
That is why utilisation sits at 0.33 on Suez while the observed corridor runs near 1.0, and why the
Cape's 39.7M bbl/wk cannot be set against 3.1M of routed cargo without separating ballast from laden
and Suezmax parcels from VLCCs. Closing that gap is a vessel-class question, not a price question: a
laden VLCC cannot transit Suez at all.

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
| Red Sea H2 | 62.3 | 79.2 | 0.463 | 0.7 | 48.2 |
| Ever Given peak | 42.8 | 64.6 | 0.430 | 0.6 | 40.8 |
| Panama drought | 18.8 | 24.2 | 0.163 | 0.6 | 18.0 |

These scores are about twice the error reported on the previous cost basis. That comparison is the
honest measure of what the fitted basis was buying, and it is why the share is no longer called a
validation.

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

- **The baseline is the largest error, and it is structural.** The model puts 0.9153 of Suez-plus-Cape
  transits on Suez against 0.6279 observed, and 0.87 of the barrels against 0.35. The Suez volume is
  right (21.3M bbl/wk against 21.6M); the Cape is starved (3.1M against 39.7M). That is the lane graph
  and the OD matrix, not a price: no toll, rate or premium closes it.
- **The Red Sea intensity is one number per window, and the model over-reacts to it.** One sourced
  premium reproduces the H1 share to 0.033 and misses H2 by 0.107, and in H1 it moves +235 % of
  traffic onto the Cape where observation shows +69 %. A router with no contracts, charters or
  redeployment lag substitutes harder than operators did. A time-varying premium is not implemented
  and would not fix the substitution response.
- **Ever Given overshoots the Cape.** The canal is throttled to one seventh for a whole week while the
  real blockage cleared in six days, so the model sends +192 % of traffic round the Cape against
  +5.7 % observed. A six-day blockage is absorbed by waiting, and the model has no time dimension: it
  turns a week of queueing into a 16,000 nm detour.
- **Displaced traffic is the weak spot, not the disrupted canal.** Under the Panama drought the model's
  cut lands on Panama itself and leaves Suez and the Cape untouched, while reality added 51.4 % at the
  Cape. Letting the canal run above capacity with a queue price is too permissive for a slot cut that
  lasted months.
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
