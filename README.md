# DeadZone — Intermediate

*ADSC Catalyst Project · Fall 2026*

## Overview

DeadZone builds a tool that visualizes global shipping lanes, models how lane behaviour changes under specified disruptions (e.g., a canal closure), and predicts how the economic value of certain goods shifts in response. The final artifact ties AIS ship tracking, a graph neural network, and a downstream economic model into one interactive map.

## Objective

Ship an interactive dashboard where a user can pick a shipping choke point + a disruption scenario and see (a) predicted lane rerouting and (b) predicted downstream price/availability effects on a chosen goods category.

## Suggested tech stack

- **Geospatial:** GeoPandas, Shapely, H3
- **Modeling:** PyTorch Geometric (GNN), Prophet / LSTM (for lane traffic time series)
- **Visualization:** Streamlit, Kepler.gl
- **Data sources:** AIS ship-tracking data (MarineCadastre, Global Fishing Watch), UN Comtrade for goods flows

See [`DATA.md`](DATA.md) for concrete data sources and how to access them.

## What team members will gain

- Hands-on with the Automatic Identification System (AIS) that tracks the world's ships
- A graph neural network that predicts network-level effects of local shocks
- A secondary economic model that translates traffic changes into predicted price/availability moves — the rare combination of geospatial ML + econ

## Suggested scope (v1)

Focus on **one choke point** (Suez, Panama, or Malacca) with **1–2 years of AIS data**. Global-network v1 is unnecessarily ambitious.

Build:

1. AIS ingestion + spatial gridding with H3 (resolution ~6),
2. Lane extraction: trajectories → typical lane edges as a graph,
3. GNN or LSTM on lane traffic to predict rerouting under a shock,
4. Simple economic-impact model for **one** goods category (e.g., crude oil transiting Suez) — historical traffic × price elasticity as a first cut,
5. Streamlit dashboard with the choke point on Kepler.gl, a scenario picker, and predicted rerouting + price impact.

**Out of scope for v1:** full global network, real-time AIS ingest, multi-goods portfolios, port-level congestion modeling.

See [`DELIVERABLES.md`](DELIVERABLES.md) for the suggested deliverable breakdown and rough timeline.

## Repository map

| File / folder | Purpose |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | **Start here.** How the team runs the project on GitHub — PM vs. member roles, the issue → PR → `main` flow, branching, worktrees, reviews. |
| [`DELIVERABLES.md`](DELIVERABLES.md) | Suggested deliverables and rough timeline. A living plan, not a contract. |
| [`DATA.md`](DATA.md) | Suggested data sources, how to access them, and the source register. |
| [`data/`](data/) | Local working folder for datasets. **Git-ignored** — data is never committed. |
| [`AGENTS.md`](AGENTS.md) | Machine-facing workflow rules for AI coding agents. |

## Notes for PMs

This README, [`DELIVERABLES.md`](DELIVERABLES.md), and [`DATA.md`](DATA.md) are **suggestions**, not commitments. Rewrite them as the team scopes the real project.

## Notes for members

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before touching code. Then pick up an issue from the board.
