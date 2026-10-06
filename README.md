# Blue Vector — Intermediate

*ADSC Catalyst Project · Fall 2026*

## Overview

Blue Vector builds a tool that models the global maritime shipping network as a graph: roughly 100 of the world's most significant ports as nodes, and shipping lanes (including chokepoints such as the Suez Canal) as edges. It models how traffic, transit times, and costs change across the whole network when any edge is closed or throttled, and predicts how those changes affect the price of crude oil. The final artifact ties free AIS-derived maritime data (IMF PortWatch), a network flow model, a graph neural network, and a downstream economic model into one interactive map.

## Objective

Ship an interactive dashboard where a user can pick any edge in the global shipping network (a chokepoint such as Suez, or any lane segment), close or throttle it, and see (a) predicted rerouting and propagated changes in transit counts, delays, and oil volume across every other edge, and (b) the predicted downstream effect on shipping cost per barrel and on the Brent crude oil price. The Suez Canal / Red Sea disruption is the flagship scenario and the one validated against real-world data.

## Suggested tech stack

- **Geospatial & graph:** GeoPandas, Shapely, NumPy, NetworkX, `searoute` (sea distances). H3 is optional (snapping and heatmap layers), not core.
- **Modeling:** PyTorch Geometric (GNN, the core learned model), NetworkX / SciPy (flow model and scenario simulator), Prophet / LSTM (benchmark time-series models)
- **Economics:** statsmodels, scikit-learn (price model, intervals)
- **Visualization:** Streamlit, Kepler.gl (PyDeck as a fallback if the Kepler.gl–Streamlit integration proves unmaintained)
- **Storage:** Parquet via pandas / pyarrow
- **Data sources:** IMF PortWatch (AIS-derived port and chokepoint activity), Global Fishing Watch (vessel port visits), UN Comtrade (crude oil trade flows), EIA (Brent price and chokepoint oil flows)

> NetworkX, `searoute`, statsmodels, scikit-learn, and pyarrow are additions to the original suggested stack. Confirm each before adopting (see [`AGENTS.md`](AGENTS.md) §7).

See [`DATA.md`](DATA.md) for concrete data sources and how to access them.

## What team members will gain

- Hands-on with AIS-derived maritime data and the global shipping network
- Network flow modeling: how a local shock reroutes traffic and propagates through a whole graph
- A graph neural network trained on simulated and real disruptions to predict how edge attributes change anywhere in the network
- A secondary economic model that translates network changes into shipping-cost and oil-price effects, with honest uncertainty intervals
- Validating a model against real historical events (the Red Sea diversions) instead of trusting it blindly

## Suggested scope (v1)

Model **one commodity (crude oil)** on a **global network of about 100 ports**, with **Suez Canal / Red Sea** as the flagship, validated disruption. Every chokepoint is just an edge, so any edge can be closed or throttled. Only Suez / Bab-el-Mandeb (and partly Panama) can be checked against real events, so every other scenario is labeled a what-if.

Build:

1. Port selection + graph skeleton: rank ports by volume from PortWatch to pick the ~100 most significant (with a stated coverage figure), add ~15–20 junction nodes, treat chokepoints as edges, and compute sea distances and geometry,
2. Data ingestion: PortWatch (transit counts, port calls, volume estimates), UN Comtrade (crude oil, HS 2709), EIA (Brent), and a Global Fishing Watch sample for ship-level validation,
3. Baseline flow model: build an origin-destination (OD) oil matrix from Comtrade calibrated to PortWatch port totals, then route it with minimum-cost flow including capacity limits and congestion penalties. A scenario engine closes or throttles any edge and re-solves, so every edge gets a predicted change. The flow model is also the simulator that generates training scenarios for the GNN, and the baseline the GNN must beat,
4. Graph neural network (core model): trained on simulated disruption scenarios from the flow model plus real PortWatch data (including the 2023–24 Red Sea diversions), predicting how every edge attribute changes when any edge is closed or throttled, and evaluated against the flow model on held-out real events,
5. Oil price model: shipping cost per barrel computed from the network, plus a statistical model of the Brent price using network-derived features (indices built from the edge-level predictions), reporting intervals, not point estimates, and benchmarked against a naive random-walk forecast,
6. Validation against real events: the 2023–24 Red Sea diversions (primary), the 2021 Ever Given blockage, and the 2023–24 Panama drought (partial),
7. Streamlit dashboard with the network on Kepler.gl, an edge picker with a close/throttle control, propagated changes shown on the map, a price-impact plot, and an edge-criticality ranking.

### Graph attributes (v1 core)

Every dynamic attribute carries a `source` field: `observed` (real data), or `modeled` (model output). The dashboard shows which is which.

| Attribute | Type | Observed or modeled |
|---|---|---|
| Length, line geometry, minimum transit days | Static | Computed |
| Vessel-size limit, toll / fixed cost, region tag | Static | Domain rules and published tariffs |
| Capacity proxy (max observed throughput) | Static | Derived from history |
| Tanker transit count | Dynamic | Observed at chokepoint edges; modeled elsewhere |
| Other vessel types (container, dry bulk, general cargo, Ro-Ro) transit counts | Dynamic | Observed at chokepoints only; not modeled in v1 |
| Estimated trade volume and estimated oil volume | Dynamic | PortWatch estimates at chokepoints; modeled elsewhere (crude-share assumption) |
| Average vessel size, utilization, anomaly vs. seasonal baseline | Dynamic | Derived |
| Transit days and delay (between two ports) | Dynamic | Sampled from tracked ships; modeled elsewhere |
| Predicted flow, change vs. baseline, share rerouted, extra days, cost per barrel | Scenario output | Modeled |

**Every dynamic tanker metric is a model output for every edge**, in the baseline and in any scenario, together with the change between them: oil volume, tanker transits, utilization, delay, anomaly vs. seasonal baseline, share rerouted, and cost per barrel for routes using the edge. Length, minimum transit days, size limits, and tolls are inputs; capacity is the input a scenario changes. Version 1 models tankers (crude oil) only.

Node attributes: port calls and import/export tons by vessel type, capacity proxy, role tag (exporter, importer, refining hub, transshipment), and country. Transit counts between any two nodes are stored as an origin-destination matrix: sampled from tracked ships, constrained by PortWatch port totals, and modeled for the rest.

**Out of scope for v1:** processing raw global AIS, real-time AIS ingest, multi-goods portfolios, modeling non-tanker vessel types, port-level congestion modeling, trajectory-level lane extraction or full-ocean H3 gridding, ship-in-transit time-stepped simulation (stretch only), and crude grade or refinery-level modeling.

See [`DELIVERABLES.md`](DELIVERABLES.md) for the suggested deliverable breakdown and rough timeline.

## Repository map

| File / folder | Purpose |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | **Start here.** How the team runs the project on GitHub — PM vs. member roles, the issue → PR → `main` flow, branching, worktrees, reviews. |
| [`DELIVERABLES.md`](DELIVERABLES.md) | Suggested deliverables and rough timeline. A living plan, not a contract. |
| [`DATA.md`](DATA.md) | Suggested data sources, how to access them, and the source register. |
| [`TEAMS.md`](TEAMS.md) | Subteam structure, source ownership, and the pipeline build plan for Weeks 1–2. |
| [`FIRST_DELIVERABLE.md`](FIRST_DELIVERABLE.md) | The first deliverable: what is due, who does what, and what data we need from each source. |
| [`GITHUB_GUIDE.md`](GITHUB_GUIDE.md) | A short, step-by-step guide to the GitHub workflow for members. |
| [`data/`](data/) | Local working folder for datasets. **Git-ignored** — data is never committed. |
| [`AGENTS.md`](AGENTS.md) | Machine-facing workflow rules for AI coding agents. |
| [`CODEOWNERS`](CODEOWNERS) | **Team roster + review policy.** The PM, members, and the code-owner rule for PRs into `main`. |

## Team

The current PM and members for this project are listed in [`CODEOWNERS`](CODEOWNERS). The PM listed there is the code owner for PRs into `main`.

Subteams, source ownership, and the pipeline build plan are in [`TEAMS.md`](TEAMS.md).

## Notes for the PM

This README, [`DELIVERABLES.md`](DELIVERABLES.md), and [`DATA.md`](DATA.md) are **suggestions**, not commitments. Rewrite them as the team scopes the real project.

## Notes for members

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before touching code ([`GITHUB_GUIDE.md`](GITHUB_GUIDE.md) is the short version). Then pick up an issue from the board.