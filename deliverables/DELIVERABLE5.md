# 🧠 Fifth Deliverable: GNN & Crude Oil Price Model

**📅 Due: Wednesday, November 18, 2026**

> **Integration results, 2026-11-22:** the executed comparison and corrected methodology are in
> [section 9](#9-integration-results--2026-11-22). That section supersedes the original numerical
> examples and modeling assumptions below. Both simulated-plus-real and strictly real-supervised
> GNNs were run. Historical route costs and causal Brent supply-risk premiums are not observed.


**Who this is for.** Seven people, none of whom built Deliverable 4. Section 2 gives you
everything from Deliverable 4 you need to start; you do not have to read the old scripts. When you
want the full physics detail, read [`FLOW_MODEL.md`](../FLOW_MODEL.md).

## 🎯 1. What you are building

Two layers that sit on top of Deliverable 4's physics model.

**The GNN (4 people).** Deliverable 4 routes demand with a solver that takes about 7 seconds per
scenario. The GNN learns that same mapping — graph plus throttle settings in, per-edge flow,
transits, utilization and delay out — so it can answer in milliseconds and handle combinations
nobody ran through the solver. It also produces a short summary vector (an "embedding") describing
the whole network, which the price model can use as a feature.

**The price model (3 people).** Turns network change into money: shipping cost per barrel under
three tanker charter rates, three network indices, then a weekly Brent model with prediction ranges,
backtested against the 2023–24 Red Sea crisis and against a random walk.

Both halves run in parallel because the two file contracts in section 3 are fixed before anyone
starts writing code.

"Done" means all four of these are true:

- GNN predictions match the physics solver on scenarios it has seen, and its error on held-out real
  events is reported side by side with the solver's.
- Shipping cost and the three indices are computed by one function, identically for history and for
  scenarios.
- The Brent model is compared against a random walk on 2023–24, with interval coverage reported, and
  the result is stated honestly whether it wins or loses.
- Every output table has a `source` column saying whether a row is `observed`, `modeled` or `gnn`.

## 📚 2. Deliverable 4 in ten minutes

### 2.1 The network

A graph of the world's crude oil shipping:

- **142 nodes:** 100 ports plus 42 open-sea junctions (points where lanes meet, like the Gulf of Aden).
- **279 edges:** 10 chokepoints (Suez, Bab-el-Mandeb, Hormuz, Malacca, Gibraltar, Panama, Bosporus,
  Cape of Good Hope, Sunda, Magellan), 69 open-sea lanes, and 200 port connectors.
- **Port connectors** are how a ship enters or leaves a port. Ports are start and end points only;
  a route may never pass through a port as a shortcut.
- Every edge carries `length_nm`, a capacity proxy, and a toll where one exists.

### 2.2 The demand

An origin-destination (OD) matrix built from UN Comtrade and scaled to PortWatch port totals:
**232,164,410 barrels per week**, which is roughly 33 million barrels per day. One week is the time
step everywhere in this project.

### 2.3 How the routing works

The solver finds the cheapest path for every OD pair and moves the barrels along it. "Cheapest"
means dollars per barrel, not miles:

```
cost_per_barrel = (sailing_days + delay_days) * charter_rate / barrels_per_ship
                + toll_per_barrel
                + war_risk_per_barrel
```

It loads demand in ten passes, recomputing congestion after each one, so traffic spreads instead of
all piling onto one route. A short congested canal can be worse than a long quiet detour, and the
model captures that.

### 2.4 What a "throttle" means

A throttle is the **fraction of pre-event crude throughput that is still available**: `1.0` open,
`0.65` means 65 % still passes, `0.0` fully closed. It is not a fraction of the capacity proxy,
because that proxy counts all tankers while this model routes crude only.

When a throttled edge reaches its ceiling, extra cargo reroutes or, if there is no sea alternative
(Hormuz), becomes **stranded**. Stranded barrels are the model's way of saying "this oil cannot move".

### 2.5 What Deliverable 4 produced

| File | What is in it |
|---|---|
| `data/processed/scenario_edge_flows.parquet` | Four solved scenarios: `baseline`, `red_sea_2024`, `ever_given_2021`, `panama_drought_2023`. Every scenario has all 279 edges, so the table has 1,116 rows. |
| `data/processed/gnn_simulated_scenarios.parquet` | 337 training scenarios, 94,023 rows: every lane and chokepoint closed or throttled at 0.0/0.25/0.5/0.75, plus five compound cases. `source = "simulated"`. |
| `data/processed/validation_targets.csv` | Observed PortWatch chokepoint transits for the real events, split into calibration and held-out windows. `source = "observed"`. |
| `data/processed/validation_report.csv` | How far the physics solver is from reality on the held-out events. This is the bar you are measured against. |
| `data/processed/edge_criticality.csv` | Every chokepoint (and 15 busiest lanes) closed one at a time, with stranded share and extra days. |
| `config/flow_params.json` | The solver's settings: 13 knots, congestion `alpha = 0.10`, `beta = 3.0`, $50,000/day charter, 1,000,000 bbl per transit. |

Two weeks of context you will see in the data: the Red Sea crisis cut Suez transits by about 36 %,
and closing Suez does not strand cargo (ships go round the Cape) while closing Hormuz strands about
21 % of demand.

### 2.6 The numbers you have to beat

Held-out events, physics solver versus observed PortWatch (from `validation_report.csv`):

| Event | MAE (transits/week) | RMSE | MAPE | Direction match |
|---|---|---|---|---|
| Red Sea, Mar–Jun 2024 | 18.7 | 24.3 | 0.143 | 0.8 |
| Ever Given peak week | 21.0 | 29.8 | 0.227 | 0.7 |
| Panama drought | 17.5 | 23.5 | 0.273 | 0.6 |

Direction match is the share of corridors where the model moved the right way: 0.8 means 8 of 10.
The solver is good at the disrupted canal and weaker at predicting where diverted traffic lands.
That gap is the honest target for the GNN, not "beat the solver on everything".

### 2.7 The 22-column table contract

Every edge table in the project has these columns. You must produce them exactly.

| Group | Columns | Notes |
|---|---|---|
| Identity | `scenario_id`, `edge_id`, `from_node`, `to_node`, `edge_type`, `chokepoint_portid`, `searoute_passage` | `edge_type` is `lane`, `chokepoint` or `connector` |
| Geometry | `length_nm` | nautical miles |
| State | `throttle_factor`, `oil_volume_bbl`, `tanker_transits`, `capacity_weekly`, `utilization`, `free_flow_days`, `delay_days`, `anomaly_z`, `cost_per_barrel`, `stranded_barrels` | barrels and transits are per week; `utilization = tanker_transits / capacity_weekly` |
| Change vs baseline | `delta_volume_bbl`, `delta_transits`, `share_rerouted` | `share_rerouted` is the fraction of baseline volume that left the edge, floored at zero |
| Provenance | `source` | `observed`, `modeled`, `simulated` or `gnn` |

### 2.8 Words you will see

| Term | Plain meaning |
|---|---|
| Node / edge | A place / a sea route between two places |
| Chokepoint | A narrow passage every ship must use, modelled as a single edge |
| OD pair | One origin port and one destination port |
| Baseline | The world with nothing closed |
| Scenario | The same world with one or more edges throttled |
| Stranded | Demand that cannot reach its destination at all, in barrels per week |
| BPR congestion | Standard traffic formula: delay grows with the cube of utilization |
| Holdout | Real events kept out of training so the score is honest |
| MAE / RMSE | Average absolute error / error that punishes big misses more |
| MAPE | Average error as a percentage of the observed value |
| Direction match | Did the model move this corridor up or down correctly |
| Message passing | A GNN layer where each node and edge updates itself from its neighbours |
| Embedding | A short vector that summarises the whole graph for another model |
| Quantile regression | Predicting a range (e.g. 5th to 95th percentile) instead of one number |
| Interval coverage | Share of actual values that fall inside the predicted range; 0.9 is the target for a 90 % interval |

## 🤝 3. The two contracts

**3.1 `data/processed/gnn_edge_predictions.parquet`** — the GNN's output, in the 22-column contract
from section 2.7, with `source = "gnn"` and one `scenario_id` per predicted scenario. The price model
must be able to read it with the same code it uses for the physics table.

**3.2 `data/processed/network_price_features.parquet`** — the price model's input, identical columns
for history and scenarios:

| Column | Meaning |
|---|---|
| `week_or_scenario_id` | Friday-ending date for history, `scenario_id` for scenarios |
| `suez_tanker_share` | `suez / (suez + cape)` tanker transits |
| `avg_voyage_days` | barrel-weighted voyage days across active edges |
| `rerouted_barrel_miles` | `SUM(ABS(delta_volume_bbl) * length_nm)`, in billion barrel-nautical-miles |
| `shipping_cost_per_bbl_low` / `_base` / `_high` | fleet-average cost at $35k / $50k / $75k per day charter rates |
| `delta_cost_per_bbl_base` | change against the pre-disruption baseline |
| `gnn_emb_0` … `gnn_emb_7` | 8 graph-level embedding values; zeros when running on physics or history |
| `source` | `observed`, `modeled` or `gnn` |

## 👥 4. Who does what: 7 people

The GNN group has **4 people, one part each**. The price group has **3 people** covering four
scripts, so one person takes the two foundation scripts. People choose their own slot inside their
group; sizes are there so the split stays fair.

| Slot | Group | Owns | Size | Can start once |
|---|---|---|---|---|
| **G1** | GNN | Dataset and splits (`gnn_dataset.py`) | Medium | Immediately |
| **G2** | GNN | Model architecture (`gnn_model.py`) | Large | Immediately (tensor shapes are fixed) |
| **G3** | GNN | Training and export (`train_gnn.py`) | Large | G1 and G2 exist |
| **G4** | GNN | Evaluation scorecard (`evaluate_gnn.py`) | Medium | G3 exports predictions |
| **E1** | Price | Shipping cost + network indices (`shipping_cost.py`, `network_indices.py`) | Large | Immediately |
| **E2** | Price | Brent model and intervals (`brent_model.py`) | Large | E1 outputs |
| **E3** | Price | Backtest and scenario impacts (`backtest_price.py`) | Medium | E2 outputs |

Branch: GNN group pushes to `issue-5/gnn`, price group to `issue-5/price-model`, and one person per
group opens the pull request into `issue-5/integration`.

## 🧠 5. GNN group

**Hand in:** dataset builder, model, training pipeline, and `gnn_evaluation_report.csv`.

### G1 — Build the dataset (`scripts/gnn/gnn_dataset.py`)

**What it does.** Turns the tables into tensors. The graph is fixed, so build the index once
(`edge_index`, shape `[2, 279]`) and reuse it for every scenario.

**Node features (6):** `is_port`, `is_junction`, `lat_norm`, `lon_norm`, `export_bbl_norm`,
`import_bbl_norm`.

**Edge features (11):** `is_lane`, `is_chokepoint`, `is_connector`, `length_nm_norm`,
`free_flow_days_norm`, `capacity_weekly_norm`, `toll_usd_per_bbl`, `throttle_factor`,
`baseline_volume_norm`, `baseline_transits_norm`, `baseline_utilization`.

**Targets (4):** `oil_volume_bbl`, `tanker_transits`, `utilization`, `delay_days`.

**Data sources.** Pretraining comes from the 337 simulated scenarios. Fine-tuning uses
`chokepoint_weekly_attributes.parquet` — real weekly transits at chokepoints — with a mask so only
chokepoint edges contribute to that loss, because those are the only edges we actually observe.

**Splits.** Hold out `ever_given_2021`, `panama_drought_2023`, and Red Sea Mar–Jun 2024. Normalise
features using training data only; using the full period leaks the answer into the inputs.

**Acceptance:** no `NaN` or `Inf` in any tensor; held-out events provably absent from the training
split; shapes match 142 nodes and 279 edges.

### G2 — The model (`scripts/gnn/gnn_model.py`)

**Why an edge-centric GNN.** Our predictions live on edges, not nodes, so the model must update edge
states as well as node states. Message passing means each layer lets every node and edge look at its
neighbours and revise itself; three layers gives information a three-hop reach, which is enough for a
shock at Suez to reach the Gulf.

```
h_node0 = MLP_node(X_node)
h_edge0 = MLP_edge(X_edge)

for l in 1..3:
    h_node = LayerNorm(h_node + ReLU(Linear(aggregate(incoming edge messages))))
    h_edge = LayerNorm(h_edge + MLP_edge_update([h_node_u, h_node_v, h_edge]))
```

**Two heads.** One regresses the four edge targets through `Softplus` so nothing goes negative, and
forces `oil_volume_bbl = 0` wherever `throttle_factor == 0` (a closed canal cannot carry oil). The
other pools edge states into an 8-value graph embedding.

**Acceptance:** a forward pass on the real graph returns `(279, 4)` non-negative predictions and an
`(8,)` embedding; closed edges return exactly zero flow.

### G3 — Training and export (`scripts/gnn/train_gnn.py`)

**Two losses.**

1. Huber loss on scaled targets — robust to the handful of huge chokepoint values.
2. Flow conservation: at every non-port junction, incoming barrels should equal outgoing barrels. This
   is the physics the solver respects and the GNN otherwise would not.

**Two stages.** Pretrain on simulated scenarios (plenty of data, exact labels), then fine-tune on
observed chokepoint weeks with the masked loss. Save weights, then export predictions in the
22-column contract and the embeddings table.

**Acceptance:** validation loss falls and then plateaus (no divergence); the exported table has all 22
columns, `source = "gnn"`, no `NaN` on active edges.

### G4 — Evaluation (`scripts/gnn/evaluate_gnn.py`)

**What to compare.** On the held-out events and on synthetic multi-edge closures, score three
models on the same edges and the same weeks: the physics solver, the GNN, and a time-series baseline
(ARIMA or a small LSTM on weekly chokepoint transits).

**Metrics.** MAE, RMSE, MAPE and direction match, computed the same way as
`validation_report.csv` so the numbers sit in one table without conversion.

**Be honest about what this measures.** The GNN is trained on solver output, so on synthetic
scenarios it is mostly measuring how well it copies the solver. The real test is the held-out
PortWatch events, where the solver is also imperfect. Report both columns separately.

**Acceptance:** `gnn_evaluation_report.csv` has one row per (event, model) with all four metrics, and
the physics numbers reproduce `validation_report.csv` exactly. If they do not, the comparison is
broken, not the solver.

## 📈 6. Price group

**Hand in:** shipping cost calculator, network index builder, Brent model, and backtest report.

### E1 — Shipping cost and indices (`shipping_cost.py`, `network_indices.py`)

**Cost per barrel on an edge.** Charter rate is the cost of hiring the ship; dividing by cargo size
turns it into dollars per barrel, so a bigger ship is cheaper per barrel:

```
edge_days = free_flow_days + delay_days
edge_cost_per_bbl(rate) = edge_days * rate / barrels_per_ship + toll_per_barrel
```

Run it at $35,000, $50,000 and $75,000 per day. The three values are the uncertainty band on freight,
not a prediction. Fleet-level cost is barrel-weighted across edges, and the scenario number is the
change against baseline.

**Three indices.** One function, used identically for history and scenarios:

1. `suez_tanker_share = suez / (suez + cape)` — the Europe–Asia diversion.
2. `avg_voyage_days = SUM(barrels * days) / SUM(barrels)` — how long the average barrel is at sea.
3. `rerouted_barrel_miles = SUM(ABS(delta_volume) * length_nm) / 1e9` — work the fleet absorbs.

Join weekly indices to `eia_brent_weekly.parquet` to make `historical_price_dataset.parquet`, and
compute the same columns for every scenario into `scenario_price_features.parquet`.

**Acceptance:** closing Suez raises the Europe corridor cost by roughly $1.50–$3.50/bbl across the
three rates; no `NaN`; history and scenario paths return identical column names and units.

### E2 — Brent model and intervals (`brent_model.py`)

**Why an autoregressive anchor.** Oil prices are close to a random walk week to week, so a model
built only on shipping features will produce spurious fits. Include lagged price (`brent_lag1`,
`brent_ma4`) and let the network features explain the deviation from it.

**Features:** the anchor terms, `delta_cost_per_bbl_base`, `suez_tanker_share` change, extra voyage
days, `rerouted_barrel_miles`, and optionally PCA-compressed `gnn_emb_*`.

**Model:** ARX / Ridge for the mean, quantile regression for the 5th and 95th percentiles. That gives
a range rather than false precision.

**Outputs:** `pred_brent_mean`, `pred_brent_lower_90`, `pred_brent_upper_90`, plus the decomposition
into freight surcharge and supply-risk premium.

**Acceptance:** intervals are ordered (`lower < mean < upper`) for every row; scenario impacts for
Red Sea, Hormuz, Malacca and Ever Given have plausible signs and sizes; no negative prices.

### E3 — Backtest and scenario impacts (`backtest_price.py`)

**Setup.** Train on 2019-01 to 2023-10 only. Test on 2023-11 to 2024-06, one week ahead. Four
models, same test weeks:

1. naive random walk (`pred_t = price_{t-1}`)
2. AR(1) on price alone
3. Blue Vector with physics/PortWatch indices
4. Blue Vector with GNN embeddings added

**Report:** RMSE, MAE, directional accuracy, and interval coverage. Random walk is a hard baseline in
oil markets; matching it is a real result and beating it badly on four weeks is probably luck.

**Also produce** `scenario_price_impacts.csv`: predicted Brent impact of closing Suez, Bab-el-Mandeb,
Hormuz, Malacca and Panama, with ranges.

**Acceptance:** the backtest trains only on pre-November 2023 data (check the code, not the promise),
all four models appear in `price_backtest_report.csv`, and the conclusion sentence in the report
matches the table.

## ✅ 7. Done checklist

**GNN (G1–G4)**
- [x] `gnn_dataset.py` builds tensors with no `NaN`/`Inf` and excludes the held-out events
- [x] `gnn_model.py` returns non-negative `(279, 4)` edge predictions plus `(8,)` embeddings, zero flow on closed edges
- [x] `train_gnn.py` saves weights and exports `gnn_edge_predictions.parquet` in the 22-column contract with `source = "gnn"`
- [x] `evaluate_gnn.py` writes `gnn_evaluation_report.csv` with solver, GNN and baseline side by side

**Price (E1–E3)**
- [x] `shipping_cost.py` writes Low/Base/High cost changes to `scenario_shipping_costs.csv`
- [x] `network_indices.py` builds compatible history/scenario tables, with unavailable historical route fields explicitly missing (methodology corrected in §9)
- [x] `brent_model.py` writes scenario impacts with ordered 90 % intervals
- [x] `backtest_price.py` writes `price_backtest_report.csv` against random walk and AR(1)

**Both**
- [x] Outputs land in `data/processed/`; nothing under `data/` is committed
- [ ] Publication/PR updates: deferred; PM requested completed commits remain local

## ⚠️ 8. Traps

- **Comparing all-tanker transits to a crude-only model.** PortWatch counts every tanker; the model
  routes crude. Compare percentage shifts, as Deliverable 4 does, or you will report a fake error.
- **Leakage.** Normalising features over the full period, or fine-tuning on the same weeks you test
  on, makes the score look good and means nothing.
- **Closed edges carrying flow.** If `throttle_factor == 0`, volume and transits must be zero. This is
  the first thing a reviewer will check.
- **Judging the price model on price levels.** Report skill on changes or against the random walk;
  R² on a trending series is meaningless.
- **Calling synthetic accuracy validation.** A GNN trained on the solver will copy the solver well.
  The held-out real events are the test.
- **Missing `source`.** Every table says where its rows came from.


## 9. Integration results — 2026-11-22

### What landed and what was completed

After fetching origin, both group tips were already ancestors of `issue-5/integration`:
`issue-5/gnn` at `abc9701` and `issue-5/price-model` at `9ace92f`.
The existing G1 dataset work, G2 architecture, and E1/E2 price scripts were retained.
Training/export (G3), evaluation (G4), and the price backtest (E3) were missing and are now implemented.
The original G1 builder remains available; the new temporal experiment uses its dataset/batching
protocol with explicitly lagged inputs and its own chronological splits.

The local checkout initially contained no datasets. Existing ingestion and OD scripts rebuilt the
inputs from adopted PortWatch, Comtrade and EIA sources. Comtrade now caches bounded yearly
requests, validates sea mode `2100`, normalizes resumed CSV/JSON schemas, and sends credentials in
headers. OD fitting converged in 75 iterations with maximum relative marginal error `9.4e-7`;
all OD checks passed. The rebuilt demand is **144,826,807 barrels/week**, not the original
232,164,410 example. This is the current reproducible input vintage; earlier flow-model scores
are not silently reused. The existing graph and flow calibration settings were preserved.

### GNN: real observations, two training modes

The real task is **one-week-ahead all-tanker transit prediction at the ten observed chokepoints**.
No real labels are invented for unobserved lanes, crude cargo volumes or delays.
Static geometry and node types are shared; OD demand and current tariff values are excluded
from the real-only inputs. Three temporal edge inputs are added to G2's 11-column interface:
previous-week transits, the previous four-week mean, and the 52-week lag (past expanding-mean
fallback when a year is unavailable). Transit inputs and targets are divided by training-only
per-corridor means. The model has 32 hidden units and three message-passing layers.
Only observed transit targets contribute to real-data loss.

The solver stores both travel directions in each physical edge. G2 therefore passes messages
at both endpoints. A signed incoming-minus-outgoing loss on the CSV's arbitrary orientation
would be incorrect, so **directional flow conservation is not claimed**. The simulator surrogate
reconciles volume/transit and transit/capacity identities on export. Its inferred stranded cargo
is a connector-volume residual, and its anomaly field is a zero placeholder, not an observation;
neither is independently supervised. The solver remains authoritative for scenario feasibility.

**Real-only:** all optimization labels come from PortWatch. **Simulated-plus-real:** pretrain
on single-edge flow scenarios, then fine-tune the same architecture on the same real-data
training/validation splits. Simulator labels use training-only target scales. All 20 compound
scenarios are excluded from pretraining and checkpoint selection. The simulator's later-vintage
OD matrix and Red Sea calibration make mixed pretraining a **retrospective prior**, not a clean
as-of-2021 information set. Geographic port selection is also retrospective.

All runs use seeds **7, 19, 43**, a maximum of 150 pretraining/180 fine-tuning epochs,
Adam at `0.001`, Huber loss, gradient clipping, and validation early stopping with patience 25.
Predictions are averaged across all three declared seeds; no test-selected seed is reported.
Previous observed test-week counts may feed the following week's forecast, as in a rolling
one-week prediction; model weights remain frozen. PortWatch publication lag and historical
revisions are not reconstructed, so these are observation-time backtests, not live release-time
trading forecasts.

| Event | Train through | Validation | Test Fridays | Chokepoint-week pairs |
|---|---|---|---|---:|
| Ever Given | 2020-12-25 | 2021-01-01–03-12 | 2021-03-19–04-09 | 40 |
| Panama | 2023-04-28 | 2023-05-05–07-28 | 2023-08-04–12-29 | 220 |
| Red Sea | 2023-07-28 | 2023-08-04–10-27 | 2024-03-01–06-28 | 180 |

Each model uses only the past relative to its event; earlier events can appear in later-event
training histories. Red Sea H1 observations are not used for temporal fine-tuning or checkpoint
selection. Full seven-day weeks are required; missing weeks stay missing. These windows include
pre/post disruption observations and are not identical to Deliverable 4's event-average windows.

**RMSE, tanker transits/week; smaller is better:**

| Event | Real-only GNN | Simulated + real GNN | Persistence | Seasonal |
|---|---:|---:|---:|---:|
| Ever Given | 25.20 | 25.16 | 26.90 | 31.94 |
| Panama | 20.41 | 20.00 | 24.60 | 26.29 |
| Red Sea | 24.52 | 27.09 | 23.11 | 53.87 |

Mixed training improves on persistence by 6.5% for Ever Given and 18.7% for Panama, but loses
by 17.2% on Red Sea. Real-only loses by 6.1% on Red Sea. Thus simulator pretraining does not
uniformly improve real generalization. These few historical events, particularly four Ever Given
weeks, do not establish performance on unseen disruptions or unobserved corridors.
MAE, MAPE, zero-denominator counts, directional accuracy, seed variation and per-edge scores are
in `gnn_evaluation_report.csv`, `gnn_seed_evaluation.csv`, and `gnn_evaluation_by_edge.csv`.
Persistence predicts no change, so its sign-based directional accuracy is near zero by definition;
its competitive RMSE is the more useful comparison.

`physics_conditional` in the weekly scorecard applies the solver's crude percentage changes to
training-period observed tanker means. It shares the GNN's weekly observations and units, but is
a static estimate given the disruption, not a lag-only forecast. Do not call this a fair forecasting
contest. The separate existing event-average protocol was rerun unchanged:

| Deliverable 4 event-average window | Physics MAE | Physics RMSE | MAPE | Direction match |
|---|---:|---:|---:|---:|
| Ever Given peak | 23.628 | 31.794 | 23.42% | 80% |
| Panama drought | 16.233 | 23.040 | 11.18% | 70% |
| Red Sea H2 | 22.160 | 30.115 | 17.72% | 80% |

Those numbers reproduce this run's `validation_report.csv`; they must not be compared directly
with weekly GNN errors because averaging suppresses noise and the windows differ.

### GNN: copying the solver is a separate test

The separate simulator-only surrogate exports the 22-column `gnn_edge_predictions.parquet` for
20 unseen compound closures and three historical scenario inputs. It is **not** the real-only
forecaster and must not be described as a real-validated counterfactual model.
The following scores use the reconciled, three-seed exported predictions on the 20 unseen
compound cases (5,580 edge-scenario pairs), with the unchanged baseline as a comparator:

| Target | MAE | RMSE | WAPE | RMSE skill over unchanged baseline |
|---|---:|---:|---:|---:|
| Oil volume, barrels/week | 500,036 | 2,113,152 | 17.9% | +10.3% |
| Crude-equivalent tanker transits/week | 0.662 | 3.004 | 17.1% | +32.0% |
| Utilization | 0.0089 | 0.1082 | 27.1% | −5.5% |
| Delay, days | 0.0202 | 0.2671 | 64.6% | +4.7% |

Closed edges have exactly zero volume and transits. WAPE divides total absolute error by total
actual target magnitude, avoiding per-row division by zero. Delay is still weak; most edges have
little delay, so a small MAE alone would be misleading. No claim of exact flow conservation,
held-out-chokepoint generalization or validated unobserved-route outputs is made.

### Price: an honest small-sample backtest

The mean model predicts the deviation from last week's Brent price with standardized Ridge
(`lambda = 10`, intercept unpenalized). Only lagged Brent, the trailing price-average gap, the
previous observed Suez share and its lagged change enter the network model. The representation
model adds eight GNN summaries. That GNN was trained before August 2022 and selected using
validation through December 2022, before all price calibration/test weeks. Its embedding head is
a fixed seeded projection of learned edge states, not an independently trained economic factor.

All four models use **204 common fitting weeks through 2022-12-30**, **43 calibration weeks
in January–October 2023**, and **35 test weeks, 2023-11-03 through 2024-06-28**. No model is
refit on calibration/test prices. Scalers use fitting weeks only. Intervals use the finite-sample
90th percentile of absolute calibration residuals, not the original unvalidated quantile-IRLS
implementation. Serial dependence and regime changes limit coverage guarantees.

| Model | MAE, $/bbl | RMSE, $/bbl | RMSE skill vs random walk | 90% interval coverage | Mean interval width, $/bbl |
|---|---:|---:|---:|---:|---:|
| Random walk | 1.990 | 2.463 | — | 100.0% | 10.572 |
| AR(1) | 1.970 | 2.446 | +0.7% | 100.0% | 10.164 |
| Observed network + AR anchor | 1.895 | 2.400 | +2.6% | 97.1% | 10.115 |
| Network + GNN summaries | 1.907 | 2.425 | +1.6% | 97.1% | 9.803 |

The network model has the lowest error in this window, but **the gain is not established as
statistically reliable**. A paired circular four-week block bootstrap (2,000 draws, fixed seed)
gives a 95% interval of **[−$0.260, +$0.125]/bbl** for its RMSE minus random-walk RMSE.
Both signs are included. This short historical backtest is evidence of a modest possible signal,
not a reliable oil-price forecasting advantage. Adding GNN summaries does not improve on the
observed-network model here. Complete predictions and intervals are exported, not just averages.

### Shipping costs and scenario interpretation

Costs use the flow model's edge-specific cargo sizes, with the configured cargo assumption only
on idle edges. Recovering baseline tolls subtracts both sailing and delay costs, preventing delay
from being charged twice. Both cost scripts call the same implementation. Fleet voyage cost and
days sum all route legs and divide by served barrels (half the sum of connector flows), so splitting
a lane into more edges cannot change the answer. The original edge-traversal denominator was an
average leg, not a complete voyage. Charter-rate bands are assumptions, not statistical intervals.

In this data vintage, fully closing Suez changes average network cost by **+$0.023 / +$0.046 /
+$0.086 per served barrel** at $35k/$50k/$75k per day. These are global averages across the
whole modeled network, not surcharges for a particular Europe–Asia voyage. They should not be
forced to match the old $1.50–$3.50 corridor illustration. Costs exclude external freight-market
responses, and a scenario that strands demand changes the population of served voyages.

History observes the all-tanker Suez/Cape share, not global route days, rerouted barrel-miles or
freight costs. Those historical route fields remain missing instead of being fabricated. Scenario
shares apply solver percentage changes to January–October 2023 observed tanker means before
forming the ratio, avoiding a crude-only/all-tanker population mismatch.

`brent_scenario_impacts.parquet` reports **conditional statistical sensitivities**, with explicit
interpretation and extrapolation flags. It is not a causal estimate or a forecast. Negative
associations are retained rather than forced to expected signs. The observational regression
cannot identify a freight pass-through or a Hormuz supply-risk premium; those decomposition
fields are missing and `causal_decomposition_identified = False`. A physically meaningful
scenario oil-price impact therefore remains unsupported by these data. Do not present these
conditional sensitivities as dollar losses caused by a canal closure. The displayed residual
bands describe forecast noise; they do not cover uncertainty in causal or out-of-support effects.
The current-vintage scenario regression is fitted separately from the historical backtest, reserving
the latest 52 observed weeks for its own residual calibration. Its fit/calibration dates and radius
are recorded in `scenario_price_calibration.csv`; a 2023 radius is not reused after refitting.

### Reproduction and verification

See [`scripts/README.md`](../scripts/README.md#deliverable-5-reproduction) for the complete run
order and tested package versions. All datasets, checkpoints, loss curves, per-seed predictions,
run manifests and scorecards remain under ignored `data/`; credentials and data are never committed.
The script outputs include 21 event/pretraining checkpoints plus the frozen price representation.

Checks passed for finite tensors, closed-edge zero flow, transit-label mapping, annual-to-weekly
conversion, OD marginals, future-data perturbation invariance, forecast-origin lagging, cost/delay
identity, voyage-segmentation invariance, identical cost/index outputs, and ordered nonnegative
price intervals. The original G1 builder and G2 forward/backward smoke test also run.
The retrospective design and the failed Red Sea/price-significance claims are part of the result,
not hidden unfinished validations. Directional conservation, live release-time forecasting,
causal scenario Brent impacts, and unobserved-route validation remain outside what this evidence
supports; the scenario surrogate should remain experimental.
