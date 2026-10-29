"""EIA daily Brent spot price (RBRTE) plus Friday-ending weekly average.

What it does
    Pulls daily Europe Brent spot price (USD per barrel) from the EIA API v2,
    2019-01-01 to the latest available day, caches the raw JSON, and saves
    daily and weekly Parquet tables.

Output
    data/raw/eia/brent_daily.json                (cached API response)
    data/interim/eia_brent_daily.parquet         columns: date, brent_usd_per_bbl
    data/interim/eia_brent_weekly.parquet        columns: week_ending (Friday), brent_weekly_avg, n_days

How to run (from the repo root)
    export EIA_API_KEY=your_key      # or put EIA_API_KEY=... in a .env file (git-ignored)
    python scripts/eia_brent.py              # uses the cached response if present
    python scripts/eia_brent.py --refresh    # re-pull from the API

Needs
    pip install requests pandas pyarrow python-dotenv
    A free EIA API key: https://www.eia.gov/opendata/register.php
    The key is read from the environment only. Never write it into a file in the repo.

Notes
    * Holidays have no row in EIA's series. They are left missing, never filled with zero.
    * Weekly average = mean of the daily values that exist in that Friday-ending week;
      n_days says how many days went into it.
    * The API returns at most 5,000 rows per request, so this pages with offset.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import requests

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
SERIES = "RBRTE"          # Europe Brent Spot Price FOB, USD per barrel
START = "2019-01-01"
PAGE = 5000

ROOT = Path(__file__).resolve().parents[1]  # repo root, so it runs from anywhere
RAW_FILE = ROOT / "data" / "raw" / "eia" / "brent_daily.json"
INTERIM = ROOT / "data" / "interim"
DAILY_OUT = INTERIM / "eia_brent_daily.parquet"
WEEKLY_OUT = INTERIM / "eia_brent_weekly.parquet"


def fetch_all(api_key: str) -> list:
    rows, offset = [], 0
    while True:
        params = {
            "api_key": api_key,
            "frequency": "daily",
            "data[0]": "value",
            "facets[series][]": SERIES,
            "start": START,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": offset,
            "length": PAGE,
        }
        r = requests.get(API_URL, params=params, timeout=60)
        if r.status_code != 200:
            # Print status only; never print the URL (it contains the key).
            sys.exit(f"EIA request failed (HTTP {r.status_code}). Check the key and try again.")
        body = r.json()["response"]
        rows.extend(body["data"])
        total = int(body.get("total", len(rows)))
        offset += PAGE
        if offset >= total:
            break
    return rows


def clean(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["period"])
    df["brent_usd_per_bbl"] = pd.to_numeric(df["value"], errors="coerce")  # blanks -> NaN
    df = df.dropna(subset=["brent_usd_per_bbl"])
    df = df.sort_values("date").drop_duplicates("date")
    return df[["date", "brent_usd_per_bbl"]].reset_index(drop=True)


def weekly(daily: pd.DataFrame) -> pd.DataFrame:
    g = daily.set_index("date")["brent_usd_per_bbl"].resample("W-FRI")
    out = pd.DataFrame({"brent_weekly_avg": g.mean(), "n_days": g.count()}).reset_index()
    out = out.rename(columns={"date": "week_ending"})
    return out[out["n_days"] > 0].reset_index(drop=True)  # weeks with no data stay out, not zero


def report(daily: pd.DataFrame, wk: pd.DataFrame) -> None:
    gap = daily["date"].diff().dt.days.max()
    print(f"\nDaily rows: {len(daily)}   first: {daily['date'].min().date()}   latest: {daily['date'].max().date()}")
    print(f"Longest gap between observations: {int(gap)} days (4-5 is a normal holiday stretch)")
    print(f"Weekly rows: {len(wk)}   latest week ending: {wk['week_ending'].max().date()}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="Re-pull from the API even if cached")
    args = ap.parse_args()

    if RAW_FILE.exists() and not args.refresh:
        print(f"Using cached {RAW_FILE} (use --refresh to re-pull)")
        rows = json.loads(RAW_FILE.read_text())
    else:
        key = os.environ.get("EIA_API_KEY")
        if not key:
            sys.exit("EIA_API_KEY is not set. Put it in your environment or a .env file (never in the repo).")
        rows = fetch_all(key)
        RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
        RAW_FILE.write_text(json.dumps(rows))
        print(f"Pulled {len(rows)} rows, cached to {RAW_FILE}")

    daily = clean(rows)
    wk = weekly(daily)
    INTERIM.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(DAILY_OUT, index=False)
    wk.to_parquet(WEEKLY_OUT, index=False)
    print(f"Saved {DAILY_OUT} and {WEEKLY_OUT}")
    report(daily, wk)


if __name__ == "__main__":
    main()
