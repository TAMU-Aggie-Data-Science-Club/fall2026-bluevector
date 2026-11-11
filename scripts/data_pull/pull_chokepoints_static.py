"""Pull the PortWatch chokepoints database (static info: coordinates, codes) and save it.

Run from the repository root:  python pull_chokepoints_static.py
Needs:  pip install requests pandas pyarrow
No API key needed. Outputs (never committed, data/ is git-ignored):
  data/raw/portwatch_chokepoints_database_raw.csv       every row, as downloaded
  data/interim/portwatch_chokepoints_database.parquet   same table, cleaned

This is the static table (one row per chokepoint, with `lat` and `lon`).
The daily counts come from pull_chokepoints.py.
"""
from pathlib import Path

import pandas as pd
import requests

# If this URL returns an error, copy the exact layer URL from the layer's page
# (the one that ends in /FeatureServer/0) and paste it here.
URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "PortWatch_chokepoints_database/FeatureServer/0/query")
DAILY_RAW = Path("data/raw/portwatch_chokepoints_daily_raw.csv")   # made by pull_chokepoints.py
# searoute passage name -> word to look for in `portname` (check the printed list and adjust)
PASSAGES = {"suez": "suez", "babalmandab": "bab", "ormuz": "hormuz", "malacca": "malacca",
            "gibraltar": "gibraltar", "panama": "panama", "south_africa": "cape of good hope"}

RAW = Path("data/raw"); INTERIM = Path("data/interim")
RAW.mkdir(parents=True, exist_ok=True); INTERIM.mkdir(parents=True, exist_ok=True)


def fetch_all() -> pd.DataFrame:
    rows, offset = [], 0
    while True:
        params = dict(where="1=1", outFields="*", orderByFields="ObjectId ASC",
                      resultOffset=offset, resultRecordCount=1000,
                      returnGeometry="false", f="json")
        r = requests.get(URL, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        feats = data.get("features", [])
        rows += [f["attributes"] for f in feats]
        if not feats or (len(feats) < 1000 and not data.get("exceededTransferLimit")):
            break
        offset += len(feats)
    return pd.DataFrame(rows)


def main():
    df = fetch_all()
    df.to_csv(RAW / "portwatch_chokepoints_database_raw.csv", index=False)
    print(f"{len(df)} chokepoints, columns: {list(df.columns)}")
    missing = df[df["lat"].isna() | df["lon"].isna()]
    print(f"Rows without coordinates: {len(missing)}")
    try:
        df.to_parquet(INTERIM / "portwatch_chokepoints_database.parquet", index=False)
    except ImportError:
        df.to_csv(INTERIM / "portwatch_chokepoints_database.csv", index=False)
        print("pyarrow not installed: saved CSV instead")

    cols = [c for c in ["portid", "portname", "lat", "lon", "LOCODE"] if c in df.columns]
    print("\nAll chokepoints:")
    print(df[cols].sort_values("portid").to_string(index=False))

    print("\nPriority chokepoints (searoute passage -> PortWatch row):")
    for passage, word in PASSAGES.items():
        hit = df[df["portname"].str.lower().str.contains(word)]
        if hit.empty:
            print(f"  {passage:12} NOT FOUND (word '{word}')")
        for _, r in hit.iterrows():
            print(f"  {passage:12} {r['portid']:14} {r['portname']:28} lat={r['lat']:.3f} lon={r['lon']:.3f}")

    # Do the ids match the daily table? (needs pull_chokepoints.py to have run first)
    if DAILY_RAW.exists():
        daily = pd.read_csv(DAILY_RAW, usecols=["portid"])
        a, b = set(df["portid"]), set(daily["portid"])
        print(f"\nIn this table but not in the daily data: {sorted(a - b)}")
        print(f"In the daily data but not in this table: {sorted(b - a)}")
    else:
        print("\nRun pull_chokepoints.py first to check the ids match the daily data.")


if __name__ == "__main__":
    main()