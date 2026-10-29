"""Add endpoint coordinates and sea distance (nautical miles) to the edge list using searoute.

Run:  python config/searoute_script.py
Needs: pip install searoute pandas

Inputs, looked up next to this script first, then in the current folder:
  edges_core.csv (or edges.csv)  one row per edge: edge_id, from_node, to_node, edge_type, ...
  junction.csv                   node_id, lat, lon      (the 15-20 junctions)
  ports_selected.csv             node_id, lat, lon      (from select_ports.py; node_id is port_<portid>)
  nodes.csv                      node_id, lat, lon      (optional: any other nodes)

Output (next to the edges input): edges.csv with these columns added or replaced
  from_lat, from_lon, to_lat, to_lon   coordinates of each edge's two ends
  length_nm                            searoute distance in nautical miles
  traversed_passages                   passages searoute's route goes through (semicolon-separated)
  from_snap_nm, to_snap_nm             how far searoute moved each end onto its sea network (nm)

Chokepoint edges are checked: if the `searoute_passage` column is filled and the route does not
go through that passage, a warning is printed at the end.
Notes: searoute snaps each end to the nearest point on its own sea network (the open ocean has few
network points, so a junction can be moved 100+ nm). `length_nm` is the distance between the snapped
points, so it includes those moves, which matters most for short edges. The snap columns show the size
of the effect; nodes snapped more than SNAP_WARN_NM are listed at the end. `length_nm` does not depend
on speed; SPEED_KNOTS only affects searoute's duration.
"""
from pathlib import Path

import math

import pandas as pd
import searoute as sr

HERE = Path(__file__).resolve().parent
NODE_FILES = ["junction.csv", "ports_selected.csv", "nodes.csv"]
EDGES_INPUTS = ["edges_core.csv", "edges.csv"]
EDGES_OUTPUT_NAME = "edges.csv"
SPEED_KNOTS = 13
SNAP_WARN_NM = 100  # list nodes that searoute moves farther than this
UNITS = "naut"  # searoute's nautical-mile unit ("nmi" is NOT valid and raises KeyError)


def find(name):
    """Look for a file next to this script, then in the current folder."""
    for base in (HERE, Path.cwd()):
        if (base / name).exists():
            return base / name
    return None


def load_nodes():
    """node_id -> (lat, lon), from every node file that exists."""
    coords = {}
    for name in NODE_FILES:
        path = find(name)
        if path is None:
            print(f"(no {name} found; skipping)")
            continue
        df = pd.read_csv(path)
        low = {c.lower(): c for c in df.columns}
        missing = [c for c in ("node_id", "lat", "lon") if c not in low]
        if missing:
            raise SystemExit(f"{path} needs columns node_id, lat, lon; missing {missing}; has {list(df.columns)}")
        n_before = len(coords)
        for _, row in df.iterrows():
            node_id = str(row[low["node_id"]])
            lat, lon = row[low["lat"]], row[low["lon"]]
            if pd.isna(lat) or pd.isna(lon):
                print(f"WARNING: {node_id} in {path.name} has no coordinates; skipped")
                continue
            lat, lon = float(lat), float(lon)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                print(f"WARNING: {node_id} in {path.name} has impossible coordinates ({lat}, {lon}); skipped")
                continue
            if node_id in coords and coords[node_id] != (lat, lon):
                print(f"WARNING: {node_id} appears twice with different coordinates; using the one in {path.name}")
            coords[node_id] = (lat, lon)
        print(f"Loaded {len(coords) - n_before} nodes from {path.name}")
    return coords


def distance_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def route_info(from_ll, to_ll):
    """Return (length_nm, passages, from_snap_nm, to_snap_nm) for a route between two (lat, lon) points."""
    route = sr.searoute([from_ll[1], from_ll[0]], [to_ll[1], to_ll[0]],  # searoute wants [lon, lat]
                        units=UNITS, speed_knot=SPEED_KNOTS, return_passages=True)
    if route is None:
        return None, None, None, None
    props = route.get("properties", {}) if isinstance(route, dict) else getattr(route, "properties", {})
    length = props.get("length")
    passages = props.get("traversed_passages") or []
    geometry = route.get("geometry", {}) if isinstance(route, dict) else getattr(route, "geometry", {})
    line = geometry.get("coordinates") or []
    snap_from = snap_to = None
    if line:  # first and last points of the route are the network points the ends were moved to
        snap_from = round(distance_nm(from_ll[0], from_ll[1], line[0][1], line[0][0]), 1)
        snap_to = round(distance_nm(to_ll[0], to_ll[1], line[-1][1], line[-1][0]), 1)
    return (round(length, 2) if length is not None else None), list(passages), snap_from, snap_to


def main():
    edges_path = next((p for p in (find(n) for n in EDGES_INPUTS) if p), None)
    if edges_path is None:
        raise SystemExit(f"Could not find any of {EDGES_INPUTS}")
    edges = pd.read_csv(edges_path)
    node_coords = load_nodes()
    print(f"Edges: {len(edges)} from {edges_path}")

    cols = {k: [] for k in ("from_lat", "from_lon", "to_lat", "to_lon", "length_nm", "traversed_passages",
                            "from_snap_nm", "to_snap_nm")}
    problems = []
    snaps = {}  # node_id -> farthest snap seen
    for _, row in edges.iterrows():
        from_id, to_id = str(row["from_node"]), str(row["to_node"])
        a, b = node_coords.get(from_id), node_coords.get(to_id)
        cols["from_lat"].append(a[0] if a else None)
        cols["from_lon"].append(a[1] if a else None)
        cols["to_lat"].append(b[0] if b else None)
        cols["to_lon"].append(b[1] if b else None)
        if a is None or b is None:
            missing = [n for n, c in ((from_id, a), (to_id, b)) if c is None]
            problems.append(f"{row['edge_id']}: no coordinates for {', '.join(missing)}")
            for k in ("length_nm", "traversed_passages", "from_snap_nm", "to_snap_nm"):
                cols[k].append(None)
            continue
        try:
            length, passages, snap_from, snap_to = route_info(a, b)
        except Exception as exc:  # searoute raises on points it cannot connect
            problems.append(f"{row['edge_id']} ({from_id} -> {to_id}): searoute error {type(exc).__name__}: {exc}")
            length, passages, snap_from, snap_to = None, None, None, None
        if length is None and passages is None:
            for k in ("length_nm", "traversed_passages", "from_snap_nm", "to_snap_nm"):
                cols[k].append(None)
            continue
        cols["length_nm"].append(length)
        cols["traversed_passages"].append(";".join(passages))
        cols["from_snap_nm"].append(snap_from)
        cols["to_snap_nm"].append(snap_to)
        for node, snap in ((from_id, snap_from), (to_id, snap_to)):
            if snap is not None:
                snaps[node] = max(snaps.get(node, 0), snap)
        expected = row.get("searoute_passage")
        if row.get("edge_type") == "chokepoint" and isinstance(expected, str) and expected and expected not in passages:
            problems.append(f"{row['edge_id']} ({from_id} -> {to_id}): chokepoint edge expects passage "
                            f"'{expected}' but the route goes through {passages or 'no passages'}")

    # replace any old versions of the new columns, then put the coordinates next to their nodes
    edges = edges.drop(columns=[c for c in cols if c in edges.columns])
    edges.insert(edges.columns.get_loc("from_node") + 1, "from_lat", cols["from_lat"])
    edges.insert(edges.columns.get_loc("from_lat") + 1, "from_lon", cols["from_lon"])
    edges.insert(edges.columns.get_loc("to_node") + 1, "to_lat", cols["to_lat"])
    edges.insert(edges.columns.get_loc("to_lat") + 1, "to_lon", cols["to_lon"])
    edges["length_nm"] = cols["length_nm"]
    edges["traversed_passages"] = cols["traversed_passages"]
    edges["from_snap_nm"] = cols["from_snap_nm"]
    edges["to_snap_nm"] = cols["to_snap_nm"]

    out_path = edges_path.parent / EDGES_OUTPUT_NAME
    edges.to_csv(out_path, index=False)
    done = edges["length_nm"].notna().sum()
    print(f"\nSaved {out_path}: {done}/{len(edges)} edges have a length")
    if done:
        print(f"Length range: {edges['length_nm'].min():,.0f} to {edges['length_nm'].max():,.0f} nm")
    far = sorted(((v, k) for k, v in snaps.items() if v > SNAP_WARN_NM), reverse=True)
    if far:
        print(f"\nNodes searoute moved more than {SNAP_WARN_NM} nm onto its sea network "
              "(edge lengths include this offset):")
        for v, k in far:
            print(f"  {k}: {v:.0f} nm")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print("  -", p)


if __name__ == "__main__":
    main()