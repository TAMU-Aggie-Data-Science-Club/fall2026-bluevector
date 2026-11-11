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
    "alpha": 0.12,
    "beta": 3.0,
    "daily_tanker_rate": 50000.0,
    "avg_barrels_per_transit": 1000000.0,
    # Fraction of pre-crisis crude throughput retained at Suez / Bab-el-Mandeb (fitted, see calibrate_flow.py)
    "red_sea_throttle": 0.65,
    # War-risk insurance surcharge at full disruption, $/bbl (linear in the lost throughput share)
    "red_sea_war_risk_per_bbl": 0.60,
    "risk_passages": ["suez", "babalmandab"],
    # Nominal queue / convoy service time used as the BPR base for chokepoints (days)
    "chokepoint_queue_days": 1.0,
    "max_util_for_delay": 5.0,
    "n_congestion_iters": 10,
}

_PARAM_KEYS_FOR_BASELINE = (
    "speed_knots",
    "alpha",
    "beta",
    "daily_tanker_rate",
    "avg_barrels_per_transit",
    "chokepoint_queue_days",
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
    n_iters = int(params.get("n_congestion_iters", 5))
    queue_days = float(params.get("chokepoint_queue_days", 1.0))
    max_util = float(params.get("max_util_for_delay", 5.0))
    war_risk = float(params.get("red_sea_war_risk_per_bbl", 0.0))
    risk_passages = {str(p).strip().lower() for p in params.get("risk_passages", [])}

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

        # Calibrated barrels per tanker transit on this edge
        if (
            pd.notna(e.get("est_oil_volume"))
            and pd.notna(e.get("weekly_tanker_transits"))
            and float(e.get("weekly_tanker_transits", 0)) > 0
        ):
            bbl_per_ship = float(e["est_oil_volume"]) / float(e["weekly_tanker_transits"])
            bbl_per_ship = float(np.clip(bbl_per_ship, 250_000.0, 2_200_000.0))
        else:
            bbl_per_ship = default_bbl_per_ship

        # Weekly capacity on the Deliverable 3 statistical basis (unconstrained for lanes/connectors)
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

        toll_bbl = float(e["toll_usd_per_bbl"]) if pd.notna(e.get("toll_usd_per_bbl")) else 0.0

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
            # BPR congestion acts on the nominal queue/convoy time at a chokepoint, not on the
            # length of a 300-500 nm edge segment (which would make waiting almost free).
            "service_days": queue_days if e_type == "chokepoint" else free_flow_days,
            "risk_per_bbl": war_risk if (through_frac < 1.0 and passage in risk_passages) else 0.0,
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

            delay = m["service_days"] * alpha * (min(util, max_util) ** beta)
            m["delay_days"] = delay
            voyage_cost_bbl = ((m["free_flow_days"] + delay) * daily_rate) / m["bbl_per_ship"]

            # Partial closure of a conflict-zone chokepoint adds a war-risk insurance surcharge
            risk_penalty = m["risk_per_bbl"] * (1.0 - m["throughput_factor"])

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

        # A throttled edge that has reached its throughput ceiling is taken out of the graph for the
        # remaining increments, so the surplus reroutes (or strands) instead of queueing for free.
        # Unthrottled lanes and chokepoints keep the soft BPR curve only, because their capacity
        # proxy is statistical rather than a physical wall (see DELIVERABLE4 'Known risks').
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

        voyage_cost_bbl = ((m["free_flow_days"] + delay_days) * daily_rate) / m["bbl_per_ship"]
        risk_penalty = m["risk_per_bbl"] * (1.0 - m["throughput_factor"])
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
