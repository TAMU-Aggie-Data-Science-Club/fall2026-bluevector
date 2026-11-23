"""
scripts/price/network_indices.py  —  Deliverable 5, slot E1 (part 2 of 2)

PURPOSE
-------
Build the three network-level indices for every *historical week* (from
PortWatch / EIA data) and for every *scenario* (from the scenario edge tables).
Scenario route quantities share the standalone cost implementation. History
provides the observed Suez share; unavailable route quantities remain missing.

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
Scenario indices (history supplies only the observed Suez share):

1. suez_tanker_share
       suez_transits / (suez_transits + cape_transits)
   Measures the Europe–Asia routing split.  When Suez is blocked ships go
   around the Cape, so this ratio falls.
   "suez"  -> searoute_passage == "suez"
   "cape"  -> searoute_passage == "south_africa"

2. avg_voyage_days (barrel-weighted)
       SUM(oil_volume_bbl * total_days) / SUM(oil_volume_bbl)
   where total_days = free_flow_days + delay_days.
   Longer diversions show up as a higher number.

3. rerouted_barrel_miles (in billion barrel-nautical-miles)
       SUM(|delta_volume_bbl| * length_nm) / 1e9
   The extra work the fleet absorbs when routes shift.

Historical PortWatch weeks provide the Suez/Cape share only. Route days,
rerouted work, shipping costs and GNN embeddings are unavailable at this stage
and remain missing. The price backtest joins separately trained lagged GNN
embeddings and uses only observable network inputs.

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
from scripts.price.shipping_cost import _derive_toll, compute_edge_costs, _fleet_average_cost

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

        # 2. Voyage days: sum all legs, divide by served (loaded) barrels.
        active = grp[grp["oil_volume_bbl"] > 0]
        total_days = active["free_flow_days"] + active["delay_days"]
        total_vol = active.loc[active.edge_type.eq("connector"), "oil_volume_bbl"].sum() / 2
        avg_days = (
            float((total_days * active["oil_volume_bbl"]).sum() / total_vol)
            if total_vol > 0 else float("nan")
        )

        # 3. rerouted_barrel_miles (billion bbl-nm)
        rerouted = float(
            (grp["delta_volume_bbl"].abs() * grp["length_nm"]).sum() / 1e9
        )

        # Use exactly the same cargo sizes, tolls and served-cargo denominator
        # as the standalone shipping-cost output.
        costs = compute_edge_costs(grp, toll_by_edge, rate_low, rate_base, rate_high, bbl_per_ship)
        costs['scenario_id'] = str(grp_id)
        fleet = _fleet_average_cost(costs, [(f'cost_per_bbl_{k}', f'fleet_cost_per_bbl_{k}') for k in ['low', 'base', 'high']]).iloc[0]
        cost_low, cost_base, cost_high = [float(fleet[f'fleet_cost_per_bbl_{k}']) for k in ['low', 'base', 'high']]

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
    """Observed Suez share; unobserved historical route metrics remain missing.

    PortWatch counts alone do not reconstruct global voyages or freight costs.
    Missing route values are deliberate, never zero-filled or fit as price labels.
    """
    work = chokepoint.copy()
    if 'n_days_observed' in work:
        work = work[work.n_days_observed.eq(7)]
    piv = work.pivot(index='week_ending', columns='searoute_passage', values='weekly_tanker_transits')
    share = piv.suez / (piv.suez + piv.south_africa).replace(0, np.nan)
    hist = share.rename('suez_tanker_share').reset_index().rename(columns={'week_ending': 'week_or_scenario_id'})
    hist = hist.merge(brent.rename(columns={'week_ending': 'week_or_scenario_id'}), on='week_or_scenario_id', how='left', validate='one_to_one')
    for col in ['avg_voyage_days', 'rerouted_barrel_miles', 'shipping_cost_per_bbl_low',
                'shipping_cost_per_bbl_base', 'shipping_cost_per_bbl_high', 'delta_cost_per_bbl_base']:
        hist[col] = np.nan
    for i in range(8):
        hist[f'gnn_emb_{i}'] = np.nan
    hist['source'] = 'observed'
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
    observed_reference: pd.Series | None = None,
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

    # The solver counts crude-carrying transits; history counts all tankers.
    # Apply solver percentage changes to pre-event observed tanker means before
    # taking the share. Comparing the raw crude share with history mixes populations.
    if observed_reference is not None:
        base_cp = baseline.groupby('searoute_passage').tanker_transits.sum()
        scenario_cp = combined.pivot_table(index='scenario_id', columns='searoute_passage', values='tanker_transits', aggfunc='sum')
        implied_suez = scenario_cp.suez / base_cp['suez'] * observed_reference['suez']
        implied_cape = scenario_cp.south_africa / base_cp['south_africa'] * observed_reference['south_africa']
        shares = implied_suez / (implied_suez + implied_cape).replace(0, np.nan)
        result['suez_tanker_share'] = result.week_or_scenario_id.map(shares)

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
    toll_by_edge = _derive_toll(baseline, rate_base, bbl_per_ship)

    # ---- HISTORY -----------------------------------------------------------
    print("[network_indices] Building historical price dataset …")
    hist = build_history(
        chokepoint, brent, baseline, toll_by_edge,
        rate_low=rate_low, rate_base=rate_base, rate_high=rate_high,
        bbl_per_ship=bbl_per_ship,
    )
    hist = _reorder(hist, _HISTORY_EXTRA)
    hist.to_parquet(OUT_HISTORY, index=False)
    print(f"[network_indices] wrote {len(hist):,} history rows -> {OUT_HISTORY}")

    # ---- SCENARIOS ---------------------------------------------------------
    print("[network_indices] Building scenario price features …")
    combined = pd.concat([scenarios, simulated], ignore_index=True)
    scen_features = build_scenarios(
        combined, baseline, toll_by_edge,
        rate_low=rate_low, rate_base=rate_base, rate_high=rate_high,
        bbl_per_ship=bbl_per_ship,
        observed_reference=chokepoint[
            pd.to_datetime(chokepoint.week_ending).between('2023-01-01', '2023-10-31')
            & chokepoint.n_days_observed.eq(7)
        ].groupby('searoute_passage').weekly_tanker_transits.mean(),
    )
    scen_features = _reorder(scen_features, [])
    scen_features.to_parquet(OUT_SCENARIO, index=False)
    scen_features.to_parquet(OUT_CONTRACT, index=False)   # §3.2 alias
    print(f"[network_indices] wrote {len(scen_features):,} scenario rows -> {OUT_SCENARIO}")
    print(f"[network_indices] also wrote alias -> {OUT_CONTRACT}")

    if check:
        _acceptance_check(hist, scen_features)


def _acceptance_check(hist: pd.DataFrame, scen: pd.DataFrame) -> None:
    print("\n[network_indices] Running acceptance check …")

    # 1. No NaN in key columns (history)
    key_hist = ["suez_tanker_share"]
    nan_hist = hist[key_hist].isna().sum().sum()
    assert nan_hist == 0, "Missing observed Suez shares"
    print(f"  History NaN in key columns: {nan_hist} ({'PASS' if nan_hist == 0 else 'FAIL'})")

    # 2. Scenario columns match history columns (minus brent_weekly_avg)
    scen_cols = set(scen.columns)
    hist_cols = set(hist.columns) - {"brent_weekly_avg"}
    missing = hist_cols - scen_cols
    extra = scen_cols - hist_cols
    assert not missing and not extra, "History/scenario schemas differ"
    numeric = ["suez_tanker_share", "avg_voyage_days", "rerouted_barrel_miles", "shipping_cost_per_bbl_low", "shipping_cost_per_bbl_base", "shipping_cost_per_bbl_high"]
    assert np.isfinite(scen[numeric].to_numpy()).all(), "Nonfinite scenario features"
    print(f"  Scenario vs history columns match: missing={missing}, extra={extra} "
          f"({'PASS' if not missing and not extra else 'FAIL'})")

    # 3. Suez-closed scenario has suez_share ~ 0
    suez_closed = scen[scen["week_or_scenario_id"] == "sim_E001_th_000"]
    if not suez_closed.empty:
        share = suez_closed["suez_tanker_share"].iloc[0]
        assert share < 0.05, "Closed Suez has a nonzero tanker share"
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
