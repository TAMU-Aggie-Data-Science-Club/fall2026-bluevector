"""
Run:  python config/validate_graph.py
Needs: pip install searoute pandas networkx global-land-mask

Inputs, looked up next to this script first, then in the current folder:
  junctions.csv        node_id, node_type, name, lat, lon, country_iso3, portwatch_portid
  edges.csv            edge_id, from_node, from_lat, from_lon, to_node, to_lat, to_lon, edge_type,
                       chokepoint_portid, alternative_group, searoute_passage, length_nm,
                       traversed_passages, from_snap_nm, to_snap_nm

Output (written next to the script):
  validation_results.csv   one row per check: check, status (PASS / WARN / FAIL), detail

Checks:
  1- unique IDs, edge endpoints exist, no self-loops or duplicate pairs, length_nm > 0,
                      edge coordinates match the node table, snap distances (WARN above SNAP_WARN_NM)
  2- the junction network is a single connected component
  3- chokepoint edges: searoute_passage == traversed_passages; non-chokepoint edges
                      cross no passage, so nothing bypasses a chokepoint edge
  4- remove each passage's edges and test whether its two ends are still connected;
                      Hormuz (ormuz) and Bosporus must have none; alternative_group labels must agree;
                      each non-"none" group needs at least 2 passages
  5a - length_nm is within EDGE_TOL (5%) of a fresh searoute between the edge's ends
  5b - shortest graph path between random junction pairs is within PATH_MAX_RATIO (1.15)
                      of direct searoute
  6 - every junction is in water (global-land-mask)

Notes: this version validates the core junction/chokepoint graph only; port connectors and
ports_selected.csv are not required. The land mask is coarse (about 1 km), so junctions in narrow
straits can be flagged falsely; check those on a map. Check 5a calls searoute once per edge, so it
is slow on a large edge file. searoute snaps each end to its own sea network, so small length
differences are expected; snaps above SNAP_WARN_NM are WARNs, not FAILs. "Practical" alternatives
are not scored: a path that exists but is far longer than the direct route (e.g. around the Cape)
still counts as an alternative.

Findings (core graph, no connectors: 54 edges, 29 checks):
  - PASS  all integrity checks (1): unique IDs, endpoints resolve, no self-loops or duplicate
          pairs, positive lengths, coordinates match junctions.csv.
  - PASS  connectivity (2): the junction graph is a single component.
  - PASS  passage consistency (3): chokepoint edges agree with traversed_passages and no
          non-chokepoint edge crosses a passage, so no edge bypasses a chokepoint.
  - PASS  alternatives (4): Suez, Bab-el-Mandeb, South Africa, Panama, Chile, Malacca, Sunda and
          Gibraltar all have a path around them; Hormuz and Bosporus have none, as expected.
          Detours are large where expected (Suez 12,468 nm vs 319 nm direct; Bab-el-Mandeb 12,517
          vs 270; Panama 10,599 vs 154) and modest in SE Asia (Malacca 3,447 vs 698; Sunda 3,816
          vs 330). All three groups hold >= 2 passages: asia_europe (babalmandab, south_africa,
          suez), atlantic_pacific (chili, panama), indian_pacific (malacca, sunda).
  - PASS  edge lengths (5a): 54 of 54 within 5% of searoute.
  - PASS  all junctions in water (6).
  - WARN  large snaps on some chokepoint edges (E004, E007, E014, E017, E024, E028, E029, E030).
          Accepted: searoute has few network points in open ocean.
  - FAIL  'none' labels (4): Gibraltar is labelled alternative_group "none" but the graph reaches
          the Med via Suez and the Cape (12,500 nm detour vs 286 nm direct), so it is reported as
          having an alternative. Decision needed: add "gibraltar" to EXPECTED_NONE (treating the
          detour as impractical) or relabel the edge into a real group.
  - FAIL  junction-to-junction paths (5b): 4 of 19 sampled pairs exceed 1.15x searoute.
          J_sunda_indian -> J_andaman_sea 1.36 and J_australia_west -> J_cape_indian 1.61 are the
          worst; J_gulf_of_guinea -> J_pacific_east 1.17 and J_magellan_atlantic -> J_east_asia
          1.16 are marginal. Indicates missing direct junction edges across the Indian Ocean
          (Sunda/Andaman and Australia-west/Cape legs) rather than bad lengths, since 5a passes.
"""

import random
import pandas as pd
import networkx as nx
import searoute as sr                 # pip install searoute
from global_land_mask import globe    # pip install global-land-mask

JUNCTIONS_CSV, EDGES_CSV = "config/junctions.csv", "config/edges.csv"

EXPECTED_NONE = {"ormuz", "bosporus"}   # passages with no alternative (searoute names)
CHOKEPOINT = "chokepoint"               # check edges.edge_type.unique()
SNAP_WARN_NM = 100 #checks for large snap
EDGE_TOL = 0.05
PATH_MAX_RATIO = 1.15
N_SAMPLE_PAIRS = 40
COORD_TOL = 1e-3

nodes = pd.read_csv(JUNCTIONS_CSV)
edges = pd.read_csv(EDGES_CSV)
print("edge types:", edges.edge_type.value_counts().to_dict())

junction_ids = set(nodes[nodes.node_type == "junction"].node_id)
coords = {r.node_id: (r.lon, r.lat) for r in nodes.itertuples()}

results = []
def record(check, ok, detail="", warn=False):
    results.append((check, "PASS" if ok else ("WARN" if warn else "FAIL"), detail))

def no_passage(x):
    return pd.isna(x) or str(x).strip() in ("", "[]")

#1
record("junction node_id unique", nodes.node_id.is_unique)
record("edge_id unique", edges.edge_id.is_unique)
unk = edges[~edges.from_node.isin(coords) | ~edges.to_node.isin(coords)]
record("edge endpoints exist in node table", unk.empty, f"{unk.edge_id.tolist()[:5]}")
record("no self-loops", (edges.from_node != edges.to_node).all())
pair = edges.apply(lambda r: tuple(sorted((r.from_node, r.to_node))), axis=1)
dups = edges[pair.duplicated(keep=False)]
record("no duplicate node pairs", dups.empty, f"{dups.edge_id.tolist()[:6]}")
record("length_nm positive and non-null",
       edges.length_nm.notna().all() and (edges.length_nm > 0).all())

def coord_mismatch(r):
    for n, lat, lon in ((r.from_node, r.from_lat, r.from_lon),
                        (r.to_node, r.to_lat, r.to_lon)):
        if n in coords and (abs(coords[n][0] - lon) > COORD_TOL
                            or abs(coords[n][1] - lat) > COORD_TOL):
            return True
    return False
cm = edges[edges.apply(coord_mismatch, axis=1)]
record("edge coordinates match node table", cm.empty, f"{cm.edge_id.tolist()[:5]}")

snap = edges[(edges.from_snap_nm > SNAP_WARN_NM) | (edges.to_snap_nm > SNAP_WARN_NM)]
record(f"snap distances <= {SNAP_WARN_NM} nm", snap.empty,
       f"{snap.edge_id.tolist()[:8]}", warn=True)

def route_nm(lon1, lat1, lon2, lat2):
    route = sr.searoute([lon1, lat1], [lon2, lat2], units="naut")
    return route["properties"]["length"]

# graph from junctions only

G = nx.Graph()
for e in edges.to_dict("records"):
    G.add_edge(e["from_node"], e["to_node"], length_nm=e["length_nm"])
G.add_nodes_from(junction_ids)

#2
J = G.subgraph(junction_ids)
comps = list(nx.connected_components(J))
record("junction graph is connected", len(comps) == 1,
       "" if len(comps) == 1 else
       f"{len(comps)} components; smallest: {sorted(min(comps, key=len))[:10]}")

#3
choke = edges[edges.edge_type == CHOKEPOINT]
mism = choke[choke.searoute_passage != choke.traversed_passages]
record("chokepoint edges: searoute_passage == traversed_passages", mism.empty,
       f"{mism.edge_id.tolist()[:5]}")
# any non-chokepoint edge crossing a passage would bypass its chokepoint edge
leak = edges[(edges.edge_type != CHOKEPOINT) & ~edges.traversed_passages.apply(no_passage)]
record("non-chokepoint edges cross no passage", leak.empty,
       f"{leak[['edge_id','traversed_passages']].values.tolist()[:8]}")

#4
labelled_none = set(choke[choke.alternative_group == "none"].searoute_passage)
record("'none' labels match expected (Hormuz, Bosporus)",
       labelled_none == EXPECTED_NONE,
       f"labelled none: {sorted(labelled_none)}; expected: {sorted(EXPECTED_NONE)}")

for passage, grp in choke.groupby("searoute_passage"):
    H = G.copy()
    H.remove_edges_from(zip(grp.from_node, grp.to_node))
    u, v = grp.iloc[0].from_node, grp.iloc[0].to_node
    has_alt = nx.has_path(H, u, v)
    expect_alt = passage not in EXPECTED_NONE
    detail = ""
    if has_alt:
        detour = nx.shortest_path_length(H, u, v, weight="length_nm")
        detail = f"detour {detour:.0f} nm vs {grp.length_nm.min():.0f} nm direct"
    record(f"{passage}: alternative {'exists' if expect_alt else 'absent (expected)'}",
           has_alt == expect_alt, detail)

# each alternative_group (except none) should hold >= 2 passages
for grp_name, g in choke[choke.alternative_group != "none"].groupby("alternative_group"):
    passages = sorted(g.searoute_passage.unique())
    record(f"group {grp_name}: >= 2 passages", len(passages) >= 2,
           f"passages: {passages}", warn=True)

#5a
bad = []
for e in edges.to_dict("records"):
    d = route_nm(e["from_lon"], e["from_lat"], e["to_lon"], e["to_lat"])
    if d > 0 and abs(e["length_nm"] - d) / d > EDGE_TOL:
        bad.append((e["edge_id"], round(e["length_nm"]), round(d)))
record(f"edge length within {EDGE_TOL:.0%} of searoute ({len(edges)} edges)",
       not bad, f"(edge, ours, searoute): {bad[:8]}")

#5b
random.seed(1)
jl = [j for j in junction_ids if j in G]
bad_r, n_ok = [], 0
for _ in range(min(N_SAMPLE_PAIRS, len(jl) // 2)):
    a, b = random.sample(jl, 2)
    try:
        dg = nx.shortest_path_length(G, a, b, weight="length_nm")
    except nx.NetworkXNoPath:
        bad_r.append((a, b, "no path")); continue
    dd = route_nm(*coords[a], *coords[b])
    n_ok += 1
    if not (0.95 <= dg / dd <= PATH_MAX_RATIO):
        bad_r.append((a, b, round(dg / dd, 2)))
record(f"junction-to-junction paths within {PATH_MAX_RATIO}x of searoute ({n_ok} pairs)",
       not bad_r, f"outliers: {bad_r[:8]}")

#6
j = nodes[nodes.node_type == "junction"]
land = j[[globe.is_land(r.lat, r.lon) for r in j.itertuples()]]
record("all junctions are in water", land.empty, f"{land.node_id.tolist()[:10]}")

report = pd.DataFrame(results, columns=["check", "status", "detail"])
print(report.to_string(index=False, max_colwidth=90))
report.to_csv("config/validation_results.csv", index=False)