"""
scripts/price/shipping_cost.py  —  Deliverable 5, slot E1 (part 1 of 2)

PURPOSE
-------
Compute shipping cost per barrel on every edge, at three tanker charter rates
(low / base / high), for every scenario in `scenario_edge_flows.parquet` and
for every simulated scenario in `gnn_simulated_scenarios.parquet`.

Output
------
data/processed/scenario_shipping_costs.csv
    One row per (scenario_id, edge_id).
    Columns: scenario_id, edge_id, from_node, to_node, edge_type,
             searoute_passage, length_nm, free_flow_days, delay_days,
             toll_per_barrel, oil_volume_bbl, tanker_transits,
             cost_per_bbl_low, cost_per_bbl_base, cost_per_bbl_high,
             fleet_cost_per_bbl_low, fleet_cost_per_bbl_base,
             fleet_cost_per_bbl_high, source

HOW IT WORKS
------------
Edge cost formula (from DELIVERABLE5.md §6 E1):

    edge_days            = free_flow_days + delay_days
    cost_per_bbl(rate)   = edge_days * rate / barrels_per_ship + toll_per_barrel

where
    rate             — daily charter rate in USD/day  ($35k / $50k / $75k)
    barrels_per_ship — average cargo per transit      (1,000,000 bbl, from flow_params.json)
    toll_per_barrel  — per-edge toll, derived from cost_per_barrel in baseline
                       minus its sailing component (see note below)

Fleet-average cost:
    Weighted by oil_volume_bbl across all active edges in the scenario;
    zero-volume edges are excluded from the average.

Toll derivation
    The 22-column table's cost_per_barrel already includes toll.  At baseline
    (throttle_factor == 1), the toll component is:
        toll_per_barrel = baseline_cost_per_barrel
                         - baseline_free_flow_days * base_rate / barrels_per_ship
    This is computed once from the baseline scenario and joined to every other
    scenario so the toll is consistent everywhere.

Usage
-----
    python -m scripts.price.shipping_cost          # writes output file and exits
    python -m scripts.price.shipping_cost --check  # also runs acceptance check
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = pathlib.Path(__file__).resolve().parents[2]
PROCESSED = REPO / "data" / "processed"
CONFIG = REPO / "config" / "flow_params.json"

SCENARIO_FILE = PROCESSED / "scenario_edge_flows.parquet"
SIM_FILE = PROCESSED / "gnn_simulated_scenarios.parquet"
OUT_FILE = PROCESSED / "scenario_shipping_costs.csv"


# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------
def _load_params() -> dict:
    with open(CONFIG) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Toll derivation
# ---------------------------------------------------------------------------
def _derive_toll(baseline: pd.DataFrame, base_rate: float, bbl_per_ship: float) -> pd.Series:
    """
    Returns a Series indexed by edge_id with the toll in USD/barrel.

    For the baseline scenario with throttle_factor == 1:
        cost_per_barrel = free_flow_days * base_rate / bbl_per_ship + toll_per_barrel
    So:
        toll_per_barrel = cost_per_barrel - free_flow_days * base_rate / bbl_per_ship

    Tolls are floored at zero (rounding artefacts can produce tiny negatives).
    """
    bl = baseline[baseline["throttle_factor"] == 1.0].copy()
    sailing = bl["free_flow_days"] * base_rate / bbl_per_ship
    toll = (bl["cost_per_barrel"] - sailing).clip(lower=0.0)
    toll.index = bl["edge_id"]
    return toll


# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------
def compute_edge_costs(
    df: pd.DataFrame,
    toll_by_edge: pd.Series,
    rate_low: float,
    rate_base: float,
    rate_high: float,
    bbl_per_ship: float,
) -> pd.DataFrame:
    """
    Add cost_per_bbl_low / _base / _high columns to df.

    Closed edges (throttle_factor == 0) → cost is undefined/zero; we still
    report the formula value so the row is not NaN, but oil_volume_bbl == 0
    already flags these edges.

    Parameters
    ----------
    df : DataFrame with 22-column contract plus 'edge_id', 'free_flow_days',
         'delay_days' columns.
    toll_by_edge : Series[float] indexed by edge_id.
    """
    out = df.copy()

    # Map toll onto every row (default 0.0 for edges not in baseline)
    out["toll_per_barrel"] = out["edge_id"].map(toll_by_edge).fillna(0.0)

    edge_days = out["free_flow_days"] + out["delay_days"]

    for label, rate in [("low", rate_low), ("base", rate_base), ("high", rate_high)]:
        out[f"cost_per_bbl_{label}"] = (
            edge_days * rate / bbl_per_ship + out["toll_per_barrel"]
        )

    return out


def _fleet_average_cost(df: pd.DataFrame, rates: list[tuple[str, str]]) -> pd.Series:
    """
    Barrel-weighted mean cost across all active (oil_volume_bbl > 0) edges
    for each scenario.

    Parameters
    ----------
    df     : DataFrame with scenario_id, oil_volume_bbl, cost_per_bbl_* columns.
    rates  : list of (src_col, dst_col) pairs, e.g. [("cost_per_bbl_low", "fleet_cost_per_bbl_low")]
    Returns a wide DataFrame with scenario_id as index and fleet_cost columns.
    """
    active = df[df["oil_volume_bbl"] > 0].copy()

    result_rows = []
    for scenario_id, grp in active.groupby("scenario_id", sort=False):
        total_vol = grp["oil_volume_bbl"].sum()
        row: dict = {"scenario_id": scenario_id}
        for src_col, dst_col in rates:
            row[dst_col] = (grp[src_col] * grp["oil_volume_bbl"]).sum() / total_vol
        result_rows.append(row)

    return pd.DataFrame(result_rows).set_index("scenario_id")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(check: bool = False) -> None:
    params = _load_params()
    rate_low = float(params["daily_tanker_rate_low"])
    rate_base = float(params["daily_tanker_rate"])
    rate_high = float(params["daily_tanker_rate_high"])
    bbl_per_ship = float(params["avg_barrels_per_transit"])

    # ---- load data ---------------------------------------------------------
    scenarios = pd.read_parquet(SCENARIO_FILE)
    simulated = pd.read_parquet(SIM_FILE)

    # Combine; keep only columns we need to avoid schema conflicts
    keep = [
        "scenario_id", "edge_id", "from_node", "to_node",
        "edge_type", "searoute_passage", "length_nm",
        "throttle_factor", "oil_volume_bbl", "tanker_transits",
        "free_flow_days", "delay_days", "cost_per_barrel", "source",
    ]
    combined = pd.concat(
        [scenarios[keep], simulated[keep]],
        ignore_index=True,
    )

    # ---- derive toll from baseline -----------------------------------------
    baseline = scenarios[scenarios["scenario_id"] == "baseline"]
    toll_by_edge = _derive_toll(baseline, rate_base, bbl_per_ship)

    # ---- compute edge-level costs ------------------------------------------
    out = compute_edge_costs(
        combined, toll_by_edge, rate_low, rate_base, rate_high, bbl_per_ship
    )

    # ---- fleet-average costs (one row per scenario) ------------------------
    rate_pairs = [
        ("cost_per_bbl_low",  "fleet_cost_per_bbl_low"),
        ("cost_per_bbl_base", "fleet_cost_per_bbl_base"),
        ("cost_per_bbl_high", "fleet_cost_per_bbl_high"),
    ]
    fleet = _fleet_average_cost(out, rate_pairs)
    out = out.join(fleet, on="scenario_id")

    # ---- select output columns ---------------------------------------------
    out_cols = [
        "scenario_id", "edge_id", "from_node", "to_node", "edge_type",
        "searoute_passage", "length_nm", "free_flow_days", "delay_days",
        "toll_per_barrel", "oil_volume_bbl", "tanker_transits",
        "cost_per_bbl_low", "cost_per_bbl_base", "cost_per_bbl_high",
        "fleet_cost_per_bbl_low", "fleet_cost_per_bbl_base", "fleet_cost_per_bbl_high",
        "source",
    ]
    out = out[out_cols]

    # ---- write output ------------------------------------------------------
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_FILE, index=False)
    print(f"[shipping_cost] wrote {len(out):,} rows → {OUT_FILE}")

    # ---- acceptance check --------------------------------------------------
    if check:
        _acceptance_check(out)


def _acceptance_check(df: pd.DataFrame) -> None:
    """
    From spec: closing Suez raises Europe corridor cost by roughly $1.50–$3.50/bbl
    across the three rates.
    """
    print("\n[shipping_cost] Running acceptance check …")

    # Identify Suez-fully-closed simulated scenario
    suez_closed = df[
        (df["scenario_id"] == "sim_E001_th_000") &
        (df["searoute_passage"] == "suez")
    ]
    baseline_suez = df[
        (df["scenario_id"] == "sim_baseline") &
        (df["searoute_passage"] == "suez")
    ]

    if suez_closed.empty or baseline_suez.empty:
        print("  WARNING: could not find Suez rows for check")
        return

    # Fleet-level delta for the closed scenario vs baseline
    for rate in ["low", "base", "high"]:
        col = f"fleet_cost_per_bbl_{rate}"
        delta = (
            suez_closed[col].iloc[0] - baseline_suez[col].iloc[0]
            if col in suez_closed.columns else float("nan")
        )
        print(f"  fleet_cost_per_bbl_{rate}: baseline={baseline_suez[col].iloc[0]:.4f}, "
              f"suez_closed={suez_closed[col].iloc[0]:.4f}, delta={delta:+.4f}")

    # No NaN check
    nan_count = df[["cost_per_bbl_low", "cost_per_bbl_base", "cost_per_bbl_high"]].isna().sum().sum()
    print(f"  NaN count in cost columns: {nan_count}  ({'PASS' if nan_count == 0 else 'FAIL'})")

    print("[shipping_cost] acceptance check complete\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="Run acceptance checks after writing")
    args = parser.parse_args()
    main(check=args.check)
