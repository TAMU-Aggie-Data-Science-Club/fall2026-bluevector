"""Deliverable 4 — Flow & Scenarios Group, Part 3: Scenario Engine & Edge Criticality.

What it does:
  1. Exposes run_scenario(throttles, scenario_id) which solves the network under any single-
     or multi-edge throttle and returns (edge_flows_df, summary_dict). Throttles are throughput
     fractions: 0.6 = "60% of pre-event crude throughput still available", 0.0 = closed.
  2. Defines the historical disruption scenarios from the events themselves (6 blocked days for
     Ever Given, ACP booking-slot cut for Panama, calibrated Red Sea throughput loss), so the
     scenario engine never depends on the validation targets it is later scored against.
  3. Runs those scenarios and writes data/processed/scenario_edge_flows.parquet.
  4. Closes every chokepoint edge and the busiest lanes one by one and ranks criticality in
     data/processed/edge_criticality.csv. Ton-mile change is computed on the same origin-destination
     pairs that still move, so a closure that strands cargo is scored by stranded share rather than
     by a negative (meaningless) ton-mile change.

Run from the repository root:
    .venv/bin/python scripts/flow_model/scenario_engine.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.flow_model.edge_metrics import compute_edge_metrics
from scripts.flow_model.flow_solver import load_params, solve_network

CONFIG_DIR = ROOT / "config"
PROCESSED_DIR = ROOT / "data" / "processed"

# Ever Given: the canal was blocked for 6 of the 7 days in the peak week
EVER_GIVEN_THROUGHPUT = 1.0 / 7.0
# Panama drought: ACP cut daily booking slots from ~36 to 24 (Oct 2023 - Mar 2024)
PANAMA_THROUGHPUT = 24.0 / 36.0

# Red Sea war-risk premium, sourced rather than fitted. Insurers quote an additional war-risk
# premium (AWRP) as a percentage of hull value per transit through a JWC-listed area: Lloyd's List
# (5 Jan 2024) reports 0.3-0.35 %, Lloyd's List (17 Jan 2024) and Reuters (16 Jan 2024) report ~1 %,
# and OECD/ITF (Mar 2024) summarises the same range. Converted on the same Suezmax the model prices
# everywhere -- $82.8M hull, 1,000,000 bbl cargo (cost_barrels_per_cargo) -- 0.5 % is $414k, or
# $0.41/bbl, and 1 % is $828k, or $0.83/bbl.
# H1 (Dec 2023 - Feb 2024) averages 0.5 %, H2 (Mar - Jun 2024) sits at the ~1 % peak.
AWRP_RED_SEA_H1_USD_PER_BBL = 0.41
AWRP_RED_SEA_H2_USD_PER_BBL = 0.83
# One insurance bill per transit, not per chokepoint: charge at the southern gate of the listed area
# (Bab-el-Mandeb) and not again at Suez.
RED_SEA_RISK_PASSAGES = ["babalmandab"]

# Canal tariffs in force in each validation window, per transit, Suezmax / 1.0M bbl cargo.
# Suez is laden, one way, Suezmax SCNT 78,500: 2020 schedule in Mar 2021 ($305-311k), the 1 Jan 2023
# schedule plus the 25 % laden tanker surcharge from 1 Apr 2023 ($444,200), and the 15 Jan 2024
# schedule plus surcharge ($510,700). Panama is ACP item 1010: $300,000 fixed + $1.30 per PC/UMS ton
# in 2023 and $2.25 in 2024, at an assumed 65,000 PC/UMS. Bosporus is one way, half the double
# passage, at gold-franc $4.42 and 50,000 NT.
SUEZ_TOLL_2021 = 305_000.0
SUEZ_TOLL_2023 = 444_200.0
SUEZ_TOLL_H1 = 477_450.0   # Dec-Feb: 45 days at the 2023 tariff, 45 at the 2024 one
SUEZ_TOLL_2024 = 510_700.0
PANAMA_TOLL_2023 = 384_500.0
PANAMA_TOLL_2024 = 446_250.0
BOSPORUS_TOLL_2021 = 7_767.0
BOSPORUS_TOLL_2023 = 42_914.0

_BASE_CACHE: dict = {}

# Only these parameters change the baseline solve; throttles and risk surcharges do not.
_BASELINE_PARAM_KEYS = (
    "speed_knots",
    "alpha",
    "beta",
    "daily_tanker_rate",
    "avg_barrels_per_transit",
    "cost_barrels_per_cargo",
    "max_util_for_delay",
    "n_congestion_iters",
)


def _cache_key(params_override: dict | None):
    if not params_override:
        return None
    parts = [
        (k, round(float(params_override[k]), 6))
        for k in _BASELINE_PARAM_KEYS
        if k in params_override and isinstance(params_override[k], (int, float))
    ]
    tolls = params_override.get("toll_usd_per_transit")
    if isinstance(tolls, dict):
        parts.append(
            ("toll_usd_per_transit", tuple(sorted((str(k), float(v)) for k, v in tolls.items())))
        )
    return tuple(sorted(parts, key=lambda kv: kv[0]))


def get_baseline(params_override: dict | None = None):
    """Return (baseline_edge_flows, baseline_od_table) for the given parameter set.

    One solve. Capacity on every edge is PortWatch's observed `capacity_proxy` (95th percentile of
    weekly tanker transits, 2019-2023). The earlier version re-based capacity to the model's own
    baseline output and solved twice, so the capacity became a function of the answer it was meant
    to constrain; that also hid the fact that congestion is inert at observed utilisation.
    """
    key = _cache_key(params_override)
    if key in _BASE_CACHE:
        return _BASE_CACHE[key]

    cache_path = PROCESSED_DIR / "baseline_edge_flows.parquet"
    raw, base_od = solve_network(
        {}, "baseline", params_override=params_override, save_output=False, return_od=True
    )
    base_df = compute_edge_metrics(raw, baseline_df=raw, scenario_id="baseline", save_output=False)
    if key is None:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        base_df.to_parquet(cache_path, index=False)
        base_df.to_csv(PROCESSED_DIR / "baseline_edge_flows.csv", index=False)

    _BASE_CACHE[key] = (base_df, base_od)
    return _BASE_CACHE[key]


def event_scenarios(params: dict | None = None) -> dict[str, dict[str, float]]:
    """Historical disruption scenarios as throughput fractions of pre-event crude flow.

    The Red Sea entries are deliberately empty. Suez stayed open throughout Dec 2023 - Jun 2024, so
    modelling the diversion as a capacity cut would replay the observation inside the constraint
    that is supposed to predict it. The diversion has to come from the sourced premium in
    `event_scenario_params`. Only the two physical closures, Ever Given and the Panama slots, cut
    throughput.
    """
    return {
        "red_sea_2023h1": {},
        "red_sea_2024": {},
        "ever_given_2021": {"suez": EVER_GIVEN_THROUGHPUT},
        "panama_drought_2023": {"panama": PANAMA_THROUGHPUT},
    }


def event_scenario_params() -> dict[str, dict]:
    """Per-scenario parameter overrides: the sourced war-risk premium for each window.

    The premium comes from the insurance market, not from the transit counts the scenarios are
    scored against, so using the H2 value on H2 does not make the held-out test circular.
    """
    return {
        "red_sea_2023h1": {
            "awrp_usd_per_bbl": AWRP_RED_SEA_H1_USD_PER_BBL,
            "risk_passages": RED_SEA_RISK_PASSAGES,
            "toll_usd_per_transit": {
                "suez": SUEZ_TOLL_H1,
                "panama": PANAMA_TOLL_2023,
                "bosporus": BOSPORUS_TOLL_2023,
            },
        },
        "red_sea_2024": {
            "awrp_usd_per_bbl": AWRP_RED_SEA_H2_USD_PER_BBL,
            "risk_passages": RED_SEA_RISK_PASSAGES,
            "toll_usd_per_transit": {
                "suez": SUEZ_TOLL_2024,
                "panama": PANAMA_TOLL_2024,
                "bosporus": BOSPORUS_TOLL_2023,
            },
        },
        # Mar 2021: 2020 Suez schedule with no tanker surcharge, and Bosporus at the old gold franc.
        # No sourced Panama tanker toll exists for 2021, so Panama keeps its 2023 figure, which no
        # route in this scenario depends on.
        "ever_given_2021": {
            "toll_usd_per_transit": {
                "suez": SUEZ_TOLL_2021,
                "bosporus": BOSPORUS_TOLL_2021,
            },
        },
        # Oct-Dec 2023: the 2023 Panama tariff and the pre-Jan-2024 Suez schedule.
        "panama_drought_2023": {
            "toll_usd_per_transit": {
                "suez": SUEZ_TOLL_2023,
                "panama": PANAMA_TOLL_2023,
                "bosporus": BOSPORUS_TOLL_2023,
            },
        },
    }


def _od_summary(od: pd.DataFrame, base_od: pd.DataFrame) -> dict:
    """Barrel-miles and voyage days, compared on the OD pairs that are still served."""
    routed = od[od["barrels"] > 0].copy()
    if routed.empty:
        return {
            "total_barrel_miles": 0.0,
            "pct_change_barrel_miles": 0.0,
            "rerouted_barrel_miles": 0.0,
            "avg_voyage_days": 0.0,
            "base_avg_voyage_days": 0.0,
            "served_barrels": 0.0,
        }

    base = base_od.rename(
        columns={"barrels": "base_barrels", "barrel_nm": "base_barrel_nm", "barrel_days": "base_barrel_days"}
    )[["origin_portid", "destination_portid", "base_barrels", "base_barrel_nm", "base_barrel_days"]]
    m = routed.merge(base, on=["origin_portid", "destination_portid"], how="left")
    m["own_nm_per_bbl"] = m["barrel_nm"] / m["barrels"]
    m["own_days_per_bbl"] = m["barrel_days"] / m["barrels"]
    m["base_nm_per_bbl"] = (m["base_barrel_nm"] / m["base_barrels"]).where(lambda s: s > 0).fillna(m["own_nm_per_bbl"])
    m["base_days_per_bbl"] = (m["base_barrel_days"] / m["base_barrels"]).where(lambda s: s > 0).fillna(m["own_days_per_bbl"])

    total_tm = float(m["barrel_nm"].sum())
    comparable_tm = float((m["barrels"] * m["base_nm_per_bbl"]).sum())
    served = float(m["barrels"].sum())
    avg_days = float(m["barrel_days"].sum()) / served
    base_days = float((m["barrels"] * m["base_days_per_bbl"]).sum()) / served

    return {
        "total_barrel_miles": total_tm,
        "pct_change_barrel_miles": ((total_tm - comparable_tm) / comparable_tm * 100.0) if comparable_tm > 0 else 0.0,
        "rerouted_barrel_miles": max(0.0, total_tm - comparable_tm),
        "avg_voyage_days": avg_days,
        "base_avg_voyage_days": base_days,
        "served_barrels": served,
    }


def run_scenario(
    throttles: dict[str, float],
    scenario_id: str,
    params_override: dict | None = None,
    save_files: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Run a disruption scenario and return (edge_flows_df, summary_metrics_dict)."""
    base_df, base_od = get_baseline(params_override)
    base_tr = {str(k): float(v) for k, v in zip(base_df["edge_id"], base_df["tanker_transits"])}

    raw, od = solve_network(
        throttles=throttles,
        scenario_id=scenario_id,
        params_override=params_override,
        save_output=False,
        return_od=True,
        baseline_transits=base_tr,
    )
    edge_flows = compute_edge_metrics(raw, baseline_df=base_df, scenario_id=scenario_id, save_output=save_files)
    stats = _od_summary(od, base_od)

    suez_tr = float(edge_flows.loc[edge_flows["searoute_passage"] == "suez", "tanker_transits"].sum())
    cape_tr = float(edge_flows.loc[edge_flows["searoute_passage"] == "south_africa", "tanker_transits"].sum())
    suez_share = suez_tr / (suez_tr + cape_tr) if (suez_tr + cape_tr) > 0 else 0.0

    summary = {
        "scenario_id": scenario_id,
        "stranded_barrels": float(od["stranded"].sum()),
        "total_barrel_miles": stats["total_barrel_miles"],
        "pct_change_barrel_miles": round(stats["pct_change_barrel_miles"], 4),
        "rerouted_barrel_miles": stats["rerouted_barrel_miles"],
        "avg_voyage_days": round(stats["avg_voyage_days"], 3),
        "extra_voyage_days": round(stats["avg_voyage_days"] - stats["base_avg_voyage_days"], 3),
        "served_barrels": stats["served_barrels"],
        "total_od_barrels": float(od["barrels"].sum() + od["stranded"].sum()),
        "suez_tanker_transits": round(suez_tr, 2),
        "cape_tanker_transits": round(cape_tr, 2),
        "suez_tanker_share": round(suez_share, 4),
    }
    return edge_flows, summary


def compute_edge_criticality() -> pd.DataFrame:
    """Close each chokepoint and major lane edge one by one and rank criticality."""
    edges = pd.read_csv(CONFIG_DIR / "edges.csv")
    base_df, base_od = get_baseline(None)
    total_od_weekly = float(base_od["barrels"].sum() + base_od["stranded"].sum())

    cp_edges = edges[edges["edge_type"] == "chokepoint"].copy()
    lane_ids = base_df[base_df["edge_type"] == "lane"].nlargest(15, "oil_volume_bbl")["edge_id"].tolist()
    target_edges = pd.concat([cp_edges, edges[edges["edge_id"].isin(lane_ids)]]).drop_duplicates("edge_id")

    records = []
    for _, row in target_edges.iterrows():
        e_id = str(row["edge_id"])
        passage = (
            str(row["searoute_passage"])
            if pd.notna(row.get("searoute_passage"))
            else f"{row['from_node']}->{row['to_node']}"
        )
        base_edge_vol = float(base_df.loc[base_df["edge_id"] == e_id, "oil_volume_bbl"].iloc[0])

        _, summary = run_scenario({e_id: 0.0}, scenario_id=f"close_{e_id}", save_files=False)

        stranded_share = summary["stranded_barrels"] / max(total_od_weekly, 1.0)
        pct_inc_tm = max(0.0, summary["pct_change_barrel_miles"]) / 100.0
        extra_days = max(0.0, summary["extra_voyage_days"])

        # Combined criticality score (stranded cargo dominates, then detour length, then delay)
        score = 0.50 * stranded_share + 0.35 * pct_inc_tm + 0.15 * (extra_days / 10.0)

        records.append(
            {
                "edge_id": e_id,
                "edge_type": row["edge_type"],
                "name_or_passage": passage,
                "from_node": row["from_node"],
                "to_node": row["to_node"],
                "baseline_oil_volume_bbl": round(base_edge_vol, 0),
                "stranded_barrels_if_closed": round(summary["stranded_barrels"], 0),
                "stranded_share_pct": round(stranded_share * 100.0, 2),
                "routed_share_pct": round((1.0 - stranded_share) * 100.0, 2),
                "pct_increase_ton_miles": round(summary["pct_change_barrel_miles"], 2),
                "extra_voyage_days": round(extra_days, 3),
                "suez_tanker_share": summary["suez_tanker_share"],
                "criticality_score": round(score, 4),
                "rank_scope": "chokepoint" if row["edge_type"] == "chokepoint" else "lane_sensitivity",
            }
        )

    # Chokepoints are physical single points of failure and are ranked first. An open-sea lane is not a
    # point of failure at this graph resolution (a 1,000+ nm lane is one edge, and the natural short
    # detour through a port is forbidden by the port-transit rule), so lane closures are kept as a
    # clearly labelled sensitivity rather than competing with Hormuz or Suez in the main ranking.
    crit_df = (
        pd.DataFrame(records)
        .sort_values(["rank_scope", "criticality_score"], ascending=[True, False])
        .reset_index(drop=True)
    )
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    crit_df.to_csv(PROCESSED_DIR / "edge_criticality.csv", index=False)
    return crit_df


def main():
    base_df, _ = get_baseline(None)
    scenarios = [("baseline", {})]
    scenarios += [(sc_id, th) for sc_id, th in event_scenarios().items()]
    scen_params = event_scenario_params()

    frames = []
    summaries = []
    for sc_id, th in scenarios:
        df, s = run_scenario(
            th,
            scenario_id=sc_id,
            save_files=(sc_id == "baseline"),
            params_override=scen_params.get(sc_id) or None,
        )
        frames.append(df)
        summaries.append(s)

    all_scen = pd.concat(frames, ignore_index=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    all_scen.to_parquet(PROCESSED_DIR / "scenario_edge_flows.parquet", index=False)
    all_scen.to_csv(PROCESSED_DIR / "scenario_edge_flows.csv", index=False)

    print("Scenario summaries:")
    print(pd.DataFrame(summaries).to_string(index=False))

    crit_df = compute_edge_criticality()
    chokepoints = crit_df[crit_df["rank_scope"] == "chokepoint"]
    print("\nMost Critical Chokepoints (data/processed/edge_criticality.csv):")
    print(chokepoints.head(10).to_string(index=False))
    print(f"\n{len(crit_df) - len(chokepoints)} lane closures kept as sensitivity rows (rank_scope=lane_sensitivity).")


if __name__ == "__main__":
    main()
