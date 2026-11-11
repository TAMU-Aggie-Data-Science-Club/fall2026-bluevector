# scripts/

All executable code for Blue Vector. `config/` holds only the small CSV reference lists and graph
tables these scripts read and write (Deliverable 4 loads `config/nodes.csv` and `config/edges.csv`).

## Layout

| Folder | Scripts | What it does |
|---|---|---|
| `data_pull/` | `pull_ports_static.py`, `pull_port_totals.py`, `pull_portwatch_transits_2019_2025.py`, `pull_chokepoints.py`, `pull_chokepoints_static.py`, `comtrade_script.py`, `eia_brent.py`, `eia_chokepoints.py`, `worldbank_monthly_crude.py` | Fetch from PortWatch, UN Comtrade, EIA, World Bank into `data/raw/` and `data/interim/` |
| `ports/` | `select_ports.py`, `country_codes.py` | Rank ports, build `config/ports_selected.csv`, build the ISO3 crosswalk |
| `graph/` | `build_nodes.py`, `build_connector_edges.py`, `build_edge_attributes.py`, `graph_validation.py`, `searoute_script.py` | Build and check `config/nodes.csv`, `config/edges_core.csv`, `config/edges.csv` |
| `od_matrix/` | `od_country_flows.py`, `od_port_shares.py`, `od_crude_share.py`, `od_matrix.py`, `od_checks.py` | Build and check the OD matrix in `data/interim/` |

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

The deliverable write-ups name scripts flat (`scripts/od_country_flows.py`,
`scripts/flow_solver.py`). Grouped paths are the same files:
`od_*` lives in `scripts/od_matrix/`, graph builders in `scripts/graph/`, data pulls in
`scripts/data_pull/`, port selection in `scripts/ports/`.
