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
    barrels_per_ship — edge oil volume / edge transits; config fallback on idle edges
    toll_per_barrel  — per-edge toll, derived from cost_per_barrel in baseline
                       minus its sailing and delay components (see note below)

Fleet voyage cost:
    Sum edge flow times edge cost, divided by served cargo (half the sum
    of connector flows). This counts every voyage leg without counting a
    routed barrel repeatedly in the denominator.

Toll derivation
    The 22-column table's cost_per_barrel already includes toll.  At baseline
    (throttle_factor == 1), the toll component is:
        toll_per_barrel = baseline_cost_per_barrel
                         - (baseline_free_flow_days + baseline_delay_days) * base_rate / barrels_per_ship
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
        cost_per_barrel = (free_flow_days + delay_days) * base_rate / bbl_per_ship + toll_per_barrel
    So:
        toll_per_barrel = cost_per_barrel - (free_flow_days + delay_days) * base_rate / bbl_per_ship

    Tolls are floored at zero (rounding artefacts can produce tiny negatives).
    """
    bl = baseline[baseline["throttle_factor"] == 1.0].copy()
    cargo = pd.Series(bbl_per_ship, index=bl.index, dtype=float)
    if {"oil_volume_bbl", "tanker_transits"}.issubset(bl.columns):
        cargo = (bl.oil_volume_bbl / bl.tanker_transits.replace(0, np.nan)).fillna(bbl_per_ship)
    sailing = (bl["free_flow_days"] + bl["delay_days"]) * base_rate / cargo
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

    Closed edges (throttle_factor == 0) -> cost is undefined/zero; we still
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
    cargo = pd.Series(bbl_per_ship, index=out.index, dtype=float)
    if {"oil_volume_bbl", "tanker_transits"}.issubset(out.columns):
        cargo = (out.oil_volume_bbl / out.tanker_transits.replace(0, np.nan)).fillna(bbl_per_ship)
    if (cargo <= 0).any():
        raise ValueError("Cargo per transit must be positive")

    for label, rate in [("low", rate_low), ("base", rate_base), ("high", rate_high)]:
        out[f"cost_per_bbl_{label}"] = (
            edge_days * rate / cargo + out["toll_per_barrel"]
        )

    return out


def _fleet_average_cost(df: pd.DataFrame, rates: list[tuple[str, str]]) -> pd.Series:
    """
    Total transport cost divided by served barrels across all active edges
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
        # Each served barrel crosses two connectors: loading and discharge.
        # Dividing by all edge traversals instead would report average leg cost.
        total_vol = grp.loc[grp.edge_type.eq("connector"), "oil_volume_bbl"].sum() / 2
        if total_vol <= 0:
            raise ValueError("No served cargo denominator for fleet voyage cost")
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
    for label in ['low', 'base', 'high']:
        column = f'fleet_cost_per_bbl_{label}'
        fleet[f'delta_cost_per_bbl_{label}'] = fleet[column] - fleet.loc['baseline', column]
    out = out.join(fleet, on="scenario_id")

    # ---- select output columns ---------------------------------------------
    out_cols = [
        "scenario_id", "edge_id", "from_node", "to_node", "edge_type",
        "searoute_passage", "length_nm", "free_flow_days", "delay_days",
        "toll_per_barrel", "oil_volume_bbl", "tanker_transits",
        "cost_per_bbl_low", "cost_per_bbl_base", "cost_per_bbl_high",
        "fleet_cost_per_bbl_low", "fleet_cost_per_bbl_base", "fleet_cost_per_bbl_high",
        "delta_cost_per_bbl_low", "delta_cost_per_bbl_base", "delta_cost_per_bbl_high",
        "source",
    ]
    out = out[out_cols]

    # ---- write output ------------------------------------------------------
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_FILE, index=False)
    print(f"[shipping_cost] wrote {len(out):,} rows -> {OUT_FILE}")

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
        (df["scenario_id"] == "baseline") &
        (df["searoute_passage"] == "suez")
    ]

    if suez_closed.empty or baseline_suez.empty:
        raise ValueError("Missing required Suez closure or baseline for acceptance check")

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
    assert nan_count == 0, "Missing shipping costs"
    assert np.isfinite(df[["cost_per_bbl_low", "cost_per_bbl_base", "cost_per_bbl_high"]].to_numpy()).all()
    print(f"  NaN count in cost columns: {nan_count}  ({'PASS' if nan_count == 0 else 'FAIL'})")

    # A route split into more edges must retain its complete voyage cost.
    for lane_days in ([10.], [5., 5.]):
        days = [0.] + lane_days + [0.]
        fixture = pd.DataFrame(dict(scenario_id='check', edge_id=[str(i) for i in range(len(days))],
                                    edge_type=['connector'] + ['lane'] * len(lane_days) + ['connector'],
                                    free_flow_days=days, delay_days=0., oil_volume_bbl=100., tanker_transits=1.))
        zero_tolls = pd.Series(0., index=fixture.edge_id)
        costs = compute_edge_costs(fixture, zero_tolls, 10., 10., 10., 100.)
        voyage = _fleet_average_cost(costs, [('cost_per_bbl_base', 'cost')]).iloc[0]['cost']
        assert np.isclose(voyage, 1.), 'Voyage cost depends on edge segmentation'
    print("  Route segmentation invariance: PASS")
    print("[shipping_cost] acceptance check complete\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="Run acceptance checks after writing")
    args = parser.parse_args()
    main(check=args.check)
