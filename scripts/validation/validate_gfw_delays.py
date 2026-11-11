"""Deliverable 4 — Calibration & Validation Group, Part 4: Vessel Transit Time & Delay Check.

What it does:
  Computes shortest-path route distance (nm) and voyage days (free_flow_days + delay_days)
  on the assembled graph across crude oil corridors under Baseline (Suez open) vs. Red Sea
  crisis (Suez + Bab-el-Mandeb closed, i.e. rerouted via the Cape of Good Hope), and compares
  the modeled extra voyage days against published voyage-time benchmarks.

  No Global Fishing Watch port-visit sample is checked into this repo, so the benchmarks are the
  published AIS-based voyage durations cited in `benchmark_source`. Corridors whose expected extra
  time is structurally zero act as controls: if the model diverts Atlantic or Asia-Pacific traffic
  that never used the Red Sea, the control error shows it.

Run from the repository root:
    .venv/bin/python scripts/validation/validate_gfw_delays.py
"""

import sys
from pathlib import Path

import networkx as nx
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.flow_model.scenario_engine import run_scenario

INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"

TOLERANCE_DAYS = 2.0

CORRIDORS = [
    {
        "name": "Ras Tanura -> Rotterdam (Persian Gulf to ARA)",
        "origin_node": "port_port1091",
        "destination_node": "port_port1114",
        "benchmark_baseline_days": 19.0,
        "benchmark_crisis_days": 35.0,
        "benchmark_source": (
            "EIA Today in Energy 2024-02-13: laden Suezmax at 14 kn, Persian Gulf -> ARA "
            "19 days via Suez, ~35 days via Cape of Good Hope"
        ),
    },
    {
        "name": "Ras Tanura -> Ningbo (Asia control, no Red Sea routing)",
        "origin_node": "port_port1091",
        "destination_node": "port_port824",
        "benchmark_baseline_days": None,
        "benchmark_crisis_days": None,
        "benchmark_source": "structural control: eastbound Gulf-Asia route never transits Suez or Bab-el-Mandeb",
    },
    {
        "name": "Houston -> Rotterdam (Atlantic control)",
        "origin_node": "port_port481",
        "destination_node": "port_port1114",
        "benchmark_baseline_days": None,
        "benchmark_crisis_days": None,
        "benchmark_source": "structural control: transatlantic route never transits Suez or Bab-el-Mandeb",
    },
]


def build_route_graph(edge_df: pd.DataFrame) -> nx.Graph:
    G = nx.Graph()
    for _, r in edge_df.iterrows():
        if float(r["throttle_factor"]) <= 0.0:
            continue
        days = float(r["free_flow_days"]) + float(r["delay_days"])
        G.add_edge(
            str(r["from_node"]),
            str(r["to_node"]),
            days=days,
            length_nm=float(r["length_nm"]),
        )
    return G


def validate_delays() -> pd.DataFrame:
    base_df, _ = run_scenario({}, scenario_id="baseline", save_files=False)
    crisis_df, _ = run_scenario({"suez": 0.0, "babalmandab": 0.0}, scenario_id="red_sea_closed", save_files=False)

    G_base = build_route_graph(base_df)
    G_crisis = build_route_graph(crisis_df)

    rows = []
    for corridor in CORRIDORS:
        orig = corridor["origin_node"]
        dest = corridor["destination_node"]
        for node in (orig, dest):
            if node not in G_base:
                raise ValueError(f"Corridor node {node} is missing from config/nodes.csv; fix the corridor list.")

        base_days = nx.shortest_path_length(G_base, orig, dest, weight="days")
        base_nm = nx.shortest_path_length(G_base, orig, dest, weight="length_nm")
        crisis_days = nx.shortest_path_length(G_crisis, orig, dest, weight="days")
        crisis_nm = nx.shortest_path_length(G_crisis, orig, dest, weight="length_nm")

        mod_extra = crisis_days - base_days
        bench_base = corridor["benchmark_baseline_days"]
        bench_crisis = corridor["benchmark_crisis_days"]
        if bench_base is not None and bench_crisis is not None:
            bench_extra = bench_crisis - bench_base
        else:
            bench_extra = 0.0

        rows.append(
            {
                "corridor": corridor["name"],
                "origin_node": orig,
                "destination_node": dest,
                "baseline_nm": round(base_nm, 1),
                "crisis_nm": round(crisis_nm, 1),
                "modeled_baseline_days": round(base_days, 2),
                "modeled_crisis_days": round(crisis_days, 2),
                "modeled_extra_delay_days": round(mod_extra, 2),
                "benchmark_baseline_days": bench_base,
                "benchmark_crisis_days": bench_crisis,
                "benchmark_extra_days": round(bench_extra, 2),
                "abs_error_days": round(abs(mod_extra - bench_extra), 2),
                "benchmark_source": corridor["benchmark_source"],
            }
        )

    out_df = pd.DataFrame(rows)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(INTERIM_DIR / "corridor_delays_validation.csv", index=False)
    out_df.to_csv(PROCESSED_DIR / "corridor_delays_validation.csv", index=False)

    max_err = float(out_df["abs_error_days"].max())
    status = "PASS" if max_err <= TOLERANCE_DAYS else "FAIL"
    print("Saved corridor voyage & delay check -> data/interim/corridor_delays_validation.csv")
    print(out_df.drop(columns=["benchmark_source"]).to_string(index=False))
    print(f"\nCorridor check: max |error| {max_err:.2f} days (tolerance {TOLERANCE_DAYS:.0f} days) -> {status}")
    for r in out_df.itertuples():
        print(f"  {r.corridor}: {r.benchmark_source}")
    return out_df


if __name__ == "__main__":
    validate_delays()
