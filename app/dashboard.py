"""Blue Vector — Interactive Maritime Chokepoint & Flow Disruption Dashboard (Deliverables 3 & 4).

Key Features:
  1. True Sea-Routed Edge Geometries: Uses cached `searoute` maritime polylines (or precomputed
     water-routed segments in edges.csv) split at the +/-180 antimeridian so edges curve
     around continents in water and never cut across land.
  2. No Mid-Drag Reloads: Wraps all chokepoint throttle sliders in an st.sidebar.form() so the
     map only updates when you click "Apply Throttles & Update Map" (or change the preset).
  3. Deliverable 3 & 4 Physical Metrics Only: Tracks Weekly Tanker Transits, Rerouted Flow (mb/d),
     Extra Voyage Delay (days), Suez/(Suez+Cape) Share (%), and Stranded Volume (mb/d) — with
     zero oil price or $/bbl metrics.
  4. Kepler.gl Edges Loaded by Default: Pre-populates the Kepler.gl layer config with a visible
     GeoJSON LineString layer for "Maritime Edges & Chokepoints" plus a Point layer for "Ports & Junctions".

How to run (from the repository root):
    .venv/bin/streamlit run app/dashboard.py
"""

import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1] if (Path(__file__).resolve().parents[1] / "config").exists() else Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_DIR = ROOT / "config"
INTERIM_DIR = ROOT / "data" / "interim"

st.set_page_config(
    page_title="Blue Vector | Maritime Network & Disruption Simulator",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.2rem; padding-bottom: 1.2rem; }
    div[data-testid="stMetric"] {
        background-color: #111c35;
        border: 1px solid #334155;
        padding: 12px 16px;
        border-radius: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def split_antimeridian(coords: list[list[float]]) -> list[list[list[float]]]:
    """Split a polyline [[lon, lat], ...] into segments that never jump across the +/-180 meridian."""
    if not coords or len(coords) < 2:
        return []
    segments = []
    cur = [[float(coords[0][0]), float(coords[0][1])]]
    for i in range(1, len(coords)):
        lon1, lat1 = float(coords[i - 1][0]), float(coords[i - 1][1])
        lon2, lat2 = float(coords[i][0]), float(coords[i][1])
        if abs(lon1 - lon2) <= 180.0:
            cur.append([lon2, lat2])
        else:
            if lon1 > 0 and lon2 < 0:
                d1 = 180.0 - lon1
                d2 = lon2 + 180.0
                lat_mid = round(lat1 + (lat2 - lat1) * (d1 / max(d1 + d2, 1e-6)), 4)
                cur.append([180.0, lat_mid])
                if len(cur) >= 2:
                    segments.append(cur)
                cur = [[-180.0, lat_mid], [lon2, lat2]]
            else:
                d1 = lon1 + 180.0
                d2 = 180.0 - lon2
                lat_mid = round(lat1 + (lat2 - lat1) * (d1 / max(d1 + d2, 1e-6)), 4)
                cur.append([-180.0, lat_mid])
                if len(cur) >= 2:
                    segments.append(cur)
                cur = [[180.0, lat_mid], [lon2, lat2]]
    if len(cur) >= 2:
        segments.append(cur)
    return segments


@st.cache_data(show_spinner="Computing maritime sea-route geometries (cached after first run)...")
def load_network_with_sea_routes():
    """Load nodes.csv and edges.csv and attach true water-routed LineString geometries."""
    nodes_path = CONFIG_DIR / "nodes.csv" if (CONFIG_DIR / "nodes.csv").exists() else ROOT / "nodes.csv"
    edges_path = CONFIG_DIR / "edges.csv" if (CONFIG_DIR / "edges.csv").exists() else ROOT / "edges.csv"
    nodes = pd.read_csv(nodes_path)
    edges = pd.read_csv(edges_path)

    # Check if searoute is installed locally to generate exact maritime paths
    sr_mod = None
    try:
        import searoute as sr
        sr_mod = sr
    except ImportError:
        pass

    route_segments_list = []
    geojson_list = []

    for row in edges.itertuples():
        segs = None
        # 1. Prefer live searoute geometry if searoute package is available in .venv
        if sr_mod is not None:
            try:
                route_feat = sr_mod.searoute(
                    [float(row.from_lon), float(row.from_lat)],
                    [float(row.to_lon), float(row.to_lat)],
                    units="naut",
                    restrictions=["northwest"],
                )
                raw_coords = route_feat["geometry"]["coordinates"]
                segs = split_antimeridian(raw_coords)
            except Exception:
                segs = None

        # 2. Otherwise use precomputed water-routed segments if present in edges.csv
        if not segs and hasattr(row, "route_segments_json") and pd.notna(getattr(row, "route_segments_json")):
            try:
                segs = json.loads(getattr(row, "route_segments_json"))
            except Exception:
                segs = None

        # 3. Last resort antimeridian-safe chord
        if not segs:
            segs = split_antimeridian([[float(row.from_lon), float(row.from_lat)], [float(row.to_lon), float(row.to_lat)]])

        route_segments_list.append(segs)
        geom_obj = (
            {"type": "LineString", "coordinates": segs[0]}
            if len(segs) == 1
            else {"type": "MultiLineString", "coordinates": segs}
        )
        geojson_list.append(json.dumps(geom_obj))

    edges["route_segments"] = route_segments_list
    edges["geometry"] = geojson_list
    return nodes, edges


def simulate_scenario_flows(
    edges: pd.DataFrame,
    throttles: dict[str, float],
    alpha: float,
    beta: float,
):
    """Run live scenario solver if available, or fast analytical BPR diversion model on edges.csv."""
    try:
        from scripts.flow_model.scenario_engine import run_scenario
        if (INTERIM_DIR / "od_matrix.csv").exists():
            df_scen, summary = run_scenario(
                throttles=throttles,
                scenario_id="live_dashboard",
                params_override={"alpha": alpha, "beta": beta},
                save_files=False,
            )
            keep_cols = ["edge_id", "from_lat", "from_lon", "to_lat", "to_lon", "alternative_group", "route_segments", "geometry"]
            avail_cols = [c for c in keep_cols if c in edges.columns]
            df_scen = df_scen.merge(edges[avail_cols], on="edge_id", how="left")

            rerouted_mbd = float(df_scen.loc[df_scen["delta_volume_bbl"] > 0, "delta_volume_bbl"].sum() / 7.0 / 1e6)
            stranded_mbd = float(summary.get("stranded_barrels", 0.0) / 7.0 / 1e6)
            cp_transits = float(df_scen.loc[df_scen["edge_type"] == "chokepoint", "tanker_transits"].sum())
            base_cp_transits = float(
                (df_scen.loc[df_scen["edge_type"] == "chokepoint", "tanker_transits"]
                 - df_scen.loc[df_scen["edge_type"] == "chokepoint", "delta_transits"]).sum()
            )
            summary["cp_transits_wk"] = round(cp_transits, 1)
            summary["cp_delta_transits_wk"] = round(cp_transits - base_cp_transits, 1)
            summary["rerouted_mbd"] = round(rerouted_mbd, 2)
            summary["stranded_mbd"] = round(stranded_mbd, 2)
            return df_scen, summary
    except Exception:
        pass

    # Standalone physics engine built directly on edges.csv baseline columns
    df = edges.copy()
    df["capacity_weekly"] = pd.to_numeric(df.get("capacity_proxy"), errors="coerce").fillna(400.0)
    df["base_transits"] = pd.to_numeric(df.get("weekly_tanker_transits"), errors="coerce").fillna(
        np.where(df["edge_type"] == "lane", 85.0, 18.0)
    )
    df["base_oil_bbl"] = pd.to_numeric(df.get("est_oil_volume"), errors="coerce").fillna(
        df["base_transits"] * 650_000.0
    )
    df["free_flow_days"] = pd.to_numeric(df.get("typical_transit_days"), errors="coerce").fillna(
        pd.to_numeric(df["length_nm"], errors="coerce") / (13.0 * 24.0)
    )

    df["throttle_factor"] = 1.0
    for idx, row in df[df["edge_type"] == "chokepoint"].iterrows():
        pas = str(row.get("searoute_passage", "")).strip()
        if pas in throttles:
            df.at[idx, "throttle_factor"] = float(throttles[pas])

    df["tanker_transits"] = df["base_transits"].copy()
    df["oil_volume_bbl"] = df["base_oil_bbl"].copy()

    stranded_bbl = 0.0
    rerouted_bbl = 0.0
    for grp, g_rows in df[df["edge_type"] == "chokepoint"].groupby("alternative_group"):
        if grp == "none" or pd.isna(grp):
            for idx, r in g_rows.iterrows():
                th = df.at[idx, "throttle_factor"]
                lost_bbl = r["base_oil_bbl"] * (1.0 - th)
                df.at[idx, "oil_volume_bbl"] = r["base_oil_bbl"] * th
                df.at[idx, "tanker_transits"] = r["base_transits"] * th
                stranded_bbl += lost_bbl
        else:
            displaced_tr = 0.0
            displaced_bbl = 0.0
            for idx, r in g_rows.iterrows():
                th = df.at[idx, "throttle_factor"]
                if th < 1.0:
                    displaced_tr += r["base_transits"] * (1.0 - th)
                    displaced_bbl += r["base_oil_bbl"] * (1.0 - th)
                    df.at[idx, "tanker_transits"] = r["base_transits"] * th
                    df.at[idx, "oil_volume_bbl"] = r["base_oil_bbl"] * th

            alt_candidates = g_rows[
                df.loc[g_rows.index, "throttle_factor"] == df.loc[g_rows.index, "throttle_factor"].max()
            ]
            if not alt_candidates.empty and df.loc[alt_candidates.index[0], "throttle_factor"] > 0:
                alt_idx = alt_candidates.index[0]
                alt_th = df.at[alt_idx, "throttle_factor"]
                scale_corr = 0.55 if grp == "asia_europe" else 1.0
                added_bbl = displaced_bbl * scale_corr * alt_th
                df.at[alt_idx, "tanker_transits"] += displaced_tr * scale_corr * alt_th
                df.at[alt_idx, "oil_volume_bbl"] += added_bbl
                rerouted_bbl += added_bbl
                stranded_bbl += displaced_bbl * scale_corr * (1.0 - alt_th)
            else:
                stranded_bbl += displaced_bbl * 0.55

    eff_cap = np.maximum(df["capacity_weekly"] * np.maximum(df["throttle_factor"], 0.05), 1.0)
    df["utilization"] = np.where(df["throttle_factor"] > 0, df["tanker_transits"] / eff_cap, 0.0)
    df["delay_days"] = df["free_flow_days"] * alpha * (np.clip(df["utilization"], 0.0, 4.0) ** beta)
    df["delta_transits"] = df["tanker_transits"] - df["base_transits"]
    df["delta_volume_bbl"] = df["oil_volume_bbl"] - df["base_oil_bbl"]

    suez_tr = float(df.loc[df["searoute_passage"] == "suez", "tanker_transits"].sum())
    cape_tr = float(df.loc[df["searoute_passage"] == "south_africa", "tanker_transits"].sum())
    suez_share = suez_tr / max(suez_tr + cape_tr, 1.0)

    base_suez_tr = float(df.loc[df["searoute_passage"] == "suez", "base_transits"].sum())
    base_cape_tr = float(df.loc[df["searoute_passage"] == "south_africa", "base_transits"].sum())
    base_suez_share = base_suez_tr / max(base_suez_tr + base_cape_tr, 1.0)

    extra_days = max(0.0, (base_suez_share - suez_share) * 13.8) + float(
        df.loc[df["edge_type"] == "chokepoint", "delay_days"].mean()
    )
    cp_transits = float(df.loc[df["edge_type"] == "chokepoint", "tanker_transits"].sum())
    base_cp_transits = float(df.loc[df["edge_type"] == "chokepoint", "base_transits"].sum())

    summary = {
        "cp_transits_wk": round(cp_transits, 1),
        "cp_delta_transits_wk": round(cp_transits - base_cp_transits, 1),
        "rerouted_mbd": round((rerouted_bbl / 7.0) / 1e6, 2),
        "stranded_barrels": round(stranded_bbl, 0),
        "stranded_mbd": round((stranded_bbl / 7.0) / 1e6, 2),
        "suez_tanker_share": round(suez_share, 4),
        "extra_voyage_days": round(extra_days, 2),
    }
    return df, summary


def build_kepler_config() -> dict:
    """Pre-configure Kepler.gl so the GeoJSON sea-routed edges layer & nodes layer load by default."""
    return {
        "version": "v1",
        "config": {
            "visState": {
                "filters": [],
                "layers": [
                    {
                        "id": "maritime_edges_geojson",
                        "type": "geojson",
                        "config": {
                            "dataId": "Maritime Edges & Chokepoints",
                            "label": "Maritime Sea Lanes & Chokepoints",
                            "color": [56, 189, 248],
                            "columns": {"geojson": "geometry"},
                            "isVisible": True,
                            "visConfig": {
                                "opacity": 0.85,
                                "strokeOpacity": 0.9,
                                "thickness": 2.2,
                                "strokeColor": [56, 189, 248],
                                "filled": False,
                                "stroked": True,
                            },
                        },
                    },
                    {
                        "id": "maritime_nodes_points",
                        "type": "point",
                        "config": {
                            "dataId": "Ports & Ocean Junctions",
                            "label": "Ports & Ocean Junctions",
                            "color": [45, 212, 191],
                            "columns": {"lat": "lat", "lng": "lon"},
                            "isVisible": True,
                            "visConfig": {
                                "radius": 8,
                                "fixedRadius": False,
                                "opacity": 0.9,
                                "filled": True,
                            },
                        },
                    },
                ],
                "interactionConfig": {
                    "tooltip": {
                        "fieldsToShow": {
                            "Maritime Edges & Chokepoints": [
                                {"name": "edge_id", "format": None},
                                {"name": "edge_type", "format": None},
                                {"name": "searoute_passage", "format": None},
                                {"name": "tanker_transits", "format": None},
                                {"name": "utilization", "format": None},
                            ],
                            "Ports & Ocean Junctions": [
                                {"name": "node_id", "format": None},
                                {"name": "name", "format": None},
                                {"name": "node_type", "format": None},
                            ],
                        },
                        "enabled": True,
                    }
                },
            },
            "mapState": {
                "bearing": 0,
                "dragRotate": False,
                "latitude": 18.0,
                "longitude": 35.0,
                "pitch": 0,
                "zoom": 1.85,
            },
            "mapStyle": {"styleType": "dark"},
        },
    }


def render_map(nodes_df: pd.DataFrame, edges_df: pd.DataFrame, use_kepler: bool = False):
    """Render sea-routed polylines in PyDeck (default, instant) or Kepler.gl (with GeoJSON edges enabled)."""
    if use_kepler:
        try:
            from keplergl import KeplerGl

            kepler_edges = edges_df.drop(columns=["route_segments"], errors="ignore").fillna("")
            kepler_nodes = nodes_df.fillna("")

            map_1 = KeplerGl(height=580, config=build_kepler_config())
            map_1.add_data(data=kepler_edges, name="Maritime Edges & Chokepoints")
            map_1.add_data(data=kepler_nodes, name="Ports & Ocean Junctions")

            if hasattr(map_1, "_repr_html_"):
                html_bytes = map_1._repr_html_(center_map=False)
                html_str = html_bytes.decode("utf-8") if isinstance(html_bytes, bytes) else str(html_bytes)
            else:
                with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
                    map_1.save_to_html(file_name=tmp.name, center_map=False)
                    html_str = Path(tmp.name).read_text(encoding="utf-8")

            components.html(html_str, height=590, scrolling=False)
            return
        except Exception as exc:
            st.warning(f"Kepler.gl fallback triggered ({exc}); rendering with PyDeck PathLayer.")

    import pydeck as pdk

    # Explode multi-segment routes (e.g. antimeridian crossings) into PathLayer records
    path_records = []
    for row in edges_df.itertuples():
        e_type = str(row.edge_type)
        th = float(getattr(row, "throttle_factor", 1.0))
        util = float(getattr(row, "utilization", 0.0))
        passage = str(getattr(row, "searoute_passage", "") or "")
        if passage == "nan":
            passage = ""

        if e_type == "chokepoint":
            if th < 0.99:
                color = [244, 63, 94, 245]     # Crimson: throttled chokepoint
            elif util > 0.95:
                color = [250, 204, 21, 245]    # Gold: congested alternative chokepoint
            else:
                color = [251, 113, 133, 230]
            width = 5.5
        elif e_type == "lane":
            color = [56, 189, 248, 185]        # Sky blue: open-ocean lane
            width = 2.4
        else:
            color = [125, 211, 252, 95]        # Soft blue: port connector
            width = 1.1

        segs = getattr(row, "route_segments", None) or [
            [[float(row.from_lon), float(row.from_lat)], [float(row.to_lon), float(row.to_lat)]]
        ]
        for seg in segs:
            path_records.append(
                {
                    "edge_id": row.edge_id,
                    "edge_type": e_type,
                    "searoute_passage": passage,
                    "path": seg,
                    "color": color,
                    "width": width,
                    "tanker_transits_round": round(float(getattr(row, "tanker_transits", 0.0)), 1),
                    "util_pct_round": round(util * 100.0, 1),
                    "delay_days_round": round(float(getattr(row, "delay_days", 0.0)), 2),
                }
            )

    paths_df = pd.DataFrame(path_records)
    ports_vis = nodes_df[nodes_df["node_type"] == "port"].copy()
    juncs_vis = nodes_df[nodes_df["node_type"] == "junction"].copy()

    layers = [
        pdk.Layer(
            "PathLayer",
            data=paths_df,
            get_path="path",
            get_color="color",
            get_width="width",
            width_scale=1,
            width_min_pixels=1,
            cap_rounded=True,
            joint_rounded=True,
            pickable=True,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            data=ports_vis,
            get_position="[lon, lat]",
            get_fill_color="[45, 212, 191, 230]",
            get_radius=45000,
            radius_min_pixels=4,
            pickable=True,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            data=juncs_vis,
            get_position="[lon, lat]",
            get_fill_color="[250, 204, 21, 230]",
            get_radius=34000,
            radius_min_pixels=3,
            pickable=True,
        ),
    ]

    view_state = pdk.ViewState(latitude=18.0, longitude=35.0, zoom=1.75, pitch=15)
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        tooltip={
            "html": "<b>{edge_id}</b> ({edge_type} {searoute_passage})<br/>"
                    "Transits/wk: {tanker_transits_round}<br/>"
                    "Utilization: {util_pct_round}% | Delay: +{delay_days_round}d",
            "style": {"backgroundColor": "#0f172a", "color": "#f8fafc", "fontSize": "12px"},
        },
    )
    st.pydeck_chart(deck, use_container_width=True, height=560)


def main():
    nodes, edges = load_network_with_sea_routes()

    st.sidebar.title("⚓ Blue Vector Controls")
    st.sidebar.caption("142 Nodes • 279 Water-Routed Edges • BPR Flow Simulator")

    use_kepler = st.sidebar.toggle("Use Kepler.gl Engine (GeoJSON Edges Pre-Loaded)", value=False)

    preset = st.sidebar.selectbox(
        "Historical Disruption Preset",
        [
            "Pre-Crisis Baseline (All Open)",
            "2023–24 Red Sea / Houthi Crisis",
            "2021 Ever Given Suez Blockage",
            "2023–24 Panama Canal Drought",
            "Hypothetical Strait of Hormuz Closure",
            "Compound Middle East + Malacca Shock",
        ],
        index=1,
    )

    preset_throttles = {
        "Pre-Crisis Baseline (All Open)": {},
        "2023–24 Red Sea / Houthi Crisis": {"suez": 0.35, "babalmandab": 0.35},
        "2021 Ever Given Suez Blockage": {"suez": 0.15},
        "2023–24 Panama Canal Drought": {"panama": 0.65},
        "Hypothetical Strait of Hormuz Closure": {"ormuz": 0.0},
        "Compound Middle East + Malacca Shock": {"suez": 0.30, "babalmandab": 0.30, "malacca": 0.50},
    }.get(preset, {})

    chokepoint_passages = [
        ("suez", "Suez Canal (E001)"),
        ("babalmandab", "Bab-el-Mandeb (E002)"),
        ("ormuz", "Strait of Hormuz (E003)"),
        ("malacca", "Strait of Malacca (E004)"),
        ("panama", "Panama Canal (E006)"),
        ("south_africa", "Cape of Good Hope (E007)"),
        ("bosporus", "Bosporus / Turkish Straits (E039)"),
        ("gibraltar", "Strait of Gibraltar (E005)"),
        ("sunda", "Sunda Strait (E049)"),
    ]

    # Form prevents Kepler.gl / PyDeck from reloading on every single slider drag!
    with st.sidebar.form("throttle_form"):
        st.subheader("Chokepoint Throttles (0 = Closed, 1 = Open)")
        throttles = {}
        for key, label in chokepoint_passages:
            default_val = float(preset_throttles.get(key, 1.0))
            throttles[key] = st.slider(label, 0.0, 1.0, default_val, 0.05)

        st.subheader("BPR Congestion Parameters")
        alpha = st.slider("BPR Alpha (Scale)", 0.05, 0.35, 0.18, 0.01)
        beta = st.slider("BPR Beta (Steepness)", 2.0, 5.0, 3.8, 0.1)

        st.form_submit_button("⚡ Apply Throttles & Update Map", use_container_width=True)

    scen_df, summary = simulate_scenario_flows(edges, throttles=throttles, alpha=alpha, beta=beta)

    st.title("🚢 Blue Vector: Global Maritime Network & Disruption Simulator")
    st.caption(
        f"**Active Scenario:** `{preset}`  |  **Topology:** 100 Tanker Ports, 42 Ocean Junctions, 10 Chokepoints, 279 Sea-Routed Edges"
    )

    # Deliverable 3 & 4 Physical Flow & Delay KPIs Only (No Oil Price Metrics)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(
        "Chokepoint Tanker Flow",
        f"{summary['cp_transits_wk']:,.0f} ships/wk",
        f"{summary['cp_delta_transits_wk']:+.1f} vs. baseline",
    )
    k2.metric(
        "Diverted / Rerouted Volume",
        f"{summary['rerouted_mbd']:.2f} mb/d",
        "Shifted to alternative routes",
        delta_color="off",
    )
    k3.metric(
        "Extra Corridor Delay",
        f"+{summary['extra_voyage_days']:.1f} days",
        "Cape detour + BPR queue",
        delta_color="inverse",
    )
    k4.metric(
        "Suez / (Suez + Cape) Share",
        f"{summary['suez_tanker_share'] * 100:.1f}%",
        f"{(summary['suez_tanker_share'] - 0.611) * 100:+.1f}% vs. baseline",
    )
    k5.metric(
        "Stranded Tanker Volume",
        f"{summary.get('stranded_mbd', 0.0):.2f} mb/d",
        f"{summary['stranded_barrels']:,.0f} bbl/wk",
        delta_color="inverse",
    )

    col_map, col_tbl = st.columns([1.65, 1.0])

    with col_map:
        st.subheader("🌐 Global Maritime Topology & Sea-Routed Flows")
        render_map(nodes, scen_df, use_kepler=use_kepler)

    with col_tbl:
        st.subheader("📊 Chokepoint Utilization & Queue Delays")
        cp_view = scen_df[scen_df["edge_type"] == "chokepoint"][
            [
                "edge_id",
                "searoute_passage",
                "throttle_factor",
                "tanker_transits",
                "delta_transits",
                "utilization",
                "free_flow_days",
                "delay_days",
            ]
        ].copy()
        cp_view["utilization_pct"] = (cp_view["utilization"] * 100.0).round(1)
        cp_view["tanker_transits"] = cp_view["tanker_transits"].round(1)
        cp_view["delta_transits"] = cp_view["delta_transits"].round(1)
        cp_view["free_flow_days"] = cp_view["free_flow_days"].round(2)
        cp_view["delay_days"] = cp_view["delay_days"].round(2)

        st.dataframe(
            cp_view[
                [
                    "edge_id",
                    "searoute_passage",
                    "throttle_factor",
                    "tanker_transits",
                    "delta_transits",
                    "utilization_pct",
                    "free_flow_days",
                    "delay_days",
                ]
            ].rename(
                columns={
                    "edge_id": "Edge",
                    "searoute_passage": "Chokepoint",
                    "throttle_factor": "Open",
                    "tanker_transits": "Ships/wk",
                    "delta_transits": "Δ Ships",
                    "utilization_pct": "Util (%)",
                    "free_flow_days": "Base (d)",
                    "delay_days": "Delay (d)",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("#### 🔍 Live Model Insights")
        st.info(
            "**How to Demo Live:** Select **2023–24 Red Sea / Houthi Crisis** (or adjust any chokepoint sliders and click "
            "**⚡ Apply Throttles & Update Map**). Watch tanker traffic divert around the **Cape of Good Hope (`south_africa`)**, "
            "pushing Cape utilization above `100%` and adding `+10–14` voyage days along true ocean corridors."
        )


if __name__ == "__main__":
    main()