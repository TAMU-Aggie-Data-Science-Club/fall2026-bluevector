"""Pull PortWatch daily chokepoint data and save raw + cleaned files.

Run from the repository root:  python pull_chokepoints.py
Needs:  pip install requests pandas pyarrow
No API key needed. Outputs (never committed, data/ is git-ignored):
  data/raw/portwatch_chokepoints_daily_raw.csv       every row, as downloaded
  data/interim/portwatch_chokepoints_daily.parquet   priority chokepoints, 2019 onward
"""
import time
from pathlib import Path

import pandas as pd
import requests

URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "Daily_Chokepoints_Data/FeatureServer/0/query")
PAGE = 1000                      # the layer returns at most 1,000 rows per request
START = "2019-01-01"
# Matched case-insensitively against `portname`; check the printed list and adjust.
PRIORITY = ["suez", "bab", "hormuz", "malacca", "gibraltar", "panama", "cape of good hope"]

RAW = Path("data/raw"); INTERIM = Path("data/interim")
RAW.mkdir(parents=True, exist_ok=True); INTERIM.mkdir(parents=True, exist_ok=True)


def fetch_all() -> pd.DataFrame:
    rows, offset = [], 0
    while True:
        params = dict(where="1=1", outFields="*", orderByFields="date ASC",
                      resultOffset=offset, resultRecordCount=PAGE,
                      returnGeometry="false", f="json")
        r = requests.get(URL, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        feats = data.get("features", [])
        rows += [f["attributes"] for f in feats]
        print(f"  fetched {len(rows):,} rows", end="\r")
        if not feats or (len(feats) < PAGE and not data.get("exceededTransferLimit")):
            break
        offset += len(feats)
        time.sleep(0.2)               # be polite to the service
    print()
    return pd.DataFrame(rows)


def to_datetime(s: pd.Series) -> pd.Series:
    # ArcGIS date fields usually arrive as epoch milliseconds
    return pd.to_datetime(s, unit="ms") if pd.api.types.is_numeric_dtype(s) else pd.to_datetime(s)


def main():
    df = fetch_all()
    df["date"] = to_datetime(df["date"])
    df.to_csv(RAW / "portwatch_chokepoints_daily_raw.csv", index=False)
    print(f"Raw: {len(df):,} rows, {df['portname'].nunique()} chokepoints, "
          f"{df['date'].min().date()} to {df['date'].max().date()}")
    print("All chokepoint names:", sorted(df["portname"].unique()))

    pat = "|".join(PRIORITY)
    keep = df[df["portname"].str.lower().str.contains(pat) & (df["date"] >= START)].copy()
    print("\nKept (check these are the chokepoints you want):")
    print(keep.groupby(["portid", "portname"])["date"].agg(["min", "max", "count"]))
    try:
        keep.to_parquet(INTERIM / "portwatch_chokepoints_daily.parquet", index=False)
    except ImportError:
        keep.to_csv(INTERIM / "portwatch_chokepoints_daily.csv", index=False)
        print("pyarrow not installed: saved CSV instead")

    # Known real values from subteam A's check (2026-10-11): Suez 20, Cape of Good Hope 15
    chk = df[df["date"].dt.strftime("%Y-%m-%d") == "2026-10-11"]
    print("\nSanity check, expect Suez n_tanker=20 and Cape of Good Hope n_tanker=15:")
    print(chk[chk["portname"].str.lower().str.contains("suez|cape of good hope")][["portname", "n_tanker"]])
    # Gaps: days missing inside each series
    for name, g in keep.groupby("portname"):
        full = pd.date_range(g["date"].min(), g["date"].max())
        missing = len(full) - g["date"].nunique()
        if missing:
            print(f"  gap warning: {name} is missing {missing} days")


if __name__ == "__main__":
    main()