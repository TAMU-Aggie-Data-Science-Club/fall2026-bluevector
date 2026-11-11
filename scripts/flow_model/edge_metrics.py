"""Deliverable 4 — Flow & Scenarios Group, Part 2: Edge Metrics Calculator.

What it does:
  Converts raw barrel flows from flow_solver.py into the full 18-column Shared Schema Contract:
  - Calibrates chokepoint tanker_transits to PortWatch baseline vessel sizes where available
  - Computes utilization, BPR delay_days, seasonal anomaly_z, cost_per_barrel
  - Computes delta_volume_bbl, delta_transits, and share_rerouted relative to baseline

Run from the repository root:
    .venv/bin/python scripts/flow_model/edge_metrics.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_DIR = ROOT / "config"
PROCESSED_DIR = ROOT / "data" / "processed"

SHARED_SCHEMA_COLS = [
    "scenario_id",
    "edge_id",
    "from_node",
    "to_node",
    "edge_type",
    "chokepoint_portid",
    "searoute_passage",
    "length_nm",
    "throttle_factor",
    "oil_volume_bbl",
    "tanker_transits",
    "capacity_weekly",
    "utilization",
    "free_flow_days",
    "delay_days",
    "anomaly_z",
    "delta_volume_bbl",
    "delta_transits",
    "share_rerouted",
    "cost_per_barrel",
    "stranded_barrels",
    "source",
]


def load_seasonal_stats() -> dict[str, tuple[float, float]]:
    """Load baseline mean and std of weekly tanker transits per chokepoint from Deliverable 3."""
    stats = {}
    wk_path = PROCESSED_DIR / "chokepoint_weekly_attributes.parquet"
    if wk_path.exists():
        wk = pd.read_parquet(wk_path)
        wk["week_ending"] = pd.to_datetime(wk["week_ending"])
        base = wk[wk["week_ending"] <= pd.Timestamp("2023-10-31")]
        for pid, grp in base.groupby("chokepoint_portid"):
            m = float(grp["weekly_tanker_transits"].mean())
            s = float(grp["weekly_tanker_transits"].std())
            stats[str(pid)] = (m, max(s, 1.0))
    return stats


def compute_edge_metrics(
    raw_df: pd.DataFrame,
    baseline_df: pd.DataFrame | None = None,
    scenario_id: str | None = None,
    save_output: bool = False,
) -> pd.DataFrame:
    df = raw_df.copy()
    if scenario_id is not None:
        df["scenario_id"] = scenario_id

    seasonal_stats = load_seasonal_stats()

    # Compute anomaly_z relative to pre-disruption baseline mean/std
    anom = []
    for _, r in df.iterrows():
        cp_id = str(r.get("chokepoint_portid") or "")
        trans = float(r["tanker_transits"])
        if cp_id in seasonal_stats:
            mean_val, std_val = seasonal_stats[cp_id]
            # Scale modeled transits relative to baseline mean if baseline_df is provided
            if baseline_df is not None:
                b_row = baseline_df[baseline_df["edge_id"] == r["edge_id"]]
                if not b_row.empty and float(b_row.iloc[0]["tanker_transits"]) > 0:
                    ratio = trans / float(b_row.iloc[0]["tanker_transits"])
                    equiv_obs = mean_val * ratio
                    anom.append(round((equiv_obs - mean_val) / std_val, 3))
                    continue
            anom.append(round((trans - mean_val) / std_val, 3))
        else:
            anom.append(0.0)
    df["anomaly_z"] = anom

    # Deltas vs baseline
    if baseline_df is None:
        base_path = PROCESSED_DIR / "baseline_edge_flows.parquet"
        if base_path.exists() and str(df["scenario_id"].iloc[0]) != "baseline":
            baseline_df = pd.read_parquet(base_path)

    if baseline_df is not None and str(df["scenario_id"].iloc[0]) != "baseline":
        base_vol = baseline_df.set_index("edge_id")["oil_volume_bbl"].to_dict()
        base_tr = baseline_df.set_index("edge_id")["tanker_transits"].to_dict()
        d_vol, d_tr, s_rerouted = [], [], []
        for _, r in df.iterrows():
            eid = r["edge_id"]
            bv = float(base_vol.get(eid, 0.0))
            bt = float(base_tr.get(eid, 0.0))
            dv = float(r["oil_volume_bbl"]) - bv
            dt = float(r["tanker_transits"]) - bt
            sr = max(0.0, -dv / bv) if bv > 0 else 0.0
            d_vol.append(round(dv, 2))
            d_tr.append(round(dt, 3))
            s_rerouted.append(round(sr, 4))
        df["delta_volume_bbl"] = d_vol
        df["delta_transits"] = d_tr
        df["share_rerouted"] = s_rerouted
    else:
        df["delta_volume_bbl"] = 0.0
        df["delta_transits"] = 0.0
        df["share_rerouted"] = 0.0

    df["source"] = df.get("source", "modeled")
    if "stranded_barrels" not in df.columns:
        df["stranded_barrels"] = 0.0

    out_cols = [c for c in SHARED_SCHEMA_COLS if c in df.columns]
    out_df = df[out_cols].copy()

    if save_output:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        scen_name = str(out_df["scenario_id"].iloc[0])
        out_df.to_parquet(PROCESSED_DIR / f"{scen_name}_edge_flows.parquet", index=False)
        out_df.to_csv(PROCESSED_DIR / f"{scen_name}_edge_flows.csv", index=False)

    return out_df


def main():
    raw_path = PROCESSED_DIR / "baseline_raw_flows.parquet"
    if not raw_path.exists():
        from scripts.flow_model.flow_solver import solve_network
        raw_df = solve_network(throttles={}, scenario_id="baseline", save_output=True)
    else:
        raw_df = pd.read_parquet(raw_path)

    out_df = compute_edge_metrics(raw_df, baseline_df=raw_df, scenario_id="baseline", save_output=True)
    print(f"Edge metrics calculated successfully -> data/processed/baseline_edge_flows.parquet ({len(out_df)} rows).")


if __name__ == "__main__":
    main()