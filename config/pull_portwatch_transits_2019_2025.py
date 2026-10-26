"""Download IMF PortWatch daily chokepoint transits for 2019-01-01 to 2025-12-31.

From the repository root:
    pip install requests pandas pyarrow
    python config/pull_portwatch_transits_2019_2025.py
    python config/pull_portwatch_transits_2019_2025.py --refresh  # re-fetch revisions

No API key is required. Every original API response is cached by year and page
under data/raw/portwatch_transits_2019_2025/. Cleaned data and a quality report
are saved under data/interim/. The repository ignores everything under data/.

This downloads transit counts for ALL PortWatch chokepoints, not vessel-level
port-to-port voyages. Counts are observed AIS-based estimates; the `capacity_*`
fields are estimates of vessel carrying capacity (tonnes), not transit capacity.
"""

import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data/raw/portwatch_transits_2019_2025"
INTERIM = REPO / "data/interim"
URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "Daily_Chokepoints_Data/FeatureServer/0/query")
YEARS = range(2019, 2026)
PAGE_SIZE = 1000  # Server's advertised maximum
COUNT_FIELDS = ["n_container", "n_dry_bulk", "n_general_cargo", "n_roro",
                "n_tanker", "n_cargo", "n_total"]


def request_json(session, params):
    """Retry transient HTTP errors, and always check for ArcGIS JSON errors."""
    for attempt in range(4):
        try:
            response = session.get(URL, params=params, timeout=60)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or "error" in result:
                raise RuntimeError(f"PortWatch API error: {result}")
            time.sleep(0.2)
            return result
        except (requests.RequestException, ValueError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("Unreachable")


def read_or_fetch(session, path, params, refresh):
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    result = request_json(session, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return result


def fetch_year(session, year, refresh=False):
    """Fetch a complete year with a stable ObjectId sort and row-count audit."""
    folder = RAW / str(year)
    where = (f"date >= TIMESTAMP '{year}-01-01 00:00:00' AND "
             f"date < TIMESTAMP '{year + 1}-01-01 00:00:00'")
    count = read_or_fetch(session, folder / "count.json",
                          {"where": where, "returnCountOnly": "true", "f": "json"},
                          refresh)
    expected = int(count["count"])
    if expected <= 0:
        raise RuntimeError(f"No PortWatch observations for {year}")
    rows = []
    offset = 0
    while offset < expected:
        params = {"where": where, "outFields": "*", "returnGeometry": "false",
                  "orderByFields": "ObjectId ASC", "resultOffset": offset,
                  "resultRecordCount": PAGE_SIZE, "f": "json"}
        result = read_or_fetch(session, folder / f"page_{offset:06d}.json",
                               params, refresh)
        features = result.get("features")
        if not isinstance(features, list) or not features:
            raise RuntimeError(f"Empty/invalid page for {year}, offset {offset}")
        attributes = [feature["attributes"] for feature in features]
        rows.extend(attributes)
        offset += len(attributes)
        if len(attributes) > PAGE_SIZE:
            raise RuntimeError(f"Page limit exceeded for {year}")
        print(f"  {year}: {offset:,}/{expected:,}", flush=True)
    if len(rows) != expected:
        raise RuntimeError(f"Incomplete year {year}: got {len(rows)}, expected {expected}")
    return rows


def normalized_date(value):
    """Accept ArcGIS epoch milliseconds or ISO date-only / timestamp strings."""
    if value is None:
        raise ValueError("Missing observation date")
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def clean_and_audit(rows):
    if not rows:
        raise RuntimeError("No PortWatch data downloaded")
    df = pd.DataFrame.from_records(rows)
    needed = {"date", "portid", "portname", "n_tanker", "n_total", "ObjectId"}
    missing = sorted(needed - set(df.columns))
    if missing:
        raise RuntimeError(f"Missing required PortWatch fields: {missing}")
    df["date"] = df["date"].map(normalized_date)
    if df["portid"].isna().any() or df["portname"].isna().any():
        raise RuntimeError("Null chokepoint ID/name encountered")
    if not df["date"].between("2019-01-01", "2025-12-31").all():
        raise RuntimeError("API returned observations outside 2019–2025")
    if df.duplicated(["portid", "date"]).any():
        dup = df.loc[df.duplicated(["portid", "date"], keep=False),
                     ["portid", "date"]].head(5).to_dict("records")
        raise RuntimeError(f"Duplicate chokepoint/day rows: {dup}")
    if df["ObjectId"].duplicated().any():
        raise RuntimeError("Duplicate ArcGIS ObjectId: pagination might have shifted")
    for field in COUNT_FIELDS:
        if field in df.columns:
            df[field] = pd.to_numeric(df[field], errors="raise").astype("Int64")
            if (df[field].dropna() < 0).any():
                raise RuntimeError(f"Negative count values in {field}")
    for field in df.columns:
        if field.startswith("capacity"):
            df[field] = pd.to_numeric(df[field], errors="raise")
    df = df.sort_values(["date", "portid"]).reset_index(drop=True)
    report = {"source": URL, "start_date": "2019-01-01",
              "end_date": "2025-12-31", "total_rows": int(len(df)),
              "chokepoints": int(df["portid"].nunique()),
              "rows_per_year": df["date"].str[:4].value_counts().sort_index().to_dict(),
              "missing_days_by_portid": {}}
    expected_dates = set(pd.date_range("2019-01-01", "2025-12-31").strftime("%Y-%m-%d"))
    for portid, group in df.groupby("portid"):
        report["missing_days_by_portid"][str(portid)] = len(
            expected_dates - set(group["date"]))
    return df, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true",
                        help="Re-fetch all years rather than use cached API responses")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    INTERIM.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        rows = []
        for year in YEARS:
            rows.extend(fetch_year(session, year, refresh=args.refresh))
    df, report = clean_and_audit(rows)
    destination = INTERIM / "portwatch_transits_2019_2025.parquet"
    df.to_parquet(destination, index=False)
    audit = INTERIM / "portwatch_transits_2019_2025_quality.json"
    audit.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved {len(df):,} rows, {report['chokepoints']} chokepoints to {destination}")
    print(f"Quality report: {audit}")
    missing = sum(report["missing_days_by_portid"].values())
    if missing:
        print(f"WARNING: {missing:,} missing chokepoint/day observations (not filled with zeros)")


if __name__ == "__main__":
    main()
