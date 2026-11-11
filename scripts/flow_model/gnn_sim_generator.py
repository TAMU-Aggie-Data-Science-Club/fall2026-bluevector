"""Deliverable 4 — Flow & Scenarios Group, Part 4: GNN Simulated Dataset Generator.

What it does:
  Batch-runs the scenario engine across a comprehensive grid of simulated disruptions
  (over 300 distinct scenarios) so Deliverable 5's PyTorch Geometric GNN has rich
  training data covering single-chokepoint throttles, single-lane closures, and
  multi-chokepoint compound crises.

  Every edge is throttled at the Deliverable 4 levels [0.0, 0.25, 0.50, 0.75], where the level is
  the share of pre-event crude throughput still available (0.0 = closed). Key multi-edge
  combinations (Red Sea, Hormuz + Bab-el-Mandeb, Malacca + Sunda, Suez + Panama, Mediterranean)
  are throttled together so the model sees compound crises, not just single-edge shocks.

  Each scenario is a full network solve, so the batch runs in a process pool by default
  (`--workers`). Results are identical to a serial run; only wall-clock time changes.

Run from the repository root:
    .venv/bin/python scripts/flow_model/gnn_sim_generator.py
    .venv/bin/python scripts/flow_model/gnn_sim_generator.py --workers 4
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.flow_model.scenario_engine import run_scenario

CONFIG_DIR = ROOT / "config"
PROCESSED_DIR = ROOT / "data" / "processed"

THROTTLE_LEVELS = (0.0, 0.25, 0.50, 0.75)


def _run_one(spec: tuple[str, dict[str, float]]) -> tuple[pd.DataFrame, dict]:
    """Run one scenario (module-level so it can be pickled into a worker process)."""
    sc_id, th_dict = spec
    df, summary = run_scenario(th_dict, scenario_id=sc_id, save_files=False)
    df["source"] = "simulated"
    return df, summary


def build_scenario_specs() -> list[tuple[str, dict[str, float]]]:
    edges = pd.read_csv(CONFIG_DIR / "edges.csv")
    edge_ids = edges["edge_id"].astype(str).tolist()

    scenario_specs: list[tuple[str, dict[str, float]]] = [("sim_baseline", {})]

    # 1. Every edge throttled across the four Deliverable 4 levels
    for e_id in edge_ids:
        for th in THROTTLE_LEVELS:
            scenario_specs.append((f"sim_{e_id}_th_{int(round(th * 100)):03d}", {e_id: th}))

    # 2. Compound multi-chokepoint scenarios
    multi_combos = [
        ("sim_redsea_compound", ["suez", "babalmandab"]),
        ("sim_mideast_compound", ["ormuz", "babalmandab"]),
        ("sim_seasia_compound", ["malacca", "sunda"]),
        ("sim_canals_compound", ["suez", "panama"]),
        ("sim_med_compound", ["suez", "bosporus", "gibraltar"]),
    ]
    for name, passages in multi_combos:
        for th in THROTTLE_LEVELS:
            scenario_specs.append((f"{name}_th_{int(round(th * 100)):03d}", {p: th for p in passages}))

    return scenario_specs


def generate_gnn_scenarios(workers: int = 1) -> pd.DataFrame:
    scenario_specs = build_scenario_specs()
    print(f"Generating {len(scenario_specs)} simulated GNN training scenarios with {workers} worker(s)...")

    frames = []
    summaries = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for idx, (df, summary) in enumerate(pool.map(_run_one, scenario_specs, chunksize=2), 1):
                frames.append(df)
                summaries.append(summary)
                if idx % 50 == 0 or idx == len(scenario_specs):
                    print(f"  Completed {idx}/{len(scenario_specs)} scenarios...")
    else:
        for idx, spec in enumerate(scenario_specs, 1):
            df, summary = _run_one(spec)
            frames.append(df)
            summaries.append(summary)
            if idx % 50 == 0 or idx == len(scenario_specs):
                print(f"  Completed {idx}/{len(scenario_specs)} scenarios...")

    combined = pd.concat(frames, ignore_index=True)
    summary_df = pd.DataFrame(summaries)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_pq = PROCESSED_DIR / "gnn_simulated_scenarios.parquet"
    out_sum = PROCESSED_DIR / "gnn_simulated_summaries.csv"
    combined.to_parquet(out_pq, index=False)
    summary_df.to_csv(out_sum, index=False)

    print(f"Saved {len(scenario_specs)} scenarios ({len(combined):,} edge rows) -> {out_pq.relative_to(ROOT)}")
    return combined


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 1),
        help="parallel worker processes (default: min(8, CPU count); 1 = serial)",
    )
    args = ap.parse_args()
    generate_gnn_scenarios(workers=max(1, args.workers))


if __name__ == "__main__":
    main()
