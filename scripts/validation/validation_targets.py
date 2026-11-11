"""Deliverable 4 — Calibration & Validation Group, Part 1: Historical Event Ground-Truth.

What it does:
  Reads actual PortWatch daily chokepoint observations (from data/raw/portwatch_chokepoints_daily_raw.csv
  or data/interim/portwatch_chokepoints_daily.parquet) and builds observed benchmark metrics across:
  1. Red Sea / Houthi Diversions (Pre: Jan–Oct 2023 vs. Crisis: Dec 2023–Jun 2024)
     - split into a H1 calibration window (Dec 2023–Feb 2024) and a held-out H2 window (Mar–Jun 2024)
  2. Ever Given Suez Blockage (Pre: Feb 1–Mar 20, 2021 vs. Shock: Mar 23–Apr 5, 2021)
     - plus the peak 7-day window (Mar 23–29, 2021) because a 6-day blockage is smoothed by
       a two-week average
  3. Panama Canal Drought (Pre: Jan 2022–Apr 2023 vs. Drought: Oct 2023–Mar 2024)

Run from the repository root:
    .venv/bin/python scripts/validation/validation_targets.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_DIR = ROOT / "config"
RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"

BARRELS_PER_TONNE = 7.3
DEFAULT_CRUDE_SHARE = 0.65

EVENT_WINDOWS = [
    {
        "event_id": "red_sea_2024",
        "event_name": "2023-24 Red Sea / Houthi Diversions (full window)",
        "pre_start": "2023-01-01",
        "pre_end": "2023-10-31",
        "crisis_start": "2023-12-01",
        "crisis_end": "2024-06-30",
    },
    {
        "event_id": "red_sea_2023h1",
        "event_name": "2023-24 Red Sea (H1 calibration window)",
        "pre_start": "2023-01-01",
        "pre_end": "2023-10-31",
        "crisis_start": "2023-12-01",
        "crisis_end": "2024-02-29",
    },
    {
        "event_id": "red_sea_2024_h2",
        "event_name": "2023-24 Red Sea (H2 held-out window)",
        "pre_start": "2023-01-01",
        "pre_end": "2023-10-31",
        "crisis_start": "2024-03-01",
        "crisis_end": "2024-06-30",
    },
    {
        "event_id": "ever_given_2021",
        "event_name": "2021 Ever Given Suez Blockage (two-week window)",
        "pre_start": "2021-02-01",
        "pre_end": "2021-03-20",
        "crisis_start": "2021-03-23",
        "crisis_end": "2021-04-05",
    },
    {
        "event_id": "ever_given_2021_peak",
        "event_name": "2021 Ever Given (peak 7-day blockage window)",
        "pre_start": "2021-02-01",
        "pre_end": "2021-03-20",
        "crisis_start": "2021-03-23",
        "crisis_end": "2021-03-29",
    },
    {
        "event_id": "panama_drought_2023",
        "event_name": "2023-24 Panama Canal Drought",
        "pre_start": "2022-01-01",
        "pre_end": "2023-04-30",
        "crisis_start": "2023-10-01",
        "crisis_end": "2024-03-31",
    },
]


def load_daily_chokepoints() -> pd.DataFrame:
    for p in [
        RAW_DIR / "portwatch_chokepoints_daily_raw.csv",
        INTERIM_DIR / "portwatch_chokepoints_daily.parquet",
        INTERIM_DIR / "portwatch_chokepoints_daily.csv",
    ]:
        if p.exists():
            df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
            df["date"] = (
                pd.to_datetime(df["date"], unit="ms")
                if pd.api.types.is_numeric_dtype(df["date"])
                else pd.to_datetime(df["date"])
            )
            return df
    raise FileNotFoundError("Missing PortWatch daily chokepoints file. Run pull_chokepoints.py first.")


def build_validation_targets() -> pd.DataFrame:
    daily = load_daily_chokepoints()
    edges = pd.read_csv(CONFIG_DIR / "edges.csv")
    cp_edges = edges[edges["edge_type"] == "chokepoint"].dropna(subset=["chokepoint_portid"]).copy()

    daily["n_tanker"] = pd.to_numeric(daily["n_tanker"], errors="coerce").fillna(0.0)
    daily["capacity_tanker"] = pd.to_numeric(daily.get("capacity_tanker", 0.0), errors="coerce").fillna(0.0)

    records = []
    for ev in EVENT_WINDOWS:
        pre_mask = daily["date"].between(pd.Timestamp(ev["pre_start"]), pd.Timestamp(ev["pre_end"]))
        cri_mask = daily["date"].between(pd.Timestamp(ev["crisis_start"]), pd.Timestamp(ev["crisis_end"]))

        for _, edge in cp_edges.iterrows():
            pid = str(edge["chokepoint_portid"])
            e_id = str(edge["edge_id"])
            passage = str(edge["searoute_passage"])
            cap_weekly = float(edge["capacity_proxy"]) if pd.notna(edge.get("capacity_proxy")) else 100.0

            sub_pre = daily[pre_mask & (daily["portid"] == pid)]
            sub_cri = daily[cri_mask & (daily["portid"] == pid)]
            if sub_pre.empty or sub_cri.empty:
                continue

            # Convert mean daily counts * 7 -> weekly rate (handles the 7-day Ever Given window cleanly)
            pre_weekly_tr = float(sub_pre["n_tanker"].mean() * 7.0)
            cri_weekly_tr = float(sub_cri["n_tanker"].mean() * 7.0)
            std_weekly_tr = max(float(sub_pre["n_tanker"].std() * np.sqrt(7.0)), 1.0)

            pre_vol_bbl = float(sub_pre["capacity_tanker"].mean() * 7.0 * DEFAULT_CRUDE_SHARE * BARRELS_PER_TONNE)
            cri_vol_bbl = float(sub_cri["capacity_tanker"].mean() * 7.0 * DEFAULT_CRUDE_SHARE * BARRELS_PER_TONNE)

            delta_tr = cri_weekly_tr - pre_weekly_tr
            pct_change_tr = (delta_tr / pre_weekly_tr * 100.0) if pre_weekly_tr > 0 else 0.0
            delta_vol = cri_vol_bbl - pre_vol_bbl
            share_rerouted = max(0.0, -delta_tr / pre_weekly_tr) if pre_weekly_tr > 0 else 0.0

            records.append(
                {
                    "scenario_id": ev["event_id"],
                    "event_name": ev["event_name"],
                    "edge_id": e_id,
                    "from_node": edge["from_node"],
                    "to_node": edge["to_node"],
                    "edge_type": "chokepoint",
                    "chokepoint_portid": pid,
                    "searoute_passage": passage,
                    "length_nm": float(edge["length_nm"]),
                    "pre_weekly_transits": round(pre_weekly_tr, 2),
                    "tanker_transits": round(cri_weekly_tr, 2),
                    "delta_transits": round(delta_tr, 2),
                    "pct_change_transits": round(pct_change_tr, 2),
                    "pre_oil_volume_bbl": round(pre_vol_bbl, 0),
                    "oil_volume_bbl": round(cri_vol_bbl, 0),
                    "delta_volume_bbl": round(delta_vol, 0),
                    "capacity_weekly": round(cap_weekly, 2),
                    "utilization": round(cri_weekly_tr / cap_weekly, 4) if cap_weekly > 0 else np.nan,
                    "anomaly_z": round(delta_tr / std_weekly_tr, 3),
                    "share_rerouted": round(share_rerouted, 4),
                    "source": "observed",
                }
            )

    out_df = pd.DataFrame(records)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(INTERIM_DIR / "validation_targets.csv", index=False)
    out_df.to_csv(PROCESSED_DIR / "validation_targets.csv", index=False)

    print(f"Saved observed validation targets ({len(out_df)} event-chokepoint rows) -> data/interim/validation_targets.csv")
    for sc in ("red_sea_2024", "red_sea_2023h1", "red_sea_2024_h2", "ever_given_2021_peak"):
        sub = out_df[out_df["scenario_id"] == sc]
        if sub.empty:
            continue
        print(f"\n{sc} observed chokepoint shifts:")
        print(
            sub[["edge_id", "searoute_passage", "pre_weekly_transits", "tanker_transits", "pct_change_transits", "anomaly_z"]]
            .to_string(index=False)
        )
    return out_df


if __name__ == "__main__":
    build_validation_targets()
