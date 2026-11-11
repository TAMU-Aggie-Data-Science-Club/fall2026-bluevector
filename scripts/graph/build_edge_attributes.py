"""Deliverable 3 — Graph Group, Part 3: Static & Dynamic Edge Attributes.

What it does
    1. Reads config/edges_core.csv (or a passed --edges path) and writes the attributes
       back to that same file, so Part 2 (build_connector_edges.py) can read one table.
    2. Computes min_transit_days from length_nm at the top of the documented 12-14 kn
       speed range (default 14 knots: minimum days = fastest documented speed).
    3. Matches chokepoint edges to PortWatch's chokepoints database to fill
       chokepoint_portid.
    4. Adds static domain attributes to edges.csv:
       - vessel_size_limit (Suezmax, Neopanamax, Turkish Straits, Malaccamax, or unrestricted)
       - max_dwt_tonnes (approximate maximum deadweight tonnage for laden tankers)
       - toll_usd_per_transit and toll_usd_per_bbl (published canal tariffs for Suez & Panama)
       - capacity_proxy (95th percentile of weekly tanker transits in the pre-Red Sea
         baseline window: 2019-01-01 to 2023-10-31)
    5. Builds the full weekly dynamic chokepoint table
       (data/processed/chokepoint_weekly_attributes.parquet & .csv) with:
       - weekly_tanker_transits (sum of daily n_tanker in Friday-ending weeks)
       - capacity_tanker_tonnes (sum of daily capacity_tanker)
       - avg_vessel_size_tonnes (capacity_tanker / n_tanker carrying-capacity proxy)
       - est_oil_volume_bbl (capacity_tanker * crude_share * 7.3)
       - utilization (weekly_tanker_transits / capacity_proxy)
       - anomaly (seasonal z-score vs. same ISO week-of-year in 2019-2022 baseline)
       - source ('observed' where PortWatch series exists, 'modeled' otherwise)
    6. Attaches summary dynamic columns (baseline median & latest week) to config/edges.csv.

How to run (from the repo root)
    python scripts/graph/build_edge_attributes.py
    python scripts/graph/build_edge_attributes.py --edges config/edges_core.csv --speed 14.0

Pipeline order
    1. build_edge_attributes.py    -> config/edges_core.csv  (static + dynamic columns)
    2. build_connector_edges.py    -> config/edges.csv        (connectors, lengths, transit days)
    3. graph_validation.py         -> config/validation_results.csv

Needs
    pip install pandas numpy pyarrow requests
"""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"

DEFAULT_EDGES_CANDIDATES = [
    CONFIG_DIR / "edges_core.csv",
]

STATIC_CP_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "PortWatch_chokepoints_database/FeatureServer/0/query"
)
DAILY_CP_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)

# Baseline assumptions from DELIVERABLE3.md & DATA.md
# Minimum transit days use the top of the documented 12-14 kn range: the fastest
# documented speed gives the minimum time for a given distance.
DEFAULT_SPEED_KNOTS = 14.0
BARRELS_PER_TONNE = 7.3
BASELINE_START = "2019-01-01"
BASELINE_END = "2023-10-31"       # Pre-Red Sea crisis cutoff for capacity_proxy
SEASONAL_BASE_END = "2022-12-31"  # Full pre-crisis calendar years for week-of-year z-score
CAPACITY_PERCENTILE = 0.95        # 95th percentile of weekly tanker transits
DEFAULT_CRUDE_SHARE = 0.65        # Fallback when EIA ratio is unavailable

# Mapping from searoute_passage in edges.csv -> substring in PortWatch portname
PASSAGE_TO_PORTWATCH = {
    "suez": ["suez"],
    "babalmandab": ["bab el-mandeb", "bab-el-mandeb", "bab"],
    "ormuz": ["hormuz", "ormuz"],
    "malacca": ["malacca"],
    "gibraltar": ["gibraltar"],
    "panama": ["panama"],
    "south_africa": ["cape of good hope", "good hope"],
    "bosporus": ["bosporus", "bosphorus", "turkish"],
    "sunda": ["sunda"],
    "chili": ["magellan"],
}

# Domain rules for static chokepoint attributes (vessel size limits & published canal tolls)
# Tolls are approximate published laden-tanker tariffs (USD per transit and USD per barrel)
CHOKEPOINT_RULES = {
    "suez": {
        "vessel_size_limit": "Suezmax (max ~20.1m draft, ~200k DWT laden; VLCCs must ballast or lighten)",
        "max_dwt_tonnes": 200000,
        "toll_usd_per_transit": 450000.0,
        "toll_usd_per_bbl": 0.45,
        "default_crude_share": 0.62,
    },
    "panama": {
        "vessel_size_limit": "Neopanamax (max 366m LOA, 51.25m beam, ~120k DWT)",
        "max_dwt_tonnes": 120000,
        "toll_usd_per_transit": 350000.0,
        "toll_usd_per_bbl": 0.60,
        "default_crude_share": 0.45,
    },
    "bosporus": {
        "vessel_size_limit": "Turkish Straits max ~300m LOA (Suezmax ~160k DWT limit)",
        "max_dwt_tonnes": 160000,
        "toll_usd_per_transit": 50000.0,
        "toll_usd_per_bbl": 0.05,
        "default_crude_share": 0.75,
    },
    "malacca": {
        "vessel_size_limit": "Malaccamax (max ~20.5m draft, ~300k DWT VLCC)",
        "max_dwt_tonnes": 300000,
        "toll_usd_per_transit": 0.0,
        "toll_usd_per_bbl": 0.0,
        "default_crude_share": 0.70,
    },
    "ormuz": {
        "vessel_size_limit": "Unrestricted (ULCC/VLCC capable)",
        "max_dwt_tonnes": 500000,
        "toll_usd_per_transit": 0.0,
        "toll_usd_per_bbl": 0.0,
        "default_crude_share": 0.75,
    },
    "babalmandab": {
        "vessel_size_limit": "Unrestricted (VLCC capable)",
        "max_dwt_tonnes": 400000,
        "toll_usd_per_transit": 0.0,
        "toll_usd_per_bbl": 0.0,
        "default_crude_share": 0.65,
    },
    "gibraltar": {
        "vessel_size_limit": "Unrestricted (ULCC/VLCC capable)",
        "max_dwt_tonnes": 500000,
        "toll_usd_per_transit": 0.0,
        "toll_usd_per_bbl": 0.0,
        "default_crude_share": 0.65,
    },
    "south_africa": {
        "vessel_size_limit": "Unrestricted (ULCC/VLCC capable)",
        "max_dwt_tonnes": 500000,
        "toll_usd_per_transit": 0.0,
        "toll_usd_per_bbl": 0.0,
        "default_crude_share": 0.70,
    },
    "sunda": {
        "vessel_size_limit": "Sunda shelf draft limit (~18m draft, ~150k DWT)",
        "max_dwt_tonnes": 150000,
        "toll_usd_per_transit": 0.0,
        "toll_usd_per_bbl": 0.0,
        "default_crude_share": 0.60,
    },
    "chili": {
        "vessel_size_limit": "Pilotage & weather constraints (~150k DWT typical)",
        "max_dwt_tonnes": 150000,
        "toll_usd_per_transit": 0.0,
        "toll_usd_per_bbl": 0.0,
        "default_crude_share": 0.55,
    },
}


def fetch_arcgis_layer(url: str, order_by: str = "ObjectId ASC") -> pd.DataFrame:
    """Fallback fetcher if pull_chokepoints_static.py / pull_chokepoints.py haven't been run."""
    rows, offset = [], 0
    while True:
        params = dict(
            where="1=1",
            outFields="*",
            orderByFields=order_by,
            resultOffset=offset,
            resultRecordCount=1000,
            returnGeometry="false",
            f="json",
        )
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS error: {data['error']}")
        feats = data.get("features", [])
        rows.extend(f["attributes"] for f in feats)
        if not feats or (len(feats) < 1000 and not data.get("exceededTransferLimit")):
            break
        offset += len(feats)
        time.sleep(0.15)
    return pd.DataFrame(rows)


def load_chokepoint_static() -> pd.DataFrame:
    """Load PortWatch static chokepoints database from local cache or fetch if missing."""
    pq_path = INTERIM_DIR / "portwatch_chokepoints_database.parquet"
    csv_path = RAW_DIR / "portwatch_chokepoints_database_raw.csv"
    if pq_path.exists():
        return pd.read_parquet(pq_path)
    if csv_path.exists():
        return pd.read_csv(csv_path)
    print("Static chokepoints file not found locally; pulling from PortWatch ArcGIS...")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df = fetch_arcgis_layer(STATIC_CP_URL, order_by="ObjectId ASC")
    df.to_csv(csv_path, index=False)
    df.to_parquet(pq_path, index=False)
    return df


def load_chokepoint_daily() -> pd.DataFrame:
    """Load PortWatch daily chokepoint series (prefer full raw CSV so all 28 chokepoints exist)."""
    raw_csv = RAW_DIR / "portwatch_chokepoints_daily_raw.csv"
    interim_pq = INTERIM_DIR / "portwatch_chokepoints_daily.parquet"
    interim_csv = INTERIM_DIR / "portwatch_chokepoints_daily.csv"

    if raw_csv.exists():
        df = pd.read_csv(raw_csv)
    elif interim_pq.exists():
        df = pd.read_parquet(interim_pq)
    elif interim_csv.exists():
        df = pd.read_csv(interim_csv)
    else:
        print("Daily chokepoints file not found locally; pulling from PortWatch ArcGIS...")
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        df = fetch_arcgis_layer(DAILY_CP_URL, order_by="date ASC")
        df["date"] = (
            pd.to_datetime(df["date"], unit="ms")
            if pd.api.types.is_numeric_dtype(df["date"])
            else pd.to_datetime(df["date"])
        )
        df.to_csv(raw_csv, index=False)

    df["date"] = (
        pd.to_datetime(df["date"], unit="ms")
        if pd.api.types.is_numeric_dtype(df["date"])
        else pd.to_datetime(df["date"])
    )
    return df[df["date"] >= pd.Timestamp(BASELINE_START)].copy()


def load_crude_shares() -> dict[str, float]:
    """Load calibrated crude shares if data/interim/crude_share.csv exists."""
    cs_path = INTERIM_DIR / "crude_share.csv"
    shares = {}
    if cs_path.exists():
        try:
            df = pd.read_csv(cs_path)
            if {"scope", "crude_share"}.issubset(df.columns):
                for _, r in df.iterrows():
                    if pd.notna(r["crude_share"]):
                        shares[str(r["scope"]).strip().lower()] = float(r["crude_share"])
        except Exception as exc:
            print(f"Note: could not parse {cs_path} ({exc}); using default crude shares.")
    return shares


def match_portwatch_chokepoints(static_df: pd.DataFrame, daily_df: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """Map searoute_passage -> (portwatch_portid, portwatch_portname)."""
    # Combine unique (portid, portname) from static and daily tables
    pool = pd.concat(
        [
            static_df[["portid", "portname"]] if {"portid", "portname"}.issubset(static_df.columns) else pd.DataFrame(),
            daily_df[["portid", "portname"]].drop_duplicates() if {"portid", "portname"}.issubset(daily_df.columns) else pd.DataFrame(),
        ],
        ignore_index=True,
    ).drop_duplicates("portid")

    mapping = {}
    for passage, keywords in PASSAGE_TO_PORTWATCH.items():
        matched = None
        for kw in keywords:
            hits = pool[pool["portname"].astype(str).str.lower().str.contains(kw, regex=False)]
            if not hits.empty:
                matched = (str(hits.iloc[0]["portid"]), str(hits.iloc[0]["portname"]))
                break
        if matched:
            mapping[passage] = matched
    return mapping


def build_weekly_chokepoint_series(
    daily_df: pd.DataFrame,
    edges_cp: pd.DataFrame,
    crude_shares: dict[str, float],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build weekly dynamic chokepoint table and per-chokepoint summary stats for edges.csv."""
    # Map portwatch_portid -> edge metadata
    cp_meta = edges_cp.dropna(subset=["chokepoint_portid"]).set_index("chokepoint_portid")
    valid_portids = set(cp_meta.index)

    sub = daily_df[daily_df["portid"].isin(valid_portids)].copy()
    if sub.empty:
        raise RuntimeError("No matching chokepoint portids found in daily PortWatch data.")

    sub["n_tanker"] = pd.to_numeric(sub["n_tanker"], errors="coerce").fillna(0.0)
    if "capacity_tanker" in sub.columns:
        sub["capacity_tanker"] = pd.to_numeric(sub["capacity_tanker"], errors="coerce").fillna(0.0)
    else:
        sub["capacity_tanker"] = np.nan

    # Aggregate into Friday-ending weeks (matching EIA weekly Brent convention)
    sub = sub.sort_values("date")
    weekly = (
        sub.set_index("date")
        .groupby(["portid", "portname"])
        .resample("W-FRI")
        .agg(
            weekly_tanker_transits=("n_tanker", "sum"),
            capacity_tanker_tonnes=("capacity_tanker", "sum"),
            n_days_observed=("n_tanker", "count"),
        )
        .reset_index()
        .rename(columns={"date": "week_ending", "portid": "chokepoint_portid"})
    )
    # Keep only weeks with full or near-full observations (at least 5 days) for clean weekly counts
    weekly = weekly[weekly["n_days_observed"] >= 5].reset_index(drop=True)

    # Attach edge_id and searoute_passage
    id_to_edge = cp_meta["edge_id"].to_dict()
    id_to_passage = cp_meta["searoute_passage"].to_dict()
    weekly["edge_id"] = weekly["chokepoint_portid"].map(id_to_edge)
    weekly["searoute_passage"] = weekly["chokepoint_portid"].map(id_to_passage)

    # Compute crude_share per row
    def get_crude_share(row):
        pid = str(row["chokepoint_portid"]).lower()
        pas = str(row["searoute_passage"]).lower()
        if pid in crude_shares:
            return crude_shares[pid]
        if pas in crude_shares:
            return crude_shares[pas]
        if "global_default" in crude_shares:
            return crude_shares["global_default"]
        return CHOKEPOINT_RULES.get(pas, {}).get("default_crude_share", DEFAULT_CRUDE_SHARE)

    weekly["crude_share"] = weekly.apply(get_crude_share, axis=1)

    # Derived metric 1: Average vessel size proxy (carrying-capacity tonnes per tanker transit)
    weekly["avg_vessel_size_tonnes"] = np.where(
        weekly["weekly_tanker_transits"] > 0,
        (weekly["capacity_tanker_tonnes"] / weekly["weekly_tanker_transits"]).round(1),
        np.nan,
    )

    # Derived metric 2: Estimated oil volume (barrels per week)
    weekly["est_oil_volume_bbl"] = (
        weekly["capacity_tanker_tonnes"] * weekly["crude_share"] * BARRELS_PER_TONNE
    ).round(0)

    # Derived metric 3: Capacity proxy (95th percentile of weekly tanker transits before Red Sea crisis)
    base_mask = (weekly["week_ending"] >= pd.Timestamp(BASELINE_START)) & (
        weekly["week_ending"] <= pd.Timestamp(BASELINE_END)
    )
    cap_proxy = (
        weekly[base_mask]
        .groupby("chokepoint_portid")["weekly_tanker_transits"]
        .quantile(CAPACITY_PERCENTILE)
        .round(2)
        .to_dict()
    )
    weekly["capacity_proxy"] = weekly["chokepoint_portid"].map(cap_proxy)

    # Derived metric 4: Utilization = weekly_tanker_transits / capacity_proxy
    weekly["utilization"] = np.where(
        weekly["capacity_proxy"] > 0,
        (weekly["weekly_tanker_transits"] / weekly["capacity_proxy"]).round(4),
        np.nan,
    )

    # Derived metric 5: Anomaly vs. seasonal baseline (z-score by ISO week-of-year in 2019-2022)
    weekly["iso_week"] = weekly["week_ending"].dt.isocalendar().week.astype(int)
    seasonal_mask = (weekly["week_ending"] >= pd.Timestamp(BASELINE_START)) & (
        weekly["week_ending"] <= pd.Timestamp(SEASONAL_BASE_END)
    )
    seasonal_stats = (
        weekly[seasonal_mask]
        .groupby(["chokepoint_portid", "iso_week"])["weekly_tanker_transits"]
        .agg(seasonal_mean="mean", seasonal_std="std")
        .reset_index()
    )
    weekly = weekly.merge(seasonal_stats, on=["chokepoint_portid", "iso_week"], how="left")
    weekly["anomaly"] = np.where(
        (weekly["seasonal_std"].notna()) & (weekly["seasonal_std"] > 0),
        ((weekly["weekly_tanker_transits"] - weekly["seasonal_mean"]) / weekly["seasonal_std"]).round(3),
        np.nan,
    )
    weekly["source"] = "observed"

    # Summary per chokepoint for edges.csv (baseline median + latest observed week)
    summary_rows = []
    for pid, grp in weekly.groupby("chokepoint_portid"):
        grp_sorted = grp.sort_values("week_ending")
        base_grp = grp_sorted[
            (grp_sorted["week_ending"] >= pd.Timestamp(BASELINE_START))
            & (grp_sorted["week_ending"] <= pd.Timestamp(BASELINE_END))
        ]
        latest = grp_sorted.iloc[-1]
        summary_rows.append(
            {
                "chokepoint_portid": pid,
                "capacity_proxy": cap_proxy.get(pid, np.nan),
                "weekly_tanker_transits": round(float(base_grp["weekly_tanker_transits"].median()), 1)
                if not base_grp.empty
                else float(latest["weekly_tanker_transits"]),
                "est_oil_volume": round(float(base_grp["est_oil_volume_bbl"].median()), 0)
                if not base_grp.empty
                else float(latest["est_oil_volume_bbl"]),
                "avg_vessel_size_tonnes": round(float(base_grp["avg_vessel_size_tonnes"].median()), 1)
                if not base_grp.empty
                else float(latest["avg_vessel_size_tonnes"]),
                "utilization": round(
                    float(base_grp["weekly_tanker_transits"].median() / cap_proxy[pid]), 4
                )
                if (not base_grp.empty and cap_proxy.get(pid, 0) > 0)
                else float(latest["utilization"]),
                "anomaly": round(float(base_grp["anomaly"].median()), 3)
                if not base_grp.empty
                else float(latest["anomaly"]),
                "latest_week_ending": str(latest["week_ending"].date()),
                "latest_weekly_tanker_transits": float(latest["weekly_tanker_transits"]),
                "latest_utilization": float(latest["utilization"]) if pd.notna(latest["utilization"]) else np.nan,
                "latest_anomaly": float(latest["anomaly"]) if pd.notna(latest["anomaly"]) else np.nan,
                "source": "observed",
            }
        )
    summary_df = pd.DataFrame(summary_rows)
    return weekly, summary_df


def resolve_edges_path(arg_path: Path | None) -> Path:
    if arg_path is not None:
        if not arg_path.exists():
            raise FileNotFoundError(f"Edges file not found: {arg_path}")
        return arg_path
    for candidate in DEFAULT_EDGES_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Could not find edges_core.csv in any of: {DEFAULT_EDGES_CANDIDATES}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--edges", type=Path, default=None, help="Path to input edges_core.csv")
    ap.add_argument("--out", type=Path, default=None, help="Output path (default: the input edges_core.csv)")
    ap.add_argument("--speed", type=float, default=DEFAULT_SPEED_KNOTS, help="Speed in knots for minimum transit days (default 14.0, the top of the 12-14 kn range)")
    args = ap.parse_args()

    edges_path = resolve_edges_path(args.edges)
    out_edges_path = args.out or edges_path

    print(f"Loading edges from: {edges_path}")
    edges = pd.read_csv(edges_path)

    # 1. Compute min_transit_days = length_nm / (speed_knots * 24)
    edges["length_nm"] = pd.to_numeric(edges["length_nm"], errors="coerce")
    edges["min_transit_days"] = (edges["length_nm"] / (args.speed * 24.0)).round(2)

    # 2. Load PortWatch static & daily chokepoint data
    static_cp = load_chokepoint_static()
    daily_cp = load_chokepoint_daily()
    crude_shares = load_crude_shares()

    # 3. Match chokepoint edges to PortWatch portids
    passage_map = match_portwatch_chokepoints(static_cp, daily_cp)
    print("\nMatched searoute passages to PortWatch chokepoints:")
    for pas, (pid, pname) in passage_map.items():
        print(f"  {pas:14} -> {pid:14} ({pname})")

    # Fill chokepoint_portid on chokepoint edges
    edges["chokepoint_portid"] = edges["chokepoint_portid"].astype("object")
    is_cp = edges["edge_type"] == "chokepoint"
    for idx, row in edges[is_cp].iterrows():
        pas = str(row.get("searoute_passage", "")).strip().lower()
        if (pd.isna(row["chokepoint_portid"]) or str(row["chokepoint_portid"]).strip() == "") and pas in passage_map:
            edges.at[idx, "chokepoint_portid"] = passage_map[pas][0]

    # 4. Add static domain attributes (vessel_size_limit, max_dwt_tonnes, tolls)
    vessel_limits, max_dwts, tolls_transit, tolls_bbl = [], [], [], []
    for _, row in edges.iterrows():
        if row["edge_type"] == "chokepoint":
            pas = str(row.get("searoute_passage", "")).strip().lower()
            rule = CHOKEPOINT_RULES.get(pas, {})
            vessel_limits.append(rule.get("vessel_size_limit", "Unrestricted"))
            max_dwts.append(rule.get("max_dwt_tonnes", 500000))
            tolls_transit.append(rule.get("toll_usd_per_transit", 0.0))
            tolls_bbl.append(rule.get("toll_usd_per_bbl", 0.0))
        else:
            vessel_limits.append("Unrestricted")
            max_dwts.append(500000)
            tolls_transit.append(0.0)
            tolls_bbl.append(0.0)

    edges["vessel_size_limit"] = vessel_limits
    edges["max_dwt_tonnes"] = max_dwts
    edges["toll_usd_per_transit"] = tolls_transit
    edges["toll_usd_per_bbl"] = tolls_bbl

    # 5. Compute weekly dynamic chokepoint table & attach summary columns to edges.csv
    weekly_df, summary_df = build_weekly_chokepoint_series(
        daily_cp, edges[is_cp], crude_shares
    )

    # Drop any old dynamic columns if re-running
    dyn_cols = [
        "capacity_proxy",
        "weekly_tanker_transits",
        "est_oil_volume",
        "avg_vessel_size_tonnes",
        "utilization",
        "anomaly",
        "latest_week_ending",
        "latest_weekly_tanker_transits",
        "latest_utilization",
        "latest_anomaly",
        "source",
    ]
    edges = edges.drop(columns=[c for c in dyn_cols if c in edges.columns])

    edges = edges.merge(summary_df, on="chokepoint_portid", how="left")
    # Non-chokepoint edges (lanes and connectors) have modeled dynamic flows
    edges["source"] = edges["source"].fillna("modeled")

    # 6. Save outputs
    out_edges_path.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)

    edges.to_csv(out_edges_path, index=False)
    weekly_pq = PROCESSED_DIR / "chokepoint_weekly_attributes.parquet"
    weekly_csv = PROCESSED_DIR / "chokepoint_weekly_attributes.csv"
    weekly_interim = INTERIM_DIR / "chokepoint_weekly_attributes.parquet"

    weekly_df.to_parquet(weekly_pq, index=False)
    weekly_df.to_csv(weekly_csv, index=False)
    weekly_df.to_parquet(weekly_interim, index=False)

    print(f"\nSaved updated edges table ({len(edges)} rows, {edges.shape[1]} cols) -> {out_edges_path}")
    print(f"Saved weekly chokepoint time series ({len(weekly_df):,} rows) -> {weekly_pq}")

    print("\nChokepoint Edges Summary (Static + Baseline Dynamic Attributes):")
    cp_display_cols = [
        "edge_id",
        "searoute_passage",
        "chokepoint_portid",
        "length_nm",
        "min_transit_days",
        "toll_usd_per_bbl",
        "capacity_proxy",
        "weekly_tanker_transits",
        "est_oil_volume",
        "utilization",
        "latest_weekly_tanker_transits",
        "latest_utilization",
        "source",
    ]
    print(edges[edges["edge_type"] == "chokepoint"][cp_display_cols].to_string(index=False))


if __name__ == "__main__":
    main()