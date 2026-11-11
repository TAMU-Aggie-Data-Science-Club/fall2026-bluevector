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
| `flow_model/` | *(Deliverable 4)* `flow_solver.py`, `edge_metrics.py`, `scenario_engine.py`, `gnn_sim_generator.py` | Route the OD matrix over the graph, compute edge metrics, run scenarios and criticality |
| `validation/` | *(Deliverable 4)* `validation_targets.py`, `calibrate_flow.py`, `validate_events.py`, `validate_gfw_delays.py` | Observed benchmarks, Red Sea calibration, event validation |

## Technology and algorithms

- **Tables:** Python 3, pandas, NumPy, pyarrow (Parquet).
- **Graph:** NetworkX. The flow model uses single-source Dijkstra (one run per origin port, weights in dollars per barrel) inside a ten-pass incremental loading loop with BPR congestion and arc saturation. The graph builders use all-pairs Dijkstra to decide port connectors, and shortest paths for connectivity and alternative-route checks.
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
