"""G1 scenario tensor builder: retrospective, event-excluded splits.

For pre-event chronological forecasts, use scripts/gnn/train_gnn.py. Excluding
event dates here does not exclude observations from later years.

Run from the repository root, after Deliverable 4 has written its tables:
    python scripts/gnn/gnn_dataset.py        # builds every dataset from config/ + data/ and prints sizes

Or import it from train_gnn.py / evaluate_gnn.py:
    from gnn_dataset import build_gnn_datasets
    bundle = build_gnn_datasets(config_dir="config", data_dir="data")
    loader = DataLoader(bundle["pretrain_train"], batch_size=32, collate_fn=collate_disjoint)  # G2 takes one graph

Inputs:
    config/edges.csv (or edges_core.csv), nodes.csv, ports_selected.csv, junctions.csv, flow_params.json
    data/processed/scenario_edge_flows.parquet        (must contain the "baseline" scenario)
    data/processed/gnn_simulated_scenarios.parquet    (337 solver runs, used for pretraining)
    data/processed/chokepoint_weekly_attributes.parquet (real weekly PortWatch transits, used for fine-tuning)
    data/interim/od_matrix.csv                        (demand; used for the port export/import features)

Each sample is one scenario (or one observed week) on the same fixed graph:
    x_node [142, 6]   node features        x_edge [279, 11]  edge features (includes throttle_factor)
    y_edge [279, 4]   targets              loss_mask [279, 4] which targets count toward the loss
The graph itself is one shared edge_index [2, 279], built once and reused for every sample.

build_gnn_datasets() returns a dict with:
    pretrain_train / pretrain_val     simulated scenarios, held-out events removed   (G3 stage 1)
    finetune_train / finetune_val     real weeks, chokepoint-only loss mask          (G3 stage 2)
    holdout_scenarios                 ever_given_2021, panama_drought_2023, red_sea_2024 with solver labels (G4)
    holdout_observed                  real weeks inside the held-out windows         (G4)
    target_scaler                     divide-only target scaler object for G3 (transform / inverse)
    metadata, flow_params, scalers    graph ordering, solver settings, fitted scaler values

Each check either passes silently or raises, so the script exits with status 1 on any failure.
FAIL (raises): wrong node/edge counts, scenario missing edges, closed edge carrying flow in the labels,
    NaN/Inf in any tensor, wrong tensor shape, any held-out event or week inside a training split.
WARN: no toll column found, export/import fell back to baseline connectors, node totals differ from the
    solver's baseline by more than 1%, observed chokepoint ids not in the graph.
INFO (logging): annual OD volume converted to weekly, OD ids matched after a prefix fix.
Needs: torch, pandas, numpy.
"""
from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

log = logging.getLogger(__name__)

# =============================================================================
# SECTION 1 - Constants
# These fix the "contract" between G1 and the rest of the GNN group: the order of the feature
# columns here is the order G2's model reads, and the targets here are what G3 trains on and
# G4 scores. Changing any list silently changes every downstream tensor.
# =============================================================================
NODE_FEATURE_NAMES = ["is_port", "is_junction", "lat_norm", "lon_norm", "export_bbl_norm", "import_bbl_norm"]
EDGE_FEATURE_NAMES = [
    "is_lane", "is_chokepoint", "is_connector", "length_nm_norm", "free_flow_days_norm",
    "capacity_weekly_norm", "toll_usd_per_bbl", "throttle_factor", "baseline_volume_norm",
    "baseline_transits_norm", "baseline_utilization",
]
TARGET_NAMES = ["oil_volume_bbl", "tanker_transits", "utilization", "delay_days"]
THR_COL = EDGE_FEATURE_NAMES.index("throttle_factor")  # column the held-out event throttles are written into
NODE_CONT_COLS = [2, 3, 4, 5]       # node columns that get standardised (lat, lon, export, import)
EDGE_CONT_COLS = [3, 4, 5, 8, 9]    # edge columns that get standardised (length, days, capacity, volume, transits)

# Real events kept out of training so the final score is honest (section 5 / G1 splits).
DEFAULT_HOLDOUT_SCENARIO_IDS = {"ever_given_2021", "panama_drought_2023", "red_sea_2024"}
DEFAULT_HOLDOUT_WINDOWS: List[Tuple[str, str, str]] = [   # same events, as date windows for the weekly data
    ("ever_given_2021", "2021-03-19", "2021-04-09"),
    ("panama_drought_2023", "2023-08-01", "2023-12-31"),
    ("red_sea_2024", "2024-03-01", "2024-06-30"),
]
EXPECTED_PORTS, EXPECTED_JUNCTIONS = 100, 42  # section 2.1; a mismatch means the node table is wrong
# Section 2.2 of the deliverable documents the demand total. Our data currently totals about 286M, so this is
# logged next to the solver's own baseline (which the labels come from), not enforced. Raise it with the D4 owners.
DOCUMENTED_WEEKLY_BARRELS = 232_164_410

# Column-name candidates: the upstream tables were written by different people, so each lookup tries
# several spellings and fails loudly (instead of filling zeros) if none match.
TOLL_COLS = ["toll_per_barrel", "toll_usd_per_bbl", "toll_usd_per_barrel", "toll_per_bbl", "toll"]
OD_ORIGIN_COLS = ["origin_node", "origin_port", "origin_portid", "origin", "from_node", "o_node", "o"]
OD_DEST_COLS = ["dest_node", "destination_portid", "destination_node", "dest_port", "dest_portid",
                "destination", "dest", "to_node", "d_node", "d"]
OD_VOLUME_COLS = ["oil_volume_bbl", "volume_bbl", "barrels_per_week", "barrels_per_year", "bbl_per_year",
                  "barrels", "bbl", "volume"]
WEEK_COLS = ["week_end", "week_ending", "date", "week_or_scenario_id", "week"]
CHOKEPOINT_ID_COLS = ["chokepoint_portid", "portid", "chokepoint_id"]


# =============================================================================
# SECTION 2 - Small helpers and containers
# Generic utilities (id cleaning, file reading, safe numeric conversion) plus the scaler,
# graph-metadata and Dataset classes. Nothing here is specific to oil shipping; it just keeps
# the sections below short and makes sure every tensor that leaves this file is clean.
# =============================================================================
def _norm_id(x: object) -> str:
    """Normalise an id to str so 123, 123.0 and '123' all match when joining tables."""
    s = str(x).strip()
    return s[:-2] if s.endswith(".0") else s


def _read(p: Path) -> Optional[pd.DataFrame]:
    """Read a parquet or csv file if it exists, else None (lets callers try several locations)."""
    if p.exists() and p.suffix == ".parquet":
        return pd.read_parquet(p)
    if p.exists() and p.suffix == ".csv":
        return pd.read_csv(p)
    return None


def _first(paths: Sequence[Path]) -> Optional[pd.DataFrame]:
    """Return the first table found in an ordered list of candidate paths (config/ before data/)."""
    return next((d for d in map(_read, paths) if d is not None), None)


def _col(df: pd.DataFrame, cands: Sequence[str], default: object = None) -> pd.Series:
    """First matching column from a list of spellings, or a constant Series if none exist."""
    return next((df[c] for c in cands if c in df.columns), pd.Series(default, index=df.index))


def _num(s: pd.Series, fill: float = 0.0) -> pd.Series:
    """Coerce to numbers; anything unparseable becomes `fill` so no NaN reaches a tensor."""
    return pd.to_numeric(s, errors="coerce").fillna(fill)


def _f32(df: pd.DataFrame, c: str, default: float = 0.0) -> np.ndarray:
    """One column as a clean float32 numpy array (the dtype every tensor here uses)."""
    return _num(_col(df, [c], default)).astype(np.float32).values


@dataclass
class StandardScaler1D:
    """Standardise features to mean 0 / std 1. Fit on TRAINING tensors only, so no test-time
    information leaks into the inputs; the fitted values are exported so G3/G4 can reuse them."""
    mean: Optional[torch.Tensor] = None
    std: Optional[torch.Tensor] = None
    eps: float = 1e-8

    def fit(self, x: torch.Tensor) -> "StandardScaler1D":
        dims = tuple(range(x.ndim - 1))  # reduce over every dim except the feature dim
        self.mean, self.std = x.mean(dim=dims), x.std(dim=dims, unbiased=False).clamp_min(self.eps)
        return self

    def transform(self, x: torch.Tensor) -> torch.Tensor:
        if self.mean is None or self.std is None:
            raise RuntimeError("StandardScaler1D must be fit before transform().")
        return (x - self.mean) / self.std

    def state_dict(self) -> Dict[str, List[float]]:
        return {} if self.mean is None else {"mean": self.mean.cpu().tolist(), "std": self.std.cpu().tolist()}


@dataclass
class TargetScaler:
    """Divide-only scaling of the 4 targets (no mean subtraction), fit on TRAINING labels only.

    Why divide-only: G2's output layer is Softplus (always >= 0) and it forces closed edges to exactly 0.
    Subtracting a mean would create negative scaled targets the model cannot output and would turn a
    closed edge's 0 into a non-zero value. G3 trains on transform(y) and calls inverse() on predictions
    before exporting the 22-column table, so users still see real barrels, transits, ratios and days.
    """
    scale: Optional[torch.Tensor] = None  # [4], one positive constant per target

    def fit(self, y: torch.Tensor, mask: torch.Tensor, q: float = 0.95) -> "TargetScaler":
        scales = []
        for t in range(y.shape[-1]):
            v = y[..., t][mask[..., t]]          # only real labels (loss_mask True)
            v = v[v > 0]                         # ignore zeros so closed edges don't shrink the scale
            scales.append(float(np.quantile(v.numpy(), q)) if v.numel() > 0 else 1.0)
        self.scale = torch.tensor(scales, dtype=torch.float32).clamp_min(1e-8)
        return self

    def transform(self, y: torch.Tensor) -> torch.Tensor:
        if self.scale is None:
            raise RuntimeError("TargetScaler must be fit before transform().")
        return y / self.scale

    def inverse(self, y_scaled: torch.Tensor) -> torch.Tensor:
        if self.scale is None:
            raise RuntimeError("TargetScaler must be fit before inverse().")
        return y_scaled * self.scale

    def state_dict(self) -> Dict[str, List[float]]:
        return {} if self.scale is None else {"scale": self.scale.cpu().tolist()}


@dataclass
class GraphMetadata:
    """The fixed graph: one canonical ordering of nodes and edges shared by every sample.
    G3 uses this to turn tensor rows back into edge_ids when it exports the 22-column table."""
    node_ids: List[str]
    node_to_idx: Dict[str, int]
    edge_ids: List[str]
    edge_to_idx: Dict[str, int]
    edge_index: torch.Tensor       # [2, 279] long; row 0 = from-node index, row 1 = to-node index
    chokepoint_mask: torch.Tensor  # [279] bool, True for the 10 chokepoint edges (the only observed edges)
    edge_df: pd.DataFrame          # canonical 279-row edge reference, sorted by edge_id


class ShippingGraphDataset(Dataset):
    """One split of samples. Validates shapes and finiteness on construction, which is how the
    G1 acceptance test ("no NaN/Inf, shapes match 142 nodes and 279 edges") is enforced."""

    def __init__(self, scenario_ids, sources, edge_index, x_node, x_edge, y_edge, loss_mask) -> None:
        self.scenario_ids, self.sources, self.edge_index = scenario_ids, sources, edge_index
        self.x_node, self.x_edge, self.y_edge, self.loss_mask = x_node, x_edge, y_edge, loss_mask
        n = len(scenario_ids)
        shapes = {"edge_index": (2, 279), "x_node": (n, 142, 6), "x_edge": (n, 279, 11),
                  "y_edge": (n, 279, 4), "loss_mask": (n, 279, 4)}
        for name, shape in shapes.items():
            if tuple(getattr(self, name).shape) != shape:
                raise ValueError(f"Bad {name} shape: expected {shape}, got {tuple(getattr(self, name).shape)}")
        for name in ("x_node", "x_edge", "y_edge"):
            if not torch.isfinite(getattr(self, name)).all():
                raise ValueError(f"NaN or Inf detected in {name}!")

    def __len__(self) -> int:
        return len(self.scenario_ids)

    def __getitem__(self, i: int) -> Dict[str, object]:
        # `source` travels with each sample so G3's export can label rows simulated/observed/gnn.
        return {"scenario_id": self.scenario_ids[i], "source": self.sources[i], "edge_index": self.edge_index,
                "x_node": self.x_node[i], "x_edge": self.x_edge[i], "y_edge": self.y_edge[i],
                "loss_mask": self.loss_mask[i]}


def collate_disjoint(batch: List[Dict[str, object]]) -> Dict[str, object]:
    """DataLoader collate_fn that merges B graphs into ONE disjoint graph, so G2's single-graph
    forward(x_node [N,6], x_edge [E,11], edge_index [2,E]) can train on batches without any change.

    Graph b's nodes are shifted by b*142 in edge_index, so messages never cross between scenarios and
    each graph's predictions are identical to running it alone. Reshape predictions back with
    pred.view(num_graphs, 279, 4). Caveat: G2 pools its graph embedding over ALL edges it is given,
    so a batch of B>1 yields one shared embedding; run one sample at a time to get per-scenario embeddings.

        loader = DataLoader(ds, batch_size=32, shuffle=True, collate_fn=collate_disjoint)
        b = next(iter(loader))
        pred, _ = model(b["x_node"], b["x_edge"], b["edge_index"])   # pred [B*279, 4]
        pred = pred.view(b["num_graphs"], 279, 4)                    # compare with b["y_edge"], b["loss_mask"]
    """
    n_graphs = len(batch)
    n_nodes, n_edges = batch[0]["x_node"].shape[0], batch[0]["x_edge"].shape[0]
    offsets = torch.arange(n_graphs, dtype=torch.long).view(n_graphs, 1, 1) * n_nodes
    edge_index = (torch.stack([s["edge_index"] for s in batch]) + offsets).permute(1, 0, 2).reshape(2, n_graphs * n_edges)
    return {"scenario_id": [s["scenario_id"] for s in batch], "source": [s["source"] for s in batch],
            "num_graphs": n_graphs, "edge_index": edge_index,
            "x_node": torch.cat([s["x_node"] for s in batch]),    # [B*142, 6]
            "x_edge": torch.cat([s["x_edge"] for s in batch]),    # [B*279, 11]
            "y_edge": torch.stack([s["y_edge"] for s in batch]),  # [B, 279, 4], real units
            "loss_mask": torch.stack([s["loss_mask"] for s in batch])}  # [B, 279, 4] bool


# =============================================================================
# SECTION 3 - Build the graph (edge_index, node/edge ordering)
# The graph never changes between scenarios, so this runs once. Sorting edges and nodes by id
# gives every other file (model, training, export) the same row order, which is what lets G3
# write predictions back to the right edge_id in the final 22-column table.
# =============================================================================
def build_graph_metadata(nodes_df, edges_df, scenario_flows_df) -> GraphMetadata:
    """Canonical 142-node / 279-edge ordering and edge_index [2, 279]."""
    # Prefer config/edges.csv; fall back to the baseline scenario's edge list if it isn't 279 rows.
    if edges_df is not None and len(edges_df) == 279:
        base = edges_df.copy()
        base["edge_id"] = base["edge_id" if "edge_id" in base.columns else base.columns[0]].astype(str)
    elif scenario_flows_df is not None:
        base = scenario_flows_df[scenario_flows_df["scenario_id"] == "baseline"].drop_duplicates("edge_id").copy()
        base["edge_id"] = base["edge_id"].astype(str)
    else:
        raise ValueError("Could not find a 279-edge table in config/edges.csv or scenario_edge_flows.")
    base = base.sort_values("edge_id").reset_index(drop=True)
    if len(base) != 279:
        raise ValueError(f"Expected 279 edges, found {len(base)}")
    if base["edge_id"].duplicated().any():
        raise ValueError(f"Duplicate edge_id values, e.g. {base.loc[base['edge_id'].duplicated(), 'edge_id'].tolist()[:5]}")

    base["from_node"] = base["from_node" if "from_node" in base.columns else "source"].astype(str)
    base["to_node"] = base["to_node" if "to_node" in base.columns else "target"].astype(str)

    # Node list: from nodes.csv if present, otherwise every node that appears on an edge.
    if nodes_df is not None and len(nodes_df) > 0:
        node_ids = sorted(nodes_df["node_id" if "node_id" in nodes_df.columns else "id"].astype(str).unique())
    else:
        node_ids = sorted(set(base["from_node"]) | set(base["to_node"]))
    if len(node_ids) != 142:
        raise ValueError(f"Expected 142 nodes, found {len(node_ids)}")
    node_to_idx = {n: i for i, n in enumerate(node_ids)}

    # Normalise edge-type labels to lane / chokepoint / connector. The 10-chokepoint count is the
    # sanity check that the labels were understood; the mask later limits fine-tuning to these edges.
    et = _col(base, ["edge_type", "type"], "lane").astype(str).str.lower().str.strip()
    et = et.map(lambda s: "connector" if "connector" in s else "chokepoint" if "choke" in s else "lane" if "lane" in s else s)
    base["edge_type"] = et
    cp_mask = torch.tensor((et == "chokepoint").values, dtype=torch.bool)
    if int(cp_mask.sum()) != 10:
        raise ValueError(f"Expected 10 chokepoint edges, found {int(cp_mask.sum())}")

    # edge_index[0] = source node index, edge_index[1] = target node index for each of the 279 edges.
    edge_ids = base["edge_id"].tolist()
    edge_index = torch.tensor([[node_to_idx[u] for u in base["from_node"]],
                               [node_to_idx[v] for v in base["to_node"]]], dtype=torch.long)
    return GraphMetadata(node_ids, node_to_idx, edge_ids, {e: i for i, e in enumerate(edge_ids)},
                         edge_index, cp_mask, base)


# =============================================================================
# SECTION 4 - Node features [142, 6]
# Each node gets: port/junction flag, location, and how much oil it ships out and takes in per week.
# Export/import tell the GNN which ports are big sources and sinks of demand. These features are
# static (they come from the baseline network, not from any scenario), so they carry no information
# about the held-out events.
# =============================================================================
def _od_exports_imports(od_df, port_set, notes) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    """Route 1: per-port weekly export/import barrels from the OD matrix (None, None if unusable)."""
    if od_df is None or len(od_df) == 0:
        notes.append("OD matrix: not found or empty.")
        return None, None
    o = next((c for c in OD_ORIGIN_COLS if c in od_df.columns), None)
    d = next((c for c in OD_DEST_COLS if c in od_df.columns), None)
    v = next((c for c in OD_VOLUME_COLS if c in od_df.columns), None)
    if None in (o, d, v):
        notes.append(f"OD matrix: could not identify origin/dest/volume columns "
                     f"(origin={o}, dest={d}, volume={v}); columns are {list(od_df.columns)}.")
        return None, None
    vol = _num(od_df[v])
    if "year" in v.lower():  # project time step is one week (section 2.2)
        vol = vol / 52.1429
        log.info("OD volume column '%s' is annual; dividing by 52.1429 to get barrels per week.", v)

    def usable(e, i):
        # Accept the ids only if at least half the ports received volume; guards against a wrong id format.
        need = 0.5 * len(port_set)
        return (e.reindex(list(port_set)).fillna(0).gt(0).sum() >= need
                and i.reindex(list(port_set)).fillna(0).gt(0).sum() >= need)

    # OD files may name ports "1201" while the graph says "port_1201"; try the common variants.
    fixes = [("as-is", lambda s: s), ("add 'port_' prefix", lambda s: f"port_{s}"),
             ("add 'port_port' prefix", lambda s: f"port_port{s}"),
             ("strip 'port_' prefix", lambda s: s[5:] if s.startswith("port_") else s)]
    for name, fix in fixes:
        e = vol.groupby(od_df[o].map(_norm_id).map(fix)).sum()   # barrels leaving each origin port
        i = vol.groupby(od_df[d].map(_norm_id).map(fix)).sum()   # barrels arriving at each destination port
        if usable(e, i):
            if name != "as-is":
                log.info("OD ids matched node ids after: %s.", name)
            return e, i
    e = vol.groupby(od_df[o].map(_norm_id)).sum()
    notes.append(f"OD matrix: only {len(set(e.index) & port_set)} of {len(port_set)} port node_ids appear as origins. "
                 f"Sample OD ids: {list(e.index[:4])}; sample node_ids: {sorted(port_set)[:4]}.")
    return None, None


def _build_raw_node_table(meta, nodes_df, ports_df, junctions_df, od_df, baseline_df) -> pd.DataFrame:
    """Unnormalised [142, 6] node features (is_port, is_junction, lat, lon, export_bbl, import_bbl)."""
    df = pd.DataFrame({"node_id": meta.node_ids})
    if nodes_df is not None and len(nodes_df) > 0:
        nd = nodes_df.copy()
        nd["node_id"] = nd["node_id" if "node_id" in nd.columns else "id"].astype(str)
        df = df.merge(nd, on="node_id", how="left")

    # 4a. Port vs junction flag. Tries the most direct source first (is_port / node_type column),
    #     then ports_selected.csv, then infers it from which nodes only touch connector edges.
    if "is_port" in df.columns:
        is_port = pd.to_numeric(df["is_port"], errors="coerce")
    elif "node_type" in df.columns:
        is_port = (df["node_type"].astype(str).str.lower() == "port").astype(float)
    elif ports_df is not None:
        is_port = df["node_id"].isin(set(ports_df["node_id" if "node_id" in ports_df.columns else ports_df.columns[0]].astype(str))).astype(float)
    else:
        ed = meta.edge_df
        conn, sea = ed[ed["edge_type"] == "connector"], ed[ed["edge_type"] != "connector"]
        port_nodes = (set(conn["from_node"]) | set(conn["to_node"])) - (set(sea["from_node"]) | set(sea["to_node"]))
        is_port = df["node_id"].isin(port_nodes).astype(float)
    if junctions_df is not None and is_port.isna().any():
        j = set(junctions_df["node_id" if "node_id" in junctions_df.columns else junctions_df.columns[0]].astype(str))
        is_port = is_port.fillna((~df["node_id"].isin(j)).astype(float))
    df["is_port"] = is_port.fillna(0.0).astype(float)
    df["is_junction"] = 1.0 - df["is_port"]
    df["lat"] = _num(_col(df, ["lat", "latitude"], 0.0))
    df["lon"] = _num(_col(df, ["lon", "lng", "longitude"], 0.0))

    # 4b. Export/import barrels per port. Route 1 = OD matrix (preferred, it is the demand itself).
    port_set = set(df.loc[df["is_port"] > 0.5, "node_id"])
    notes: List[str] = []
    exp_s, imp_s = _od_exports_imports(od_df, port_set, notes)

    # Route 2 = sum the baseline connector flows (what the solver actually routed). Used only if the
    # OD file is missing or its ids don't match; it triggers a warning so the fallback is visible.
    if exp_s is None:
        if "oil_volume_bbl" not in baseline_df.columns:
            notes.append("Baseline: no oil_volume_bbl column.")
        else:
            b = baseline_df.drop_duplicates("edge_id").set_index("edge_id").reindex(meta.edge_ids)
            ed = meta.edge_df.assign(_vol=_num(b["oil_volume_bbl"]).values)
            conn = ed[ed["edge_type"] == "connector"]
            e = conn[conn["from_node"].isin(port_set)].groupby("from_node")["_vol"].sum()
            i = conn[conn["to_node"].isin(port_set)].groupby("to_node")["_vol"].sum()
            need = 0.5 * len(port_set)
            if (e.reindex(list(port_set)).fillna(0).gt(0).sum() >= need
                    and i.reindex(list(port_set)).fillna(0).gt(0).sum() >= need):
                exp_s, imp_s = e, i
            else:
                notes.append(f"Baseline connectors: edge_type counts {meta.edge_df['edge_type'].value_counts().to_dict()}; "
                             f"{len(conn)} connector edges; connector volume sums to {float(conn['_vol'].sum()):,.0f}.")
        if exp_s is None:
            raise ValueError("Could not build export_bbl / import_bbl for the port nodes.\n  " + "\n  ".join(notes))
        warnings.warn("Export/import fell back to baseline connectors: " + " | ".join(notes))

    df["export_bbl"] = _num(df["node_id"].map(exp_s))
    df["import_bbl"] = _num(df["node_id"].map(imp_s))

    # 4c. Sanity checks: fail loudly rather than hand the model degenerate (all-zero) node features.
    n_ports, n_junc = int(df["is_port"].sum()), int(df["is_junction"].sum())
    if (n_ports, n_junc) != (EXPECTED_PORTS, EXPECTED_JUNCTIONS):
        raise ValueError(f"Expected {EXPECTED_PORTS} ports / {EXPECTED_JUNCTIONS} junctions, got {n_ports} / {n_junc}.")
    total_export = float(df["export_bbl"].sum())
    if total_export <= 0 or float(df["import_bbl"].sum()) <= 0:
        raise ValueError("export_bbl / import_bbl are all zero; check the OD matrix and baseline connector volumes.")

    # Reference total = what the solver actually routed (every barrel crosses two connectors).
    # If the node features disagree with it, the inputs and the labels describe different demand.
    solver_total = 0.0
    if {"edge_type", "oil_volume_bbl"} <= set(baseline_df.columns):
        bd = baseline_df.drop_duplicates("edge_id")
        is_conn = bd["edge_type"].astype(str).str.lower().str.contains("connector")
        solver_total = float(_num(bd.loc[is_conn, "oil_volume_bbl"]).sum()) / 2.0
    if solver_total > 0 and abs(total_export - solver_total) / solver_total > 0.01:
        warnings.warn(f"Total weekly export {total_export:,.0f} bbl differs from the solver's baseline "
                      f"{solver_total:,.0f} bbl by more than 1%; node features may not match the labels.")
    log.info("Weekly demand check (bbl): node features %s | solver baseline %s | Deliverable 5 section 2.2 documents %s.",
             f"{total_export:,.0f}", f"{solver_total:,.0f}", f"{DOCUMENTED_WEEKLY_BARRELS:,}")
    return df


# =============================================================================
# SECTION 5 - Edge features [279, 11] and targets [279, 4]
# Turns each scenario table (one row per edge) into a fixed-size tensor.
#   Inputs  (x_edge): what the model is told - edge type, length, capacity, toll, baseline flows,
#                     and the throttle for THIS scenario. Only throttle changes between scenarios.
#   Targets (y_edge): what the model must predict - the solver's flow, transits, utilization, delay.
#   loss_mask:        1 where a target is real. For simulated scenarios that is every edge; for
#                     observed weeks it is only the chokepoints, so unobserved edges never train the model.
# =============================================================================
def _lookup_toll(cfg: pd.DataFrame, base: pd.DataFrame) -> np.ndarray:
    """Tolls live in config/edges.csv; the 22-column flow tables have no toll column. An all-zero toll
    column would make Suez and Panama look free to the model, so absence is warned, not hidden."""
    for frame in (cfg, base):
        for c in TOLL_COLS:
            if c in frame.columns:
                toll = _num(frame[c]).astype(np.float32).values
                if (toll > 0).any():
                    return toll
    warnings.warn(f"No non-zero toll column found (looked for {TOLL_COLS}); toll_usd_per_bbl will be all zeros.")
    return np.zeros(len(cfg), dtype=np.float32)


def _attach_edge_ids(work: pd.DataFrame, meta: GraphMetadata, baseline_df: pd.DataFrame) -> pd.DataFrame:
    """The weekly PortWatch table is keyed by chokepoint id, not edge_id. Map one to the other so each
    observed transit count lands on the right chokepoint edge. Chokepoints not in our 10 are dropped."""
    id_col = next((c for c in CHOKEPOINT_ID_COLS if c in work.columns), None)
    if id_col is None:
        raise ValueError(f"Observed table has neither 'edge_id' nor any of {CHOKEPOINT_ID_COLS}; columns: {list(work.columns)}")
    cp_map: Dict[str, str] = {}
    for frame in (meta.edge_df, baseline_df):
        if "chokepoint_portid" in frame.columns and "edge_id" in frame.columns:
            m = frame.dropna(subset=["chokepoint_portid"])
            for k, v in zip(m["chokepoint_portid"], m["edge_id"]):
                cp_map.setdefault(_norm_id(k), str(v))
    out = work.copy()
    out["edge_id"] = out[id_col].map(_norm_id).map(cp_map)
    n_bad = int(out["edge_id"].isna().sum())
    if n_bad == len(out):
        raise ValueError("None of the observed chokepoint ids matched an edge in the graph.")
    if n_bad:
        warnings.warn(f"Dropping {n_bad} observed rows whose chokepoint id is not a graph edge.")
        out = out.dropna(subset=["edge_id"])
    return out


def _assemble_edge_tensors(df, meta, baseline_df, flow_params, group_col="scenario_id",
                           default_source="simulated", chokepoint_only_mask=False):
    """Scenario/weekly edge tables -> ids, sources, X [N,279,11], Y [N,279,4], mask [N,279,4]."""
    # Static edge properties come from the baseline world, so they are identical in every scenario.
    base = baseline_df.drop_duplicates("edge_id").set_index("edge_id").reindex(meta.edge_ids)
    cfg = meta.edge_df.set_index("edge_id").reindex(meta.edge_ids)
    et = meta.edge_df["edge_type"]
    length = _f32(base, "length_nm")
    free = (_f32(base, "free_flow_days") if "free_flow_days" in base.columns
            else (length / max(float(flow_params.get("speed_knots", 13.0)) * 24.0, 1e-6)).astype(np.float32))
    # Static columns in EDGE_FEATURE_NAMES order; None marks the per-scenario throttle slot.
    static = [(et == t).astype(np.float32).values for t in ("lane", "chokepoint", "connector")] + [
        length, free, _f32(base, "capacity_weekly"), _lookup_toll(cfg, base), None,
        _f32(base, "oil_volume_bbl"), _f32(base, "tanker_transits"), _f32(base, "utilization")]
    cp_np = meta.chokepoint_mask.numpy()

    work = df.copy()
    if chokepoint_only_mask:
        # PortWatch's upstream label name differs from simulator labels.
        if "tanker_transits" not in work and "weekly_tanker_transits" in work:
            work["tanker_transits"] = work["weekly_tanker_transits"]
        if "tanker_transits" not in work:
            raise ValueError("Observed weekly data must contain tanker transit counts.")
    if "edge_id" in work.columns:
        work["edge_id"] = work["edge_id"].astype(str)
    else:
        work = _attach_edge_ids(work, meta, baseline_df)  # observed weeks: chokepoint id -> edge_id
    if chokepoint_only_mask and not any(t in work.columns for t in TARGET_NAMES):
        raise ValueError(f"Observed table has none of the target columns {TARGET_NAMES}.")

    ids, srcs, xs, ys, ms = [], [], [], [], []
    all_edges = set(meta.edge_ids)
    for sid, grp in work.groupby(group_col, sort=True):  # one iteration = one scenario or one week
        # Every solved scenario must list all 279 edges (section 2.5). Observed weeks only have chokepoints.
        if not chokepoint_only_mask and not all_edges.issubset(set(grp["edge_id"])):
            raise ValueError(f"Scenario {sid!r} covers {len(all_edges & set(grp['edge_id']))} of 279 edges.")
        g = grp.drop_duplicates("edge_id").set_index("edge_id").reindex(meta.edge_ids)
        thr = _num(_col(g, ["throttle_factor"], 1.0), 1.0).astype(np.float32).values  # missing -> fully open
        x = np.stack([thr if a is None else a for a in static], axis=-1)  # [279, 11]
        y, m = np.zeros((279, 4), np.float32), np.zeros((279, 4), np.bool_)
        for t, col in enumerate(TARGET_NAMES):
            if col in g.columns:
                v = pd.to_numeric(g[col], errors="coerce").values.astype(np.float32)
                ok = ~np.isnan(v)  # only edges with a real value count toward the loss
                y[:, t], m[:, t] = np.nan_to_num(v, nan=0.0), (ok & cp_np if chokepoint_only_mask else ok)
        if not chokepoint_only_mask:  # closed edges must carry no flow in the labels (section 8 trap)
            bad = (thr <= 0.0) & ((m[:, 0] & (y[:, 0] > 1.0)) | (m[:, 1] & (y[:, 1] > 1e-3)))
            if bad.any():
                raise ValueError(f"Scenario {sid!r}: closed edges (throttle=0) carry flow in labels: "
                                 f"{[meta.edge_ids[i] for i in np.flatnonzero(bad)[:5]]}")
        ids.append(str(sid))
        srcs.append(str(grp["source"].iloc[0]) if "source" in grp.columns else default_source)
        xs.append(x), ys.append(y), ms.append(m)

    if chokepoint_only_mask and not any(m.any() for m in ms):
        raise ValueError("Observed table produced an all-False loss mask; fine-tuning would learn nothing.")
    return ids, srcs, torch.from_numpy(np.stack(xs)), torch.from_numpy(np.stack(ys)), torch.from_numpy(np.stack(ms))


# =============================================================================
# SECTION 6 - Held-out event detection
# Decides whether a given week belongs to one of the real events we must not train on.
# This is the piece that keeps the final scorecard honest: any week it flags is excluded from
# both pretraining-adjacent fine-tuning splits and reserved for G4's evaluation.
# =============================================================================
def _held_out_event(date_str: str, windows: Sequence[Tuple[str, str, str]] = DEFAULT_HOLDOUT_WINDOWS) -> Optional[str]:
    """Event id whose window overlaps the 7-day week ENDING on date_str, else None.

    Overlap (not end-date-inside) so straddling weeks are held out; unparseable dates raise
    instead of silently landing in the training split.
    """
    ts = pd.to_datetime(date_str, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Cannot parse week label {date_str!r} as a date; refusing to guess if it is held out.")
    start = ts - pd.Timedelta(days=6)
    return next((sid for sid, s, e in windows if start <= pd.to_datetime(e) and ts >= pd.to_datetime(s)), None)


def _is_held_out_week(date_str: str, holdout_windows: Sequence[Tuple[str, str, str]] = DEFAULT_HOLDOUT_WINDOWS) -> bool:
    """True if the week touches any held-out window (used by the leakage checks)."""
    return _held_out_event(date_str, holdout_windows) is not None


# =============================================================================
# SECTION 7 - Main builder: load, split, normalise, package
# Ties sections 3-6 together and produces the dictionary the rest of the GNN group consumes:
#   pretrain_*  -> G3 stage 1 (many exact solver labels)
#   finetune_*  -> G3 stage 2 (real chokepoint weeks, masked loss)
#   holdout_*   -> G4 scorecard (real events the model never saw)
# =============================================================================
def build_gnn_datasets(config_dir="config", data_dir="data", val_fraction: float = 0.15, seed: int = 42,
                       holdout_scenario_ids: Optional[set] = None,
                       holdout_windows: Sequence[Tuple[str, str, str]] = DEFAULT_HOLDOUT_WINDOWS) -> Dict[str, object]:
    """Build retrospective event-excluded datasets; use train_gnn for forward-only backtests."""
    cfg_p, d_p = Path(config_dir), Path(data_dir)
    proc, interim = d_p / "processed", d_p / "interim"
    holdout_ids = holdout_scenario_ids or set(DEFAULT_HOLDOUT_SCENARIO_IDS)

    # ---- 7a. Load every input table (each loader tries several locations; missing required ones raise).
    pp = cfg_p / "flow_params.json"
    flow_params = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else {}

    nodes_df = _first([cfg_p / "nodes.csv", proc / "nodes.parquet", d_p / "nodes.csv"])
    edges_df = _first([cfg_p / "edges.csv", cfg_p / "edges_core.csv", proc / "edges.parquet"])
    ports_df = _first([cfg_p / "ports_selected.csv", d_p / "ports_selected.csv"])
    junctions_df = _first([cfg_p / "junctions.csv", d_p / "junctions.csv"])
    od_df = _first([f / f"od_matrix.{ext}" for f in (proc, interim, d_p) for ext in ("parquet", "csv")])
    if od_df is None:  # last resort: any file named like an OD matrix (not od_port_fit.csv etc.)
        found = sorted(p for f in (proc, interim, d_p) if f.exists() for p in f.glob("*")
                       if p.suffix in (".parquet", ".csv") and any(k in p.name.lower() for k in ("od_matrix", "origin_dest")))
        if found:
            warnings.warn(f"od_matrix not found; using {found[0].name} (candidates: {[p.name for p in found]})")
            od_df = _read(found[0])

    scenario_flows = _first([proc / "scenario_edge_flows.parquet", d_p / "scenario_edge_flows.parquet",
                             d_p / "scenario_edge_flows.csv"])
    sim_scenarios = _first([proc / "gnn_simulated_scenarios.parquet", d_p / "gnn_simulated_scenarios.parquet",
                            d_p / "gnn_simulated_scenarios.csv"])
    if sim_scenarios is None:
        raise FileNotFoundError("Missing gnn_simulated_scenarios (.parquet or .csv) under data/.")
    # The true baseline is required: edge features such as baseline_volume come from it.
    if scenario_flows is None or not (scenario_flows["scenario_id"] == "baseline").any():
        raise FileNotFoundError("scenario_edge_flows must contain the 'baseline' scenario (see section 2.5).")
    scenario_flows = scenario_flows.assign(edge_id=scenario_flows["edge_id"].astype(str))

    # ---- 7b. Graph + node features (sections 3 and 4). Built once, reused by every sample.
    meta = build_graph_metadata(nodes_df, edges_df, scenario_flows)
    baseline_df = scenario_flows[scenario_flows["scenario_id"] == "baseline"].copy()
    missing = set(meta.edge_ids) - set(baseline_df["edge_id"])
    if missing:
        raise ValueError(f"{len(missing)} graph edge_ids missing from the baseline scenario, e.g. {sorted(missing)[:5]}.")

    raw_node = _build_raw_node_table(meta, nodes_df, ports_df, junctions_df, od_df, baseline_df)
    raw_node_t = torch.tensor(raw_node[["is_port", "is_junction", "lat", "lon", "export_bbl", "import_bbl"]].values,
                              dtype=torch.float32)

    # ---- 7c. Edge tensors for all 337 simulated scenarios (section 5).
    kw = dict(meta=meta, baseline_df=baseline_df, flow_params=flow_params)
    sim_ids, sim_srcs, sim_x, sim_y, sim_m = _assemble_edge_tensors(sim_scenarios, **kw)

    # ---- 7d. Pretrain split. Held-out events are removed FIRST, then the rest is split randomly
    #          (seeded, so the same split every run) into train and validation.
    eligible = [i for i, s in enumerate(sim_ids) if s not in holdout_ids]
    if len(eligible) < 2:
        raise ValueError(f"Only {len(eligible)} simulated scenarios left after removing held-out ids.")
    perm = np.random.default_rng(seed).permutation(eligible)
    n_val = max(1, int(len(perm) * val_fraction))
    val_idx, train_idx = sorted(perm[:n_val].tolist()), sorted(perm[n_val:].tolist())

    # ---- 7e. Normalisation. The edge scaler is fit on the TRAINING split only (leak-free). The node
    #          table is one static baseline table with no scenario information, so scaling it over
    #          all 142 nodes cannot leak anything. Scaler values are returned for G3/G4 to reuse.
    node_scaler = StandardScaler1D().fit(raw_node_t[:, NODE_CONT_COLS])
    node_x = raw_node_t.clone()
    node_x[:, NODE_CONT_COLS] = node_scaler.transform(raw_node_t[:, NODE_CONT_COLS])
    edge_scaler = StandardScaler1D().fit(sim_x[train_idx][:, :, EDGE_CONT_COLS])
    # Target scaler (divide-only, see TargetScaler): fit on pretrain-train labels where the mask is True.
    # y_edge in every dataset stays in real units; G3 applies target_scaler.transform() inside its loss.
    target_scaler = TargetScaler().fit(sim_y[train_idx], sim_m[train_idx])

    def norm_edge(x: torch.Tensor) -> torch.Tensor:
        out = x.clone()  # throttle (col 7) is left raw: it is already a 0-1 fraction
        out[:, :, EDGE_CONT_COLS] = edge_scaler.transform(x[:, :, EDGE_CONT_COLS])
        return out

    def subset(ids, srcs, x, y, m, idx) -> ShippingGraphDataset:
        """Package the chosen rows as a Dataset; the single node table is repeated for each sample."""
        return ShippingGraphDataset([ids[i] for i in idx], [srcs[i] for i in idx], meta.edge_index,
                                    node_x.unsqueeze(0).expand(len(idx), -1, -1).clone(),
                                    norm_edge(x[idx]), y[idx], m[idx])

    pre_train = subset(sim_ids, sim_srcs, sim_x, sim_y, sim_m, train_idx)
    pre_val = subset(sim_ids, sim_srcs, sim_x, sim_y, sim_m, val_idx)

    # ---- 7f. Held-out real-event scenarios: the solver's own answer for the three events, with the
    #          true throttle settings. G4 compares the GNN's prediction for these against observed data.
    ho_scen = None
    ho_df = scenario_flows[scenario_flows["scenario_id"].isin(holdout_ids)]
    if holdout_ids - set(ho_df["scenario_id"]):
        warnings.warn(f"Held-out scenarios not found in scenario_edge_flows: {sorted(holdout_ids - set(ho_df['scenario_id']))}")
    if len(ho_df) > 0:
        h = _assemble_edge_tensors(ho_df, default_source="modeled", **kw)
        ho_scen = subset(*h, list(range(len(h[0]))))

    # ---- 7g. Weekly chokepoint observations (real PortWatch data) for fine-tuning and the observed
    #          hold-out. Weeks touching a held-out window go to the hold-out set; the rest are split
    #          chronologically into fine-tune train and validation.
    cp_df = _first([proc / "chokepoint_weekly_attributes.parquet", d_p / "chokepoint_weekly_attributes.parquet",
                    d_p / "chokepoint_weekly_attributes.csv"])
    ft_train = ft_val = ho_obs = None
    if cp_df is None or len(cp_df) == 0:
        warnings.warn("chokepoint_weekly_attributes not found; no fine-tune / observed-holdout datasets built.")
    else:
        tcol = next((c for c in WEEK_COLS if c in cp_df.columns), None)
        if tcol is None:
            raise ValueError(f"No week column in chokepoint_weekly_attributes (looked for {WEEK_COLS}); columns: {list(cp_df.columns)}")
        cp_df = cp_df.copy()
        wk = pd.to_datetime(cp_df[tcol], errors="coerce")
        if wk.isna().any():  # a wrong date column here would otherwise leak held-out weeks into training
            raise ValueError(f"{int(wk.isna().sum())} rows have an unparseable '{tcol}' value.")
        cp_df[tcol] = wk.dt.strftime("%Y-%m-%d")
        cp_df["source"] = "observed"  # provenance column required on every table (section 1)
        if "throttle_factor" not in cp_df.columns:
            cp_df["throttle_factor"] = 1.0  # deliberate: non-event weeks are treated as fully open

        cp_ids, cp_srcs, cp_x, cp_y, cp_m = _assemble_edge_tensors(
            cp_df, group_col=tcol, default_source="observed", chokepoint_only_mask=True, **kw)

        events = [_held_out_event(w, holdout_windows) for w in cp_ids]
        ft_hold = [i for i, e in enumerate(events) if e is not None]
        ft_elig = [i for i, e in enumerate(events) if e is None]

        # Held-out event weeks get the solver's throttle vector for that event (the same input the
        # solver received), so the model can "see" the disruption it is being tested on. Throttle is
        # never derived from observed transits: that would put the answer into the input.
        ev_thr = {}
        for sid in holdout_ids:
            g = scenario_flows[scenario_flows["scenario_id"] == sid].drop_duplicates("edge_id").set_index("edge_id").reindex(meta.edge_ids)
            ev_thr[sid] = torch.tensor(_num(_col(g, ["throttle_factor"], 1.0), 1.0).values, dtype=torch.float32)
        for i in ft_hold:
            if events[i] in ev_thr:
                cp_x[i, :, THR_COL] = ev_thr[events[i]]

        # Validation = the latest 15% of eligible weeks (chronological, not random, to respect time order).
        n_ft_val = max(1, int(len(ft_elig) * val_fraction))
        ft_tr_idx = ft_elig[:-n_ft_val] if len(ft_elig) > n_ft_val else ft_elig
        ft_va_idx = ft_elig[-n_ft_val:] if len(ft_elig) > n_ft_val else []
        args = (cp_ids, cp_srcs, cp_x, cp_y, cp_m)
        ft_train = subset(*args, ft_tr_idx) if ft_tr_idx else None
        ft_val = subset(*args, ft_va_idx) if ft_va_idx else None
        ho_obs = subset(*args, ft_hold) if ft_hold else None

    # ---- 7h. Final guard: prove no held-out event or week is inside any training split. Raises if so.
    verify_no_leakage(pre_train, pre_val, ft_train, ft_val, holdout_ids, holdout_windows, ho_obs)
    return {"metadata": meta, "flow_params": flow_params, "pretrain_train": pre_train, "pretrain_val": pre_val,
            "finetune_train": ft_train, "finetune_val": ft_val, "holdout_scenarios": ho_scen,
            "holdout_observed": ho_obs, "target_scaler": target_scaler,
            "scalers": {"node_scaler": node_scaler.state_dict(), "edge_scaler": edge_scaler.state_dict(),
                        "target_scaler": target_scaler.state_dict()}}


# =============================================================================
# SECTION 8 - Leakage verification
# The G1 acceptance test "held-out events provably absent from the training split". It runs on every
# build and raises (so the pipeline stops) instead of printing a warning that could be missed.
# =============================================================================
def verify_no_leakage(pretrain_train_ds, pretrain_val_ds, finetune_train_ds, finetune_val_ds,
                      holdout_ids, holdout_windows, holdout_observed_ds=None) -> None:
    """Raises if any train/val split overlaps the held-out events or each other."""
    # Pretraining splits must not contain any held-out scenario id, and must not overlap each other.
    for name, ds in (("pretrain_train", pretrain_train_ds), ("pretrain_val", pretrain_val_ds)):
        if set(ds.scenario_ids) & holdout_ids:
            raise AssertionError(f"LEAKAGE DETECTED in {name}: {set(ds.scenario_ids) & holdout_ids}")
    shared = set(pretrain_train_ds.scenario_ids) & set(pretrain_val_ds.scenario_ids)
    if shared:
        raise AssertionError(f"LEAKAGE: pretrain train and val share scenarios: {sorted(shared)[:5]}")

    # Fine-tuning splits must not contain any week that touches a held-out window.
    for name, ds in (("finetune_train", finetune_train_ds), ("finetune_val", finetune_val_ds)):
        leaked = [] if ds is None else [w for w in ds.scenario_ids if _is_held_out_week(w, holdout_windows)]
        if leaked:
            raise AssertionError(f"LEAKAGE DETECTED in {name}: {leaked}")
    if finetune_train_ds is not None and finetune_val_ds is not None:
        shared = set(finetune_train_ds.scenario_ids) & set(finetune_val_ds.scenario_ids)
        if shared:
            raise AssertionError(f"LEAKAGE: finetune train and val share weeks: {sorted(shared)[:5]}")

    # The observed hold-out must contain only held-out weeks, and share none with fine-tuning.
    if holdout_observed_ds is not None:
        not_held = [w for w in holdout_observed_ds.scenario_ids if not _is_held_out_week(w, holdout_windows)]
        if not_held:
            raise AssertionError(f"Observed hold-out set contains non-held-out weeks: {not_held[:5]}")
        for name, ds in (("finetune_train", finetune_train_ds), ("finetune_val", finetune_val_ds)):
            if ds is not None and set(ds.scenario_ids) & set(holdout_observed_ds.scenario_ids):
                raise AssertionError(f"LEAKAGE: {name} shares weeks with the observed hold-out set.")


# =============================================================================
# SECTION 9 - Command-line entry point
# Quick smoke test: builds everything and prints split sizes. Expected on the current data:
# Pretrain 287 train / 50 val, 3 held-out scenarios, 46 held-out observed weeks.
# =============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    b = build_gnn_datasets(config_dir="config", data_dir="data")
    print("G1 Dataset Build Passed:")
    for label, key in (("Pretrain Train", "pretrain_train"), ("Pretrain Val", "pretrain_val"),
                       ("Finetune Train", "finetune_train"), ("Finetune Val", "finetune_val"),
                       ("Holdout Observed Weeks", "holdout_observed")):
        if b[key] is not None:
            print(f"  {label}: {len(b[key])}")
    if b["holdout_scenarios"] is not None:
        print(f"  Holdout Scenarios: {b['holdout_scenarios'].scenario_ids}")