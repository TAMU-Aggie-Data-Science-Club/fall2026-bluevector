"""Pull the PortWatch ports database (static/averaged port info) and save it as CSV.

Run from the repository root:  python pull_ports_static.py
Needs:  pip install requests pandas
No API key needed. Outputs (never committed, data/ is git-ignored):
  data/raw/portwatch_ports_database_raw.csv       every row and column, as downloaded
  data/interim/portwatch_ports_database.csv       selected variables only

This is the static table (one row per port: coordinates, LOCODE, yearly average
vessel counts for 2019-2025, top industries, country shares). The daily tonnage
series comes from the Daily_Ports_Data layer, a different layer. Rank ports with
the daily import_* / export_* tons, NOT with vessel_count_* (counts, not tons).
"""
from pathlib import Path

import pandas as pd
import requests

# GUESS based on the chokepoints layer name (PortWatch_chokepoints_database).
# If this errors or lacks the columns, open the ArcGIS Hub page for the PortWatch
# ports dataset, copy the layer URL ending in /FeatureServer/0, and paste it here.
URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "PortWatch_ports_database/FeatureServer/0/query")

# Variables requested (matched case-insensitively, e.g. vessel_count_RoRo).
WANTED = [
    "portid", "portname", "lat", "lon",
    "vessel_count_total", "vessel_count_container", "vessel_count_dry_bulk",
    "vessel_count_general_cargo", "vessel_count_roro", "vessel_count_tanker",
    "industry_top1", "industry_top2", "industry_top3",
    "share_country_maritime_import", "share_country_maritime_export",
]
# Extra join keys, kept if present (useful for matching to Comtrade / GFW / WPI).
EXTRAS = ["country", "ISO3", "continent", "LOCODE"]

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
    df.to_csv(RAW / "portwatch_ports_database_raw.csv", index=False)
    print(f"{len(df)} ports, columns: {list(df.columns)}")

    # Case-insensitive column matching, then rename to the names we asked for.
    lookup = {c.lower(): c for c in df.columns}
    keep, missing = {}, []
    for name in WANTED + EXTRAS:
        src = lookup.get(name.lower())
        if src:
            keep[src] = name
        elif name in WANTED:
            missing.append(name)
    if missing:
        print(f"\nWARNING: requested columns not found: {missing}")
        print("This is probably the wrong layer. See the URL comment at the top.")

    out = df[list(keep)].rename(columns=keep)
    out.to_csv(INTERIM / "portwatch_ports_database.csv", index=False)
    print(f"\nSaved {len(out)} rows x {out.shape[1]} columns to "
          f"{INTERIM / 'portwatch_ports_database.csv'}")

    # Quick quality checks to record in DATA.md.
    print("\nNull rates (share of rows missing):")
    check = [c for c in ["lat", "lon", "LOCODE", "ISO3", "vessel_count_tanker"] if c in out.columns]
    print(out[check].isna().mean().round(3).to_string())
    if "portid" in out.columns:
        print(f"\nDuplicate portids: {out['portid'].duplicated().sum()}")
    if {"vessel_count_total", "portname"}.issubset(out.columns):
        print("\nTop 10 ports by yearly average vessel calls:")
        print(out.nlargest(10, "vessel_count_total")[["portid", "portname", "vessel_count_total"]]
              .to_string(index=False))

    # Optional: do the ids match the daily ports data? (needs the daily pull to have run)
    daily = RAW / "portwatch_ports_daily_raw.csv"   # adjust to your daily script's output name
    if daily.exists() and "portid" in out.columns:
        d = set(pd.read_csv(daily, usecols=["portid"])["portid"])
        s = set(out["portid"])
        print(f"\nIn this table but not in the daily data: {len(s - d)}")
        print(f"In the daily data but not in this table: {len(d - s)}")


if __name__ == "__main__":
    main()