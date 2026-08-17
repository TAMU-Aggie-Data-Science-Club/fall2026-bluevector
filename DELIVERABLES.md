# Deliverables & Timeline

> **How to read this file.** This is the PMs' best current estimate of what DeadZone needs to ship and roughly when. It is a **living plan, not a contract**. The authoritative picture lives in **GitHub Issues and the Project board**.

## Milestones (suggested)

| # | Deliverable | Description | Owner (role) | Target |
|---|-------------|-------------|--------------|--------|
| 1 | Project scoping | Pick choke point + one goods category + shock scenario. Define what "predicted rerouting" means operationally. | PM | Week 1 |
| 2 | Data access | Pull AIS for the bounding box + Comtrade for the goods category + commodity prices. Documented in [`DATA.md`](DATA.md). | PM + members | Weeks 1–2 |
| 3 | AIS cleaning + gridding | Filter, dedupe, H3-grid vessel positions. Produce per-cell traffic time series. | Members | Weeks 2–3 |
| 4 | Lane graph extraction | Build the graph of lane edges from trajectories, with traffic weights. | Members | Week 3 |
| 5 | GNN / LSTM model | Predict per-edge traffic under a shock. Time-based splits. | Members | Weeks 4–5 |
| 6 | Economic-impact model | Historical traffic × price elasticity for the chosen goods category. Report intervals, not point estimates. | Members | Weeks 5–6 |
| 7 | Kepler.gl + Streamlit dashboard | Choke-point map, scenario picker, predicted rerouting overlay, predicted price impact plot. | Members + PM | Weeks 6–7 |
| 8 | Handoff & retro | Reproducibility check, short writeup with scenario examples, lessons learned. | PM | Week 8 |

## Timeline (rough)

```
Week:   1     2     3     4     5     6     7     8
        |-----|-----|-----|-----|-----|-----|-----|
Scope   ██
Access        ██
AIS clean           ████
Lane graph                ██
Model                       ████
Econ                            ████
Dashboard                                  ████
Retro                                              ██
```

## Working agreements

- **Each deliverable maps to one or more GitHub Issues.** The board is the source of truth; this file is the summary.
- **Dates are estimates.** When reality diverges, update the issue and this file if the shift is material.
- **"Done" is defined per issue** via acceptance criteria — not by a date passing.
- **Reprioritize openly.** If a deliverable changes, a PM notes why in the issue so the decision is auditable.
