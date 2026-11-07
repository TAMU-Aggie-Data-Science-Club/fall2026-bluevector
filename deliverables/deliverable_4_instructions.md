# 🌊 Fourth Deliverable: Baseline Flow Model, Scenario Engine & Validation

**📅 Due: Tuesday, November 17, 2026**

## 🎯 What this is

Deliverable 3 gave us the assembled network (`nodes.csv`, `edges.csv` with static/dynamic attributes) and the port-to-port demand (`od_matrix.csv`). Now we build the physics engine of the project and validate it against real-world disruptions:

1. **The Flow Model & Scenario Engine:** routes the OD matrix across the graph with capacity limits and congestion penalties, closes or throttles any edge, computes edge-level changes, ranks edge criticality, and batch-generates simulated scenarios for the GNN.

2. **Calibration & Validation:** tunes our congestion and rerouting parameters on the 2023–24 Red Sea crisis and evaluates model accuracy against historical PortWatch and Global Fishing Watch observations (Red Sea, Ever Given, and Panama).

Next deliverable, the **GNN and Economic/Oil Price models** will consume the scenario outputs and validated baselines produced here.

## 👥 Two groups

| Group | Builds | 
| ----- | ----- | 
| ⚙️ **Flow & Scenarios** | `flow_solver.py`, `edge_metrics.py`, `scenario_engine.py`, `gnn_sim_generator.py` | 
| 🎯 **Calibration & Validation** | `validation_targets.py`, `calibrate_flow.py`, `validate_events.py`, `validate_gfw_delays.py` | 

Each group has four parts. **Pick your parts yourselves.** Because both groups agree on a strict **Shared Schema Contract** (defined below) right at the start, every part can be built and tested immediately using mock or baseline tables without waiting on anyone else.

## 🤝 Shared Schema Contract (How the two groups connect)

To build the Flow Model and Validation scripts at the exact same time, both groups must use this exact column layout for `scenario_edge_flows.parquet` (and `.csv`):

* `scenario_id`: string identifier (e.g., `baseline`, `red_sea_2024`, `ever_given_2021`, `panama_drought_2023`, `sim_edge_12_closed`)

* `edge_id`, `from_node`, `to_node`, `edge_type`, `chokepoint_portid`, `length_nm`

* `throttle_factor`: `1.0` for open, `0.0` for fully closed, `0.3` for throttled to 30% capacity

* `oil_volume_bbl`: routed crude oil volume on this edge (barrels per week)

* `tanker_transits`: estimated weekly tanker ships on this edge

* `capacity_weekly`: weekly capacity proxy for this edge

* `utilization`: `tanker_transits / capacity_weekly`

* `free_flow_days`: `length_nm / (speed_knots * 24)`

* `delay_days`: extra congestion delay on this edge in days

* `anomaly_z`: z-score vs. pre-disruption seasonal baseline

* `delta_volume_bbl`, `delta_transits`, `share_rerouted`: change relative to `baseline`

* `cost_per_barrel`: estimated shipping + toll cost per barrel on this edge

* `source`: `modeled` (for flow model outputs) or `observed` (for PortWatch validation tables)

## ⚙️ Flow & Scenarios group

**Hand in:** the flow solver, edge metric calculator, scenario engine, criticality ranking (`edge_criticality.csv`), and GNN training scenarios (`gnn_simulated_scenarios.parquet`).

### How the Flow Model & Scenario Engine Work (Step-by-Step)

#### 1. Core Network Routing (`flow_solver.py`)

* **Weekly demand scale:** Convert `od_matrix.csv` (`barrels_per_year`) into weekly port-to-port demand (`barrels_per_week = barrels_per_year / 52.14`) so units match weekly PortWatch chokepoint capacities.

* **Port transit rule:** Ports are origins or destinations only, never pass-through shortcuts. Enforce this in the graph by splitting port connectors into directed `port -> junction` (origin only) and `junction -> port` (destination only) edges.

* **Edge generalized cost:** Ships prefer the lowest-cost path combining sailing time, congestion delay, and canal tolls:

  ```
  free_flow_days = length_nm / (speed_knots * 24)
  edge_days = free_flow_days + delay_days
  edge_cost_per_bbl = (edge_days * daily_tanker_rate / barrels_per_ship) + toll_per_bbl + (reroute_penalty if risky)
  
  ```

* **Congestion & Capacity Iteration:** Use iterative capacity-constrained routing (e.g., Frank-Wolfe / incremental loading or NetworkX `min_cost_flow` with piecewise congestion steps). Update edge travel times after each step using the standard BPR congestion curve:

  ```
  delay_days = free_flow_days * alpha * ((tanker_transits / capacity_weekly) ^ beta)
  
  ```

* **Stranded demand:** Add a high-cost "virtual bypass" edge between every origin and destination pair. If an edge with no sea alternative (like Hormuz) is closed, flow routes onto the virtual bypass and is recorded as `stranded_barrels`.

#### 2. Edge-Level Tanker Metrics (`edge_metrics.py`)

Given raw barrel flows on every edge, compute the required dynamic attributes:

* **Tanker transits:**

  ```
  tanker_transits = oil_volume_bbl / avg_barrels_per_transit
  
  ```

  *(Use calibrated cargo size per transit at chokepoints from Deliverable 3, and a default VLCC/Suezmax assumption such as `1,000,000`–`1,500,000` bbl on open lanes, respecting Suez/Panama size limits.)*

* **Utilization & Anomaly:**

  ```
  utilization = tanker_transits / capacity_weekly
  anomaly_z = (tanker_transits - baseline_mean_for_week) / baseline_std_for_week
  
  ```

* **Deltas vs. Baseline:** Compare any scenario table against the `baseline` table:

  ```
  delta_volume_bbl = scenario_volume_bbl - baseline_volume_bbl
  delta_transits = scenario_transits - baseline_transits
  share_rerouted = MAX(0, -delta_volume_bbl / baseline_volume_bbl)   # for throttled/upstream edges
  
  ```

#### 3. Scenario Engine & Edge Criticality (`scenario_engine.py`)

* **Single/Multi-edge throttle control:** Write a clean function `run_scenario(throttles: dict[edge_id, float], scenario_id: str)` that scales `capacity_weekly = base_capacity * throttle_factor` (or adds a risk penalty for conflict zones), re-solves the network, and returns the full `scenario_edge_flows` dataframe plus a summary dictionary (`stranded_barrels`, `total_ton_miles`, `avg_voyage_days`, `suez_tanker_share`).

* **Criticality ranking (`edge_criticality.csv`):** Loop over every chokepoint and major lane edge, close it (`throttle_factor = 0.0`), and rank edges by a combined criticality score:

  ```
  criticality_score = w1 * stranded_share + w2 * pct_increase_ton_miles + w3 * avg_extra_delay_days
  
  ```

#### 4. GNN Simulated Dataset Generator (`gnn_sim_generator.py`)

* To train the GNN in Deliverable 5, run `scenario_engine.py` across a grid of simulated disruptions:

  * Every single edge throttled at `[0.0, 0.25, 0.50, 0.75]`

  * Key multi-edge combinations (e.g., `Suez + Bab-el-Mandeb` throttled together; `Malacca` partial throttle; `Hormuz` partial throttle)

* Tag all rows `source = "simulated"` and save to `data/processed/gnn_simulated_scenarios.parquet`.

### 💡 Four parts (Flow & Scenarios group)

| Part | Owner | What to Build | Inputs | Outputs (`data/processed/`) | Independent Test / Acceptance Check | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **Part 1: Core Flow Solver** | Member 1 | Script `scripts/flow_solver.py`: load `nodes.csv`, `edges.csv`, `od_matrix.csv`, enforce port-endpoint-only rules, route weekly barrels with capacities & BPR delay. | `config/nodes.csv`, `config/edges.csv`, `data/processed/od_matrix.csv` *(or a 10-port toy OD)* | `baseline_raw_flows.parquet` + `solve_network()` function | Total routed + stranded barrels equals total OD matrix barrels (`< 0.1%` error); ports have zero pass-through transit flow; zero negative flows. | 
| **Part 2: Edge Metrics Calculator** | Member 2 | Script `scripts/edge_metrics.py`: convert raw barrel flows into `tanker_transits`, `utilization`, `delay_days`, `anomaly_z`, `cost_per_barrel`, and deltas vs. baseline. | `baseline_raw_flows.parquet` (or mock flow dataframe), `config/edges.csv` | `baseline_edge_flows.parquet` matching the Shared Schema Contract | All 18 required columns in the Shared Schema Contract are present, properly typed, with `source = "modeled"` and no `NaN` values on active edges. | 
| **Part 3: Scenario Engine & Criticality** | Member 3 | Script `scripts/scenario_engine.py`: expose `run_scenario()`, test closing each edge, record `stranded_barrels` and rerouted ton-miles, output criticality table. | `flow_solver.py`, `edge_metrics.py` | `edge_criticality.csv` + named scenarios (`red_sea_sim`, `ever_given_sim`, `panama_sim`) | Closing Suez shifts flow to Cape of Good Hope with `0` stranded barrels; closing Hormuz produces high `stranded_barrels`; `edge_criticality.csv` ranks top chokepoints sensibly. | 
| **Part 4: GNN Scenario Generator** | Member 4 | Script `scripts/gnn_sim_generator.py`: batch-run single-edge (`0.0, 0.25, 0.5, 0.75`) and multi-edge closures and save the combined training table. | `scenario_engine.py` | `gnn_simulated_scenarios.parquet` (tagged `simulated`) | Generates `200+` distinct scenario snapshots; every snapshot has a complete set of edge rows and summary indices ready for PyTorch Geometric. | 

## 🎯 Calibration & Validation group

**Hand in:** historical validation benchmark tables, calibrated congestion/rerouting parameters (`config/flow_params.json`), event validation metrics (`validation_report.csv`), and a GFW transit/delay check.

### How Calibration & Validation Work (Step-by-Step)

#### 1. Historical Event Ground-Truth (`validation_targets.py`)

From `portwatch_chokepoints_daily.parquet` and `portwatch_ports_daily_selected.parquet`, aggregate **observed** weekly tanker counts (`n_tanker`), estimated oil volumes, utilization, and seasonal anomalies across three historical disruption windows and their pre-event baselines:

* **Primary Event — Red Sea / Houthi Diversions:**

  * *Pre-crisis baseline:* Jan 2023 – Oct 2023

  * *Disruption window:* Dec 2023 – Jun 2024

  * *Key targets:* Suez Canal, Bab-el-Mandeb, and Cape of Good Hope weekly tanker transits (`n_tanker`), Suez share `Suez / (Suez + Cape)`, and port arrival shifts at European vs. Asian ports.

* **Secondary Event 1 — Ever Given Blockage (Suez):**

  * *Pre-event baseline:* Feb 1, 2021 – Mar 20, 2021

  * *Disruption window:* Mar 23, 2021 – Apr 5, 2021 (1-to-2-week shock and recovery)

* **Secondary Event 2 — Panama Canal Drought:**

  * *Pre-event baseline:* Jan 2022 – Apr 2023

  * *Disruption window:* Oct 2023 – Mar 2024

#### 2. Parameter Calibration on the Red Sea Crisis (`calibrate_flow.py`)

* **What we tune:**

  1. Congestion curve parameters `alpha` (e.g., `0.10`–`0.30`) and `beta` (e.g., `2.0`–`4.0`).

  2. Effective throttle / war-risk penalty on `Bab-el-Mandeb` and `Suez` during the Red Sea crisis.

* **How to tune:** Run a simple grid search over `(alpha, beta, red_sea_throttle)` comparing the Flow Model's predicted % drop in Suez/Bab-el-Mandeb tanker transits and % rise in Cape of Good Hope transits against the observed Dec 2023 – Jun 2024 targets from Part 1:

  ```
  calibration_loss = ABS(pred_suez_pct_change - obs_suez_pct_change)
                   + ABS(pred_cape_pct_change - obs_cape_pct_change)
                   + ABS(pred_suez_share - obs_suez_share)
  
  ```

* Save the winning parameters to `config/flow_params.json` so both groups use them as the project standard.

#### 3. Event Validation & Error Reporting (`validate_events.py`)

* Using time-based splits (calibrate on the first half of the Red Sea window or pre-2024 data; evaluate on held-out weeks, Ever Given, and Panama), compare `modeled` scenario outputs against `observed` PortWatch data:

  ```
  MAE_transits = MEAN(ABS(modeled_transits - observed_transits))
  MAPE_transits = MEAN(ABS(modeled_transits - observed_transits) / observed_transits)
  direction_match = (SIGN(modeled_delta) == SIGN(observed_delta))
  
  ```

* Save `data/processed/validation_report.csv` so the GNN team in Deliverable 5 has the exact **Flow Model Baseline Error** that the GNN must beat.

#### 4. Vessel Transit Time & Delay Check (`validate_gfw_delays.py`)

* Using the Global Fishing Watch tanker port-visit sample (or a distance-over-speed baseline if GFW coverage is thin on a corridor), compute observed voyage days for consecutive visits by the same tanker:

  ```
  observed_voyage_days = visit_B_start_timestamp - visit_A_end_timestamp
  observed_delay_days = median_voyage_days_in_window - baseline_median_voyage_days
  
  ```

* Compare against the Flow Model's route transit days (`sum of free_flow_days + delay_days` along the shortest path) for key corridors (e.g., Persian Gulf to Europe via Suez vs. via Cape of Good Hope, adding \~10–14 days).

### 💡 Four parts (Calibration & Validation group)

| Part | Owner | What to Build | Inputs | Outputs (`data/interim/` & `config/`) | Independent Test / Acceptance Check | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **Part 1: Historical Ground-Truth Targets** | Member 1 | Script `scripts/validation_targets.py`: build observed pre-event and crisis summary tables for Red Sea (2023–24), Ever Given (2021), and Panama (2023–24). | `data/interim/portwatch_chokepoints_daily.parquet`, `portwatch_ports_daily_selected.parquet` | `validation_targets.csv` (matching Shared Schema columns, tagged `source = "observed"`) | Clearly shows the Dec 2023+ drop in Suez/Bab-el-Mandeb tanker transits and jump in Cape of Good Hope transits; no missing priority chokepoints. | 
| **Part 2: Red Sea Parameter Calibration** | Member 2 | Script `scripts/calibrate_flow.py`: grid-search `alpha`, `beta`, and Red Sea throttle/penalty to match observed Suez-to-Cape diversion shares. | `validation_targets.csv` + `scenario_engine.py` *(can test early on a mock response function)* | `config/flow_params.json` (`alpha`, `beta`, `speed_knots`, `red_sea_throttle`, `daily_tanker_rate`) | Produces `config/flow_params.json` where predicted Suez share `Suez / (Suez + Cape)` is within `5%` of observed Red Sea crisis levels. | 
| **Part 3: Event Validation & Benchmark Report** | Member 3 | Script `scripts/validate_events.py`: compute MAE, RMSE, MAPE, and transit shift accuracy across held-out Red Sea weeks, Ever Given, and Panama. | `validation_targets.csv` + `scenario_edge_flows.parquet` *(testable immediately on mock predictions)* | `data/processed/validation_report.csv` + markdown summary table | Outputs a clean scorecard comparing `modeled` vs `observed` at all 7 priority chokepoints, ready to serve as the benchmark the GNN must beat. | 
| **Part 4: Route Transit Days & Delay Check** | Member 4 | Script `scripts/validate_gfw_delays.py`: compute port-to-port voyage days and delays from GFW tanker visits (plus searoute baseline) pre- and post-Red Sea crisis. | `data/interim/gfw_tanker_visits.parquet` (or PortWatch/searoute), `config/edges.csv` | `data/interim/corridor_delays_validation.csv` | Confirms that Europe-bound voyages rerouting around the Cape of Good Hope add \~10–15 days over the Suez baseline and checks agreement with modeled route days. | 

## 🧱 Base assumptions

These are our starting assumptions. Keep them in `config/flow_params.json` so any script can read and adjust them in one place.

* **Time unit for routing:** 1 week (`annual_barrels / 52.14`), matching PortWatch weekly capacity and transit series.

* **Ship speed:** `13.0` knots default (`free_flow_days = length_nm / (13.0 * 24)`).

* **BPR congestion defaults (pre-calibration):** `alpha = 0.15`, `beta = 4.0`.

* **Default cargo per tanker transit:** Calibrated per chokepoint where observed (`capacity_tanker * crude_share * 7.3 / n_tanker`), and `1,000,000` barrels on unconstrained sea lanes.

* **Tanker daily rate assumption:** Low = `$35,000/day`, Base = `$50,000/day`, High = `$75,000/day` (used to translate extra voyage days into `$/bbl`).

* **Observed vs. modeled:** Every output table must include a `source` column set to `observed`, `modeled`, or `simulated`.

## 🔐 Reproducible and safe

Someone with a fresh clone should be able to run your scripts from top to bottom. Keep parameters in `config/flow_params.json`, never commit anything under `data/`, list any new packages in your pull request, and add a short "how to run" docstring to the top of each script.

## 📬 How to submit

This deliverable uses issue 4 and the integration branch `issue-4/integration`. Each group has one branch:

| Group | Branch | 
| ----- | ----- | 
| ⚙️ Flow & Scenarios | `issue-4/flow-model` | 
| 🎯 Calibration & Validation | `issue-4/validation` | 

Everyone pushes to their group's branch (pull before you push), and **one person per group** opens a pull request into `issue-4/integration`. See [`GITHUB_GUIDE.md`](GITHUB_GUIDE.md).

## ✅ Done checklist

**⚙️ Flow & Scenarios**

* \[ \] `scripts/flow_solver.py` routes `od_matrix.csv` over `nodes.csv` and `edges.csv` with capacity limits and congestion penalties

* \[ \] `scripts/edge_metrics.py` outputs all required columns in the Shared Schema Contract (`oil_volume_bbl`, `tanker_transits`, `utilization`, `delay_days`, `anomaly_z`, `share_rerouted`, `cost_per_barrel`)

* \[ \] `scripts/scenario_engine.py` closes/throttles any edge, reports `stranded_barrels`, and saves `edge_criticality.csv`

* \[ \] `scripts/gnn_sim_generator.py` saves `gnn_simulated_scenarios.parquet` for GNN training

**🎯 Calibration & Validation**

* \[ \] `scripts/validation_targets.py` builds observed PortWatch benchmark tables for Red Sea (2023–24), Ever Given (2021), and Panama (2023–24)

* \[ \] `scripts/calibrate_flow.py` saves calibrated parameters to `config/flow_params.json`

* \[ \] `scripts/validate_events.py` produces `validation_report.csv` (the baseline scorecard the GNN must beat)

* \[ \] `scripts/validate_gfw_delays.py` checks route transit days and Cape rerouting delays

**Both**

* \[ \] No API keys or `data/` files committed (`config/flow_params.json` is safe to commit)

* \[ \] Pull request open into `issue-4/integration`

## ⚠️ Known risks

* **Capacity proxy is statistical, not physical:** Because a 95th–99th percentile of historical transits isn't a hard physical wall (ships can surge past it, like around the Cape of Good Hope), use a soft BPR congestion penalty rather than a hard solver cutoff on open-sea lanes.

* **Ever Given was a 6-day shock:** Weekly aggregation can smooth out a 6-day blockage, so Part 1 & Part 3 of Validation should check both daily and 7-day rolling windows for March–April 2021.

* **Panama Canal is minor for crude oil:** Crude tankers use Panama much less than product tankers or container ships, so expect smaller signal-to-noise on Panama than on Suez / Bab-el-Mandeb.