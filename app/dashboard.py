"""Blue Vector — Full-Globe Interactive Maritime Disruption Simulator.

Architecture:
  - Full-viewport interactive 3-D globe rendered via a self-contained HTML/JS page
    (deck.gl + MapLibre) served through st.components.v1.html().
  - Streamlit acts only as the Python back-end: it re-runs the flow solver or loads GNN
    predictions, serialises results to JSON, and injects them into the globe HTML.
  - All UI chrome (left panel, right panel, pop-ups, Hormuz mode) is pure HTML/CSS/JS
    so it stays pixel-perfect and recruiter-ready without Streamlit widget overhead.

How to run:
    .venv/bin/streamlit run app/dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

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

CONFIG_DIR = ROOT / "config"
PROCESSED_DIR = ROOT / "data" / "processed"
INTERIM_DIR = ROOT / "data" / "interim"

# ---------------------------------------------------------------------------
# Streamlit page config — no sidebar, wide layout
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Blue Vector | Maritime Disruption Intelligence",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Hide Streamlit chrome so the globe is truly full-screen
st.markdown(
    """
    <style>
    #MainMenu, header, footer { visibility: hidden; }
    .block-container {
        padding: 0 !important;
        max-width: 100% !important;
    }
    section[data-testid="stMain"] > div { padding: 0 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Antimeridian split helper (kept from original)
# ---------------------------------------------------------------------------
def split_antimeridian(coords: list) -> list:
    if not coords or len(coords) < 2:
        return []
    segments: list = []
    cur = [[float(coords[0][0]), float(coords[0][1])]]
    for i in range(1, len(coords)):
        lon1, lat1 = float(coords[i - 1][0]), float(coords[i - 1][1])
        lon2, lat2 = float(coords[i][0]), float(coords[i][1])
        if abs(lon1 - lon2) <= 180.0:
            cur.append([lon2, lat2])
        else:
            if lon1 > 0 and lon2 < 0:
                d1 = 180.0 - lon1; d2 = lon2 + 180.0
                lat_mid = round(lat1 + (lat2 - lat1) * (d1 / max(d1 + d2, 1e-6)), 4)
                cur.append([180.0, lat_mid])
                if len(cur) >= 2: segments.append(cur)
                cur = [[-180.0, lat_mid], [lon2, lat2]]
            else:
                d1 = lon1 + 180.0; d2 = 180.0 - lon2
                lat_mid = round(lat1 + (lat2 - lat1) * (d1 / max(d1 + d2, 1e-6)), 4)
                cur.append([-180.0, lat_mid])
                if len(cur) >= 2: segments.append(cur)
                cur = [[180.0, lat_mid], [lon2, lat2]]
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

    # Attach sea-route geometry
    sr_mod = None
    try:
        import searoute as sr
        sr_mod = sr
    except ImportError:
        pass

    segs_list, geom_list = [], []
    for row in edges.itertuples():
        segs = None
        if sr_mod is not None:
            try:
                feat = sr_mod.searoute(
                    [float(row.from_lon), float(row.from_lat)],
                    [float(row.to_lon), float(row.to_lat)],
                    units="naut", restrictions=["northwest"],
                )
                segs = split_antimeridian(feat["geometry"]["coordinates"])
            except Exception:
                segs = None
        if not segs and hasattr(row, "route_segments_json") and pd.notna(getattr(row, "route_segments_json", None)):
            try:
                segs = json.loads(getattr(row, "route_segments_json"))
            except Exception:
                segs = None
        if not segs:
            segs = split_antimeridian(
                [[float(row.from_lon), float(row.from_lat)], [float(row.to_lon), float(row.to_lat)]]
            )
        segs_list.append(segs)
        geom = (
            {"type": "LineString", "coordinates": segs[0]}
            if len(segs) == 1
            else {"type": "MultiLineString", "coordinates": segs}
        )
        geom_list.append(json.dumps(geom))

    edges["route_segments"] = segs_list
    edges["geometry"] = geom_list
    return nodes, edges


@st.cache_data(show_spinner="Running flow scenario…")
def run_flow_scenario(
    throttles_json: str,  # JSON string so it's hashable for cache
    alpha: float,
    beta: float,
    removed_edges_json: str,  # JSON list of edge_ids to force-close
) -> tuple[str, str]:  # returns (scen_df_json, summary_json)
    from scripts.flow_model.scenario_engine import run_scenario

    throttles: dict = json.loads(throttles_json)
    removed: list = json.loads(removed_edges_json)

    # Force-removed edges get throttle = 0
    if removed:
        edges_df = pd.read_csv(CONFIG_DIR / "edges.csv")
        for eid in removed:
            rows = edges_df[edges_df["edge_id"] == eid]
            if not rows.empty:
                passage = rows.iloc[0].get("searoute_passage", "")
                if pd.notna(passage) and passage:
                    throttles[str(passage)] = 0.0
                else:
                    throttles[str(eid)] = 0.0

    df, summary = run_scenario(
        throttles=throttles,
        scenario_id="live_dashboard",
        params_override={"alpha": alpha, "beta": beta},
        save_files=False,
    )
    return df.to_json(orient="records"), json.dumps(summary)


@st.cache_data(show_spinner="Loading GNN predictions…")
def load_gnn_predictions(scenario_key: str) -> pd.DataFrame | None:
    """Load GNN edge predictions for a named scenario if available."""
    gnn_path = PROCESSED_DIR / "gnn_edge_predictions.parquet"
    if not gnn_path.exists():
        return None
    try:
        df = pd.read_parquet(gnn_path)
        if "scenario_id" in df.columns:
            subset = df[df["scenario_id"] == scenario_key]
            if not subset.empty:
                return subset
        return df
    except Exception:
        return None


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
def util_to_rgb(util: float) -> list[int]:
    """Map utilisation [0, 1+] to a green→yellow→red RGBA colour."""
    u = min(max(float(util), 0.0), 1.5)
    if u < 0.6:
        t = u / 0.6
        r = int(56 + t * (250 - 56))
        g = int(189 + t * (204 - 189))
        b = int(248 - t * (21 - 248))  # blue fades out
        b = max(0, min(255, b))
    elif u < 1.0:
        t = (u - 0.6) / 0.4
        r = int(250 + t * (239 - 250))
        g = int(204 - t * 204)
        b = int(21 - t * 21)
    else:
        t = min((u - 1.0) / 0.5, 1.0)
        r = int(239 - t * (239 - 220))
        g = int(0)
        b = int(0)
    return [max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), 220]


def build_edge_records(
    edges_base: pd.DataFrame,
    scen_df: pd.DataFrame,
    gnn_df: pd.DataFrame | None,
    use_gnn: bool,
    removed_edges: list[str],
) -> list[dict]:
    """Merge base geometry with scenario results and produce deck.gl path records."""
    geo_cols = ["edge_id", "route_segments", "from_lat", "from_lon", "to_lat", "to_lon"]
    avail = [c for c in geo_cols if c in edges_base.columns]
    geo = edges_base[avail].copy()

    if use_gnn and gnn_df is not None:
        # Merge GNN predictions onto base geometry
        gnn_key_cols = [
            "edge_id", "edge_type", "searoute_passage",
            "oil_volume_bbl", "tanker_transits", "utilization", "delay_days",
            "throttle_factor", "length_nm", "free_flow_days",
            "delta_volume_bbl", "delta_transits", "anomaly_z", "cost_per_barrel",
        ]
        gnn_avail = [c for c in gnn_key_cols if c in gnn_df.columns]
        merged = geo.merge(gnn_df[gnn_avail], on="edge_id", how="left")
    else:
        # Use flow model results
        flow_key_cols = [
            "edge_id", "edge_type", "searoute_passage",
            "oil_volume_bbl", "tanker_transits", "utilization", "delay_days",
            "throttle_factor", "length_nm", "free_flow_days",
            "delta_volume_bbl", "delta_transits", "anomaly_z", "cost_per_barrel",
        ]
        flow_avail = [c for c in flow_key_cols if c in scen_df.columns]
        merged = geo.merge(scen_df[flow_avail], on="edge_id", how="left")

    records = []
    for row in merged.itertuples():
        eid = str(row.edge_id)
        is_removed = eid in removed_edges
        util = float(getattr(row, "utilization", 0.0) or 0.0)
        e_type = str(getattr(row, "edge_type", "lane") or "lane")
        passage = str(getattr(row, "searoute_passage", "") or "")
        if passage == "nan":
            passage = ""
        transits = round(float(getattr(row, "tanker_transits", 0.0) or 0.0), 1)
        delay = round(float(getattr(row, "delay_days", 0.0) or 0.0), 2)
        throttle = float(getattr(row, "throttle_factor", 1.0) or 1.0)
        volume = round(float(getattr(row, "oil_volume_bbl", 0.0) or 0.0) / 1e6, 2)
        length = round(float(getattr(row, "length_nm", 0.0) or 0.0), 0)
        cost = round(float(getattr(row, "cost_per_barrel", 0.0) or 0.0), 2)
        anomaly = round(float(getattr(row, "anomaly_z", 0.0) or 0.0), 2)

        if is_removed:
            color = [80, 80, 80, 120]
            width = 1.0
        elif e_type == "chokepoint":
            if throttle < 0.01:
                color = [220, 38, 38, 255]   # Hard red: closed
            elif util > 0.95:
                color = [245, 158, 11, 240]  # Amber: severely congested chokepoint
            else:
                color = util_to_rgb(util)
            width = 6.0 if util > 0.8 else 4.5
        elif e_type == "lane":
            color = util_to_rgb(util)
            width = max(1.5, min(4.0, 1.5 + util * 2.5))
        else:
            color = [100, 180, 255, 140]
            width = 1.0

        segs = getattr(row, "route_segments", None) or [
            [[float(row.from_lon), float(row.from_lat)], [float(row.to_lon), float(row.to_lat)]]
        ]

        for seg in segs:
            records.append({
                "edge_id": eid,
                "edge_type": e_type,
                "passage": passage,
                "path": seg,
                "color": color,
                "width": width,
                "util_pct": round(util * 100, 1),
                "transits": transits,
                "delay_days": delay,
                "throttle": round(throttle, 2),
                "volume_mbd": volume,
                "length_nm": length,
                "cost_per_bbl": cost,
                "anomaly_z": anomaly,
                "is_removed": is_removed,
            })
    return records


def build_node_records(nodes_df: pd.DataFrame) -> list[dict]:
    recs = []
    for row in nodes_df.itertuples():
        recs.append({
            "node_id": str(row.node_id),
            "name": str(getattr(row, "name", row.node_id) or row.node_id),
            "node_type": str(getattr(row, "node_type", "junction") or "junction"),
            "lat": float(row.lat),
            "lon": float(row.lon),
            "country": str(getattr(row, "country", "") or ""),
        })
    return recs


# ---------------------------------------------------------------------------
# KPI helpers
# ---------------------------------------------------------------------------
def compute_kpis(scen_df: pd.DataFrame, summary: dict) -> dict:
    rerouted_mbd = float(
        scen_df.loc[scen_df.get("delta_volume_bbl", pd.Series(dtype=float)) > 0, "delta_volume_bbl"].sum()
        / 7.0 / 1e6
        if "delta_volume_bbl" in scen_df.columns else 0.0
    )
    stranded_mbd = float(summary.get("stranded_barrels", 0.0) / 7.0 / 1e6)
    cp_transits = float(
        scen_df.loc[scen_df.get("edge_type", "") == "chokepoint", "tanker_transits"].sum()
        if "tanker_transits" in scen_df.columns else 0.0
    )
    extra_days = float(summary.get("extra_voyage_days", 0.0))
    suez_share = float(summary.get("suez_tanker_share", 0.0)) * 100.0
    return {
        "cp_transits": round(cp_transits, 0),
        "rerouted_mbd": round(rerouted_mbd, 2),
        "extra_days": round(extra_days, 1),
        "suez_share_pct": round(suez_share, 1),
        "stranded_mbd": round(stranded_mbd, 2),
    }


# ---------------------------------------------------------------------------
# Main globe HTML builder
# ---------------------------------------------------------------------------
SCENARIO_LABEL_MAP = {
    "Pre-Crisis Baseline (All Open)": "baseline",
    "2023–24 Red Sea / Houthi Crisis": "red_sea_2024",
    "2021 Ever Given Suez Blockage": "ever_given_2021",
    "2023–24 Panama Canal Drought": "panama_drought_2023",
    "Hypothetical Strait of Hormuz Closure": "hormuz_closure",
    "Compound Middle East + Malacca Shock": "compound_shock",
}

PRESET_THROTTLES = {
    "Pre-Crisis Baseline (All Open)": {},
    "2023–24 Red Sea / Houthi Crisis": {"suez": 0.35, "babalmandab": 0.35},
    "2021 Ever Given Suez Blockage": {"suez": 0.15},
    "2023–24 Panama Canal Drought": {"panama": 0.65},
    "Hypothetical Strait of Hormuz Closure": {"ormuz": 0.0},
    "Compound Middle East + Malacca Shock": {"suez": 0.30, "babalmandab": 0.30, "malacca": 0.50},
}

CHOKEPOINTS = [
    ("suez", "Suez Canal"),
    ("babalmandab", "Bab-el-Mandeb"),
    ("ormuz", "Strait of Hormuz"),
    ("malacca", "Strait of Malacca"),
    ("panama", "Panama Canal"),
    ("south_africa", "Cape of Good Hope"),
    ("bosporus", "Bosporus"),
    ("gibraltar", "Strait of Gibraltar"),
    ("sunda", "Sunda Strait"),
]


def build_globe_html(
    edge_records: list[dict],
    node_records: list[dict],
    kpis: dict,
    summary: dict,
    brent_data: dict | None,
    hormuz_data: list | None,
    model_mode: str,
    scenario_label: str,
    hormuz_mode: bool,
    removed_edges: list[str],
) -> str:
    edge_json = json.dumps(edge_records)
    node_json = json.dumps(node_records)
    kpi_json = json.dumps(kpis)
    brent_json = json.dumps(brent_data or {})
    hormuz_json = json.dumps(hormuz_data or [])
    removed_json = json.dumps(removed_edges)
    model_label = "GNN Prediction" if model_mode == "gnn" else "Flow Model"
    hormuz_active = "true" if hormuz_mode else "false"

    # Colour-scale legend items
    legend_items = [
        ("Low (<40%)", "#38bdf8"),
        ("Moderate (40–70%)", "#facc15"),
        ("High (70–95%)", "#f97316"),
        ("Congested (>95%)", "#ef4444"),
        ("Closed / Removed", "#4b5563"),
    ]
    legend_html = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">'
        f'<div style="width:28px;height:4px;border-radius:2px;background:{c}"></div>'
        f'<span style="font-size:11px;color:#94a3b8">{label}</span></div>'
        for label, c in legend_items
    )

    return textwrap.dedent(f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width,initial-scale=1"/>
    <title>Blue Vector</title>
    <script src="https://unpkg.com/deck.gl@^9/dist.min.js"></script>
    <script src="https://unpkg.com/maplibre-gl@^4/dist/maplibre-gl.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@^4/dist/maplibre-gl.css"/>
    <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{ width: 100%; height: 100%; background: #020817; font-family: 'Inter', system-ui, sans-serif; overflow: hidden; }}
    #deck-container {{ position: absolute; inset: 0; }}

    /* ── Glassmorphism panels ── */
    .panel {{
        position: absolute;
        background: rgba(2, 8, 23, 0.82);
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(56, 189, 248, 0.18);
        border-radius: 14px;
        color: #e2e8f0;
        padding: 18px 16px;
        z-index: 10;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(56,189,248,0.07) inset;
    }}
    .panel h2 {{
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #38bdf8;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        gap: 7px;
    }}
    .panel h3 {{
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #64748b;
        margin: 14px 0 8px;
    }}

    /* ── Left control panel ── */
    #left-panel {{
        top: 20px; left: 20px;
        width: 240px;
        max-height: calc(100vh - 40px);
        overflow-y: auto;
    }}
    #left-panel::-webkit-scrollbar {{ width: 4px; }}
    #left-panel::-webkit-scrollbar-track {{ background: transparent; }}
    #left-panel::-webkit-scrollbar-thumb {{ background: rgba(56,189,248,0.3); border-radius:2px; }}

    /* ── Right KPI panel ── */
    #right-panel {{
        top: 20px; right: 20px;
        width: 220px;
    }}

    /* ── Top bar (branding + Hormuz toggle) ── */
    #top-bar {{
        top: 20px;
        left: 50%;
        transform: translateX(-50%);
        display: flex;
        align-items: center;
        gap: 14px;
        padding: 10px 20px;
        border-radius: 40px;
        white-space: nowrap;
        z-index: 11;
    }}
    #top-bar .brand {{
        font-size: 15px;
        font-weight: 800;
        letter-spacing: 0.04em;
        color: #f8fafc;
        display: flex;
        align-items: center;
        gap: 7px;
    }}
    #top-bar .brand span {{ color: #38bdf8; }}
    #top-bar .scenario-badge {{
        font-size: 11px;
        font-weight: 600;
        color: #94a3b8;
        background: rgba(56,189,248,0.1);
        border: 1px solid rgba(56,189,248,0.2);
        border-radius: 20px;
        padding: 3px 10px;
    }}

    /* ── Pop-up card ── */
    #popup {{
        position: absolute;
        display: none;
        background: rgba(2, 8, 23, 0.95);
        backdrop-filter: blur(20px);
        border: 1px solid rgba(56,189,248,0.3);
        border-radius: 12px;
        color: #e2e8f0;
        padding: 14px 16px;
        min-width: 220px;
        max-width: 280px;
        z-index: 20;
        box-shadow: 0 12px 40px rgba(0,0,0,0.6);
        pointer-events: auto;
    }}
    #popup h4 {{
        font-size: 13px;
        font-weight: 700;
        color: #38bdf8;
        margin-bottom: 10px;
        border-bottom: 1px solid rgba(56,189,248,0.15);
        padding-bottom: 8px;
    }}
    .popup-row {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin: 5px 0;
        font-size: 12px;
    }}
    .popup-row .label {{ color: #64748b; }}
    .popup-row .value {{ color: #f1f5f9; font-weight: 600; }}
    .popup-badge {{
        display: inline-block;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.05em;
        padding: 2px 8px;
        border-radius: 20px;
        margin-bottom: 8px;
    }}
    .badge-chokepoint {{ background: rgba(239,68,68,0.2); color: #ef4444; border: 1px solid rgba(239,68,68,0.3); }}
    .badge-lane {{ background: rgba(56,189,248,0.2); color: #38bdf8; border: 1px solid rgba(56,189,248,0.3); }}
    .badge-connector {{ background: rgba(100,116,139,0.2); color: #94a3b8; border: 1px solid rgba(100,116,139,0.3); }}
    .badge-node {{ background: rgba(45,212,191,0.2); color: #2dd4bf; border: 1px solid rgba(45,212,191,0.3); }}
    .btn-remove {{
        margin-top: 10px;
        width: 100%;
        padding: 7px;
        border-radius: 7px;
        border: 1px solid rgba(239,68,68,0.5);
        background: rgba(239,68,68,0.1);
        color: #ef4444;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.05em;
        cursor: pointer;
        transition: background 0.15s;
    }}
    .btn-remove:hover {{ background: rgba(239,68,68,0.25); }}
    .btn-remove.restore {{
        border-color: rgba(56,189,248,0.5);
        background: rgba(56,189,248,0.1);
        color: #38bdf8;
    }}
    .btn-remove.restore:hover {{ background: rgba(56,189,248,0.25); }}
    #popup .close-btn {{
        position: absolute;
        top: 10px; right: 12px;
        font-size: 16px;
        color: #64748b;
        cursor: pointer;
        line-height: 1;
        background: none;
        border: none;
    }}
    #popup .close-btn:hover {{ color: #e2e8f0; }}

    /* ── KPI metric ── */
    .kpi-item {{
        background: rgba(56,189,248,0.05);
        border: 1px solid rgba(56,189,248,0.1);
        border-radius: 8px;
        padding: 10px 12px;
        margin-bottom: 8px;
    }}
    .kpi-label {{ font-size: 10px; font-weight: 600; color: #64748b; letter-spacing: 0.06em; text-transform: uppercase; }}
    .kpi-value {{ font-size: 20px; font-weight: 800; color: #f1f5f9; margin-top: 2px; }}
    .kpi-delta {{ font-size: 10px; color: #94a3b8; margin-top: 2px; }}
    .kpi-delta.warn {{ color: #f97316; }}
    .kpi-delta.danger {{ color: #ef4444; }}

    /* ── Model toggle ── */
    .model-toggle {{
        display: flex;
        background: rgba(0,0,0,0.3);
        border: 1px solid rgba(56,189,248,0.15);
        border-radius: 8px;
        overflow: hidden;
        margin-bottom: 12px;
    }}
    .model-toggle button {{
        flex: 1;
        padding: 7px 6px;
        font-size: 11px;
        font-weight: 700;
        border: none;
        background: transparent;
        color: #64748b;
        cursor: pointer;
        transition: all 0.15s;
        letter-spacing: 0.03em;
    }}
    .model-toggle button.active {{
        background: rgba(56,189,248,0.2);
        color: #38bdf8;
    }}

    /* ── Hormuz mode banner ── */
    #hormuz-banner {{
        display: none;
        position: absolute;
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%);
        background: rgba(239,68,68,0.15);
        border: 1px solid rgba(239,68,68,0.4);
        border-radius: 10px;
        padding: 10px 20px;
        color: #ef4444;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.05em;
        z-index: 15;
        display: none;
        align-items: center;
        gap: 8px;
        white-space: nowrap;
        backdrop-filter: blur(10px);
    }}

    /* ── Hormuz toggle button ── */
    .hormuz-btn {{
        padding: 7px 14px;
        border-radius: 20px;
        border: 1px solid rgba(239,68,68,0.4);
        background: rgba(239,68,68,0.08);
        color: #ef4444;
        font-size: 11px;
        font-weight: 700;
        cursor: pointer;
        transition: all 0.15s;
        letter-spacing: 0.04em;
        white-space: nowrap;
    }}
    .hormuz-btn.active {{
        background: rgba(239,68,68,0.25);
        border-color: rgba(239,68,68,0.7);
    }}

    /* ── Legend ── */
    #legend {{
        position: absolute;
        bottom: 20px;
        left: 20px;
        padding: 12px 14px;
    }}
    #legend h3 {{
        font-size: 10px;
        font-weight: 700;
        color: #64748b;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 8px;
    }}

    /* ── Brent box ── */
    .brent-box {{
        background: rgba(250,204,21,0.07);
        border: 1px solid rgba(250,204,21,0.2);
        border-radius: 8px;
        padding: 10px 12px;
        margin-top: 8px;
    }}
    .brent-label {{ font-size: 10px; font-weight: 600; color: #64748b; letter-spacing: 0.06em; text-transform: uppercase; }}
    .brent-value {{ font-size: 18px; font-weight: 800; color: #facc15; margin-top: 2px; }}
    .brent-ci {{ font-size: 10px; color: #94a3b8; margin-top: 1px; }}

    /* ── Hormuz chart ── */
    #hormuz-chart {{
        margin-top: 10px;
        height: 100px;
        position: relative;
    }}
    canvas#hchart {{ width: 100%; height: 100%; }}

    /* ── Scroll hint ── */
    #scroll-hint {{
        position: absolute;
        bottom: 20px;
        right: 20px;
        font-size: 10px;
        color: #334155;
        pointer-events: none;
        z-index: 5;
    }}

    /* scrollbar for right panel */
    #right-panel {{ max-height: calc(100vh - 40px); overflow-y: auto; }}
    #right-panel::-webkit-scrollbar {{ width: 4px; }}
    #right-panel::-webkit-scrollbar-track {{ background: transparent; }}
    #right-panel::-webkit-scrollbar-thumb {{ background: rgba(56,189,248,0.3); border-radius:2px; }}
    </style>
    </head>
    <body>
    <div id="deck-container"></div>

    <!-- Top bar -->
    <div class="panel" id="top-bar">
        <div class="brand">🌐 <span>BLUE</span>VECTOR</div>
        <div class="scenario-badge" id="scenario-badge">{scenario_label}</div>
        <div class="scenario-badge" id="model-badge" style="color:#a78bfa;border-color:rgba(167,139,250,0.3);background:rgba(167,139,250,0.1)">{model_label}</div>
        <button class="hormuz-btn {'active' if hormuz_mode else ''}" id="hormuz-toggle" onclick="toggleHormuz()">
            🛢 Hormuz Crisis 2026
        </button>
    </div>

    <!-- Left panel: controls -->
    <div class="panel" id="left-panel">
        <h2>⚙ Model Controls</h2>

        <div class="model-toggle">
            <button id="btn-flow" class="{'active' if model_mode == 'flow' else ''}" onclick="setModel('flow')">Flow Model</button>
            <button id="btn-gnn" class="{'active' if model_mode == 'gnn' else ''}" onclick="setModel('gnn')">GNN</button>
        </div>

        <h3>Active Scenario</h3>
        <div style="font-size:12px;color:#94a3b8;line-height:1.5;margin-bottom:4px">{scenario_label}</div>

        <h3>Congestion Legend</h3>
        {legend_html}

        <h3>Removed Edges</h3>
        <div id="removed-list" style="font-size:11px;color:#94a3b8;min-height:18px">
            {"<em>None</em>" if not removed_edges else ""}
        </div>
        <button onclick="clearRemoved()" style="margin-top:8px;width:100%;padding:6px;border-radius:6px;border:1px solid rgba(100,116,139,0.3);background:rgba(100,116,139,0.1);color:#94a3b8;font-size:11px;font-weight:700;cursor:pointer;display:{'block' if removed_edges else 'none'}" id="clear-btn">
            ↺ Restore All Edges
        </button>

        <h3>Instructions</h3>
        <div style="font-size:11px;color:#475569;line-height:1.6">
            • Click an <b style="color:#38bdf8">edge</b> to inspect & remove<br>
            • Click a <b style="color:#2dd4bf">node</b> to view port info<br>
            • Drag to rotate the globe<br>
            • Scroll to zoom
        </div>
    </div>

    <!-- Right panel: KPIs + Price -->
    <div class="panel" id="right-panel">
        <h2>📊 Live Metrics</h2>
        <div id="kpi-container"></div>
        <div id="brent-container"></div>
    </div>

    <!-- Pop-up card -->
    <div id="popup">
        <button class="close-btn" onclick="closePopup()">✕</button>
        <div id="popup-content"></div>
    </div>

    <!-- Hormuz crisis banner -->
    <div id="hormuz-banner">
        🚨 HORMUZ CRISIS MODE ACTIVE — 2026 Transfer Analysis
    </div>

    <!-- Legend -->
    <div class="panel" id="legend">
        <h3>Edge Utilisation</h3>
        {legend_html}
    </div>

    <!-- Scroll hint -->
    <div id="scroll-hint">scroll to zoom · drag to orbit</div>

    <script>
    // ── Data ──────────────────────────────────────────────────────────────
    const EDGE_RECORDS   = {edge_json};
    const NODE_RECORDS   = {node_json};
    const KPI_DATA       = {kpi_json};
    const BRENT_DATA     = {brent_json};
    const HORMUZ_DATA    = {hormuz_json};
    let   REMOVED_EDGES  = {removed_json};
    let   MODEL_MODE     = "{model_mode}";
    let   HORMUZ_MODE    = {hormuz_active};

    // ── KPI rendering ─────────────────────────────────────────────────────
    function renderKPIs() {{
        const c = document.getElementById('kpi-container');
        const d = KPI_DATA;
        c.innerHTML = `
            <div class="kpi-item">
                <div class="kpi-label">Chokepoint Crossings</div>
                <div class="kpi-value">${{d.cp_transits.toLocaleString()}}</div>
                <div class="kpi-delta">transits / week</div>
            </div>
            <div class="kpi-item">
                <div class="kpi-label">Rerouted Flow</div>
                <div class="kpi-value">${{d.rerouted_mbd}}</div>
                <div class="kpi-delta">mb/d added traffic</div>
            </div>
            <div class="kpi-item">
                <div class="kpi-label">Extra Corridor Delay</div>
                <div class="kpi-value ${{d.extra_days > 3 ? 'danger' : d.extra_days > 1 ? 'warn' : ''}}">${{d.extra_days > 0 ? '+' : ''}}${{d.extra_days}} d</div>
                <div class="kpi-delta">Cape detour + BPR queue</div>
            </div>
            <div class="kpi-item">
                <div class="kpi-label">Suez / (Suez + Cape) Share</div>
                <div class="kpi-value">${{d.suez_share_pct}}%</div>
                <div class="kpi-delta">Europe–Asia routing split</div>
            </div>
            <div class="kpi-item">
                <div class="kpi-label">Stranded Volume</div>
                <div class="kpi-value ${{d.stranded_mbd > 0.5 ? 'danger' : ''}}">${{d.stranded_mbd}}</div>
                <div class="kpi-delta">mb/d unserved demand</div>
            </div>
        `;
        // Brent
        const bc = document.getElementById('brent-container');
        if (BRENT_DATA && BRENT_DATA.pred_brent_mean !== undefined) {{
            const mean = BRENT_DATA.pred_brent_mean ? BRENT_DATA.pred_brent_mean.toFixed(1) : '—';
            const lo   = BRENT_DATA.pred_brent_lower_90 ? BRENT_DATA.pred_brent_lower_90.toFixed(1) : '—';
            const hi   = BRENT_DATA.pred_brent_upper_90 ? BRENT_DATA.pred_brent_upper_90.toFixed(1) : '—';
            const delta = BRENT_DATA.delta_brent_mean;
            const dStr  = delta !== undefined ? (delta >= 0 ? '+' : '') + delta.toFixed(1) : '';
            bc.innerHTML = `
                <h3>Brent Price Signal</h3>
                <div class="brent-box">
                    <div class="brent-label">Conditional Estimate</div>
                    <div class="brent-value">$$${{mean}} / bbl</div>
                    <div class="brent-ci">90% CI: $$${{lo}} – $$${{hi}}</div>
                    ${{dStr ? '<div class="brent-ci" style="color:#f97316;margin-top:3px">Δ vs baseline: ' + dStr + ' $/bbl</div>' : ''}}
                    <div class="brent-ci" style="margin-top:4px;color:#334155;font-size:9px">Conditional association · Ridge regression · Not a forecast</div>
                </div>
            `;
        }} else {{
            bc.innerHTML = `<h3>Brent Price Signal</h3><div style="font-size:11px;color:#334155">Price model data unavailable for this scenario.</div>`;
        }}
    }}

    // ── Pop-up ────────────────────────────────────────────────────────────
    const popup = document.getElementById('popup');
    const popupContent = document.getElementById('popup-content');

    function closePopup() {{
        popup.style.display = 'none';
    }}

    function showEdgePopup(info, x, y) {{
        const e = info.object;
        if (!e) return;
        const removed = REMOVED_EDGES.includes(e.edge_id);
        const badgeClass = e.edge_type === 'chokepoint' ? 'badge-chokepoint' : e.edge_type === 'lane' ? 'badge-lane' : 'badge-connector';
        const utilColor = e.util_pct > 95 ? '#ef4444' : e.util_pct > 70 ? '#f97316' : e.util_pct > 40 ? '#facc15' : '#38bdf8';

        popupContent.innerHTML = `
            <h4>${{e.edge_id}}</h4>
            <span class="popup-badge ${{badgeClass}}">${{e.edge_type.toUpperCase()}}</span>
            ${{e.passage ? '<span class="popup-badge" style="background:rgba(167,139,250,0.15);color:#a78bfa;border:1px solid rgba(167,139,250,0.3);margin-left:4px">' + e.passage + '</span>' : ''}}
            <div class="popup-row"><span class="label">Utilization</span><span class="value" style="color:${{utilColor}}">${{e.util_pct}}%</span></div>
            <div class="popup-row"><span class="label">Tanker Transits</span><span class="value">${{e.transits}} / wk</span></div>
            <div class="popup-row"><span class="label">Corridor Delay</span><span class="value">+${{e.delay_days}} d</span></div>
            <div class="popup-row"><span class="label">Volume</span><span class="value">${{e.volume_mbd}} mb/d</span></div>
            <div class="popup-row"><span class="label">Length</span><span class="value">${{e.length_nm.toLocaleString()}} nm</span></div>
            ${{e.cost_per_bbl ? '<div class="popup-row"><span class="label">Cost / bbl</span><span class="value">$' + e.cost_per_bbl + '</span></div>' : ''}}
            ${{e.anomaly_z ? '<div class="popup-row"><span class="label">Anomaly Z</span><span class="value">' + e.anomaly_z + '</span></div>' : ''}}
            <div class="popup-row"><span class="label">Throttle</span><span class="value">${{(e.throttle * 100).toFixed(0)}}% open</span></div>
            ${{removed
                ? '<button class="btn-remove restore" onclick="restoreEdge(\'' + e.edge_id + '\')">↺ Restore this edge</button>'
                : '<button class="btn-remove" onclick="removeEdge(\'' + e.edge_id + '\')">✕ Remove edge & re-route</button>'
            }}
        `;
        positionPopup(x, y);
    }}

    function showNodePopup(info, x, y) {{
        const n = info.object;
        if (!n) return;
        popupContent.innerHTML = `
            <h4>${{n.name || n.node_id}}</h4>
            <span class="popup-badge badge-node">${{(n.node_type || 'NODE').toUpperCase()}}</span>
            <div class="popup-row"><span class="label">Node ID</span><span class="value">${{n.node_id}}</span></div>
            <div class="popup-row"><span class="label">Type</span><span class="value">${{n.node_type}}</span></div>
            <div class="popup-row"><span class="label">Country</span><span class="value">${{n.country || '—'}}</span></div>
            <div class="popup-row"><span class="label">Coordinates</span><span class="value">${{parseFloat(n.lat).toFixed(2)}}°, ${{parseFloat(n.lon).toFixed(2)}}°</span></div>
        `;
        positionPopup(x, y);
    }}

    function positionPopup(x, y) {{
        popup.style.display = 'block';
        // Keep in-viewport
        const pw = popup.offsetWidth || 260;
        const ph = popup.offsetHeight || 200;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        let px = x + 12;
        let py = y - 20;
        if (px + pw > vw - 10) px = x - pw - 12;
        if (py + ph > vh - 10) py = vh - ph - 10;
        if (py < 10) py = 10;
        popup.style.left = px + 'px';
        popup.style.top  = py + 'px';
    }}

    // ── Edge remove / restore ─────────────────────────────────────────────
    function removeEdge(edgeId) {{
        if (!REMOVED_EDGES.includes(edgeId)) {{
            REMOVED_EDGES.push(edgeId);
        }}
        closePopup();
        notifyStreamlit('remove_edge', edgeId);
    }}

    function restoreEdge(edgeId) {{
        REMOVED_EDGES = REMOVED_EDGES.filter(e => e !== edgeId);
        closePopup();
        notifyStreamlit('restore_edge', edgeId);
    }}

    function clearRemoved() {{
        REMOVED_EDGES = [];
        notifyStreamlit('clear_removed', '');
    }}

    function setModel(mode) {{
        MODEL_MODE = mode;
        document.getElementById('btn-flow').className = mode === 'flow' ? 'active' : '';
        document.getElementById('btn-gnn').className  = mode === 'gnn'  ? 'active' : '';
        notifyStreamlit('set_model', mode);
    }}

    function toggleHormuz() {{
        HORMUZ_MODE = !HORMUZ_MODE;
        const btn = document.getElementById('hormuz-toggle');
        btn.className = 'hormuz-btn' + (HORMUZ_MODE ? ' active' : '');
        const banner = document.getElementById('hormuz-banner');
        banner.style.display = HORMUZ_MODE ? 'flex' : 'none';
        notifyStreamlit('hormuz_mode', HORMUZ_MODE ? 'on' : 'off');
    }}

    // ── Streamlit communication ───────────────────────────────────────────
    function notifyStreamlit(action, value) {{
        // We encode state changes in the URL hash which Streamlit can read via
        // st.query_params in a future turn. For now, we use postMessage to the
        // parent frame, which the Streamlit st.experimental_set_query_params can pick up.
        window.parent.postMessage({{type: 'streamlit:setComponentValue', value: {{action, value}}}}, '*');
    }}

    // ── Hormuz panel ──────────────────────────────────────────────────────
    function renderHormuzPanel() {{
        if (!HORMUZ_DATA || HORMUZ_DATA.length === 0) return;
        const banner = document.getElementById('hormuz-banner');
        if (HORMUZ_MODE) banner.style.display = 'flex';
    }}

    // ── Deck.gl setup ─────────────────────────────────────────────────────
    const {{DeckGL, GlobeView, PathLayer, ScatterplotLayer, TextLayer}} = deck;

    // Build path data from edge records
    const pathData = EDGE_RECORDS.map(e => ({{
        path: e.path,
        color: e.color,
        width: e.width,
        edge_id: e.edge_id,
        edge_type: e.edge_type,
        passage: e.passage,
        util_pct: e.util_pct,
        transits: e.transits,
        delay_days: e.delay_days,
        throttle: e.throttle,
        volume_mbd: e.volume_mbd,
        length_nm: e.length_nm,
        cost_per_bbl: e.cost_per_bbl,
        anomaly_z: e.anomaly_z,
        is_removed: e.is_removed,
    }}));

    const portData    = NODE_RECORDS.filter(n => n.node_type === 'port');
    const junctionData = NODE_RECORDS.filter(n => n.node_type !== 'port');

    const pathLayer = new PathLayer({{
        id: 'edges',
        data: pathData,
        getPath: d => d.path,
        getColor: d => d.color,
        getWidth: d => d.width,
        widthScale: 1,
        widthMinPixels: 1,
        widthMaxPixels: 12,
        capRounded: true,
        jointRounded: true,
        pickable: true,
        onClick: (info, event) => {{
            if (info.object) showEdgePopup(info, event.srcEvent.clientX, event.srcEvent.clientY);
        }},
    }});

    const portLayer = new ScatterplotLayer({{
        id: 'ports',
        data: portData,
        getPosition: d => [d.lon, d.lat],
        getFillColor: [45, 212, 191, 220],
        getRadius: 55000,
        radiusMinPixels: 3,
        radiusMaxPixels: 10,
        pickable: true,
        onClick: (info, event) => {{
            if (info.object) showNodePopup(info, event.srcEvent.clientX, event.srcEvent.clientY);
        }},
    }});

    const junctionLayer = new ScatterplotLayer({{
        id: 'junctions',
        data: junctionData,
        getPosition: d => [d.lon, d.lat],
        getFillColor: [250, 204, 21, 190],
        getRadius: 38000,
        radiusMinPixels: 2,
        radiusMaxPixels: 7,
        pickable: true,
        onClick: (info, event) => {{
            if (info.object) showNodePopup(info, event.srcEvent.clientX, event.srcEvent.clientY);
        }},
    }});

    const deckgl = new DeckGL({{
        container: 'deck-container',
        views: [new GlobeView({{id: 'globe', controller: true}})],
        initialViewState: {{
            longitude: 50.0,
            latitude: 20.0,
            zoom: 1.2,
        }},
        layers: [pathLayer, portLayer, junctionLayer],
        parameters: {{
            clearColor: [0.008, 0.031, 0.09, 1],
        }},
        getTooltip: ({{object, layer}}) => null,  // We use custom pop-up
        onClick: (info, event) => {{
            if (!info.object) closePopup();
        }},
        // Globe background map (optional — use a dark blank tile)
        map: false,
    }});

    // ── Init ──────────────────────────────────────────────────────────────
    renderKPIs();
    renderHormuzPanel();

    // Update removed edge list in left panel
    (function updateRemovedList() {{
        const el = document.getElementById('removed-list');
        const btn = document.getElementById('clear-btn');
        if (REMOVED_EDGES.length === 0) {{
            el.innerHTML = '<em style="color:#334155">None</em>';
            if (btn) btn.style.display = 'none';
        }} else {{
            el.innerHTML = REMOVED_EDGES.map(e =>
                '<div style="display:flex;justify-content:space-between;margin-bottom:3px">' +
                '<span style="color:#ef4444;font-size:11px">' + e + '</span>' +
                '<span onclick="restoreEdge(\'' + e + '\')" style="cursor:pointer;color:#334155;font-size:11px">✕</span>' +
                '</div>'
            ).join('');
            if (btn) btn.style.display = 'block';
        }}
    }})();

    // Hormuz mode init
    if (HORMUZ_MODE) {{
        document.getElementById('hormuz-banner').style.display = 'flex';
    }}
    </script>
    </body>
    </html>
    """)


# ---------------------------------------------------------------------------
# State management via st.session_state
# ---------------------------------------------------------------------------
def init_state() -> None:
    if "removed_edges" not in st.session_state:
        st.session_state.removed_edges = []
    if "model_mode" not in st.session_state:
        st.session_state.model_mode = "flow"
    if "hormuz_mode" not in st.session_state:
        st.session_state.hormuz_mode = False
    if "scenario" not in st.session_state:
        st.session_state.scenario = "2023–24 Red Sea / Houthi Crisis"
    if "alpha" not in st.session_state:
        st.session_state.alpha = 0.18
    if "beta" not in st.session_state:
        st.session_state.beta = 3.8


# ---------------------------------------------------------------------------
# Thin Streamlit control widgets (hidden sidebar used for form submit only)
# ---------------------------------------------------------------------------
def render_controls() -> tuple[dict, float, float]:
    """Render minimal sidebar controls for scenario selection & BPR params."""
    with st.sidebar:
        st.markdown("### ⚙ Blue Vector Controls")
        st.caption("Full-globe view — controls overlay the map")

        st.session_state.scenario = st.selectbox(
            "Scenario",
            list(PRESET_THROTTLES.keys()),
            index=list(PRESET_THROTTLES.keys()).index(st.session_state.scenario),
            key="scenario_select",
        )
        st.session_state.model_mode = st.radio(
            "Model",
            ["flow", "gnn"],
            format_func=lambda x: "Flow Model (BPR)" if x == "flow" else "GNN Prediction",
            index=0 if st.session_state.model_mode == "flow" else 1,
            horizontal=True,
        )
        st.session_state.hormuz_mode = st.toggle(
            "🛢 Hormuz Crisis Mode",
            value=st.session_state.hormuz_mode,
        )

        st.markdown("#### BPR Parameters")
        alpha = st.slider("Alpha", 0.05, 0.35, st.session_state.alpha, 0.01)
        beta = st.slider("Beta", 2.0, 5.0, st.session_state.beta, 0.1)
        st.session_state.alpha = alpha
        st.session_state.beta = beta

        st.markdown("#### Chokepoint Throttles")
        throttles: dict = dict(PRESET_THROTTLES.get(st.session_state.scenario, {}))
        with st.form("throttle_form"):
            for key, label in CHOKEPOINTS:
                default = float(throttles.get(key, 1.0))
                throttles[key] = st.slider(label, 0.0, 1.0, default, 0.05, key=f"th_{key}")
            submitted = st.form_submit_button("⚡ Apply & Update Globe")

        # Apply removed-edge overrides
        if st.session_state.removed_edges:
            edges_df = pd.read_csv(CONFIG_DIR / "edges.csv")
            for eid in st.session_state.removed_edges:
                rows = edges_df[edges_df["edge_id"] == eid]
                if not rows.empty:
                    passage = rows.iloc[0].get("searoute_passage", "")
                    if pd.notna(passage) and passage and passage != "nan":
                        throttles[str(passage)] = 0.0

        return throttles, alpha, beta


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    from app.cloud_data import FILES, restore_bundle

    # Cloud data restoration (same as original)
    try:
        bundle_url = st.secrets.get("DATA_BUNDLE_URL", "")
    except FileNotFoundError:
        bundle_url = ""
    if bundle_url:
        try:
            with st.spinner("Loading verified dashboard data…"):
                restore_bundle(ROOT, bundle_url, st.secrets.get("DATA_BUNDLE_SHA256", ""))
        except Exception:
            st.error("Dashboard data could not be verified.")
            st.stop()
    if any(not (ROOT / name).is_file() for name in FILES):
        st.error(
            "Dashboard data not configured. Supply DATA_BUNDLE_URL and DATA_BUNDLE_SHA256 in Streamlit Secrets."
        )
        st.stop()

    init_state()

    # Sidebar controls (always visible even though sidebar is collapsed by default;
    # users can expand it for fine-grained control)
    throttles, alpha, beta = render_controls()

    # Load base network
    nodes, edges = load_network()

    # Run flow scenario
    try:
        scen_df_json, summary_json = run_flow_scenario(
            throttles_json=json.dumps({k: v for k, v in throttles.items() if v < 1.0}),
            alpha=alpha,
            beta=beta,
            removed_edges_json=json.dumps(st.session_state.removed_edges),
        )
        scen_df = pd.DataFrame(json.loads(scen_df_json))
        summary = json.loads(summary_json)
    except Exception as exc:
        st.error(f"Flow solver error: {exc}")
        st.stop()

    # Merge geometry columns back
    geo_cols = [c for c in ["edge_id", "route_segments", "from_lat", "from_lon", "to_lat", "to_lon"] if c in edges.columns]
    if "edge_id" in scen_df.columns and "edge_id" in edges.columns:
        scen_df = scen_df.merge(edges[geo_cols], on="edge_id", how="left")

    # GNN predictions
    scenario_key = SCENARIO_LABEL_MAP.get(st.session_state.scenario, "baseline")
    gnn_df = None
    if st.session_state.model_mode == "gnn":
        gnn_df = load_gnn_predictions(scenario_key)

    # Price model
    brent_data: dict | None = None
    price_df = load_price_impacts()
    if price_df is not None:
        row = price_df[price_df["week_or_scenario_id"] == scenario_key]
        if row.empty:
            row = price_df[price_df["week_or_scenario_id"] == "baseline"]
        if not row.empty:
            r = row.iloc[0]
            brent_data = {
                "pred_brent_mean": float(r.get("pred_brent_mean", 0)) if pd.notna(r.get("pred_brent_mean")) else None,
                "pred_brent_lower_90": float(r.get("pred_brent_lower_90", 0)) if pd.notna(r.get("pred_brent_lower_90")) else None,
                "pred_brent_upper_90": float(r.get("pred_brent_upper_90", 0)) if pd.notna(r.get("pred_brent_upper_90")) else None,
                "delta_brent_mean": float(r.get("delta_brent_mean", 0)) if pd.notna(r.get("delta_brent_mean")) else None,
            }

    # Hormuz data
    hormuz_records: list | None = None
    if st.session_state.hormuz_mode:
        hormuz_df = load_hormuz_data()
        if hormuz_df is not None:
            # Return only ensemble mean (real_only model) for display
            ensemble = hormuz_df[hormuz_df["model"] == "real_only"][["week_ending", "actual", "prediction", "persistence"]].copy()
            ensemble["week_ending"] = ensemble["week_ending"].astype(str)
            hormuz_records = ensemble.to_dict(orient="records")

    # Build rendering data
    edge_records = build_edge_records(
        edges, scen_df, gnn_df,
        use_gnn=(st.session_state.model_mode == "gnn" and gnn_df is not None),
        removed_edges=st.session_state.removed_edges,
    )
    node_records = build_node_records(nodes)
    kpis = compute_kpis(scen_df, summary)

    # Render full-viewport globe
    html = build_globe_html(
        edge_records=edge_records,
        node_records=node_records,
        kpis=kpis,
        summary=summary,
        brent_data=brent_data,
        hormuz_data=hormuz_records,
        model_mode=st.session_state.model_mode,
        scenario_label=st.session_state.scenario,
        hormuz_mode=st.session_state.hormuz_mode,
        removed_edges=st.session_state.removed_edges,
    )

    # Height: 100vh via CSS but components.html needs an explicit pixel height.
    # We use window.innerHeight via JS to avoid scroll bars.
    components.html(html, height=900, scrolling=False)

    # Handle component messages (edge removal) via query params
    # Streamlit doesn't natively relay JS postMessage, so we use st.query_params
    # as the bridge. The JS sends via postMessage; we also expose sidebar buttons
    # as the reliable control path.
    qp = st.query_params
    action = qp.get("action", "")
    value = qp.get("value", "")

    if action == "remove_edge" and value:
        if value not in st.session_state.removed_edges:
            st.session_state.removed_edges.append(value)
        st.query_params.clear()
        st.rerun()
    elif action == "restore_edge" and value:
        st.session_state.removed_edges = [e for e in st.session_state.removed_edges if e != value]
        st.query_params.clear()
        st.rerun()
    elif action == "clear_removed":
        st.session_state.removed_edges = []
        st.query_params.clear()
        st.rerun()
    elif action == "set_model" and value in ("flow", "gnn"):
        st.session_state.model_mode = value
        st.query_params.clear()
        st.rerun()
    elif action == "hormuz_mode":
        st.session_state.hormuz_mode = (value == "on")
        st.query_params.clear()
        st.rerun()

    # Thin Streamlit fallback controls rendered below the globe for reliability
    st.markdown("---")
    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        if st.button("↺ Restore All Removed Edges", use_container_width=True):
            st.session_state.removed_edges = []
            st.rerun()
    with col_b:
        removed_input = st.text_input(
            "Remove edge by ID (press Enter)",
            placeholder="e.g. E001",
            label_visibility="collapsed",
        )
        if removed_input.strip():
            eid = removed_input.strip()
            if eid not in st.session_state.removed_edges:
                st.session_state.removed_edges.append(eid)
                st.rerun()
    with col_c:
        if st.session_state.removed_edges:
            st.caption(f"Removed: {', '.join(st.session_state.removed_edges)}")
    with col_d:
        st.caption(
            "🛈 Expand the sidebar ← for fine-grained chokepoint throttles and BPR parameters."
        )


if __name__ == "__main__":
    main()
