"""Download IMF PortWatch DAILY PORT data (port calls and estimated import/export
tons by vessel type) for 2019-01-01 to 2025-12-31.

From the repository root:
    pip install requests pandas pyarrow
    python config/pull_ports_daily_2019_2025.py
    python config/pull_ports_daily_2019_2025.py --refresh  # re-fetch revisions
    python config/pull_ports_daily_2019_2025.py --ports config/nodes.csv   # selected ports only (fast)
    python config/pull_ports_daily_2019_2025.py --workers 6   # years downloaded in parallel (default 4)

FAST PATH: pull_ports_totals.py ranks ports in seconds. After choosing ~100 ports,
use --ports (a CSV with a `portid` or `portwatch_portid` column; other rows, such
as junctions with no id, are ignored) to download only those ports' daily series.
Filtered outputs get a _selected suffix.

No API key is required. Outputs (never committed, data/ is git-ignored):
  data/raw/portwatch_ports_daily_raw.csv        every row, as downloaded
  data/interim/portwatch_ports_daily.parquet    same rows, cleaned and typed
  data/interim/portwatch_ports_daily_quality.json   row counts, gaps, null rates
Every original API response is also cached by year and page under
data/raw/portwatch_ports_daily_2019_2025/ so reruns and restarts use no quota.

Without --ports this pulls ALL PortWatch ports, which is large (millions of rows at
1,000 per request); use pull_ports_totals.py for ranking instead. Reruns read the
cache. Port calls are observed AIS-based counts;
`import_*` and `export_*` are modeled tonnage ESTIMATES, not customs data, and
"tanker" is not crude-only.
"""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[1]
RAW_DIR = REPO / "data/raw"
RAW = RAW_DIR / "portwatch_ports_daily_2019_2025"   # page cache
INTERIM = REPO / "data/interim"
URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "Daily_Ports_Data/FeatureServer/0/query")
YEARS = range(2019, 2026)
PAGE_SIZE = 1000  # Server's advertised maximum
PAGE_SIZES = [1000, 500, 100, 25]  # tried in order if a page keeps failing
VESSEL_TYPES = ["container", "dry_bulk", "general_cargo", "roro", "tanker"]
# Needed for the Port selection formula in DATA.md (import + export tons, all types and tanker)
REQUIRED = {"date", "portid", "portname", "country", "ISO3", "ObjectId",
            "import_tanker", "export_tanker"}


def request_json(session, params, attempts=4):
    """Retry network errors AND ArcGIS error replies (the service intermittently
    answers 'Invalid query parameters' to queries that succeed on a retry)."""
    last = "unknown error"
    for attempt in range(attempts):
        try:
            response = session.get(URL, params=params, timeout=60)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                last = f"Unexpected response: {str(result)[:200]}"
            elif "error" in result:
                last = f"PortWatch API error: {result}\nRequest was: {response.url}"
            else:
                time.sleep(0.1)
                return result
        except (requests.RequestException, ValueError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(2 ** attempt)  # 1, 2, 4, 8 s
    raise RuntimeError(last)


def read_or_fetch(session, path, params, refresh):
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    result = request_json(session, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return result


def fetch_year(session, year, refresh=False, extra_where=None, cache_root=None):
    """Fetch a complete year by ObjectId keyset paging, with a row-count audit.
    extra_where (e.g. a portid IN (...) filter) gets its own cache_root so cached
    pages from a filtered pull are never mixed with a full pull."""
    folder = (cache_root or RAW) / str(year)
    # The ports layer has integer year/month/day fields, so filter on `year`
    # rather than TIMESTAMP syntax (which returned "Invalid URL").
    where = f"year = {year}" + (f" AND ({extra_where})" if extra_where else "")
    count = read_or_fetch(session, folder / "count.json",
                          {"where": where, "returnCountOnly": "true", "f": "json"},
                          refresh)
    expected = int(count["count"])
    if expected <= 0:
        raise RuntimeError(f"No PortWatch observations for {year}")
    # Keyset paging: ask for rows with ObjectId above the last one seen, instead of
    # a growing resultOffset (the server rejected deep offsets, e.g. 7000).
    rows = []
    last_id = -1
    page = 0
    size_idx = 0  # index into PAGE_SIZES; sticks to the size that last worked
    while len(rows) < expected:
        result, failures = None, []
        if page % 25 == 0:
            size_idx = 0  # periodically try the full page size again
        for idx in range(size_idx, len(PAGE_SIZES)):  # smaller pages if a size keeps failing
            size = PAGE_SIZES[idx]
            params = {"where": f"{where} AND ObjectId > {last_id}", "outFields": "*",
                      "returnGeometry": "false", "orderByFields": "ObjectId ASC",
                      "resultRecordCount": size, "f": "json"}
            try:
                result = read_or_fetch(session, folder / f"page_{page:05d}.json",
                                       params, refresh)
                if idx != size_idx:
                    print(f"  {year}: page {page} needed page size {size}", flush=True)
                size_idx = idx
                break
            except RuntimeError as exc:
                failures.append(f"size {size}: {exc}")
        if result is None:
            raise RuntimeError(f"Page {page} of {year} failed at every page size "
                               f"after ObjectId {last_id}:\n" + "\n".join(failures))
        features = result.get("features")
        if not isinstance(features, list) or not features:
            raise RuntimeError(f"Empty/invalid page {page} for {year} "
                               f"after ObjectId {last_id}; have {len(rows):,}/{expected:,}")
        attributes = [feature["attributes"] for feature in features]
        if len(attributes) > PAGE_SIZE:
            raise RuntimeError(f"Page limit exceeded for {year}")
        oid_key = next((k for k in attributes[0] if k.lower() == "objectid"), None)
        if oid_key is None:
            raise RuntimeError(f"No ObjectId in response; columns: {list(attributes[0])}")
        for a in attributes:
            if oid_key != "ObjectId":
                a["ObjectId"] = a.pop(oid_key)
        new_last = max(a["ObjectId"] for a in attributes)
        if new_last <= last_id:
            raise RuntimeError(f"ObjectId did not advance for {year} (page {page})")
        last_id = new_last
        rows.extend(attributes)
        page += 1
        if page % 25 == 0 or len(rows) >= expected:
            print(f"  {year}: {len(rows):,}/{expected:,}", flush=True)
    if len(rows) != expected:
        raise RuntimeError(f"Incomplete year {year}: got {len(rows)}, expected {expected}")
    return pd.DataFrame.from_records(rows)  # one frame per year keeps memory lower


def normalized_date(value):
    """Accept ArcGIS epoch milliseconds or ISO date-only / timestamp strings."""
    if value is None:
        raise ValueError("Missing observation date")
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def clean_and_audit(df):
    if df.empty:
        raise RuntimeError("No PortWatch data downloaded")
    missing = sorted(REQUIRED - set(df.columns))
    if missing:
        raise RuntimeError(f"Missing required PortWatch fields: {missing} "
                           f"(columns found: {list(df.columns)})")
    df["date"] = df["date"].map(normalized_date)
    if df["portid"].isna().any() or df["portname"].isna().any():
        raise RuntimeError("Null port ID/name encountered")
    if not df["date"].between("2019-01-01", "2025-12-31").all():
        raise RuntimeError("API returned observations outside 2019–2025")
    if df.duplicated(["portid", "date"]).any():
        dup = df.loc[df.duplicated(["portid", "date"], keep=False),
                     ["portid", "date"]].head(5).to_dict("records")
        raise RuntimeError(f"Duplicate port/day rows: {dup}")
    if df["ObjectId"].duplicated().any():
        raise RuntimeError("Duplicate ArcGIS ObjectId: pagination might have shifted")

    # Port calls are counts; import_* / export_* are metric-ton estimates (all integers).
    count_fields = [c for c in df.columns if c.startswith("portcalls")]
    tonnage_fields = [c for c in df.columns if c.startswith(("import", "export"))]
    for field in count_fields + tonnage_fields:
        df[field] = pd.to_numeric(df[field], errors="raise")
        if (df[field].dropna() < 0).any():
            raise RuntimeError(f"Negative values in {field}")
    for field in count_fields + tonnage_fields:  # all integer fields in the layer schema
        df[field] = df[field].round().astype("Int64")
    df = df.sort_values(["date", "portid"]).reset_index(drop=True)

    # Port coverage: how many of the 2,557 days each port has (days are not zero-filled).
    n_days = len(pd.date_range("2019-01-01", "2025-12-31"))
    days_per_port = df.groupby("portid")["date"].nunique()
    incomplete = days_per_port[days_per_port < n_days]
    null_rates = df[tonnage_fields].isna().mean().round(4).to_dict()
    report = {"source": URL, "start_date": "2019-01-01", "end_date": "2025-12-31",
              "total_rows": int(len(df)), "ports": int(df["portid"].nunique()),
              "rows_per_year": df["date"].str[:4].value_counts().sort_index().to_dict(),
              "days_in_period": n_days,
              "ports_with_missing_days": int(len(incomplete)),
              "fewest_days_for_a_port": int(days_per_port.min()),
              "null_rate_tonnage_fields": null_rates,
              "tonnage_fields": tonnage_fields, "portcall_fields": count_fields}
    df["date"] = pd.to_datetime(df["date"])  # real datetimes in the cleaned file
    return df, report


def print_sanity_check(df):
    """Top ports by estimated tons, using the Port selection formula inputs from DATA.md."""
    imp = [f"import_{t}" for t in VESSEL_TYPES if f"import_{t}" in df.columns]
    exp = [f"export_{t}" for t in VESSEL_TYPES if f"export_{t}" in df.columns]
    tons = df[["portid", "portname", "country"]].copy()
    tons["tons_total"] = df[imp + exp].sum(axis=1, skipna=True)
    tons["tons_tanker"] = df[["import_tanker", "export_tanker"]].sum(axis=1, skipna=True)
    top = (tons.groupby(["portid", "portname", "country"], as_index=False)
           [["tons_total", "tons_tanker"]].sum())
    print("\nTop 10 ports by estimated total tons, 2019-2025 (sanity check):")
    print(top.nlargest(10, "tons_total").to_string(index=False))
    print("\nTop 10 ports by estimated tanker tons:")
    print(top.nlargest(10, "tons_tanker").to_string(index=False))


def load_port_ids(path):
    """Read portids from a CSV (column `portid` or `portwatch_portid`)."""
    ids = pd.read_csv(path, dtype=str)
    col = next((c for c in ("portid", "portwatch_portid") if c in ids.columns), None)
    if col is None:
        raise SystemExit(f"{path} needs a `portid` or `portwatch_portid` column; has {list(ids.columns)}")
    ids = sorted(set(ids[col].dropna().str.strip()) - {""})
    if not ids:
        raise SystemExit(f"No portids found in {path}")
    return ids


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true",
                        help="Re-fetch all years rather than use cached API responses")
    parser.add_argument("--ports", help="CSV of selected ports: pull only their daily rows")
    parser.add_argument("--workers", type=int, default=4,
                        help="Years downloaded in parallel (default 4; lower it if you see errors)")
    args = parser.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    INTERIM.mkdir(parents=True, exist_ok=True)

    extra_where, cache_root, suffix, wanted = None, None, "", []
    if args.ports:
        wanted = load_port_ids(args.ports)
        quoted = ",".join("'" + i.replace("'", "''") + "'" for i in wanted)
        extra_where = f"portid IN ({quoted})"
        tag = hashlib.md5(",".join(wanted).encode()).hexdigest()[:8]
        cache_root, suffix = RAW / f"selected_{tag}", "_selected"
        print(f"Selected ports only: {len(wanted)} portids (cache: {cache_root.name})")

    def worker(year):
        with requests.Session() as session:  # one session per thread
            return fetch_year(session, year, args.refresh, extra_where, cache_root)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        frames = list(pool.map(worker, YEARS))
    df = pd.concat(frames, ignore_index=True)
    df["date"] = df["date"].map(normalized_date)   # readable dates in the raw CSV
    raw_path = RAW_DIR / f"portwatch_ports_daily{suffix}_raw.csv"
    df.to_csv(raw_path, index=False)
    print(f"Raw: {len(df):,} rows saved to {raw_path}")

    df, report = clean_and_audit(df)
    destination = INTERIM / f"portwatch_ports_daily{suffix}.parquet"
    try:
        df.to_parquet(destination, index=False)
    except ImportError:
        destination = INTERIM / f"portwatch_ports_daily{suffix}.csv"
        df.to_csv(destination, index=False)
        print("pyarrow not installed: saved CSV instead")
    audit = INTERIM / f"portwatch_ports_daily{suffix}_quality.json"
    audit.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved {len(df):,} rows, {report['ports']:,} ports to {destination}")
    print(f"Quality report: {audit}")
    if report["ports_with_missing_days"]:
        print(f"NOTE: {report['ports_with_missing_days']:,} ports lack some days "
              f"(fewest: {report['fewest_days_for_a_port']} of {report['days_in_period']}); "
              "missing days are not filled with zeros")
    if wanted:
        absent = sorted(set(wanted) - set(df["portid"]))
        print(f"\nRequested portids with no data: {len(absent)}" + (f" -> {absent}" if absent else ""))
    print_sanity_check(df)


if __name__ == "__main__":
    main()