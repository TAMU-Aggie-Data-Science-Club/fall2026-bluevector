"""Blue Vector — Full-Globe Interactive Maritime Disruption Intelligence.

Architecture
------------
* ``app/globe_template.py`` holds one self-contained deck.gl page. It is served
  through ``st.iframe`` so the globe, the panels and their JavaScript all live
  in the same document.
* Route geometry comes from ``searoute`` (computed once per edge and cached).
  Connectors are drawn as straight lines: they are port-to-junction stubs of a
  few nautical miles, so running a global sea router on them costs time and
  can produce nonsense arcs.
* Every interaction (remove edge, model toggle, Hormuz) sets a query parameter
  and lets Streamlit rerun, so the solver state always matches the map.

Run:
    .venv/bin/python -m streamlit run app/dashboard.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
ROOT = (
    Path(__file__).resolve().parents[1]
    if (Path(__file__).resolve().parents[1] / "config").exists()
    else Path.cwd()
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.globe_template import render_globe_html  # noqa: E402

CONFIG_DIR    = ROOT / "config"
PROCESSED_DIR = ROOT / "data" / "processed"

# ---------------------------------------------------------------------------
# Page config — no sidebar chrome, full-bleed
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Blue Vector | Maritime Disruption Intelligence",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Strip every pixel of Streamlit chrome.
# MUST use st.markdown(unsafe_allow_html=True) — st.html() creates its own
# sandboxed micro-iframe so CSS doesn't reach the main page.
st.markdown(
    """
    <style>
    #MainMenu, header, footer { display: none !important; }
    .stApp > header { display: none !important; }
    .stApp { background: #020817 !important; }
    .block-container {
        padding: 0 !important;
        max-width: 100vw !important;
        margin: 0 !important;
    }
    section[data-testid="stMain"] > div:first-child {
        padding: 0 !important;
    }
    /* Any residual vertical padding becomes a visible band above/below the
       globe, because the component iframe is stretched to the viewport. */
    [data-testid="stMain"], [data-testid="stMainBlockContainer"],
    [data-testid="stVerticalBlock"], [data-testid="stElementContainer"] {
        padding: 0 !important; margin: 0 !important; gap: 0 !important;
    }
    iframe { display: block !important; border: none !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Antimeridian split
# ---------------------------------------------------------------------------
def split_antimeridian(coords: list) -> list:
    if not coords or len(coords) < 2:
        return []
    segments: list = []
    cur = [[float(coords[0][0]), float(coords[0][1])]]
    for i in range(1, len(coords)):
        lon1 = float(coords[i - 1][0]); lat1 = float(coords[i - 1][1])
        lon2 = float(coords[i][0]);     lat2 = float(coords[i][1])
        if abs(lon1 - lon2) <= 180.0:
            cur.append([lon2, lat2])
        else:
            if lon1 > 0 and lon2 < 0:
                d1 = 180.0 - lon1; d2 = lon2 + 180.0
                lm = round(lat1 + (lat2 - lat1) * (d1 / max(d1 + d2, 1e-6)), 4)
                cur.append([180.0, lm])
                if len(cur) >= 2: segments.append(cur)
                cur = [[-180.0, lm], [lon2, lat2]]
            else:
                d1 = lon1 + 180.0; d2 = 180.0 - lon2
                lm = round(lat1 + (lat2 - lat1) * (d1 / max(d1 + d2, 1e-6)), 4)
                cur.append([-180.0, lm])
                if len(cur) >= 2: segments.append(cur)
                cur = [[180.0, lm], [lon2, lat2]]
    if len(cur) >= 2:
        segments.append(cur)
    return segments


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="Loading maritime network…")
def load_network() -> tuple[pd.DataFrame, pd.DataFrame]:
    nodes = pd.read_csv(CONFIG_DIR / "nodes.csv")
    edges = pd.read_csv(CONFIG_DIR / "edges.csv")

    sr_mod = None
    try:
        import searoute as sr
        sr_mod = sr
    except ImportError:
        pass

    segs_list = []
    for row in edges.itertuples():
        segs = None
        # Connectors are port-to-junction links, and in this graph they are long
        # (median 512 nm): a straight segment is the honest picture and saves one
        # global sea-router call per link.
        if sr_mod is not None and str(getattr(row, "edge_type", "")) != "connector":
            try:
                feat = sr_mod.searoute(
                    [float(row.from_lon), float(row.from_lat)],
                    [float(row.to_lon),   float(row.to_lat)],
                    units="naut", restrictions=["northwest"],
                )
                segs = split_antimeridian(feat["geometry"]["coordinates"])
            except Exception:
                segs = None
        if not segs:
            segs = split_antimeridian(
                [[float(row.from_lon), float(row.from_lat)],
                 [float(row.to_lon),   float(row.to_lat)]]
            )
        segs_list.append(segs)

    edges["route_segments"] = segs_list
    return nodes, edges


@st.cache_data(show_spinner="Running flow scenario…")
def run_flow_scenario(throttles_json: str, alpha: float, beta: float) -> tuple[str, str]:
    from scripts.flow_model.scenario_engine import run_scenario
    df, summary = run_scenario(
        throttles=json.loads(throttles_json),
        scenario_id="live_dashboard",
        params_override={"alpha": alpha, "beta": beta},
        save_files=False,
    )
    return df.to_json(orient="records"), json.dumps(summary)


@st.cache_data(show_spinner="Loading GNN predictions…")
def load_gnn_predictions(scenario_key: str) -> pd.DataFrame | None:
    """GNN edge predictions for one scenario, or None when none are on disk.

    ``gnn_edge_predictions.parquet`` is written by ``scripts/gnn/evaluate_gnn.py``
    (seed-averaged scenario predictions on the shared edge schema). Older
    ``gnn_simulated_scenarios.parquet`` files hold the *solver targets* the GNN
    imitates, not its predictions, so they are not accepted here.
    """
    path = PROCESSED_DIR / "gnn_edge_predictions.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if "scenario_id" not in df.columns:
        return None
    sub = df[df["scenario_id"] == scenario_key]
    return sub.reset_index(drop=True) if not sub.empty else None


@st.cache_data(show_spinner=False)
def gnn_scenarios() -> set[str]:
    """Scenario ids present in the exported GNN predictions.

    Empty when the file is absent, so callers can tell *no GNN output yet* apart
    from *GNN output exists but not for this scenario*.
    """
    path = PROCESSED_DIR / "gnn_edge_predictions.parquet"
    if not path.exists():
        return set()
    try:
        return set(pd.read_parquet(path, columns=["scenario_id"])["scenario_id"].unique())
    except Exception:
        return set()


@st.cache_data(show_spinner="Loading price model…")
def load_price_impacts() -> pd.DataFrame | None:
    path = PROCESSED_DIR / "brent_scenario_impacts.parquet"
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


@st.cache_data(show_spinner="Loading Hormuz crisis data…")
def load_hormuz_data() -> pd.DataFrame | None:
    path = PROCESSED_DIR / "hormuz_transfer_weekly_predictions.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        df["week_ending"] = pd.to_datetime(df["week_ending"])
        return df
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
def util_to_color(util: float, edge_type: str, throttle_factor: float) -> list[int]:
    """Edge colour for a utilisation, on the bands LEGEND_ITEMS advertises.

    Red means *congested* (>95% of capacity), not shut: a closed edge reads as
    the legend's grey "Closed / Removed" swatch, so the two are never confused.
    """
    if throttle_factor < 0.01:
        return [60, 60, 80, 90]
    u = min(max(float(util), 0.0), 1.5)
    if edge_type == "chokepoint":
        if u > 0.95: return [239, 68,  68,  255]
        if u > 0.70: return [249, 115, 22,  240]
        if u > 0.40: return [250, 204, 21,  220]
        return [56, 189, 248, 230]
    elif edge_type == "lane":
        if u > 0.95: return [239, 68,  68,  210]
        if u > 0.70: return [249, 115, 22,  200]
        if u > 0.40: return [250, 204, 21,  185]
        return [56, 189, 248, 170]
    return [100, 180, 255, 100]


def util_to_width(util: float, edge_type: str) -> float:
    u = min(max(float(util), 0.0), 1.5)
    if edge_type == "chokepoint": return max(5.0, min(9.0, 5.0 + u * 4.0))
    if edge_type == "lane":       return max(1.5, min(4.5, 1.5 + u * 3.0))
    return 0.8


# ---------------------------------------------------------------------------
# Build globe layer data
# ---------------------------------------------------------------------------
def build_layer_data(
    edges_base:    pd.DataFrame,
    scen_df:       pd.DataFrame,
    gnn_df:        pd.DataFrame | None,
    use_gnn:       bool,
    removed_edges: list[str],
    nodes_df:      pd.DataFrame,
    throttles:     dict | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:

    geo_cols = [c for c in
                ["edge_id", "route_segments", "from_lat", "from_lon", "to_lat", "to_lon"]
                if c in edges_base.columns]
    geo = edges_base[geo_cols].copy()

    src = gnn_df if (use_gnn and gnn_df is not None) else scen_df
    metric_cols = [
        "edge_id", "edge_type", "searoute_passage",
        "oil_volume_bbl", "tanker_transits", "utilization", "delay_days",
        "throttle_factor", "length_nm", "delta_volume_bbl", "anomaly_z", "cost_per_barrel",
    ]
    avail  = [c for c in metric_cols if c in src.columns]
    merged = geo.merge(src[avail], on="edge_id", how="left")

    path_rows: list[dict] = []
    for row in merged.itertuples():
        eid        = str(row.edge_id)
        is_removed = eid in removed_edges
        util       = float(getattr(row, "utilization",     0.0) or 0.0)
        e_type     = str(getattr(row,  "edge_type",        "lane") or "lane")
        passage    = str(getattr(row,  "searoute_passage", "") or "")
        if passage == "nan": passage = ""
        transits   = round(float(getattr(row, "tanker_transits",  0.0) or 0.0), 1)
        delay      = round(float(getattr(row, "delay_days",       0.0) or 0.0), 2)
        throttle   = float(getattr(row,  "throttle_factor",  1.0) or 1.0)
        # The scenario throttle is what the user asked for; throttle_factor is the
        # capacity/proxy ratio the solver stores, which is a different number.
        throughput = float((throttles or {}).get(eid, (throttles or {}).get(passage, 1.0)))
        volume     = round(float(getattr(row, "oil_volume_bbl",   0.0) or 0.0) / 1e6, 2)
        length     = round(float(getattr(row, "length_nm",        0.0) or 0.0), 0)
        cost       = round(float(getattr(row, "cost_per_barrel",  0.0) or 0.0), 2)
        anomaly    = round(float(getattr(row, "anomaly_z",        0.0) or 0.0), 2)

        color = [60, 60, 80, 90]              if is_removed else util_to_color(util, e_type, throttle)
        width = 1.0                           if is_removed else util_to_width(util, e_type)

        segs = getattr(row, "route_segments", None)
        if segs is None or (isinstance(segs, float) and pd.isna(segs)):
            segs = [[[float(row.from_lon), float(row.from_lat)],
                     [float(row.to_lon),   float(row.to_lat)]]]
        for seg in segs:
            path_rows.append({
                "edge_id":    eid, "edge_type":  e_type, "passage": passage,
                "path":       seg, "color":      color,  "width":   width,
                "util_pct":   round(util * 100, 1),
                "transits":   transits, "delay_days": delay,
                "throttle":   round(throttle, 2),
                "throughput_pct": round(throughput * 100),
                "volume_mbd": volume,  "length_nm":  int(length),
                "cost_per_bbl": cost,  "anomaly_z":  anomaly,
                "is_removed": is_removed,
            })

    port_rows = [
        {"node_id":   str(r.node_id),
         "name":      str(getattr(r, "name",      r.node_id) or r.node_id),
         "node_type": "port",
         "lat": float(r.lat), "lon": float(r.lon),
         "country": str(getattr(r, "country", "") or "")}
        for r in nodes_df[nodes_df["node_type"] == "port"].itertuples()
    ]
    junction_rows = [
        {"node_id":   str(r.node_id),
         "name":      str(getattr(r, "name",      r.node_id) or r.node_id),
         "node_type": str(getattr(r, "node_type", "junction") or "junction"),
         "lat": float(r.lat), "lon": float(r.lon),
         "country": str(getattr(r, "country", "") or "")}
        for r in nodes_df[nodes_df["node_type"] != "port"].itertuples()
    ]
    return path_rows, port_rows, junction_rows


def closed_edge_ids(edges: pd.DataFrame, throttles: dict) -> list[str]:
    """Edges whose effective throttle is zero — the solver drops them from the graph."""
    closed: list[str] = []
    for row in edges.itertuples():
        eid     = str(row.edge_id)
        passage = str(getattr(row, "searoute_passage", "") or "")
        if passage == "nan":
            passage = ""
        if float(throttles.get(eid, throttles.get(passage, 1.0))) <= 0.0:
            closed.append(eid)
    return closed


# ---------------------------------------------------------------------------
# KPI helpers
# ---------------------------------------------------------------------------
def compute_kpis(scen_df: pd.DataFrame, summary: dict) -> dict:
    rerouted_mbd = 0.0
    if "delta_volume_bbl" in scen_df.columns:
        rerouted_mbd = float(
            scen_df.loc[scen_df["delta_volume_bbl"] > 0, "delta_volume_bbl"].sum() / 7.0 / 1e6
        )
    stranded_mbd = float(summary.get("stranded_barrels",  0.0) / 7.0 / 1e6)
    cp_transits  = 0.0
    if "tanker_transits" in scen_df.columns and "edge_type" in scen_df.columns:
        cp_transits = float(
            scen_df.loc[scen_df["edge_type"] == "chokepoint", "tanker_transits"].sum()
        )
    extra_days   = float(summary.get("extra_voyage_days", 0.0))
    suez_share   = float(summary.get("suez_tanker_share", 0.0)) * 100.0
    return {
        "cp_transits":    int(round(cp_transits)),
        "rerouted_mbd":   round(rerouted_mbd, 2),
        "extra_days":     round(extra_days,   1),
        "suez_share_pct": round(suez_share,   1),
        "stranded_mbd":   round(stranded_mbd, 2),
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCENARIO_LABEL_MAP = {
    "Pre-Crisis Baseline (All Open)":          "baseline",
    "2023–24 Red Sea / Houthi Crisis":         "red_sea_2024",
    "2021 Ever Given Suez Blockage":           "ever_given_2021",
    "2023–24 Panama Canal Drought":            "panama_drought_2023",
    "Hypothetical Strait of Hormuz Closure":   "hormuz_closure",
    "Compound Middle East + Malacca Shock":    "compound_shock",
}

# Sliders step by 0.05, so scenario throughputs are snapped onto that grid.
SLIDER_STEP = 0.05


def snap_step(value: float) -> float:
    return round(round(float(value) / SLIDER_STEP) * SLIDER_STEP, 2)


def preset_throttles(params: dict) -> dict[str, dict]:
    """Scenario presets, taken from the same parameters the flow model was calibrated with."""
    red_sea = snap_step(params.get("red_sea_throttle", 0.65))
    return {
        "Pre-Crisis Baseline (All Open)":          {},
        "2023–24 Red Sea / Houthi Crisis":         {"suez": red_sea, "babalmandab": red_sea},
        "2021 Ever Given Suez Blockage":           {"suez": snap_step(1.0 / 7.0)},
        "2023–24 Panama Canal Drought":            {"panama": snap_step(24.0 / 36.0)},
        "Hypothetical Strait of Hormuz Closure":   {"ormuz": 0.0},
        "Compound Middle East + Malacca Shock":    {"suez": 0.30, "babalmandab": 0.30, "malacca": 0.50},
    }


CHOKEPOINTS = [
    ("suez",         "Suez Canal"),
    ("babalmandab",  "Bab-el-Mandeb"),
    ("ormuz",        "Strait of Hormuz"),
    ("malacca",      "Strait of Malacca"),
    ("panama",       "Panama Canal"),
    ("south_africa", "Cape of Good Hope"),
    ("bosporus",     "Bosporus"),
    ("gibraltar",    "Strait of Gibraltar"),
    ("sunda",        "Sunda Strait"),
]

CHOKEPOINT_KEYS = {key for key, _ in CHOKEPOINTS}

# Query-param actions carry scenario ids; the sidebar works in display labels.
KEY_TO_LABEL = {key: label for label, key in SCENARIO_LABEL_MAP.items()}

LEGEND_ITEMS = [
    ("Low utilisation (<40%)",       "#38bdf8"),
    ("Moderate (40–70%)",            "#facc15"),
    ("High (70–95%)",                "#f97316"),
    ("Congested / Throttled (>95%)", "#ef4444"),
    ("Closed / Removed",             "#3c3c50"),
]


def legend_html() -> str:
    return "".join(
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
        f'<div style="width:28px;height:3px;border-radius:2px;background:{c}"></div>'
        f'<span style="font-size:11px;color:#94a3b8">{label}</span></div>'
        for label, c in LEGEND_ITEMS
    )


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def init_state(params: dict) -> None:
    for k, v in {
        "removed_edges": [],
        # Per-edge throttle overrides from the globe popup, keyed by edge_id.
        "edge_throttles": {},
        "model_mode":    "flow",
        "hormuz_mode":   False,
        "scenario":      "2023–24 Red Sea / Houthi Crisis",
        # Calibrated values from config/flow_params.json, not ad-hoc defaults.
        "alpha":         float(params.get("alpha", 0.12)),
        "beta":          float(params.get("beta", 3.0)),
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def reset_edge_overrides() -> None:
    """A new scenario starts from its own calibrated presets, not per-edge tweaks."""
    st.session_state.edge_throttles = {}


def render_sidebar(params: dict) -> dict:
    presets = preset_throttles(params)
    with st.sidebar:
        st.markdown("### ⚓ Blue Vector Controls")
        st.caption("Fine-grained controls — the globe overlays are interactive too.")

        # Keyed so the globe's scenario button can move it: a query-param action
        # writes st.session_state.scenario, and a keyless widget would restore
        # its own remembered value and discard that write.
        scenario = st.selectbox(
            "Scenario", list(presets.keys()), key="scenario",
            on_change=reset_edge_overrides,
        )

        # Both widgets carry their session-state key: the globe's own toggles write
        # st.session_state.model_mode / hormuz_mode and rerun, and a keyless widget
        # would restore its remembered value and overwrite that write.
        st.radio(
            "Model", ["flow", "gnn"],
            format_func=lambda x: "Flow Model (BPR)" if x == "flow" else "GNN Prediction",
            horizontal=True, key="model_mode",
        )

        st.toggle(
            "🛢 Hormuz Crisis Mode",
            help="Weekly Hormuz transfer predictions. Disabled until "
                 "data/processed/hormuz_transfer_weekly_predictions.csv exists.",
            disabled=load_hormuz_data() is None, key="hormuz_mode",
        )

        st.markdown("#### BPR Parameters")
        st.session_state.alpha = st.slider(
            "Alpha", 0.05, 0.35, float(st.session_state.alpha), 0.01)
        st.session_state.beta = st.slider(
            "Beta",  2.0,  5.0,  float(st.session_state.beta),  0.1)

        st.markdown("#### Chokepoint Throttles")
        throttles: dict = dict(presets.get(scenario, {}))
        with st.form("throttle_form"):
            for key, label in CHOKEPOINTS:
                # Key includes the scenario so a new scenario starts from its own
                # preset. The default reads the key back, because the globe's edge
                # slider can carry a chokepoint throttle into it across a reload.
                state_key = f"th_{scenario}_{key}"
                default = float(st.session_state.get(state_key, throttles.get(key, 1.0)))
                throttles[key] = st.slider(
                    label, 0.0, 1.0, default, SLIDER_STEP, key=state_key)
            if st.form_submit_button("⚡ Apply & Update Globe", use_container_width=True):
                # These sliders are the canonical chokepoint control, so applying
                # them supersedes any per-edge override set from the globe.
                st.session_state.edge_throttles = {}

    return throttles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # Optional cloud-data gate — skip gracefully in dev (no secrets.toml)
    try:
        from app.cloud_data import FILES, restore_bundle
        try:
            bundle_url = st.secrets.get("DATA_BUNDLE_URL", "")
        except (FileNotFoundError, KeyError):
            bundle_url = ""
        if bundle_url:
            with st.spinner("Loading verified dashboard data…"):
                try:
                    restore_bundle(ROOT, bundle_url,
                                   st.secrets.get("DATA_BUNDLE_SHA256", ""))
                except Exception as exc:
                    st.error(f"Dashboard data could not be verified: {exc}")
                    st.stop()
            if any(not (ROOT / name).is_file() for name in FILES):
                st.error(
                    "Dashboard data not configured. "
                    "Supply DATA_BUNDLE_URL / DATA_BUNDLE_SHA256 in Streamlit Secrets."
                )
                st.stop()
    except ImportError:
        pass

    from scripts.flow_model.flow_solver import load_params
    params = load_params()
    init_state(params)

    # Handle query-param actions (globe buttons → URL nav → Streamlit rerun)
    qp     = st.query_params
    action = qp.get("bv_action", "")
    value  = qp.get("bv_value",  "")
    if action:
        # The reload that carries the action also starts a fresh Streamlit
        # session, so the globe sends the rest of its state (scenario, model,
        # Hormuz mode, throttles) with the action and it is restored here first.
        carried_scenario = KEY_TO_LABEL.get(qp.get("bv_scenario", ""))
        if carried_scenario:
            st.session_state.scenario = carried_scenario
        carried_model = qp.get("bv_model", "")
        if carried_model in ("flow", "gnn"):
            st.session_state.model_mode = carried_model
        carried_hormuz = qp.get("bv_hormuz", "")
        if carried_hormuz in ("on", "off"):
            st.session_state.hormuz_mode = (carried_hormuz == "on")

        # Switching scenario restarts from that scenario's calibrated presets, so
        # the previous scenario's throttle values must not follow it.
        switch_scenario = action == "set_scenario" and value in KEY_TO_LABEL
        if switch_scenario:
            st.session_state.scenario = KEY_TO_LABEL[value]
        st.session_state.edge_throttles = {}
        if not switch_scenario:
            for part in qp.get("bv_throttles", "").split(","):
                key, _, raw = part.partition(":")
                try:
                    frac = min(max(float(raw), 0.0), 1.0)
                except ValueError:
                    continue
                if key in CHOKEPOINT_KEYS:
                    # Chokepoint throttles live in the sidebar slider's state.
                    st.session_state[f"th_{st.session_state.scenario}_{key}"] = frac
                elif key:
                    st.session_state.edge_throttles[key] = frac
        if action == "set_throttle" and value:
            try:
                frac = float(qp.get("bv_throttle", "1") or 1.0)
            except ValueError:
                frac = 1.0
            st.session_state.edge_throttles[value] = min(max(frac, 0.0), 1.0)
        elif action == "restore_edge" and value:
            # Restore means "open this edge", whatever the scenario preset says.
            st.session_state.edge_throttles[value] = 1.0
        elif action == "clear_removed":
            for eid in [e for e in value.split(",") if e]:
                st.session_state.edge_throttles[eid] = 1.0
        st.query_params.clear()
        st.rerun()

    # Load network
    nodes, edges = load_network()

    throttles = render_sidebar(params)
    # Per-edge overrides from the globe win over the scenario preset: the solver
    # checks the edge_id key before chokepoint_portid and searoute_passage.
    throttles.update(st.session_state.edge_throttles)
    scenario_key = SCENARIO_LABEL_MAP.get(st.session_state.scenario, "baseline")
    # Edges the solver drops from the routing graph (throttle 0) — grey on the
    # map and listed in the panel, exactly like the old removed-edge list.
    closed_edges = closed_edge_ids(edges, throttles)

    # Run flow model
    throttle_active = {k: v for k, v in throttles.items() if v < 1.0}
    try:
        scen_df_json, summary_json = run_flow_scenario(
            json.dumps(throttle_active),
            float(st.session_state.alpha),
            float(st.session_state.beta),
        )
        scen_df = pd.DataFrame(json.loads(scen_df_json))
        summary = json.loads(summary_json)
    except Exception as exc:
        st.error(f"Flow solver error: {exc}")
        st.stop()

    # Merge geometry into scen_df
    geo_cols = [c for c in
                ["edge_id", "route_segments", "from_lat", "from_lon", "to_lat", "to_lon"]
                if c in edges.columns]
    if "edge_id" in scen_df.columns and geo_cols:
        scen_df = scen_df.merge(edges[geo_cols], on="edge_id", how="left")

    # GNN predictions (only the historical scenarios are trained and exported)
    gnn_df: pd.DataFrame | None = None
    if st.session_state.model_mode == "gnn":
        gnn_df = load_gnn_predictions(scenario_key)
        if gnn_df is not None and geo_cols and "route_segments" not in gnn_df.columns:
            gnn_df = gnn_df.merge(edges[geo_cols], on="edge_id", how="left")

    use_gnn = st.session_state.model_mode == "gnn" and gnn_df is not None
    gnn_available = load_gnn_predictions(scenario_key) is not None
    if st.session_state.model_mode == "gnn" and not gnn_available:
        st.sidebar.warning(
            "No GNN predictions for this scenario in data/processed — showing the flow model."
        )

    # Price model: exact scenario match only. Falling back to the baseline row
    # would print a baseline estimate under a crisis scenario's name.
    brent_data: dict | None = None
    price_df = load_price_impacts()
    if price_df is not None:
        col = next((c for c in ("week_or_scenario_id", "scenario_id")
                    if c in price_df.columns), None)
        if col is not None:
            row = price_df[price_df[col] == scenario_key]
            if not row.empty:
                r = row.iloc[0]

                def _safe(c: str) -> float | None:
                    v = r.get(c)
                    return float(v) if v is not None and pd.notna(v) else None

                brent_data = {
                    "pred_brent_mean":     _safe("pred_brent_mean"),
                    "pred_brent_lower_90": _safe("pred_brent_lower_90"),
                    "pred_brent_upper_90": _safe("pred_brent_upper_90"),
                    "delta_brent_mean":    _safe("delta_brent_mean"),
                }

    # Hormuz transfer predictions (GNN branch output, optional)
    hormuz_records: list | None = None
    hdf = load_hormuz_data()
    has_hormuz = hdf is not None
    if has_hormuz and st.session_state.hormuz_mode:
        cols = [c for c in
                ["week_ending", "actual", "prediction", "persistence"]
                if c in hdf.columns]
        mc  = "model" if "model" in hdf.columns else None
        sub = hdf[hdf[mc] == "real_only"][cols].copy() if mc else hdf[cols].copy()
        sub["week_ending"] = sub["week_ending"].astype(str)
        hormuz_records = sub.to_dict(orient="records")

    hormuz_rows_html = ""
    if hormuz_records:
        for rec in hormuz_records[-8:]:
            we  = str(rec.get("week_ending", ""))[:10]
            act = rec.get("actual",     "")
            prd = rec.get("prediction", "")
            try:
                hormuz_rows_html += (
                    f'<div class="hz-row">'
                    f'<span class="hz-week">{we}</span>'
                    f'<span class="hz-val">{float(act):.1f}<small> act</small></span>'
                    f'<span class="hz-val hz-pred">{float(prd):.1f}<small> pred</small></span>'
                    f'</div>'
                )
            except (TypeError, ValueError):
                pass
    if not hormuz_rows_html:
        hormuz_rows_html = '<div class="bv-hint">No prediction data available.</div>'

    gnn_note_html = ""
    if not gnn_available:
        if gnn_scenarios():
            gnn_note_html = (
                '<div class="bv-note">The GNN is trained on the three historical shocks '
                'and has no predictions for this scenario — showing the flow model.</div>'
            )
        else:
            gnn_note_html = (
                '<div class="bv-note">GNN edge predictions are not in data/processed '
                '(run scripts/gnn/evaluate_gnn.py) — showing the flow model.</div>'
            )

    # Build layer data
    path_rows, port_rows, junction_rows = build_layer_data(
        edges_base    = edges,
        scen_df       = scen_df,
        gnn_df        = gnn_df,
        use_gnn       = use_gnn,
        removed_edges = closed_edges,
        nodes_df      = nodes,
        throttles     = throttles,
    )
    kpis = compute_kpis(scen_df, summary)

    html = render_globe_html(
        paths          = path_rows,
        ports          = port_rows,
        junctions      = junction_rows,
        kpis           = kpis,
        brent_data     = brent_data,
        removed_edges  = closed_edges,
        model_mode     = "gnn" if use_gnn else "flow",
        scenario_key   = scenario_key,
        throttles      = throttles,
        hormuz_mode    = st.session_state.hormuz_mode,
        has_hormuz     = has_hormuz,
        gnn_available  = gnn_available,
        legend_html    = legend_html(),
        hormuz_rows_html = hormuz_rows_html,
        gnn_note_html  = gnn_note_html,
    )

    # height is a placeholder: the page's own script expands the iframe to the
    # parent viewport once it loads.
    st.iframe(html, height=1000)


if __name__ == "__main__":
    main()
