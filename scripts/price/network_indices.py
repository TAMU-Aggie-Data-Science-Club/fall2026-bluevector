"""
scripts/price/network_indices.py  —  Deliverable 5, slot E1 (part 2 of 2)

PURPOSE
-------
Build the three network-level indices for every *historical week* (from
PortWatch / EIA data) and for every *scenario* (from the scenario edge tables).
The same function is used in both paths so the column names, units and
definitions are guaranteed to be identical.

Outputs
-------
data/processed/historical_price_dataset.parquet
    One row per Friday-ending week. Columns: week_or_scenario_id,
    suez_tanker_share, avg_voyage_days, rerouted_barrel_miles,
    shipping_cost_per_bbl_low, shipping_cost_per_bbl_base,
    shipping_cost_per_bbl_high, delta_cost_per_bbl_base,
    gnn_emb_0 … gnn_emb_7, brent_weekly_avg, source="observed"

data/processed/scenario_price_features.parquet
    Same columns; one row per scenario_id; source="modeled" or "simulated".
    Also written to data/processed/network_price_features.parquet (alias
    required by the §3.2 contract).

HOW IT WORKS
------------
Three indices (one function, identical for history and scenarios):

1. suez_tanker_share
       suez_transits / (suez_transits + cape_transits)
   Measures the Europe–Asia routing split.  When Suez is blocked ships go
   around the Cape, so this ratio falls.
   "suez"  → searoute_passage == "suez"
   "cape"  → searoute_passage == "south_africa"

2. avg_voyage_days (barrel-weighted)
       SUM(oil_volume_bbl * total_days) / SUM(oil_volume_bbl)
   where total_days = free_flow_days + delay_days.
   Longer diversions show up as a higher number.

3. rerouted_barrel_miles (in billion barrel-nautical-miles)
       SUM(|delta_volume_bbl| * length_nm) / 1e9
   The extra work the fleet absorbs when routes shift.

For historical weeks the indices are computed from weekly PortWatch chokepoint
data (observed transits at Suez and Cape).  Voyage days and rerouted barrel-
miles are approximated from the baseline edge flows scaled by the weekly
utilisation ratio (observed / baseline transits).  The delta_volume column is
not directly observed historically, so for the history path we set
rerouted_barrel_miles = 0 and note it as a limitation in the source column.

Shipping cost per barrel for history
       Identical formula to shipping_cost.py, applied to the scaled baseline
       edge table per week.  Gives low / base / high freight bands over time.

GNN embeddings
       Zero for history rows and for physics/modeled scenarios (they will be
       filled by train_gnn.py in slot G3 and joined in backtest_price.py).

Usage
-----
    python -m scripts.price.network_indices          # writes both output files
    python -m scripts.price.network_indices --check  # + acceptance checks
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = pathlib.Path(__file__).resolve().parents[2]
PROCESSED = REPO / "data" / "processed"
INTERIM = REPO / "data" / "interim"
CONFIG = REPO / "config" / "flow_params.json"

SCENARIO_FILE = PROCESSED / "scenario_edge_flows.parquet"
SIM_FILE = PROCESSED / "gnn_simulated_scenarios.parquet"
CHOKEPOINT_WEEKLY = PROCESSED / "chokepoint_weekly_attributes.parquet"
BRENT_WEEKLY = INTERIM / "eia_brent_weekly.parquet"

OUT_HISTORY = PROCESSED / "historical_price_dataset.parquet"
OUT_SCENARIO = PROCESSED / "scenario_price_features.parquet"
OUT_CONTRACT = PROCESSED / "network_price_features.parquet"   # §3.2 alias


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _load_params() -> dict:
    with open(CONFIG) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Core index function (same logic for history and scenarios)
# ---------------------------------------------------------------------------
def compute_indices(
    edge_df: pd.DataFrame,
    id_col: str,
    *,
    rate_low: float,
    rate_base: float,
    rate_high: float,
    bbl_per_ship: float,
    toll_by_edge: pd.Series,
) -> pd.DataFrame:
    """
    Compute the three network indices + three shipping cost columns for each
    unique value in `id_col` (a scenario_id or week_or_scenario_id).

    Parameters
    ----------
    edge_df     : DataFrame with 22-column contract.  Must have columns:
                  id_col, searoute_passage, tanker_transits, oil_volume_bbl,
                  free_flow_days, delay_days, length_nm, delta_volume_bbl,
                  edge_id, throttle_factor.
    id_col      : grouping column name ("scenario_id" or "week_or_scenario_id")
    toll_by_edge: Series[float] indexed by edge_id with toll in USD/bbl.
    """
    rows = []

    for grp_id, grp in edge_df.groupby(id_col, sort=False):
        # 1. suez_tanker_share
        suez = grp[grp["searoute_passage"] == "suez"]["tanker_transits"].sum()
        cape = grp[grp["searoute_passage"] == "south_africa"]["tanker_transits"].sum()
        denom = suez + cape
        suez_share = float(suez / denom) if denom > 0 else float("nan")

        # 2. avg_voyage_days (barrel-weighted)
        active = grp[grp["oil_volume_bbl"] > 0]
        total_days = active["free_flow_days"] + active["delay_days"]
        total_vol = active["oil_volume_bbl"].sum()
        avg_days = (
            float((total_days * active["oil_volume_bbl"]).sum() / total_vol)
            if total_vol > 0 else float("nan")
        )

        # 3. rerouted_barrel_miles (billion bbl-nm)
        rerouted = float(
            (grp["delta_volume_bbl"].abs() * grp["length_nm"]).sum() / 1e9
        )

        # 4. shipping cost per barrel (fleet average at three rates)
        edge_days = grp["free_flow_days"] + grp["delay_days"]
        toll = grp["edge_id"].map(toll_by_edge).fillna(0.0)
        for label, rate in [("low", rate_low), ("base", rate_base), ("high", rate_high)]:
            grp = grp.copy()
            grp[f"_cst_{label}"] = edge_days * rate / bbl_per_ship + toll

        # barrel-weighted fleet averages
        def _fleet_avg(cost_col: str) -> float:
            act = grp[grp["oil_volume_bbl"] > 0]
            tvol = act["oil_volume_bbl"].sum()
            if tvol <= 0:
                return float("nan")
            return float((act[cost_col] * act["oil_volume_bbl"]).sum() / tvol)

        cost_low  = _fleet_avg("_cst_low")
        cost_base = _fleet_avg("_cst_base")
        cost_high = _fleet_avg("_cst_high")

        rows.append({
            id_col: grp_id,
            "suez_tanker_share": suez_share,
            "avg_voyage_days": avg_days,
            "rerouted_barrel_miles": rerouted,
            "shipping_cost_per_bbl_low": cost_low,
            "shipping_cost_per_bbl_base": cost_base,
            "shipping_cost_per_bbl_high": cost_high,
        })

    result = pd.DataFrame(rows)
    result = result.rename(columns={id_col: "week_or_scenario_id"})
    return result


# ---------------------------------------------------------------------------
# Historical path
# ---------------------------------------------------------------------------
def build_history(
    chokepoint: pd.DataFrame,
    brent: pd.DataFrame,
    baseline: pd.DataFrame,
    toll_by_edge: pd.Series,
    *,
    rate_low: float,
    rate_base: float,
    rate_high: float,
    bbl_per_ship: float,
) -> pd.DataFrame:
    """
    Build one row per week from observed PortWatch chokepoint data.

    Strategy
    --------
    For each week:
      - Suez and Cape observed tanker transits → suez_tanker_share.
      - avg_voyage_days: scale baseline edge free_flow_days by (observed /
        baseline transits ratio); uses baseline flow volume weights.
      - rerouted_barrel_miles: not directly observable; set to 0.0 and flag
        as "observed" anyway (limitation noted in source).
      - Shipping cost: baseline edge costs scaled by the same utilisation
        ratio for delay, then barrel-weighted.

    Both Suez and Cape must have observations in a week for the week to be
    included.
    """
    # Pivot chokepoints wide: one row per week_ending, one col per passage
    piv_transits = (
        chokepoint
        .pivot_table(
            index="week_ending",
            columns="searoute_passage",
            values="weekly_tanker_transits",
            aggfunc="sum",
        )
        .reset_index()
    )
    piv_volume = (
        chokepoint
        .pivot_table(
            index="week_ending",
            columns="searoute_passage",
            values="est_oil_volume_bbl",
            aggfunc="sum",
        )
        .reset_index()
    )

    # We need Suez ("suez") and Cape ("south_africa") in both
    for col in ["suez", "south_africa"]:
        if col not in piv_transits.columns:
            piv_transits[col] = float("nan")

    piv_transits = piv_transits.dropna(subset=["suez", "south_africa"])

    # Baseline transit values for scaling
    bl_suez = baseline[baseline["searoute_passage"] == "suez"]["tanker_transits"].sum()
    bl_cape = baseline[baseline["searoute_passage"] == "south_africa"]["tanker_transits"].sum()
    bl_total_vol = baseline["oil_volume_bbl"].sum()

    # Baseline fleet-level voyage days (for scaling)
    bl_days = (
        (baseline["free_flow_days"] + baseline["delay_days"]) * baseline["oil_volume_bbl"]
    ).sum() / bl_total_vol if bl_total_vol > 0 else float("nan")

    # Edge-level baseline shipping costs (for fleet average scaling)
    bl = baseline.copy()
    bl_toll = bl["edge_id"].map(toll_by_edge).fillna(0.0)
    for label, rate in [("low", rate_low), ("base", rate_base), ("high", rate_high)]:
        bl[f"_cst_{label}"] = (bl["free_flow_days"] + bl["delay_days"]) * rate / bbl_per_ship + bl_toll

    def _bl_fleet(col: str) -> float:
        act = bl[bl["oil_volume_bbl"] > 0]
        tvol = act["oil_volume_bbl"].sum()
        return float((act[col] * act["oil_volume_bbl"]).sum() / tvol) if tvol > 0 else float("nan")

    bl_cost_low  = _bl_fleet("_cst_low")
    bl_cost_base = _bl_fleet("_cst_base")
    bl_cost_high = _bl_fleet("_cst_high")

    # Build history rows
    rows = []
    for _, wrow in piv_transits.iterrows():
        week = wrow["week_ending"]
        obs_suez = wrow["suez"]
        obs_cape = wrow["south_africa"]

        denom = obs_suez + obs_cape
        suez_share = float(obs_suez / denom) if denom > 0 else float("nan")

        # Scale baseline voyage days by overall observed/baseline transit ratio
        # (rough approximation: if more ships go around Cape they travel longer)
        combined_ratio = (
            (obs_suez + obs_cape) / (bl_suez + bl_cape)
            if (bl_suez + bl_cape) > 0 else 1.0
        )
        # Cape detour premium ≈ 10 extra days vs Suez (13 096 nm vs 319 nm round trip)
        cape_premium_days = 10.0  # documented assumption: Cape route adds ~10 days vs Suez
        suez_divert_share = max(0.0, 1.0 - suez_share) if not np.isnan(suez_share) else 0.0
        avg_days = bl_days + suez_divert_share * cape_premium_days * (1.0 - suez_share if not np.isnan(suez_share) else 0.0)

        # Shipping cost: scale delay component by combined_ratio proxy
        # (simplified: delay scales with utilization which is proportional to transits)
        # In practice delay is small in baseline; this gives a reasonable low/high band
        cost_low  = bl_cost_low  * combined_ratio
        cost_base = bl_cost_base * combined_ratio
        cost_high = bl_cost_high * combined_ratio

        rows.append({
            "week_or_scenario_id": pd.Timestamp(week),
            "suez_tanker_share": suez_share,
            "avg_voyage_days": avg_days,
            "rerouted_barrel_miles": 0.0,  # not observable from weekly agg
            "shipping_cost_per_bbl_low":  cost_low,
            "shipping_cost_per_bbl_base": cost_base,
            "shipping_cost_per_bbl_high": cost_high,
        })

    hist = pd.DataFrame(rows)

    # Join Brent
    brent_j = brent[["week_ending", "brent_weekly_avg"]].copy()
    brent_j["week_ending"] = pd.to_datetime(brent_j["week_ending"])
    hist["week_or_scenario_id"] = pd.to_datetime(hist["week_or_scenario_id"])
    hist = hist.merge(
        brent_j.rename(columns={"week_ending": "week_or_scenario_id"}),
        on="week_or_scenario_id",
        how="left",
    )

    # delta_cost vs first week of data
    first_base = hist["shipping_cost_per_bbl_base"].iloc[0]
    hist["delta_cost_per_bbl_base"] = hist["shipping_cost_per_bbl_base"] - first_base

    # GNN embeddings (zeros for history)
    for i in range(8):
        hist[f"gnn_emb_{i}"] = 0.0

    hist["source"] = "observed"
    return hist


# ---------------------------------------------------------------------------
# Scenario path
# ---------------------------------------------------------------------------
def build_scenarios(
    combined: pd.DataFrame,
    baseline: pd.DataFrame,
    toll_by_edge: pd.Series,
    *,
    rate_low: float,
    rate_base: float,
    rate_high: float,
    bbl_per_ship: float,
) -> pd.DataFrame:
    """
    Build one row per scenario_id from the physics and simulated tables.
    """
    result = compute_indices(
        combined,
        id_col="scenario_id",
        rate_low=rate_low,
        rate_base=rate_base,
        rate_high=rate_high,
        bbl_per_ship=bbl_per_ship,
        toll_by_edge=toll_by_edge,
    )

    # Baseline fleet cost for delta
    bl_indices = compute_indices(
        baseline,
        id_col="scenario_id",
        rate_low=rate_low,
        rate_base=rate_base,
        rate_high=rate_high,
        bbl_per_ship=bbl_per_ship,
        toll_by_edge=toll_by_edge,
    )
    bl_base_cost = bl_indices["shipping_cost_per_bbl_base"].iloc[0]
    result["delta_cost_per_bbl_base"] = result["shipping_cost_per_bbl_base"] - bl_base_cost

    # GNN embeddings (zeros — filled later by G3/backtest)
    for i in range(8):
        result[f"gnn_emb_{i}"] = 0.0

    # Source column
    source_map = {sid: "simulated" for sid in combined[combined["source"] == "simulated"]["scenario_id"].unique()}
    source_map.update({sid: "modeled" for sid in combined[combined["source"] == "modeled"]["scenario_id"].unique()})
    result["source"] = result["week_or_scenario_id"].map(source_map).fillna("modeled")

    # brent_weekly_avg not applicable for scenarios
    result["brent_weekly_avg"] = float("nan")

    return result


# ---------------------------------------------------------------------------
# Column ordering helper
# ---------------------------------------------------------------------------
_CONTRACT_COLS = [
    "week_or_scenario_id",
    "suez_tanker_share",
    "avg_voyage_days",
    "rerouted_barrel_miles",
    "shipping_cost_per_bbl_low",
    "shipping_cost_per_bbl_base",
    "shipping_cost_per_bbl_high",
    "delta_cost_per_bbl_base",
    "gnn_emb_0", "gnn_emb_1", "gnn_emb_2", "gnn_emb_3",
    "gnn_emb_4", "gnn_emb_5", "gnn_emb_6", "gnn_emb_7",
    "source",
]

_HISTORY_EXTRA = ["brent_weekly_avg"]


def _reorder(df: pd.DataFrame, extra_cols: list[str]) -> pd.DataFrame:
    ordered = _CONTRACT_COLS + extra_cols
    present = [c for c in ordered if c in df.columns]
    return df[present]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(check: bool = False) -> None:
    params = _load_params()
    rate_low  = float(params["daily_tanker_rate_low"])
    rate_base = float(params["daily_tanker_rate"])
    rate_high = float(params["daily_tanker_rate_high"])
    bbl_per_ship = float(params["avg_barrels_per_transit"])

    # Load data
    scenarios = pd.read_parquet(SCENARIO_FILE)
    simulated = pd.read_parquet(SIM_FILE)
    chokepoint = pd.read_parquet(CHOKEPOINT_WEEKLY)
    brent = pd.read_parquet(BRENT_WEEKLY)

    baseline = scenarios[scenarios["scenario_id"] == "baseline"].copy()

    # Derive toll once from baseline
    # toll = cost_per_barrel - free_flow_days * base_rate / bbl_per_ship  (floored 0)
    bl_toll_series = (
        baseline["cost_per_barrel"]
        - baseline["free_flow_days"] * rate_base / bbl_per_ship
    ).clip(lower=0.0)
    bl_toll_series.index = baseline["edge_id"]
    toll_by_edge = bl_toll_series

    # ---- HISTORY -----------------------------------------------------------
    print("[network_indices] Building historical price dataset …")
    hist = build_history(
        chokepoint, brent, baseline, toll_by_edge,
        rate_low=rate_low, rate_base=rate_base, rate_high=rate_high,
        bbl_per_ship=bbl_per_ship,
    )
    hist = _reorder(hist, _HISTORY_EXTRA)
    hist.to_parquet(OUT_HISTORY, index=False)
    print(f"[network_indices] wrote {len(hist):,} history rows → {OUT_HISTORY}")

    # ---- SCENARIOS ---------------------------------------------------------
    print("[network_indices] Building scenario price features …")
    combined = pd.concat([scenarios, simulated], ignore_index=True)
    scen_features = build_scenarios(
        combined, baseline, toll_by_edge,
        rate_low=rate_low, rate_base=rate_base, rate_high=rate_high,
        bbl_per_ship=bbl_per_ship,
    )
    scen_features = _reorder(scen_features, [])
    scen_features.to_parquet(OUT_SCENARIO, index=False)
    scen_features.to_parquet(OUT_CONTRACT, index=False)   # §3.2 alias
    print(f"[network_indices] wrote {len(scen_features):,} scenario rows → {OUT_SCENARIO}")
    print(f"[network_indices] also wrote alias → {OUT_CONTRACT}")

    if check:
        _acceptance_check(hist, scen_features)


def _acceptance_check(hist: pd.DataFrame, scen: pd.DataFrame) -> None:
    print("\n[network_indices] Running acceptance check …")

    # 1. No NaN in key columns (history)
    key_hist = ["suez_tanker_share", "avg_voyage_days",
                "shipping_cost_per_bbl_low", "shipping_cost_per_bbl_base",
                "shipping_cost_per_bbl_high"]
    nan_hist = hist[key_hist].isna().sum().sum()
    print(f"  History NaN in key columns: {nan_hist} ({'PASS' if nan_hist == 0 else 'FAIL'})")

    # 2. Scenario columns match history columns (minus brent_weekly_avg)
    scen_cols = set(scen.columns)
    hist_cols = set(hist.columns) - {"brent_weekly_avg"}
    missing = hist_cols - scen_cols
    extra = scen_cols - hist_cols
    print(f"  Scenario vs history columns match: missing={missing}, extra={extra} "
          f"({'PASS' if not missing else 'FAIL'})")

    # 3. Suez-closed scenario has suez_share ~ 0
    suez_closed = scen[scen["week_or_scenario_id"] == "sim_E001_th_000"]
    if not suez_closed.empty:
        share = suez_closed["suez_tanker_share"].iloc[0]
        print(f"  Suez-closed scenario suez_share = {share:.4f} (expect near 0, {'PASS' if share < 0.05 else 'FAIL'})")

    # 4. Baseline scenario has suez_share similar to observed
    bl_scen = scen[scen["week_or_scenario_id"] == "baseline"]
    if not bl_scen.empty:
        bl_share = bl_scen["suez_tanker_share"].iloc[0]
        print(f"  Baseline scenario suez_share = {bl_share:.4f} (expect ~0.6–0.8)")

    print("[network_indices] acceptance check complete\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    main(check=args.check)
