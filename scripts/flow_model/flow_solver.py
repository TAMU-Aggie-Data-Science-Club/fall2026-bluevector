"""Deliverable 4 — Flow & Scenarios Group, Part 1: Core Network Flow Solver.

What it does:
  1. Loads config/nodes.csv, config/edges.csv, data/interim/od_matrix.csv, and config/flow_params.json.
  2. Maps OD matrix portwatch_portids (e.g. 'port1091') to graph node_ids ('port_port1091').
  3. Enforces port-endpoint-only rules:
     - Lanes and chokepoints are traversable in both directions.
     - Port connectors are split into directed origin-only (port -> junction) and
       destination-only (junction -> port) edges so no ship can ever shortcut through a port.
  4. Routes weekly crude oil demand (barrels_per_year / 52.14) with iterative capacity-constrained
     incremental assignment and BPR congestion updates plus canal tolls and war-risk surcharges.
  5. Records any unroutable demand (e.g. when Hormuz or Bosporus is 100% closed) as stranded_barrels,
     and optionally returns a per-OD table (barrels, barrel-miles, voyage days) so downstream
     scripts can compare ton-miles on a like-for-like OD basis.

Scenario semantics (important):
  A throttle is a *throughput* fraction: 0.6 means "60% of this edge's pre-event crude throughput
  is still available". It is converted to capacity as capacity_weekly = throttle * baseline_transits,
  then stored on the D3 statistical capacity basis (throttle_factor = capacity_weekly / capacity_proxy)
  so `capacity_weekly`, `throttle_factor` and `utilization` stay in the units of the Shared Schema
  Contract. The statistical PortWatch proxy (95th-99th percentile of all-tanker transits) is far
  above modeled crude throughput, so throttling the proxy directly would never bind on crude.

Run from the repository root:
    .venv/bin/python scripts/flow_model/flow_solver.py
"""

import json
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_DIR = ROOT / "config"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"

WEEKS_PER_YEAR = 52.1429

DEFAULT_PARAMS = {
    "speed_knots": 13.0,
    "alpha": 0.15,
    "beta": 4.0,
    "daily_tanker_rate": 50000.0,
    "avg_barrels_per_transit": 1000000.0,
    # Cargo used to turn per-transit dollars (toll, war-risk premium, a day's charter) into $/bbl.
    # A different question from avg_barrels_per_transit: that one is observed cargo per transit and
    # decides how many ships an edge carries; this one is the crude carrier the model prices. Set to
    # 0 to price on the observed cargo per transit, which is the behaviour before this split.
    "cost_barrels_per_cargo": 1000000.0,
    # No throughput cut by default: the Red Sea canal stayed open through 2024, so the diversion was
    # a price response. A throughput cut is reserved for closures that were physical (Ever Given,
    # Panama slots).
    # Additional war-risk premium in $/bbl, charged once per transit through a listed area.
    "awrp_usd_per_bbl": 0.0,
    "risk_passages": [],
    "max_util_for_delay": 5.0,
    "n_congestion_iters": 10,
}

_PARAM_KEYS_FOR_BASELINE = (
    "speed_knots",
    "alpha",
    "beta",
    "daily_tanker_rate",
    "avg_barrels_per_transit",
    "cost_barrels_per_cargo",
    "max_util_for_delay",
    "n_congestion_iters",
)


def load_params() -> dict:
    params = DEFAULT_PARAMS.copy()
    p_path = CONFIG_DIR / "flow_params.json"
    if p_path.exists():
        try:
            params.update(json.loads(p_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return params


def load_network():
    nodes_path = CONFIG_DIR / "nodes.csv"
    edges_path = CONFIG_DIR / "edges.csv"
    od_path = INTERIM_DIR / "od_matrix.csv"

    if not edges_path.exists():
        raise FileNotFoundError(f"Missing {edges_path}. Run build_connector_edges.py first.")
    if not od_path.exists():
        raise FileNotFoundError(f"Missing {od_path}. Run od_matrix.py first.")

    edges = pd.read_csv(edges_path)
    od = pd.read_csv(od_path)
    nodes = pd.read_csv(nodes_path) if nodes_path.exists() else pd.DataFrame()
    return nodes, edges, od


def map_portid_to_node_id(portid: str, node_lookup: dict) -> str:
    pid = str(portid).strip()
    if pid in node_lookup:
        return node_lookup[pid]
    if pid.startswith("port_"):
        return pid
    return f"port_{pid}"


def _baseline_transit_map(params: dict) -> dict[str, float]:
    """Pre-event crude transits per edge, used to convert throughput throttles into capacity."""
    override = {k: params[k] for k in _PARAM_KEYS_FOR_BASELINE if k in params}
    df = solve_network({}, "baseline", params_override=override, save_output=False)
    return {str(k): float(v) for k, v in zip(df["edge_id"], df["tanker_transits"])}


def solve_network(
    throttles: dict[str, float] | None = None,
    scenario_id: str = "baseline",
    params_override: dict | None = None,
    save_output: bool = True,
    return_od: bool = False,
    baseline_transits: dict[str, float] | None = None,
):
    """Route weekly OD crude oil demand over the network with capacities & BPR delays.

    throttles maps edge_id, chokepoint_portid or searoute_passage -> fraction of pre-event
    crude throughput retained (1.0 = open, 0.0 = closed).

    Returns the edge table, or (edge table, per-OD table) when return_od=True.
    """
    nodes, edges, od = load_network()
    params = load_params()
    if params_override:
        params.update(params_override)
    throttles = throttles or {}

    speed_knots = float(params["speed_knots"])
    alpha = float(params["alpha"])
    beta = float(params["beta"])
    daily_rate = float(params["daily_tanker_rate"])
    default_bbl_per_ship = float(params["avg_barrels_per_transit"])
    # Dollars per barrel are converted on the crude carrier this model prices, not on the all-tanker
    # average above. The tariffs and the war-risk quote are written per ship, so charging a Suezmax
    # tariff divided by a 347k bbl all-tanker average cargo put a 2.9x-larger ship's toll on a small
    # one -- at Suez, a $0.45/bbl toll against $0.15/bbl of sailing. Transits keep the observed cargo
    # per transit, so counting ships stays an observation and only the dollar conversion is priced.
    _cost_cargo = float(params.get("cost_barrels_per_cargo", default_bbl_per_ship) or 0.0)
    cost_cargo_bbl = _cost_cargo if _cost_cargo > 0 else None
    # Parcel size must come from an observed quantity. est_oil_volume is crude-only while
    # weekly_tanker_transits counts every tanker, so their ratio is not a parcel size: once the
    # numerator became crude-only that ratio fell below the clip floor at 8 of 10 chokepoints,
    # flattening every corridor onto the same 250k bbl vessel and leaving routing to pick on
    # distance alone. PortWatch's observed average vessel size per chokepoint, at crude density,
    # keeps the real dispersion between corridors.
    bbl_per_tonne_crude = 7.33
    n_iters = int(params.get("n_congestion_iters", 5))
    max_util = float(params.get("max_util_for_delay", 5.0))
    awrp = float(params.get("awrp_usd_per_bbl", 0.0))
    risk_passages = {str(p).strip().lower() for p in params.get("risk_passages", [])}
    # Tariff in force in the scenario window, per transit, keyed by searoute_passage. A canal toll is
    # not one historical fact: Suez raised crude tariffs 15 % in Jan 2023 and again in Jan 2024, and
    # Panama's per-ton rate went $1.30 -> $2.25 -> $3.25 between 2023 and 2025. The window tariff
    # overrides whatever the edge table was built with.
    toll_overrides = {
        str(k).strip().lower(): float(v)
        for k, v in (params.get("toll_usd_per_transit") or {}).items()
    }

    # Build lookup from portwatch_portid -> graph node_id (e.g. 'port1091' -> 'port_port1091')
    node_lookup = {}
    if not nodes.empty and {"portwatch_portid", "node_id"}.issubset(nodes.columns):
        for _, r in nodes.dropna(subset=["portwatch_portid", "node_id"]).iterrows():
            node_lookup[str(r["portwatch_portid"]).strip()] = str(r["node_id"]).strip()

    od = od.copy()
    od["orig_node"] = od["origin_portid"].map(lambda x: map_portid_to_node_id(x, node_lookup))
    od["dest_node"] = od["destination_portid"].map(lambda x: map_portid_to_node_id(x, node_lookup))
    od["barrels_per_week"] = pd.to_numeric(od["barrels_per_year"], errors="coerce").fillna(0.0) / WEEKS_PER_YEAR
    od = od[od["barrels_per_week"] > 0].reset_index(drop=True)

    # Identify all port nodes so we can enforce the Port Transit Rule:
    # ports are strictly origins or destinations, never intermediate pass-through nodes.
    port_nodes = set()
    if not nodes.empty and "node_type" in nodes.columns:
        port_nodes = set(nodes.loc[nodes["node_type"] == "port", "node_id"].astype(str))
    for _, e in edges[edges["edge_type"] == "connector"].iterrows():
        for endpoint in (str(e["from_node"]), str(e["to_node"])):
            if endpoint.startswith("port_"):
                port_nodes.add(endpoint)

    # Prepare edge metadata
    edge_meta = {}
    for _, e in edges.iterrows():
        e_id = str(e["edge_id"])
        u, v = str(e["from_node"]), str(e["to_node"])
        e_type = str(e["edge_type"])
        passage = str(e["searoute_passage"]).strip().lower() if pd.notna(e.get("searoute_passage")) else ""
        cp_id = str(e["chokepoint_portid"]).strip() if pd.notna(e.get("chokepoint_portid")) else None
        length_nm = float(e["length_nm"])
        free_flow_days = length_nm / (speed_knots * 24.0)

        # Barrels per tanker transit on this edge. Observed vessel size wins where PortWatch
        # reports one; the volume/transit ratio stays as a fallback for rows without it.
        if pd.notna(e.get("avg_vessel_size_tonnes")) and float(e.get("avg_vessel_size_tonnes", 0)) > 0:
            bbl_per_ship = float(e["avg_vessel_size_tonnes"]) * bbl_per_tonne_crude
        elif (
            pd.notna(e.get("est_oil_volume"))
            and pd.notna(e.get("weekly_tanker_transits"))
            and float(e.get("weekly_tanker_transits", 0)) > 0
        ):
            bbl_per_ship = float(e["est_oil_volume"]) / float(e["weekly_tanker_transits"])
            bbl_per_ship = float(np.clip(bbl_per_ship, 250_000.0, 2_200_000.0))
        else:
            bbl_per_ship = default_bbl_per_ship

        # Weekly capacity on the Deliverable 3 statistical basis (unconstrained for lanes/connectors):
        # PortWatch's capacity_proxy, the 95th percentile of that corridor's weekly tanker transits,
        # used as observed and never rescaled to the model's own output.
        if pd.notna(e.get("capacity_proxy")) and float(e.get("capacity_proxy", 0)) > 0:
            base_cap_weekly = float(e["capacity_proxy"])
        else:
            base_cap_weekly = 5000.0

        # Throttle = throughput fraction retained, keyed by edge_id, chokepoint_portid or passage
        through_frac = 1.0
        for key in (e_id, cp_id, passage):
            if key and key in throttles:
                through_frac = float(throttles[key])
                break
        through_frac = float(np.clip(through_frac, 0.0, 1.0))

        # The per-barrel toll is derived from the per-transit tariff on the same cost cargo, so the
        # two stored columns cannot disagree (Panama held $350,000/transit against $0.60/bbl, which
        # implies 583k bbl).
        edge_cost_cargo = cost_cargo_bbl or bbl_per_ship
        toll_transit = pd.to_numeric(e.get("toll_usd_per_transit"), errors="coerce")
        if passage in toll_overrides:
            toll_transit = toll_overrides[passage]
        if pd.notna(toll_transit):
            toll_bbl = float(toll_transit) / edge_cost_cargo
        elif pd.notna(e.get("toll_usd_per_bbl")):
            toll_bbl = float(e["toll_usd_per_bbl"])
        else:
            toll_bbl = 0.0

        edge_meta[e_id] = {
            "edge_id": e_id,
            "from_node": u,
            "to_node": v,
            "edge_type": e_type,
            "chokepoint_portid": cp_id,
            "searoute_passage": passage if passage else None,
            "length_nm": length_nm,
            "free_flow_days": free_flow_days,
            "base_capacity_weekly": base_cap_weekly,
            "throughput_factor": through_frac,
            "closed": through_frac <= 0.0,
            "toll_per_bbl": toll_bbl,
            "bbl_per_ship": bbl_per_ship,
            "bbl_per_ship_cost": cost_cargo_bbl or bbl_per_ship,
            "risk_per_bbl": awrp if (awrp > 0.0 and passage in risk_passages) else 0.0,
            "delay_days": 0.0,
            "removed": False,
        }

    needs_baseline = any(0.0 < m["throughput_factor"] < 1.0 for m in edge_meta.values())
    if needs_baseline and baseline_transits is None:
        baseline_transits = _baseline_transit_map(params)

    for e_id, m in edge_meta.items():
        tf = m["throughput_factor"]
        if tf >= 1.0 or baseline_transits is None:
            cap = m["base_capacity_weekly"]
        else:
            base_tr = float(baseline_transits.get(e_id, 0.0))
            # Edges that carried no crude at baseline would otherwise get zero capacity and show
            # throttle_factor 0 ("closed") while still being routable. Fall back to the statistical
            # proxy scaled by the throttle so the flag and the flow stay consistent.
            cap = tf * base_tr if base_tr > 0 else tf * m["base_capacity_weekly"]
        m["capacity_weekly"] = cap
        m["throttle_factor"] = (
            float(np.clip(cap / m["base_capacity_weekly"], 0.0, 1.0)) if m["base_capacity_weekly"] > 0 else tf
        )

    # Build directed routing graph:
    # - Port nodes are split into `<port>::orig` (out-edges only) and `<port>::dest` (in-edges only)
    #   so Dijkstra can NEVER route through an intermediate port!
    # - Lanes and chokepoints have directed arcs in both directions (u -> v and v -> u)
    #   sharing the same underlying edge_id.
    G = nx.DiGraph()
    for e_id, m in edge_meta.items():
        if m["closed"]:
            continue
        u, v = m["from_node"], m["to_node"]
        if m["edge_type"] == "connector":
            port_node = u if u in port_nodes else (v if v in port_nodes else u)
            junc_node = v if port_node == u else u
            G.add_edge(f"{port_node}::orig", junc_node, edge_id=e_id, weight=1.0)
            G.add_edge(junc_node, f"{port_node}::dest", edge_id=e_id, weight=1.0)
        else:
            G.add_edge(u, v, edge_id=e_id, weight=1.0)
            G.add_edge(v, u, edge_id=e_id, weight=1.0)

    def _drop_arcs(m: dict) -> None:
        """Remove both directed arcs of an edge from the routing graph."""
        u, v = m["from_node"], m["to_node"]
        if m["edge_type"] == "connector":
            port_node = u if u in port_nodes else (v if v in port_nodes else u)
            junc_node = v if port_node == u else u
            for a, b in ((f"{port_node}::orig", junc_node), (junc_node, f"{port_node}::dest")):
                if G.has_edge(a, b):
                    G.remove_edge(a, b)
        else:
            for a, b in ((u, v), (v, u)):
                if G.has_edge(a, b):
                    G.remove_edge(a, b)

    # Incremental capacity-constrained assignment with BPR congestion updates
    edge_flows_bbl = {e_id: 0.0 for e_id in edge_meta}
    step_fraction = 1.0 / n_iters

    # Group OD demand by origin port for fast single-source Dijkstra
    od_by_orig = {}
    for row in od.itertuples():
        od_by_orig.setdefault(row.orig_node, []).append((row.dest_node, float(row.barrels_per_week)))

    # Per-OD accounting so ton-miles can be compared on the same OD set in every scenario
    od_acc = {}

    for _ in range(n_iters):
        # Update generalized cost ($/bbl) on every active edge using current flow
        for e_id, m in edge_meta.items():
            if m["closed"]:
                continue
            flow_so_far = edge_flows_bbl[e_id]
            transits_so_far = flow_so_far / m["bbl_per_ship"]
            cap = m["capacity_weekly"]
            util = transits_so_far / cap if cap > 0 else 0.0

            # DELIVERABLE4's congestion curve, on the edge's own free-flow time:
            # delay_days = t0 * alpha * util^beta. Nothing else is added to it.
            delay = m["free_flow_days"] * alpha * (min(util, max_util) ** beta)
            m["delay_days"] = delay
            voyage_cost_bbl = ((m["free_flow_days"] + delay) * daily_rate) / m["bbl_per_ship_cost"]

            # A listed war-risk area adds a flat insurance premium per transit. It is not scaled by the
            # lost throughput share: insurers quote a percentage of hull value per transit whether or
            # not the passage is also slowed, and charging it per chokepoint would bill one voyage
            # twice (see risk_passages: one gate per listed area).
            risk_penalty = m["risk_per_bbl"]

            cost_bbl = voyage_cost_bbl + m["toll_per_bbl"] + risk_penalty

            u, v = m["from_node"], m["to_node"]
            if m["edge_type"] == "connector":
                port_node = u if u in port_nodes else (v if v in port_nodes else u)
                junc_node = v if port_node == u else u
                if G.has_edge(f"{port_node}::orig", junc_node):
                    G[f"{port_node}::orig"][junc_node]["weight"] = cost_bbl
                if G.has_edge(junc_node, f"{port_node}::dest"):
                    G[junc_node][f"{port_node}::dest"]["weight"] = cost_bbl
            else:
                if G.has_edge(u, v):
                    G[u][v]["weight"] = cost_bbl
                if G.has_edge(v, u):
                    G[v][u]["weight"] = cost_bbl

        # A throttled edge that reaches its throughput ceiling is taken out of the graph for the
        # remaining increments, so the surplus reroutes or strands instead of queueing for free.
        for e_id, m in edge_meta.items():
            if m["closed"] or m["removed"] or m["throughput_factor"] >= 1.0:
                continue
            cap = m["capacity_weekly"]
            transits_so_far = edge_flows_bbl[e_id] / m["bbl_per_ship"]
            if cap <= 0 or transits_so_far >= 0.98 * cap:
                _drop_arcs(m)
                m["removed"] = True

        # Route 1/n_iters of each OD pair's weekly demand along current shortest paths
        for orig_port, dest_list in od_by_orig.items():
            src = f"{orig_port}::orig"
            if src not in G:
                for dest_port, d_bbl in dest_list:
                    acc = od_acc.setdefault(
                        (orig_port, dest_port), {"barrels": 0.0, "barrel_nm": 0.0, "barrel_days": 0.0, "stranded": 0.0}
                    )
                    acc["stranded"] += d_bbl * step_fraction
                continue
            try:
                _, paths = nx.single_source_dijkstra(G, src, weight="weight")
            except Exception:
                for dest_port, d_bbl in dest_list:
                    acc = od_acc.setdefault(
                        (orig_port, dest_port), {"barrels": 0.0, "barrel_nm": 0.0, "barrel_days": 0.0, "stranded": 0.0}
                    )
                    acc["stranded"] += d_bbl * step_fraction
                continue

            for dest_port, d_bbl in dest_list:
                inc_bbl = d_bbl * step_fraction
                dst = f"{dest_port}::dest"
                acc = od_acc.setdefault(
                    (orig_port, dest_port), {"barrels": 0.0, "barrel_nm": 0.0, "barrel_days": 0.0, "stranded": 0.0}
                )
                if dst not in paths:
                    acc["stranded"] += inc_bbl
                    continue
                path = paths[dst]
                path_nm = 0.0
                path_days = 0.0
                for i in range(len(path) - 1):
                    e_id = G[path[i]][path[i + 1]]["edge_id"]
                    edge_flows_bbl[e_id] += inc_bbl
                    m = edge_meta[e_id]
                    path_nm += m["length_nm"]
                    path_days += m["free_flow_days"] + m["delay_days"]
                acc["barrels"] += inc_bbl
                acc["barrel_nm"] += inc_bbl * path_nm
                acc["barrel_days"] += inc_bbl * path_days

    stranded_bbl = sum(a["stranded"] for a in od_acc.values())

    # Build final raw flow table
    rows = []
    for e_id, m in edge_meta.items():
        vol_bbl = edge_flows_bbl[e_id]
        transits = vol_bbl / m["bbl_per_ship"]
        cap = m["capacity_weekly"]
        if m["closed"]:
            util = 0.0
            delay_days = 0.0
        else:
            util = transits / cap if cap > 0 else 0.0
            delay_days = m["delay_days"]

        voyage_cost_bbl = ((m["free_flow_days"] + delay_days) * daily_rate) / m["bbl_per_ship_cost"]
        risk_penalty = m["risk_per_bbl"]
        cost_per_bbl = voyage_cost_bbl + m["toll_per_bbl"] + risk_penalty

        rows.append(
            {
                "scenario_id": scenario_id,
                "edge_id": e_id,
                "from_node": m["from_node"],
                "to_node": m["to_node"],
                "edge_type": m["edge_type"],
                "chokepoint_portid": m["chokepoint_portid"],
                "searoute_passage": m["searoute_passage"],
                "length_nm": round(m["length_nm"], 2),
                "throttle_factor": round(m["throttle_factor"], 6),
                "scenario_throughput_factor": round(m["throughput_factor"], 4),
                "oil_volume_bbl": round(vol_bbl, 2),
                "tanker_transits": round(transits, 3),
                "capacity_weekly": round(cap, 2),
                "base_capacity_weekly": round(m["base_capacity_weekly"], 2),
                "utilization": round(util, 4),
                "free_flow_days": round(m["free_flow_days"], 3),
                "delay_days": round(delay_days, 4),
                "cost_per_barrel": round(cost_per_bbl, 4),
                "stranded_barrels": round(stranded_bbl, 2),
                "source": "modeled",
            }
        )

    out_df = pd.DataFrame(rows)
    if save_output:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        out_df.to_parquet(PROCESSED_DIR / f"{scenario_id}_raw_flows.parquet", index=False)
        if scenario_id == "baseline":
            out_df.to_parquet(PROCESSED_DIR / "baseline_raw_flows.parquet", index=False)

    if not return_od:
        return out_df

    od_rows = [
        {
            "origin_portid": o,
            "destination_portid": d,
            "barrels": round(v["barrels"], 2),
            "barrel_nm": v["barrel_nm"],
            "barrel_days": v["barrel_days"],
            "stranded": round(v["stranded"], 2),
        }
        for (o, d), v in od_acc.items()
    ]
    return out_df, pd.DataFrame(od_rows)


def main():
    df, od = solve_network({}, "baseline", save_output=True, return_od=True)
    cp = df[df["edge_type"] == "chokepoint"][
        ["edge_id", "searoute_passage", "oil_volume_bbl", "tanker_transits", "capacity_weekly", "utilization", "delay_days"]
    ]
    total = float(od["barrels"].sum() + od["stranded"].sum())
    routed = float(od["barrels"].sum())
    print(f"Core flow solver executed successfully ({len(df)} edges, stranded={df['stranded_barrels'].iloc[0]:,.0f} bbl/wk).")
    print(f"Routed {routed:,.0f} of {total:,.0f} bbl/wk ({routed / total:.4%}); zero negative flows: {(df['oil_volume_bbl'] < 0).sum() == 0}.")
    print("\nBaseline Chokepoint Flows:")
    print(cp.to_string(index=False))


if __name__ == "__main__":
    main()
