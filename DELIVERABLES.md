# Deliverables & Timeline

> **How to read this file.** This is the PM's best current estimate of what Blue Vector needs to ship and roughly when. It is a **living plan, not a contract**. The authoritative picture lives in **GitHub Issues and the Project board**.

## Milestones (suggested)

| # | Deliverable | Description | Owner (role) | Target |
|---|-------------|-------------|--------------|--------|
| 1 | Project scoping | Confirm Suez / Red Sea as the flagship scenario, crude oil as the single goods category, and shipping cost per barrel + Brent price as the price outputs. Fix the port-selection rule and the observed-vs-modeled labeling standard. Define what "predicted rerouting" and "propagated impact" mean operationally. | PM | Week 0 |
| 2 | Source documentation + validation | Each subteam documents its two sources in [`DATA.md`](DATA.md) and records: the blank register cells (coverage, update lag, quota or API key); the exact field names for the metrics we need; one quick check per source with pass or fail (is the data we need really there?); and `ok` or `needs change` for each derived-metric formula it feeds. Deeper checks are optional: Suez vs. Cape of Good Hope tanker transits around Dec 2023, matching PortWatch ports to the port index, Comtrade mirror mismatches over 20%, Comtrade vs. PortWatch totals, a larger Global Fishing Watch tanker test, and more searoute distance comparisons. See [`FIRST_DELIVERABLE.md`](FIRST_DELIVERABLE.md). | PM + members | Week 1 |
| 3 | Node + edge selection | Rank ports from PortWatch volumes and select the ~100 most significant (report coverage of global and tanker volume). Choose ~15–20 junction nodes and the edge list: chokepoints as edges, lane segments connecting ports to junctions. Confirm alternative routes exist around each key chokepoint. Output is the approved node and edge lists, not yet the built graph. PM sign-off. | PM + members | Weeks 1–2 |
| 4 | Initial data pipeline | Reproducible scripts, built per source as it passes its Week 1 vetting check, that fetch and store **all data needed** for the selected nodes and edges: PortWatch port and chokepoint series, Comtrade crude oil (HS 2709), EIA Brent, a Global Fishing Watch tanker sample, and reference data (port index, sea distances). Raw files in `data/raw/`, cleaned Parquet in `data/interim/`. No graph assembly yet. | Members | Weeks 1–2 |
| 5 | Graph build + attributes | Build the network graph from the approved node and edge lists: static attributes (length, geometry, minimum transit days, size limits, tolls, capacity proxy) and dynamic attribute tables (tanker transit counts, volumes, utilization, anomaly; other vessel types observed only), each tagged `observed` or `modeled`. Store in `data/processed/`. | Members | Weeks 2–4 |
| 6 | OD matrix + baseline flow model | Allocate Comtrade crude oil flows to ports, calibrated to PortWatch port totals (OD matrix work starts Week 2, as the data is ready). Route flow with minimum-cost flow including capacity limits and congestion penalties. Scenario engine closes or throttles any edge, reports stranded demand, and ranks edges by criticality. For every edge it outputs the tanker metrics for baseline and scenario plus the change: oil volume, tanker transits, utilization, delay, anomaly, share rerouted, and cost per barrel for routes using the edge. Also generates the simulated closure scenarios used to train the GNN, and serves as the baseline the GNN must beat. | Members | Weeks 2–6 |
| 7 | Calibration + validation | Calibrate the rerouting-behavior parameter on the 2023–24 Red Sea diversions. Validate Suez, Bab-el-Mandeb, and Cape of Good Hope transit changes and port-arrival shifts. Secondary checks: Ever Given (2021) and Panama drought (2023–24). Time-based splits; report errors. Evaluate the GNN against the flow model on held-out real events and held-out chokepoints. | Members + PM | Weeks 4–7 |
| 8 | GNN model | Train a graph neural network to predict how every edge attribute changes when any edge is closed or throttled. Training data: simulated closure scenarios from the flow model (physics-based) plus real PortWatch data, including the 2023–24 Red Sea diversions. Time-based splits; hold out real events (Ever Given, Panama) and some chokepoints; compare against the flow model baseline. Prophet / LSTM serve as benchmarks. | Members | Weeks 5–7 |
| 9 | Oil price model | Shipping cost per barrel computed from network output (documented tanker daily-cost assumption). Brent model using network-derived features (indices built from the edge-level predictions, plus low-dimensional GNN summaries) as inputs, with uncertainty intervals, backtested on 2023–24 and benchmarked against a random walk. | Members | Weeks 5–8 |
| 10 | Kepler.gl + Streamlit dashboard | Network map, edge picker with close/throttle control, propagated changes overlay, `observed`/`modeled` badges, price-impact plot, and edge-criticality ranking. Built against mock data from Week 3. | Members + PM | Weeks 6–8 |
| 11 | Handoff & retro | Reproducibility check (fresh clone rebuilds `data/`), short writeup with Suez scenario examples and honest limits, lessons learned. | PM | Week 8 |

## Timeline (rough)

Week 0 is a short scoping week before the 8 working weeks (Weeks 1–8). The GNN needs simulated scenarios from the flow model, so scenario generation starts as soon as the baseline flow model runs (about Week 4).

```
Week:     0     1     2     3     4     5     6     7     8
          |-----|-----|-----|-----|-----|-----|-----|-----|
Scope     ████
Sources         ████
Select          ██████████
Pipeline        ██████████
Graph                 ████████████████
Flow                  ████████████████████████████
Validate                          ██████████████████████
GNN                                     ████████████████
Price                                   ██████████████████████
Dashboard                                     ████████████████
Retro                                                     ████
```

## Working agreements

- **Each deliverable maps to one or more GitHub Issues.** The board is the source of truth; this file is the summary.
- **Dates are estimates.** When reality diverges, update the issue and this file if the shift is material.
- **"Done" is defined per issue** via acceptance criteria — not by a date passing.
- **Reprioritize openly.** If a deliverable changes, the PM notes why in the issue so the decision is auditable.
- **Cut order if time slips:** ship-level tracing first, then node count. **The GNN and validation are core and are never cut.**
- **Label every dynamic attribute `observed` or `modeled`.** Only Suez / Bab-el-Mandeb (and partly Panama) scenarios are validated against real events; every other edge removal is a what-if and is shown as one.