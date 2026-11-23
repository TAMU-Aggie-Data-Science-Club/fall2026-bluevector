# scripts/

All executable code for Blue Vector. `config/` holds only data: the small CSV reference lists and
graph tables these scripts read and write (Deliverable 4 loads `config/nodes.csv` and
`config/edges.csv`), plus the shared parameter file `config/flow_params.json` that Deliverable 4 adds.

## Layout

| Folder | Scripts | What it does |
|---|---|---|
| `data_pull/` | `pull_ports_static.py`, `pull_port_totals.py`, `pull_portwatch_transits_2019_2025.py`, `pull_chokepoints.py`, `pull_chokepoints_static.py`, `comtrade_script.py`, `eia_brent.py`, `eia_chokepoints.py`, `worldbank_monthly_crude.py` | Fetch from PortWatch, UN Comtrade, EIA, World Bank into `data/raw/` and `data/interim/` |
| `ports/` | `select_ports.py`, `country_codes.py` | Rank ports, build `config/ports_selected.csv`, build the ISO3 crosswalk |
| `graph/` | `build_nodes.py`, `build_connector_edges.py`, `build_edge_attributes.py`, `graph_validation.py`, `searoute_script.py` | Build and check `config/nodes.csv`, `config/edges_core.csv`, `config/edges.csv` |
| `od_matrix/` | `od_country_flows.py`, `od_port_shares.py`, `od_crude_share.py`, `od_matrix.py`, `od_checks.py` | Build and check the OD matrix in `data/interim/` |
| `flow_model/` | `flow_solver.py`, `edge_metrics.py`, `scenario_engine.py`, `gnn_sim_generator.py` | Route the OD matrix over the graph, compute edge metrics, run scenarios and criticality |
| `validation/` | `validation_targets.py`, `calibrate_flow.py`, `validate_events.py`, `validate_gfw_delays.py` | Observed benchmarks, Red Sea calibration, event validation |
| `gnn/` | `gnn_dataset.py`, `gnn_model.py`, `train_gnn.py`, `evaluate_gnn.py` | Two GNN training modes, chronological observed-transit backtests, a separate simulator surrogate, checkpoints, embeddings and scorecards |
| `price/` | `shipping_cost.py`, `network_indices.py`, `brent_model.py`, `backtest_price.py` | Voyage shipping costs, observed/scenario feature provenance, calibrated price intervals and a four-model held-out scorecard |

## Technology and algorithms

- **Tables:** Python 3, pandas, NumPy, pyarrow (Parquet).
- **Graph:** NetworkX. The flow model uses single-source Dijkstra (one run per origin port, weights in dollars per barrel) inside a ten-pass incremental loading loop with BPR congestion and a queue price above capacity, so a throttle makes an edge expensive rather than removing it. The graph builders use all-pairs Dijkstra to decide port connectors, and shortest paths for connectivity and alternative-route checks.
- **Distances and geometry:** searoute, GeoPandas, Shapely.
- **Fitting and scoring:** iterative proportional fitting (IPF/RAS) for the OD matrix, percentile capacity proxies, grid-search calibration with an H1/H2 time split, and MAE, RMSE, MAPE and direction match for validation.
- **Batch runs:** a `multiprocessing` process pool for the 337-scenario GNN set (`--workers`).
- **Planned for Deliverable 5 and later:** PyTorch Geometric (GNN), statsmodels/scikit-learn (price model), Prophet/LSTM benchmarks, Streamlit and Kepler.gl (dashboard).

## Run order

```sh
# 1. data (needs .env keys for Comtrade / EIA)
python scripts/data_pull/pull_ports_static.py
python scripts/data_pull/pull_port_totals.py
python scripts/data_pull/pull_portwatch_transits_2019_2025.py
python scripts/data_pull/pull_chokepoints.py
python scripts/data_pull/pull_chokepoints_static.py
python scripts/data_pull/comtrade_script.py
python scripts/data_pull/eia_brent.py
python scripts/data_pull/eia_chokepoints.py

# 2. ports
python scripts/ports/country_codes.py
python scripts/ports/select_ports.py

# 3. graph  (order matters: attributes -> connectors -> validation)
python scripts/graph/build_nodes.py
python scripts/graph/build_edge_attributes.py
python scripts/graph/build_connector_edges.py
python scripts/graph/graph_validation.py

# 4. OD matrix
python scripts/od_matrix/od_country_flows.py
python scripts/od_matrix/od_port_shares.py
python scripts/od_matrix/od_crude_share.py
python scripts/od_matrix/od_matrix.py
python scripts/od_matrix/od_checks.py
```

Every script resolves `config/` and `data/` from its own location, so it works from any working
directory. `graph_validation.py` writes `config/validation_results.csv`; `build_connector_edges.py`
writes `config/edges.csv`.

## Note on deliverable docs

`DELIVERABLE4.md` uses the grouped paths. `DELIVERABLE3.md` still names its scripts flat
(`scripts/od_country_flows.py`, `scripts/od_port_shares.py`); those files live in `scripts/od_matrix/`.
Graph builders are in `scripts/graph/`, data pulls in `scripts/data_pull/`, port selection in
`scripts/ports/`.


## Deliverable 5 reproduction

For a beginner-friendly explanation of the models, equations, and results, read
[MODEL_METHODOLOGY.md](../MODEL_METHODOLOGY.md).

Read [the executed results and limits](../deliverables/DELIVERABLE5.md#9-integration-results--2026-11-22)
before presenting a model score. Temporal forecasts, solver imitation and conditional price
sensitivities are different outputs. All artifacts stay under ignored `data/`; source code and
this written results snapshot are tracked. No credentials belong in tracked files.

The tested Windows environment was Python 3.13, torch 2.15.0+cpu, pandas 3.1.1, numpy 2.5.5,
pyarrow 26.0.1, networkx 3.7, scipy 1.18.1, requests 2.34.2, python-dotenv 1.2.4 and lxml 6.1.3.
The implemented price model uses NumPy; scikit-learn/statsmodels/PyTorch Geometric are not required.
Use a local `.venv`. Commands below use that environment's `python`; on Windows it can be invoked
explicitly as `.venv\Scripts\python.exe`, and on Unix as `.venv/bin/python`.

```sh
python -m venv .venv
# Run pip using the new environment's Python, then use that same Python below.
python -m pip install torch==2.15.0 pandas==3.1.1 numpy==2.5.5 pyarrow==26.0.1 networkx==3.7 scipy==1.18.1 requests==2.34.2 python-dotenv==1.2.4 lxml==6.1.3
```

Set `EIA_API_KEY` and `SUBSCRIPTION_KEY` in the process environment before the corresponding
pulls. Comtrade uses already adopted sea mode `2100`; yearly caches are validated and reused.
The graph/reference CSVs are already tracked. Rebuild the data without changing those references:

```sh
python scripts/data_pull/pull_chokepoints.py
python scripts/data_pull/pull_portwatch_transits_2019_2025.py --ports config/ports_selected.csv --workers 4
python scripts/data_pull/comtrade_script.py
python scripts/data_pull/eia_brent.py
python scripts/data_pull/eia_chokepoints.py
python scripts/od_matrix/od_port_shares.py
python scripts/od_matrix/od_country_flows.py
python scripts/od_matrix/od_crude_share.py --default 0.53
python scripts/od_matrix/od_matrix.py
python scripts/od_matrix/od_checks.py
python -m scripts.gnn.train_gnn --prepare-only
python scripts/flow_model/scenario_engine.py
python scripts/validation/validate_events.py
python scripts/flow_model/gnn_sim_generator.py --workers 4
python -m scripts.gnn.train_gnn --epochs 180 --pretrain-epochs 150 --seeds 7 19 43
python -m scripts.price.shipping_cost --check
python -m scripts.price.network_indices --check
python -m scripts.price.backtest_price
python -m scripts.price.brent_model
python -m scripts.gnn.train_gnn --check
```

`0.53` is the existing agreed port-universe crude-share assumption, not a newly estimated source.
Updated public observations can change scores; record the data vintage. The temporal trainer
uses fourteen edge inputs (G2's original eleven slots plus three lagged transit summaries).
The real-only mode excludes solver demand, solver labels and current tariffs. Mixed mode uses
simulator pretraining; its later-vintage simulator is explicitly a retrospective prior. Both use
only observed transit labels for fine-tuning. Full seven-day weeks are required. Normalization
and checkpoint selection never use target-event observations. The `--check` command perturbs
future observations and asserts that training/validation tensors and scales remain identical.

Useful independent commands:

```sh
python -m scripts.gnn.train_gnn --mode real_only --epochs 180 --seeds 7 19 43
python -m scripts.gnn.train_gnn --price-embeddings-only --epochs 180
python -m scripts.gnn.evaluate_gnn
python scripts/gnn/gnn_dataset.py
python scripts/gnn/gnn_model.py
```

The real-only command intentionally writes a real-only scorecard. The default command writes
both modes. `gnn_edge_predictions.parquet` is the separate simulator surrogate; real-only
counterfactual flows on unobserved edges are not identifiable. All twenty compound scenarios
are held out from its training and validation. Forecast checkpoints and per-seed diagnostics
are retained so the three-seed average can be audited.

Price backtesting uses a frozen fit through 2022, separate 2023 calibration and November 2023–
June 2024 testing on identical weeks for random walk, AR(1), observed-network Ridge and Ridge
with GNN summaries. Price summaries use a real-only GNN frozen before 2023. Network inputs are
lagged by one observation week; historical publication lags are not reconstructed. The design
matrix is saved separately as `price_model_design.parquet`, preserving the historical/scenario
feature contract. Missing historical route quantities and causal freight/risk components remain
missing. Scenario price sensitivities carry interpretation/extrapolation flags and are not causal
supply-shock estimates. Their residual bands are not uncertainty intervals for causal effects.
