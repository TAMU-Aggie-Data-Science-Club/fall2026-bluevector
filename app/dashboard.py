"""Blue Vector — Full-Globe Interactive Maritime Disruption Intelligence.

Architecture
------------
* Builds a fully self-contained HTML page using deck.gl 8.9 loaded from CDN.
  Pydeck is used ONLY for route geometry (searoute) — NOT for HTML output.
  This avoids all pydeck iframe/viewport sizing issues.
* The iframe self-resizes by patching window.frameElement.style.height from
  inside the components.html call (same-origin, so this always works).
* Overlay panels are pure HTML/CSS/JS positioned absolutely over the canvas.
* Actions communicate back to Streamlit via URL query params (bv_action=…).

Run:
    .venv/bin/streamlit run app/dashboard.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

import numpy as np
import pandas as pd
import pydeck as pdk          # used only for route geometry via searoute
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

# Strip every pixel of Streamlit chrome so the iframe can go truly full-bleed.
# st.markdown(..., unsafe_allow_html=True) is the correct way to inject
# page-level CSS — unlike st.html(), which creates its own sandboxed micro-frame.
st.markdown(
    """
    <style>
    #MainMenu, header, footer { visibility: hidden; }
    .stApp { background: #020817; }
    .block-container {
        padding: 0 !important;
        max-width: 100% !important;
        margin: 0 !important;
    }
    section[data-testid="stMain"] > div:first-child { padding: 0 !important; }
    div[data-testid="stHorizontalBlock"] { gap: 0 !important; }
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
                cur.append([180.0, lm]); segments.append(cur) if len(cur) >= 2 else None
                cur = [[-180.0, lm], [lon2, lat2]]
            else:
                d1 = lon1 + 180.0; d2 = 180.0 - lon2
                lm = round(lat1 + (lat2 - lat1) * (d1 / max(d1 + d2, 1e-6)), 4)
                cur.append([-180.0, lm]); segments.append(cur) if len(cur) >= 2 else None
                cur = [[180.0, lm], [lon2, lat2]]
    if len(cur) >= 2:
        segments.append(cur)
    return segments


# ---------------------------------------------------------------------------
# Data loading — cached
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
        if sr_mod is not None:
            try:
                feat = sr_mod.searoute(
                    [float(row.from_lon), float(row.from_lat)],
                    [float(row.to_lon),   float(row.to_lat)],
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
                [[float(row.from_lon), float(row.from_lat)],
                 [float(row.to_lon),   float(row.to_lat)]]
            )
        segs_list.append(segs)

    edges["route_segments"] = segs_list
    return nodes, edges


@st.cache_data(show_spinner="Running flow scenario…")
def run_flow_scenario(throttles_json: str, alpha: float, beta: float) -> tuple[str, str]:
    from scripts.flow_model.scenario_engine import run_scenario
    throttles: dict = json.loads(throttles_json)
    df, summary = run_scenario(
        throttles=throttles,
        scenario_id="live_dashboard",
        params_override={"alpha": alpha, "beta": beta},
        save_files=False,
    )
    return df.to_json(orient="records"), json.dumps(summary)


@st.cache_data(show_spinner="Loading GNN predictions…")
def load_gnn_predictions(scenario_key: str) -> pd.DataFrame | None:
    for name in ("gnn_edge_predictions.parquet", "gnn_simulated_scenarios.parquet"):
        path = PROCESSED_DIR / name
        if path.exists():
            try:
                df = pd.read_parquet(path)
                if "scenario_id" in df.columns:
                    sub = df[df["scenario_id"] == scenario_key]
                    if not sub.empty:
                        return sub.reset_index(drop=True)
                return df.reset_index(drop=True)
            except Exception:
                pass
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
def util_to_color(util: float, edge_type: str, throttle: float) -> list[int]:
    u = min(max(float(util), 0.0), 1.5)
    if throttle < 0.01:
        return [220, 38, 38, 255]
    if edge_type == "chokepoint":
        if u > 0.95: return [245, 158, 11, 255]
        if u > 0.70: return [249, 115, 22, 240]
        if u > 0.40: return [250, 204, 21, 220]
        return [56, 189, 248, 230]
    elif edge_type == "lane":
        if u > 0.90: return [239, 68,  68,  210]
        if u > 0.60: return [249, 115, 22,  200]
        if u > 0.35: return [250, 204, 21,  185]
        return [56, 189, 248, 170]
    return [100, 180, 255, 100]


def util_to_width(util: float, edge_type: str) -> float:
    u = min(max(float(util), 0.0), 1.5)
    if edge_type == "chokepoint": return max(5.0, min(9.0, 5.0 + u * 4.0))
    if edge_type == "lane":       return max(1.5, min(4.5, 1.5 + u * 3.0))
    return 0.8


# ---------------------------------------------------------------------------
# Build layer data
# ---------------------------------------------------------------------------
def build_layer_data(
    edges_base:    pd.DataFrame,
    scen_df:       pd.DataFrame,
    gnn_df:        pd.DataFrame | None,
    use_gnn:       bool,
    removed_edges: list[str],
    nodes_df:      pd.DataFrame,
) -> tuple[list[dict], list[dict], list[dict]]:

    geo_cols = [c for c in ["edge_id", "route_segments", "from_lat", "from_lon", "to_lat", "to_lon"]
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
        eid       = str(row.edge_id)
        is_removed = eid in removed_edges
        util      = float(getattr(row, "utilization",     0.0) or 0.0)
        e_type    = str(getattr(row,  "edge_type",        "lane") or "lane")
        passage   = str(getattr(row,  "searoute_passage", "") or "")
        if passage == "nan": passage = ""
        transits  = round(float(getattr(row, "tanker_transits",  0.0) or 0.0), 1)
        delay     = round(float(getattr(row, "delay_days",       0.0) or 0.0), 2)
        throttle  = float(getattr(row,  "throttle_factor",  1.0) or 1.0)
        volume    = round(float(getattr(row, "oil_volume_bbl",   0.0) or 0.0) / 1e6, 2)
        length    = round(float(getattr(row, "length_nm",        0.0) or 0.0), 0)
        cost      = round(float(getattr(row, "cost_per_barrel",  0.0) or 0.0), 2)
        anomaly   = round(float(getattr(row, "anomaly_z",        0.0) or 0.0), 2)

        color = [60, 60, 80, 90] if is_removed else util_to_color(util, e_type, throttle)
        width = 1.0              if is_removed else util_to_width(util, e_type)

        segs = getattr(row, "route_segments", None) or [
            [[float(row.from_lon), float(row.from_lat)],
             [float(row.to_lon),   float(row.to_lat)]]
        ]
        for seg in segs:
            path_rows.append({
                "edge_id":    eid,
                "edge_type":  e_type,
                "passage":    passage,
                "path":       seg,
                "color":      color,
                "width":      width,
                "util_pct":   round(util * 100, 1),
                "transits":   transits,
                "delay_days": delay,
                "throttle":   round(throttle, 2),
                "volume_mbd": volume,
                "length_nm":  int(length),
                "cost_per_bbl": cost,
                "anomaly_z":  anomaly,
                "is_removed": is_removed,
            })

    port_rows = [
        {"node_id": str(r.node_id), "name": str(getattr(r, "name", r.node_id) or r.node_id),
         "node_type": "port", "lat": float(r.lat), "lon": float(r.lon),
         "country": str(getattr(r, "country", "") or "")}
        for r in nodes_df[nodes_df["node_type"] == "port"].itertuples()
    ]
    junction_rows = [
        {"node_id": str(r.node_id), "name": str(getattr(r, "name", r.node_id) or r.node_id),
         "node_type": str(getattr(r, "node_type", "junction") or "junction"),
         "lat": float(r.lat), "lon": float(r.lon),
         "country": str(getattr(r, "country", "") or "")}
        for r in nodes_df[nodes_df["node_type"] != "port"].itertuples()
    ]
    return path_rows, port_rows, junction_rows


# ---------------------------------------------------------------------------
# KPI helpers
# ---------------------------------------------------------------------------
def compute_kpis(scen_df: pd.DataFrame, summary: dict) -> dict:
    rerouted_mbd = 0.0
    if "delta_volume_bbl" in scen_df.columns:
        rerouted_mbd = float(
            scen_df.loc[scen_df["delta_volume_bbl"] > 0, "delta_volume_bbl"].sum() / 7.0 / 1e6
        )
    stranded_mbd  = float(summary.get("stranded_barrels",  0.0) / 7.0 / 1e6)
    cp_transits   = 0.0
    if "tanker_transits" in scen_df.columns and "edge_type" in scen_df.columns:
        cp_transits = float(scen_df.loc[scen_df["edge_type"] == "chokepoint", "tanker_transits"].sum())
    extra_days    = float(summary.get("extra_voyage_days",  0.0))
    suez_share    = float(summary.get("suez_tanker_share",  0.0)) * 100.0
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

PRESET_THROTTLES: dict[str, dict] = {
    "Pre-Crisis Baseline (All Open)":          {},
    "2023–24 Red Sea / Houthi Crisis":         {"suez": 0.35, "babalmandab": 0.35},
    "2021 Ever Given Suez Blockage":           {"suez": 0.15},
    "2023–24 Panama Canal Drought":            {"panama": 0.65},
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

LEGEND_ITEMS = [
    ("Low utilisation (<40%)",       "#38bdf8"),
    ("Moderate (40–70%)",            "#facc15"),
    ("High (70–95%)",                "#f97316"),
    ("Congested / Throttled (>95%)", "#ef4444"),
    ("Closed / Removed",             "#3c3c50"),
]


# ---------------------------------------------------------------------------
# Globe HTML builder — pure deck.gl 8.9 from CDN, no pydeck HTML output
# ---------------------------------------------------------------------------
def build_globe_html(
    path_rows:      list[dict],
    port_rows:      list[dict],
    junction_rows:  list[dict],
    kpis:           dict,
    brent_data:     dict | None,
    hormuz_records: list | None,
    model_mode:     str,
    scenario_label: str,
    hormuz_mode:    bool,
    removed_edges:  list[str],
) -> str:

    kpi_json     = json.dumps(kpis)
    brent_json   = json.dumps(brent_data or {})
    removed_json = json.dumps(removed_edges)
    # Flatten paths for deck.gl: each path_row becomes one entry
    path_json = json.dumps(path_rows)
    port_json = json.dumps(port_rows)
    junc_json = json.dumps(junction_rows)

    model_label      = "GNN Prediction" if model_mode == "gnn" else "Flow Model (BPR)"
    hz_active_js     = "true"  if hormuz_mode else "false"
    hz_btn_cls       = "active" if hormuz_mode else ""
    flow_active_cls  = "active"     if model_mode == "flow" else ""
    gnn_active_cls   = "active-gnn" if model_mode == "gnn"  else ""

    legend_html = "".join(
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
        f'<div style="width:28px;height:3px;border-radius:2px;background:{c}"></div>'
        f'<span style="font-size:11px;color:#94a3b8">{label}</span></div>'
        for label, c in LEGEND_ITEMS
    )

    hz_rows_html = ""
    if hormuz_records:
        for rec in hormuz_records[-8:]:
            we  = str(rec.get("week_ending", ""))[:10]
            act = rec.get("actual",     "")
            prd = rec.get("prediction", "")
            try:
                hz_rows_html += (
                    f'<div class="hz-row">'
                    f'<span class="hz-week">{we}</span>'
                    f'<span class="hz-val">{float(act):.1f}<small> act</small></span>'
                    f'<span class="hz-val hz-pred">{float(prd):.1f}<small> pred</small></span>'
                    f'</div>'
                )
            except (TypeError, ValueError):
                pass

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{
  width: 100%; height: 100%;
  overflow: hidden;
  background: #020817;
  font-family: 'Inter', system-ui, sans-serif;
}}
#deck-container {{
  position: absolute;
  top: 0; left: 0;
  width: 100%; height: 100%;
}}
#deck-container canvas {{
  outline: none;
}}

/* ── Panel base ──────────────────────────────────────────────────── */
.bv-panel {{
  position: fixed;
  background: rgba(2, 8, 23, 0.86);
  backdrop-filter: blur(20px) saturate(1.4);
  -webkit-backdrop-filter: blur(20px) saturate(1.4);
  border: 1px solid rgba(56, 189, 248, 0.15);
  border-radius: 16px;
  color: #e2e8f0;
  padding: 15px 14px;
  z-index: 100;
  box-shadow: 0 8px 40px rgba(0,0,0,0.6), inset 0 0 0 1px rgba(56,189,248,0.04);
}}
.bv-panel h2 {{
  font-size: 10px; font-weight: 900; letter-spacing: 0.12em;
  text-transform: uppercase; color: #38bdf8;
  margin: 0 0 13px; display: flex; align-items: center; gap: 7px;
}}
.bv-panel h3 {{
  font-size: 9px; font-weight: 800; letter-spacing: 0.1em;
  text-transform: uppercase; color: #334155; margin: 13px 0 7px;
}}

/* ── Top bar ─────────────────────────────────────────────────────── */
#bv-topbar {{
  top: 14px; left: 50%; transform: translateX(-50%);
  display: flex; align-items: center; gap: 10px;
  padding: 10px 20px; border-radius: 40px; white-space: nowrap;
}}
.bv-brand {{ font-size: 15px; font-weight: 900; letter-spacing: 0.06em; color: #f8fafc; }}
.bv-brand em {{ color: #38bdf8; font-style: normal; }}
.bv-sep {{ width: 1px; height: 18px; background: rgba(56,189,248,0.2); flex-shrink: 0; }}
.bv-badge {{
  font-size: 10px; font-weight: 800; letter-spacing: 0.04em;
  border-radius: 20px; padding: 3px 11px; white-space: nowrap;
}}
.bv-badge-blue   {{ background:rgba(56,189,248,0.12); border:1px solid rgba(56,189,248,0.25); color:#38bdf8; }}
.bv-badge-purple {{ background:rgba(167,139,250,0.12); border:1px solid rgba(167,139,250,0.25); color:#a78bfa; }}
.bv-hz-btn {{
  padding: 5px 13px; border-radius: 20px;
  border: 1px solid rgba(239,68,68,0.35); background: rgba(239,68,68,0.07);
  color: #ef4444; font-size: 10px; font-weight: 800;
  cursor: pointer; transition: all 0.15s; letter-spacing: 0.04em;
  font-family: inherit;
}}
.bv-hz-btn.active {{ background: rgba(239,68,68,0.22); border-color: rgba(239,68,68,0.6); }}

/* ── Left panel ──────────────────────────────────────────────────── */
#bv-left {{
  top: 70px; left: 14px; width: 236px;
  max-height: calc(100vh - 90px); overflow-y: auto; overflow-x: hidden;
}}
#bv-left::-webkit-scrollbar {{ width: 3px; }}
#bv-left::-webkit-scrollbar-thumb {{ background: rgba(56,189,248,0.3); border-radius: 2px; }}

/* ── Right panel ─────────────────────────────────────────────────── */
#bv-right {{
  top: 70px; right: 14px; width: 220px;
  max-height: calc(100vh - 90px); overflow-y: auto; overflow-x: hidden;
}}
#bv-right::-webkit-scrollbar {{ width: 3px; }}
#bv-right::-webkit-scrollbar-thumb {{ background: rgba(56,189,248,0.3); border-radius: 2px; }}

/* ── Model toggle ────────────────────────────────────────────────── */
.bv-toggle {{
  display: flex; border: 1px solid rgba(56,189,248,0.18);
  border-radius: 9px; overflow: hidden; margin-bottom: 12px;
  background: rgba(0,0,0,0.3);
}}
.bv-toggle button {{
  flex: 1; padding: 7px 4px; font-size: 10px; font-weight: 800;
  letter-spacing: 0.04em; border: none; background: transparent;
  color: #334155; cursor: pointer; transition: all 0.15s; font-family: inherit;
}}
.bv-toggle button.active     {{ background: rgba(56,189,248,0.18);  color: #38bdf8; }}
.bv-toggle button.active-gnn {{ background: rgba(167,139,250,0.18); color: #a78bfa; }}

/* ── KPI cards ───────────────────────────────────────────────────── */
.kpi {{
  background: rgba(56,189,248,0.04); border: 1px solid rgba(56,189,248,0.1);
  border-radius: 10px; padding: 9px 11px; margin-bottom: 7px;
}}
.kpi-label {{ font-size: 9px; font-weight: 700; color: #475569; letter-spacing: 0.08em; text-transform: uppercase; }}
.kpi-value {{ font-size: 20px; font-weight: 900; color: #f1f5f9; margin-top: 2px; line-height: 1; }}
.kpi-sub   {{ font-size: 10px; color: #64748b; margin-top: 2px; }}
.kpi-warn  {{ color: #f97316 !important; }}
.kpi-danger{{ color: #ef4444 !important; }}
.brent-box {{
  background: rgba(250,204,21,0.06); border: 1px solid rgba(250,204,21,0.18);
  border-radius: 10px; padding: 10px 11px; margin-top: 6px;
}}
.brent-label {{ font-size: 9px; font-weight: 700; color: #64748b; letter-spacing: 0.08em; text-transform: uppercase; }}
.brent-value {{ font-size: 19px; font-weight: 900; color: #facc15; margin-top: 2px; }}
.brent-ci    {{ font-size: 10px; color: #64748b; margin-top: 2px; }}

/* ── Popup ───────────────────────────────────────────────────────── */
#bv-popup {{
  position: fixed; display: none;
  background: rgba(2, 8, 23, 0.97); backdrop-filter: blur(24px);
  border: 1px solid rgba(56,189,248,0.3); border-radius: 14px;
  color: #e2e8f0; padding: 16px 17px;
  min-width: 220px; max-width: 280px;
  z-index: 200; box-shadow: 0 20px 56px rgba(0,0,0,0.7);
}}
#bv-popup h4 {{
  font-size: 13px; font-weight: 800; color: #38bdf8;
  margin: 0 0 11px; padding-bottom: 10px;
  border-bottom: 1px solid rgba(56,189,248,0.14);
}}
.pop-row   {{ display:flex; justify-content:space-between; align-items:center; margin:5px 0; font-size:11px; }}
.pop-label {{ color:#64748b; }}
.pop-val   {{ color:#f1f5f9; font-weight:700; }}
.pop-badge {{
  display:inline-block; font-size:9px; font-weight:800;
  letter-spacing:0.06em; padding:2px 9px; border-radius:20px; margin-bottom:9px;
}}
.pb-choke {{ background:rgba(239,68,68,0.18);   color:#ef4444; border:1px solid rgba(239,68,68,0.3); }}
.pb-lane  {{ background:rgba(56,189,248,0.18);  color:#38bdf8; border:1px solid rgba(56,189,248,0.3); }}
.pb-conn  {{ background:rgba(100,116,139,0.18); color:#94a3b8; border:1px solid rgba(100,116,139,0.3); }}
.pb-node  {{ background:rgba(45,212,191,0.18);  color:#2dd4bf; border:1px solid rgba(45,212,191,0.3); }}
.btn-rm {{
  margin-top: 12px; width: 100%; padding: 8px;
  border-radius: 9px; border: 1px solid rgba(239,68,68,0.45);
  background: rgba(239,68,68,0.1); color: #ef4444;
  font-size: 10px; font-weight: 800; letter-spacing: 0.05em;
  cursor: pointer; transition: background 0.15s; font-family: inherit;
}}
.btn-rm:hover {{ background: rgba(239,68,68,0.22); }}
.btn-restore {{ border-color:rgba(56,189,248,0.45); background:rgba(56,189,248,0.1); color:#38bdf8; }}
.btn-restore:hover {{ background: rgba(56,189,248,0.22); }}
.pop-close {{
  position:absolute; top:12px; right:14px;
  background:none; border:none; color:#475569;
  font-size:16px; cursor:pointer; line-height:1; font-family:inherit;
}}
.pop-close:hover {{ color:#e2e8f0; }}

/* ── Hormuz banner ───────────────────────────────────────────────── */
#bv-hz-banner {{
  display: none; position: fixed;
  bottom: 70px; left: 50%; transform: translateX(-50%);
  background: rgba(239,68,68,0.14); border: 1px solid rgba(239,68,68,0.4);
  border-radius: 10px; padding: 9px 22px; color: #ef4444;
  font-size: 11px; font-weight: 800; letter-spacing: 0.05em;
  z-index: 100; align-items: center; gap: 8px;
  white-space: nowrap; backdrop-filter: blur(12px);
}}

/* ── Hormuz crisis panel ─────────────────────────────────────────── */
#bv-hz-panel {{
  display: none; position: fixed;
  bottom: 100px; right: 14px; width: 220px;
  background: rgba(2,8,23,0.9); backdrop-filter: blur(20px);
  border: 1px solid rgba(239,68,68,0.3); border-radius: 14px;
  padding: 13px 14px; z-index: 100;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}}
#bv-hz-panel h4 {{
  font-size: 10px; font-weight: 900; letter-spacing: 0.1em;
  text-transform: uppercase; color: #ef4444; margin-bottom: 10px;
}}
.hz-row  {{ display:flex; align-items:center; gap:4px; margin-bottom:5px; font-size:10px; }}
.hz-week {{ color:#475569; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.hz-val  {{ color:#f1f5f9; font-weight:700; }}
.hz-val small {{ color:#64748b; font-weight:400; }}
.hz-pred {{ color:#a78bfa !important; }}

/* ── Legend ──────────────────────────────────────────────────────── */
#bv-legend {{
  position: fixed; bottom: 14px; left: 14px;
  background: rgba(2,8,23,0.86); backdrop-filter: blur(18px);
  border: 1px solid rgba(56,189,248,0.14);
  border-radius: 13px; padding: 12px 15px; z-index: 100;
}}
#bv-legend h3 {{
  font-size: 9px; font-weight: 800; color: #334155;
  letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 8px;
}}

/* ── Removed list ────────────────────────────────────────────────── */
#bv-removed-list {{ min-height: 16px; }}
.rm-item {{
  display:flex; justify-content:space-between; align-items:center;
  margin-bottom:4px; font-size:11px;
}}
.rm-item span {{ color:#ef4444; }}
.rm-item button {{
  background:none; border:none; color:#334155; cursor:pointer;
  font-size:12px; line-height:1; padding:0 2px; font-family:inherit;
}}
.rm-item button:hover {{ color:#e2e8f0; }}
#bv-clear-btn {{
  margin-top:7px; width:100%; padding:6px;
  border-radius:8px; border:1px solid rgba(100,116,139,0.3);
  background:rgba(100,116,139,0.1); color:#64748b;
  font-size:10px; font-weight:700; cursor:pointer; font-family:inherit;
}}
.bv-hint {{ font-size:10px; color:#334155; line-height:1.7; }}
.bv-hint b {{ color:#38bdf8; }}
.bv-hint b.node {{ color:#2dd4bf; }}
</style>
</head>
<body>

<!-- deck.gl canvas container -->
<div id="deck-container"></div>

<!-- Top bar -->
<div class="bv-panel" id="bv-topbar">
  <div class="bv-brand"><em>BLUE</em>VECTOR&nbsp;🌐</div>
  <div class="bv-sep"></div>
  <span class="bv-badge bv-badge-blue" id="bv-scenario-badge">{scenario_label[:36]}</span>
  <span class="bv-badge bv-badge-purple" id="bv-model-badge">{model_label}</span>
  <button class="bv-hz-btn {hz_btn_cls}" id="bv-hz-btn" onclick="bvToggleHormuz()">
    🛢&nbsp;Hormuz Crisis 2026
  </button>
</div>

<!-- Left panel -->
<div class="bv-panel" id="bv-left">
  <h2>⚙&nbsp; Model Controls</h2>
  <div class="bv-toggle">
    <button id="bv-btn-flow" class="{flow_active_cls}" onclick="bvSetModel('flow')">Flow Model</button>
    <button id="bv-btn-gnn"  class="{gnn_active_cls}"  onclick="bvSetModel('gnn')">GNN</button>
  </div>
  <h3>Congestion Scale</h3>
  {legend_html}
  <h3>Removed Edges</h3>
  <div id="bv-removed-list"></div>
  <button id="bv-clear-btn" onclick="bvClearRemoved()" style="display:none">↺ Restore All</button>
  <h3>Interaction</h3>
  <div class="bv-hint">
    <b>Click edge</b> → inspect &amp; remove<br>
    <b class="node">Click node</b> → port attributes<br>
    Drag → rotate globe&nbsp;·&nbsp;Scroll → zoom
  </div>
</div>

<!-- Right panel -->
<div class="bv-panel" id="bv-right">
  <h2>📊&nbsp; Live Metrics</h2>
  <div id="bv-kpis"></div>
  <div id="bv-brent"></div>
</div>

<!-- Popup -->
<div id="bv-popup">
  <button class="pop-close" onclick="bvClosePopup()">✕</button>
  <div id="bv-popup-content"></div>
</div>

<!-- Hormuz banner -->
<div id="bv-hz-banner">🚨&nbsp; HORMUZ CRISIS MODE ACTIVE — 2026 Transfer Analysis</div>

<!-- Hormuz panel -->
<div id="bv-hz-panel">
  <h4>🛢 Hormuz Weekly Predictions</h4>
  {hz_rows_html or '<div class="bv-hint">No prediction data available.</div>'}
</div>

<!-- Legend -->
<div id="bv-legend">
  <h3>Edge Utilisation</h3>
  {legend_html}
</div>

<!-- deck.gl 8.9 — full bundle with GlobeView, PathLayer, ScatterplotLayer -->
<script src="https://cdn.jsdelivr.net/npm/deck.gl@8.9/dist.min.js"></script>

<script>
// ── Self-resize: make this iframe fill the parent viewport ─────────────────
// components.html() is same-origin, so window.frameElement is accessible.
(function() {{
  function resize() {{
    try {{
      const fe = window.frameElement;
      if (fe) {{
        fe.style.height = window.parent.innerHeight + 'px';
        fe.style.width  = '100%';
        fe.style.display = 'block';
        fe.scrolling = 'no';
      }}
    }} catch(e) {{}}
  }}
  resize();
  try {{ window.parent.addEventListener('resize', resize); }} catch(e) {{}}
}})();

// ── Data ───────────────────────────────────────────────────────────────────
const PATH_DATA     = {path_json};
const PORT_DATA     = {port_json};
const JUNCTION_DATA = {junc_json};
const KPI           = {kpi_json};
const BRENT         = {brent_json};
let   REMOVED       = {removed_json};
let   MODEL         = "{model_mode}";
let   HORMUZ_ON     = {hz_active_js};

// ── deck.gl init ───────────────────────────────────────────────────────────
const {{DeckGL, GlobeView, PathLayer, ScatterplotLayer}} = deck;

const deckgl = new DeckGL({{
  container: 'deck-container',
  views: new GlobeView({{
    id: 'globe',
    controller: {{
      dragRotate: true,
      scrollZoom: true,
      touchRotate: true,
      doubleClickZoom: true,
    }},
  }}),
  initialViewState: {{
    longitude: 48.0,
    latitude:  18.0,
    zoom:      1.3,
  }},
  parameters: {{
    depthTest: false,
    clearColor: [0.011, 0.027, 0.063, 1],
  }},
  layers: buildLayers(),
  onClick: handleClick,
  getCursor: ({{isDragging}}) => isDragging ? 'grabbing' : 'crosshair',
}});

function buildLayers() {{
  return [
    new PathLayer({{
      id: 'edges',
      data: PATH_DATA,
      getPath:  d => d.path,
      getColor: d => d.color,
      getWidth: d => d.width,
      widthScale: 1,
      widthMinPixels: 1,
      widthMaxPixels: 14,
      capRounded: true,
      jointRounded: true,
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 255, 255, 50],
    }}),
    new ScatterplotLayer({{
      id: 'ports',
      data: PORT_DATA,
      getPosition: d => [d.lon, d.lat],
      getFillColor: [45, 212, 191, 220],
      getRadius: 55000,
      radiusMinPixels: 4,
      radiusMaxPixels: 11,
      pickable: true,
      autoHighlight: true,
      highlightColor: [45, 212, 191, 80],
    }}),
    new ScatterplotLayer({{
      id: 'junctions',
      data: JUNCTION_DATA,
      getPosition: d => [d.lon, d.lat],
      getFillColor: [250, 204, 21, 190],
      getRadius: 38000,
      radiusMinPixels: 3,
      radiusMaxPixels: 8,
      pickable: true,
      autoHighlight: true,
      highlightColor: [250, 204, 21, 60],
    }}),
  ];
}}

// ── Click handling ─────────────────────────────────────────────────────────
function handleClick(info, event) {{
  if (!info || !info.object) {{ bvClosePopup(); return; }}
  const src = event && event.srcEvent ? event.srcEvent : event;
  const px  = src ? (src.clientX || 0) : 0;
  const py  = src ? (src.clientY || 0) : 0;
  if (info.layer && info.layer.id === 'edges') {{
    bvShowEdgePopup(info.object, px, py);
  }} else {{
    bvShowNodePopup(info.object, px, py);
  }}
}}

// ── KPI rendering ──────────────────────────────────────────────────────────
function bvRenderKPIs() {{
  const c = document.getElementById('bv-kpis');
  const warn = v => v > 3 ? 'kpi-danger' : v > 1 ? 'kpi-warn' : '';
  c.innerHTML = `
    <div class="kpi"><div class="kpi-label">Chokepoint Crossings</div>
      <div class="kpi-value">${{KPI.cp_transits.toLocaleString()}}</div>
      <div class="kpi-sub">tanker transits / week</div></div>
    <div class="kpi"><div class="kpi-label">Rerouted Flow</div>
      <div class="kpi-value">${{KPI.rerouted_mbd}}</div>
      <div class="kpi-sub">mb/d added corridor traffic</div></div>
    <div class="kpi"><div class="kpi-label">Extra Delay</div>
      <div class="kpi-value ${{warn(KPI.extra_days)}}">${{KPI.extra_days >= 0 ? '+' : ''}}<span style="font-size:14px">${{KPI.extra_days}}</span> d</div>
      <div class="kpi-sub">Cape detour + BPR queue</div></div>
    <div class="kpi"><div class="kpi-label">Suez / (Suez+Cape) Share</div>
      <div class="kpi-value">${{KPI.suez_share_pct}}<span style="font-size:12px">%</span></div>
      <div class="kpi-sub">Europe–Asia routing split</div></div>
    <div class="kpi"><div class="kpi-label">Stranded Volume</div>
      <div class="kpi-value ${{KPI.stranded_mbd > 0.5 ? 'kpi-danger' : ''}}">${{KPI.stranded_mbd}}</div>
      <div class="kpi-sub">mb/d unserved demand</div></div>
  `;
  const bc = document.getElementById('bv-brent');
  if (BRENT && BRENT.pred_brent_mean != null) {{
    const mean = parseFloat(BRENT.pred_brent_mean).toFixed(1);
    const lo   = parseFloat(BRENT.pred_brent_lower_90).toFixed(1);
    const hi   = parseFloat(BRENT.pred_brent_upper_90).toFixed(1);
    const d    = BRENT.delta_brent_mean;
    const ds   = d != null ? (d >= 0 ? '+' : '') + parseFloat(d).toFixed(1) : null;
    bc.innerHTML = `<h3>Brent Signal</h3>
      <div class="brent-box">
        <div class="brent-label">Conditional Estimate</div>
        <div class="brent-value">$${{mean}} / bbl</div>
        <div class="brent-ci">90% CI: $${{lo}} – $${{hi}}</div>
        ${{ds ? '<div class="brent-ci" style="color:#f97316;margin-top:3px">Δ baseline: ' + ds + ' $/bbl</div>' : ''}}
        <div class="brent-ci" style="color:#334155;font-size:9px;margin-top:4px">Ridge regression · conditional assoc.</div>
      </div>`;
  }} else {{
    bc.innerHTML = '<h3>Brent Signal</h3><div class="bv-hint">Price data unavailable.</div>';
  }}
}}

// ── Removed edge list ──────────────────────────────────────────────────────
function bvRenderRemoved() {{
  const el  = document.getElementById('bv-removed-list');
  const btn = document.getElementById('bv-clear-btn');
  if (!REMOVED.length) {{
    el.innerHTML = '<span class="bv-hint" style="font-style:italic">None</span>';
    btn.style.display = 'none';
  }} else {{
    el.innerHTML = REMOVED.map(e =>
      `<div class="rm-item"><span>${{e}}</span>` +
      `<button onclick="bvRestoreEdge('${{e}}')" title="Restore">✕</button></div>`
    ).join('');
    btn.style.display = 'block';
  }}
}}

// ── Hormuz UI ──────────────────────────────────────────────────────────────
function bvApplyHormuzUI() {{
  document.getElementById('bv-hz-btn').className     = 'bv-hz-btn' + (HORMUZ_ON ? ' active' : '');
  document.getElementById('bv-hz-banner').style.display = HORMUZ_ON ? 'flex' : 'none';
  document.getElementById('bv-hz-panel').style.display  = HORMUZ_ON ? 'block' : 'none';
}}

// ── Popup ──────────────────────────────────────────────────────────────────
function bvClosePopup() {{
  document.getElementById('bv-popup').style.display = 'none';
}}
function bvShowEdgePopup(obj, px, py) {{
  const removed = REMOVED.includes(obj.edge_id);
  const bc = obj.edge_type === 'chokepoint' ? 'pb-choke' : obj.edge_type === 'lane' ? 'pb-lane' : 'pb-conn';
  const uc = obj.util_pct > 95 ? '#ef4444' : obj.util_pct > 70 ? '#f97316' : obj.util_pct > 40 ? '#facc15' : '#38bdf8';
  document.getElementById('bv-popup-content').innerHTML = `
    <h4>${{obj.edge_id}}</h4>
    <span class="pop-badge ${{bc}}">${{(obj.edge_type||'EDGE').toUpperCase()}}</span>
    ${{obj.passage ? ' <span class="pop-badge pb-lane" style="background:rgba(167,139,250,0.15);color:#a78bfa;border-color:rgba(167,139,250,0.3)">' + obj.passage + '</span>' : ''}}
    <div class="pop-row"><span class="pop-label">Utilization</span><span class="pop-val" style="color:${{uc}}">${{obj.util_pct}}%</span></div>
    <div class="pop-row"><span class="pop-label">Transits / wk</span><span class="pop-val">${{obj.transits}}</span></div>
    <div class="pop-row"><span class="pop-label">Corridor Delay</span><span class="pop-val">+${{obj.delay_days}} d</span></div>
    <div class="pop-row"><span class="pop-label">Volume</span><span class="pop-val">${{obj.volume_mbd}} mb/d</span></div>
    <div class="pop-row"><span class="pop-label">Length</span><span class="pop-val">${{(obj.length_nm||0).toLocaleString()}} nm</span></div>
    ${{obj.cost_per_bbl ? '<div class="pop-row"><span class="pop-label">Cost / bbl</span><span class="pop-val">$' + obj.cost_per_bbl + '</span></div>' : ''}}
    <div class="pop-row"><span class="pop-label">Throttle</span><span class="pop-val">${{(obj.throttle*100).toFixed(0)}}% open</span></div>
    ${{obj.anomaly_z ? '<div class="pop-row"><span class="pop-label">Anomaly Z</span><span class="pop-val">' + obj.anomaly_z + '</span></div>' : ''}}
    ${{removed
      ? '<button class="btn-rm btn-restore" onclick="bvRestoreEdge(\\'' + obj.edge_id + '\\')">↺ Restore this edge</button>'
      : '<button class="btn-rm" onclick="bvRemoveEdge(\\'' + obj.edge_id + '\\')">✕ Remove edge &amp; re-route</button>'
    }}
  `;
  bvPositionPopup(px, py);
}}
function bvShowNodePopup(obj, px, py) {{
  document.getElementById('bv-popup-content').innerHTML = `
    <h4>${{obj.name || obj.node_id}}</h4>
    <span class="pop-badge pb-node">${{(obj.node_type||'NODE').toUpperCase()}}</span>
    <div class="pop-row"><span class="pop-label">Node ID</span><span class="pop-val">${{obj.node_id}}</span></div>
    <div class="pop-row"><span class="pop-label">Country</span><span class="pop-val">${{obj.country || '—'}}</span></div>
    <div class="pop-row"><span class="pop-label">Latitude</span><span class="pop-val">${{parseFloat(obj.lat).toFixed(2)}}°</span></div>
    <div class="pop-row"><span class="pop-label">Longitude</span><span class="pop-val">${{parseFloat(obj.lon).toFixed(2)}}°</span></div>
  `;
  bvPositionPopup(px, py);
}}
function bvPositionPopup(px, py) {{
  const popup = document.getElementById('bv-popup');
  popup.style.display = 'block';
  requestAnimationFrame(() => {{
    const pw = popup.offsetWidth  || 260;
    const ph = popup.offsetHeight || 220;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let x = px + 16, y = py - 24;
    if (x + pw > vw - 10) x = px - pw - 16;
    if (y + ph > vh - 10) y = vh - ph - 10;
    if (y < 10) y = 10;
    popup.style.left = x + 'px';
    popup.style.top  = y + 'px';
  }});
}}

// ── URL navigation (Streamlit query params) ────────────────────────────────
function bvNavigate(params) {{
  try {{
    const url = new URL(window.parent.location.href);
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    window.parent.location.href = url.toString();
  }} catch(e) {{
    const url = new URL(window.location.href);
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    window.location.href = url.toString();
  }}
}}
function bvRemoveEdge(eid) {{
  if (!REMOVED.includes(eid)) REMOVED.push(eid);
  bvClosePopup();
  bvNavigate({{bv_action: 'remove_edge', bv_value: eid}});
}}
function bvRestoreEdge(eid) {{
  REMOVED = REMOVED.filter(e => e !== eid);
  bvClosePopup();
  bvNavigate({{bv_action: 'restore_edge', bv_value: eid}});
}}
function bvClearRemoved() {{
  REMOVED = [];
  bvNavigate({{bv_action: 'clear_removed', bv_value: '1'}});
}}
function bvSetModel(mode) {{
  MODEL = mode;
  document.getElementById('bv-btn-flow').className = mode === 'flow' ? 'active' : '';
  document.getElementById('bv-btn-gnn').className  = mode === 'gnn'  ? 'active-gnn' : '';
  bvNavigate({{bv_action: 'set_model', bv_value: mode}});
}}
function bvToggleHormuz() {{
  HORMUZ_ON = !HORMUZ_ON;
  bvApplyHormuzUI();
  bvNavigate({{bv_action: 'hormuz_mode', bv_value: HORMUZ_ON ? 'on' : 'off'}});
}}

// ── Init ───────────────────────────────────────────────────────────────────
bvRenderKPIs();
bvRenderRemoved();
bvApplyHormuzUI();
document.addEventListener('click', e => {{
  if (!e.target.closest('#bv-popup') && !e.target.closest('#deck-container canvas')) {{
    bvClosePopup();
  }}
}});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def init_state() -> None:
    for k, v in {
        "removed_edges": [],
        "model_mode":    "flow",
        "hormuz_mode":   False,
        "scenario":      "2023–24 Red Sea / Houthi Crisis",
        "alpha":         0.18,
        "beta":          3.8,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar() -> dict:
    with st.sidebar:
        st.markdown("### ⚓ Blue Vector Controls")
        st.caption("Fine-grained controls — the globe overlays are interactive too.")

        st.session_state.scenario = st.selectbox(
            "Scenario", list(PRESET_THROTTLES.keys()),
            index=list(PRESET_THROTTLES.keys()).index(st.session_state.scenario),
        )
        new_model = st.radio(
            "Model", ["flow", "gnn"],
            format_func=lambda x: "Flow Model (BPR)" if x == "flow" else "GNN Prediction",
            index=0 if st.session_state.model_mode == "flow" else 1,
            horizontal=True,
        )
        if new_model:
            st.session_state.model_mode = new_model

        st.session_state.hormuz_mode = st.toggle(
            "🛢 Hormuz Crisis Mode", value=st.session_state.hormuz_mode,
        )
        st.markdown("#### BPR Parameters")
        st.session_state.alpha = st.slider("Alpha", 0.05, 0.35, float(st.session_state.alpha), 0.01)
        st.session_state.beta  = st.slider("Beta",  2.0,  5.0,  float(st.session_state.beta),  0.1)

        st.markdown("#### Chokepoint Throttles")
        throttles: dict = dict(PRESET_THROTTLES.get(st.session_state.scenario, {}))
        with st.form("throttle_form"):
            for key, label in CHOKEPOINTS:
                default = float(throttles.get(key, 1.0))
                throttles[key] = st.slider(label, 0.0, 1.0, default, 0.05, key=f"th_{key}")
            st.form_submit_button("⚡ Apply & Update Globe", use_container_width=True)

        if st.session_state.removed_edges:
            try:
                edges_df = pd.read_csv(CONFIG_DIR / "edges.csv")
                for eid in st.session_state.removed_edges:
                    rows = edges_df[edges_df["edge_id"] == eid]
                    if not rows.empty:
                        passage = str(rows.iloc[0].get("searoute_passage", ""))
                        if passage and passage != "nan":
                            throttles[passage] = 0.0
            except Exception:
                pass

    return throttles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # Optional cloud data gate — skip gracefully in dev (no secrets.toml)
    try:
        from app.cloud_data import FILES, restore_bundle
        try:
            bundle_url = st.secrets.get("DATA_BUNDLE_URL", "")
        except (FileNotFoundError, KeyError):
            bundle_url = ""
        if bundle_url:
            with st.spinner("Loading verified dashboard data…"):
                try:
                    restore_bundle(ROOT, bundle_url, st.secrets.get("DATA_BUNDLE_SHA256", ""))
                except Exception as exc:
                    st.error(f"Dashboard data could not be verified: {exc}")
                    st.stop()
            if any(not (ROOT / name).is_file() for name in FILES):
                st.error("Dashboard data not configured. Supply DATA_BUNDLE_URL / DATA_BUNDLE_SHA256 in Streamlit Secrets.")
                st.stop()
    except ImportError:
        pass

    init_state()

    # Handle query param actions from globe button clicks
    qp     = st.query_params
    action = qp.get("bv_action", "")
    value  = qp.get("bv_value",  "")
    if action:
        if action == "remove_edge" and value:
            if value not in st.session_state.removed_edges:
                st.session_state.removed_edges.append(value)
        elif action == "restore_edge" and value:
            st.session_state.removed_edges = [
                e for e in st.session_state.removed_edges if e != value
            ]
        elif action == "clear_removed":
            st.session_state.removed_edges = []
        elif action == "set_model" and value in ("flow", "gnn"):
            st.session_state.model_mode = value
        elif action == "hormuz_mode":
            st.session_state.hormuz_mode = (value == "on")
        st.query_params.clear()
        st.rerun()

    throttles = render_sidebar()

    # Load network
    nodes, edges = load_network()

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
    geo_cols = [c for c in ["edge_id", "route_segments", "from_lat", "from_lon", "to_lat", "to_lon"]
                if c in edges.columns]
    if "edge_id" in scen_df.columns and geo_cols:
        scen_df = scen_df.merge(edges[geo_cols], on="edge_id", how="left")

    # GNN predictions
    scenario_key = SCENARIO_LABEL_MAP.get(st.session_state.scenario, "baseline")
    gnn_df: pd.DataFrame | None = None
    if st.session_state.model_mode == "gnn":
        gnn_df = load_gnn_predictions(scenario_key)
        if gnn_df is not None and "edge_id" in gnn_df.columns and geo_cols:
            if "route_segments" not in gnn_df.columns:
                gnn_df = gnn_df.merge(edges[geo_cols], on="edge_id", how="left")
        if gnn_df is None:
            st.sidebar.warning("GNN predictions unavailable — falling back to flow model.")

    # Price model
    brent_data: dict | None = None
    price_df = load_price_impacts()
    if price_df is not None:
        for key in (scenario_key, "baseline"):
            col = next((c for c in ("week_or_scenario_id", "scenario_id") if c in price_df.columns), None)
            if col is None:
                break
            row = price_df[price_df[col] == key]
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
                break

    # Hormuz data
    hormuz_records: list | None = None
    if st.session_state.hormuz_mode:
        hdf = load_hormuz_data()
        if hdf is not None:
            cols = [c for c in ["week_ending", "actual", "prediction", "persistence"] if c in hdf.columns]
            model_col = "model" if "model" in hdf.columns else None
            sub = hdf[hdf[model_col] == "real_only"][cols].copy() if model_col else hdf[cols].copy()
            sub["week_ending"] = sub["week_ending"].astype(str)
            hormuz_records = sub.to_dict(orient="records")

    # Build layer data
    path_rows, port_rows, junction_rows = build_layer_data(
        edges_base    = edges,
        scen_df       = scen_df,
        gnn_df        = gnn_df,
        use_gnn       = (st.session_state.model_mode == "gnn" and gnn_df is not None),
        removed_edges = st.session_state.removed_edges,
        nodes_df      = nodes,
    )
    kpis = compute_kpis(scen_df, summary)

    # Build self-contained HTML and render
    html = build_globe_html(
        path_rows      = path_rows,
        port_rows      = port_rows,
        junction_rows  = junction_rows,
        kpis           = kpis,
        brent_data     = brent_data,
        hormuz_records = hormuz_records,
        model_mode     = st.session_state.model_mode,
        scenario_label = st.session_state.scenario,
        hormuz_mode    = st.session_state.hormuz_mode,
        removed_edges  = st.session_state.removed_edges,
    )

    # Height 10000 + self-resize JS = effectively full-viewport.
    # The JS inside the iframe sets window.frameElement.style.height = parent.innerHeight.
    components.html(html, height=10000, scrolling=False)


if __name__ == "__main__":
    main()
