#!/usr/bin/env python3
"""
Attach ports to junctions with connector edges, then fill lengths.

Inputs  (same folder as this script, or pass --dir):
    junctions.csv, ports_selected.csv, edges_core.csv   (edges_core.csv = junction-to-junction edges)
Outputs:
    edges.csv              edges_core.csv + connector edges, lengths and transit days filled
                           (this is the Deliverable 3 edge hand-in: the whole graph)

Rules (from the spec):
  1. Each port gets up to MAX_CONNECTORS connectors to its nearest junctions.
       * A connector must not cross a chokepoint (it would bypass the chokepoint edge).
         Ports that sit on a chokepoint (e.g. Algeciras) are attached by hand: MANUAL_ATTACH.
       * Skip a connector that is not at least MIN_SAVING (~5%) shorter than reaching that
         junction through a connector already kept (port -> kept junction -> junction network).
       * Ports are start/end points only, never stop-overs. A port node is
         node_type='port' in nodes.csv, so the routing step can refuse to route
         *through* it; connectors themselves are tagged edge_type='connector'.
  2. length_nm comes from searoute for every edge, with speed_knot passed explicitly,
     and min_transit_days is added.
"""
import argparse
import math
from pathlib import Path

import networkx as nx
import pandas as pd
import searoute as sr

# ----------------------------------------------------------------------------- settings
SPEED_KNOT = 13.0          # passed explicitly to searoute (assumption: 12-14 kn; default is 24)
MAX_SPEED_KNOT = 14.0      # top of the range -> used for *minimum* transit days
MAX_CONNECTORS = 3         # per port
N_CANDIDATES = 8           # nearest junctions (great-circle) to evaluate per port
MIN_SAVING = 0.05          # a new connector must be >=5% shorter than the via-existing-connector path
FALLBACK_WINDOW = 0.15    # If no edges have been found for a port force nearest junction to be edge and 2nd closest if within 15% of closest edge
MAX_CONNECTOR_NM = 1500    # sanity cap; anything longer is almost certainly a bad snap/route
RESTRICTIONS = ["northwest"]   # searoute's default; keeps Arctic route out, all else open
LENGTH_TOL = 0.005         # warn if a recomputed existing edge differs >0.5% from the file

# Ports sitting on a chokepoint: every connector would "cross" it, so attach by hand to ONE side.
# Keys are port node_ids; values are the junction(s) to attach to (no passage check applied).
MANUAL_ATTACH = {
    "port_port31": ["J_med_west"],          # Algeciras: Bay of Gibraltar, attach to the Mediterranean side
    "port_port2200": ["J_panama_pacific"],  # Taboguilla: Pacific end of the Panama Canal
}


# ----------------------------------------------------------------------------- helpers
def haversine_nm(lat1, lon1, lat2, lon2):
    """
    Gets Great-circle distance between two lat/lon points, in nautical miles.
    """
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


def route(lat1, lon1, lat2, lon2):
    """
    Ask searoute for the shortest sea route between two points.

    Returns a tuple:
        length_nm            - sea distance in nautical miles
        traversed_passages   - chokepoints crossed, e.g. ['suez']; empty if none
        from_snap_nm         - gap between the start point and where the route begins
        to_snap_nm           - gap between the end point and where the route ends
    """
    r = sr.searoute(
        [lon1, lat1], [lon2, lat2],
        units="naut",
        speed_knot=SPEED_KNOT,          # explicit, never the default 24
        restrictions=RESTRICTIONS,
        return_passages=True,
    )
    coords = r.geometry["coordinates"]
    start, end = coords[0], coords[-1]   # first/last point of the searoute network used
    return (
        float(r.properties["length"]),
        list(r.properties.get("traversed_passages") or []),
        haversine_nm(lat1, lon1, start[1], start[0]),
        haversine_nm(lat2, lon2, end[1], end[0]),
    )


def junction_graph(edges):
    """
    Build an undirected NetworkX graph of the junction network, weighted by length_nm.

    Includes lane and chokepoint edges only (connectors are skipped). Its shortest-path
    distances answer "how far is junction B if I go via junction A?", which the 5%
    redundancy rule needs when deciding whether a new connector is worth adding.
    """
    g = nx.Graph()
    for e in edges.itertuples():
        if e.edge_type == "connector":
            continue
        g.add_edge(e.from_node, e.to_node, w=e.length_nm)
    return g


# main
def main():
    """

    1. Load junctions, ports and the existing junction-to-junction edges.
    2. Recompute every existing edge's length with searoute (notes any that changed >0.5%).
    3. For each port, pick up to MAX_CONNECTORS junctions: hand-attached ports come from
       MANUAL_ATTACH
    4. Add speed_knot, min_transit_days and typical_transit_days to every edge.
    5. Write edges.csv .
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path(__file__).parent), help="folder with the input CSVs")
    ap.add_argument("--out", default=None, help="output folder (default: same as --dir)")
    args = ap.parse_args()
    d, out = Path(args.dir), Path(args.out or args.dir)

    junctions = pd.read_csv(d / "junctions.csv")
    ports = pd.read_csv(d / "ports_selected.csv")
    edges = pd.read_csv(d / "edges_core.csv")
    jpos = junctions.set_index("node_id")[["lat", "lon"]].to_dict("index")

    #  recompute lengths with searoute
    for i, e in edges.iterrows():
        length, passages, f_snap, t_snap = route(e.from_lat, e.from_lon, e.to_lat, e.to_lon)
        if pd.notna(e.length_nm) and abs(length - e.length_nm) > LENGTH_TOL * e.length_nm:
            print(f"  note: {e.edge_id} length changed {e.length_nm:.1f} -> {length:.1f} nm")
        edges.loc[i, ["length_nm", "from_snap_nm", "to_snap_nm"]] = [round(length, 2), round(f_snap, 1), round(t_snap, 1)]
        edges.loc[i, "traversed_passages"] = ";".join(passages) if passages else None

    g = junction_graph(edges)
    # all-pairs junction distances for the "is this connector redundant?" test
    jdist = dict(nx.all_pairs_dijkstra_path_length(g, weight="w"))

    # Build Connectors
    new_rows = []
    next_id = int(edges["edge_id"].str.extract(r"(\d+)")[0].astype(int).max()) + 1

    def add_edge(port, jn, length, passages, p_snap, j_snap, kind):
        """Append one connector edge (port -> junction) to new_rows with the next free edge_id.

        `kind` is recorded in connector_kind: 'auto', 'manual' or 'fallback'.
        """
        nonlocal next_id
        new_rows.append(dict(
            edge_id=f"E{next_id:03d}", from_node=port.node_id, from_lat=port.lat, from_lon=port.lon,
            to_node=jn, to_lat=jpos[jn]["lat"], to_lon=jpos[jn]["lon"],
            edge_type="connector", chokepoint_portid=None, alternative_group=None,
            searoute_passage=None, length_nm=round(length, 2),
            traversed_passages=";".join(passages) if passages else None,
            from_snap_nm=round(p_snap, 1), to_snap_nm=round(j_snap, 1),
            connector_kind=kind,
            # connectors carry no dynamic attributes until the flow model routes over
            # them; tag them 'modeled' so every row has an observed/modeled label
            source="modeled",
        ))
        next_id += 1

    for port in ports.itertuples():
        kept = []   # (junction, length_nm)

        if port.node_id in MANUAL_ATTACH:
            for jn in MANUAL_ATTACH[port.node_id]:
                length, passages, p_snap, j_snap = route(port.lat, port.lon, jpos[jn]["lat"], jpos[jn]["lon"])
                add_edge(port, jn, length, passages, p_snap, j_snap, "manual")
            continue

        # nearest junctions by great-circle -> evaluate each with searoute
        cands = sorted(jpos, key=lambda j: haversine_nm(port.lat, port.lon, jpos[j]["lat"], jpos[j]["lon"]))[:N_CANDIDATES]
        evaluated = []
        for jn in cands:
            length, passages, p_snap, j_snap = route(port.lat, port.lon, jpos[jn]["lat"], jpos[jn]["lon"])
            evaluated.append((length, jn, passages, p_snap, j_snap))
        evaluated.sort()   # shortest sea distance first

        for length, jn, passages, p_snap, j_snap in evaluated:
            if len(kept) >= MAX_CONNECTORS:
                break
            rec = dict(port=port.name, junction=jn, length_nm=round(length, 1), passages=";".join(passages))
            if passages or length > MAX_CONNECTOR_NM:
                continue
            # redundancy: would going via an already-kept connector be nearly as short?
            via = min((kl + jdist[kj].get(jn, math.inf) for kj, kl in kept), default=math.inf)
            if length > (1 - MIN_SAVING) * via:
                continue
            kept.append((jn, length))
            add_edge(port, jn, length, passages, p_snap, j_snap, "auto")

        if not kept:
            # Fallback: no junction within MAX_CONNECTOR_NM. Use the nearest one that does not cross a
            # chokepoint, plus the next nearest if it is within FALLBACK_WINDOW (15%) of the nearest.
            # Flagged as 'fallback': the real fix is a junction closer to this port.
            ok = [c for c in evaluated if not c[2]]   # already sorted shortest first
            if ok:
                picks = [ok[0]]
                if len(ok) > 1 and ok[1][0] <= ok[0][0] * (1 + FALLBACK_WINDOW):
                    picks.append(ok[1])
                for length, jn, passages, p_snap, j_snap in picks:
                    add_edge(port, jn, length, passages, p_snap, j_snap, "fallback")
                    print(f"  fallback: {port.name} -> {jn} ({length:.0f} nm). Consider adding a junction nearby.")
            else:
                print(f"  WARNING: {port.name} ({port.node_id}) got no connectors; add it to MANUAL_ATTACH")


    full = pd.concat([edges, pd.DataFrame(new_rows)], ignore_index=True)
    if "connector_kind" not in full.columns:
        full["connector_kind"] = None

    # Compute speed and minimum transit days for every edge
    full["speed_knot"] = SPEED_KNOT
    full["min_transit_days"] = (full["length_nm"] / (MAX_SPEED_KNOT * 24)).round(2)   # at top speed
    full["typical_transit_days"] = (full["length_nm"] / (SPEED_KNOT * 24)).round(2)

    full.to_csv(out / "edges.csv", index=False)

    # ---- quick sanity checks
    conn = full[full.edge_type == "connector"]
    per_port = conn.groupby("from_node").size()
    missing = set(ports.node_id) - set(per_port.index)
    print(f"\nconnectors added: {len(conn)} for {per_port.size}/{len(ports)} ports "
          f"(avg {per_port.mean():.1f} per port)")
    if missing:
        print("ports with NO connector:", sorted(missing))
    bad = conn[conn.traversed_passages.notna() & (conn.connector_kind != "manual")]
    print("auto connectors crossing a chokepoint (should be 0):", len(bad))
    # port-side snap only: junctions are open-ocean points, so a large junction snap is expected
    big = conn[conn.from_snap_nm > 50]
    if len(big):
        print("ports snapping > 50 nm from the searoute network (check coordinates):")
        print(big[["edge_id", "from_node", "to_node", "from_snap_nm", "to_snap_nm"]].to_string(index=False))

    # connectivity (ports are terminals; junction network must connect them all)
    G = nx.Graph()
    for e in full.itertuples():
        G.add_edge(e.from_node, e.to_node)
    print("fully connected:", nx.is_connected(G), f"({G.number_of_nodes()} nodes, {G.number_of_edges()} edges)")


if __name__ == "__main__":
    main()