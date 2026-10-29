"""Pull PortWatch port TOTALS (2019-2025) by letting the server do the summing.

Run from the repository root:  python config/pull_ports_totals.py
Needs:  pip install requests pandas pyarrow
No API key needed. Outputs (never committed, data/ is git-ignored):
  data/raw/portwatch_ports_totals_raw.csv       one row per port, sums as returned
  data/interim/portwatch_ports_totals.parquet   cleaned, with tons_total, tons_tanker and ranks

Why: the daily layer has millions of rows (1,000 per request), but ranking ports
for the Port selection formula only needs each port's total import + export tons.
ArcGIS can compute those sums server-side (outStatistics) and return ~2,000 rows.
The server times out (~55 s) if asked to sum all seven years at once, so the sums are
requested one half-year at a time (splitting further if a slice is still too slow),
several slices in parallel, and added up here. Pull the DAILY series afterwards for just
the selected ports:  python config/pull_ports_daily_2019_2025.py --ports <csv>

Tons are PortWatch ESTIMATES (not customs data), and "tanker" is not crude-only.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data/raw"; INTERIM = REPO / "data/interim"
URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "Daily_Ports_Data/FeatureServer/0/query")
# The server rejects grouping by the long text fields (portname, country, ISO3), so we group by
# portid only and get the names separately (max() per port, else the static ports table).
NAME_FIELDS = ["portname", "country", "ISO3"]
TYPES = ["container", "dry_bulk", "general_cargo", "roro", "tanker"]
# `import` and `export` are SQL reserved words, so we sum the per-type fields and add them up here.
SUM_FIELDS = ([f"import_{t}" for t in TYPES] + [f"export_{t}" for t in TYPES]
              + ["portcalls", "portcalls_tanker"])
PAGE = 1000
YEARS = range(2019, 2026)
WORKERS = 1        # slices fetched in parallel
SLOW_SECONDS = 30  # an error after this long is a server timeout, not a flaky reply


class SlowQuery(RuntimeError):
    """The server failed only after a long time: a server-side timeout, so retrying is pointless."""


def get(params, attempts=5, label=""):
    """One request, sent as POST (the outStatistics JSON is too long for a GET URL: ArcGIS
    answered 404). Retries quick errors (the service is flaky). An error reply that took
    over SLOW_SECONDS is a server timeout: raise SlowQuery so the caller can ask for less."""
    last = "unknown error"
    for attempt in range(attempts):
        started = time.time()
        try:
            r = requests.post(URL, data=params, timeout=240)
            if r.status_code in (404, 414):  # not transient: fail fast with the cause
                raise RuntimeError(f"{label} HTTP {r.status_code} (request body: {len(r.request.body or '')} chars)")
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                elapsed = time.time() - started
                last = f"{label} PortWatch API error {data['error']} after {elapsed:.1f}s"
                if elapsed > SLOW_SECONDS:
                    raise SlowQuery(last)
            else:
                return data
        except (requests.RequestException, ValueError) as exc:
            last = f"{label} {type(exc).__name__}: {exc}"
        time.sleep(2 ** attempt)
    raise RuntimeError(last)


def sum_stat(field):
    return {"statisticType": "sum", "onStatisticField": field, "outStatisticFieldName": f"sum_{field}"}


def fetch_slice(year, m0, m1, stats) -> pd.DataFrame:
    """Grouped statistics for one slice of time (months m0..m1 of a year), all ports, paged by portid."""
    where = f"year = {year} AND month >= {m0} AND month <= {m1}"
    stats_json = json.dumps(stats)
    rows, offset = [], 0
    while True:
        params = dict(where=where, groupByFieldsForStatistics="portid", outStatistics=stats_json,
                      orderByFields="portid ASC", resultOffset=offset, resultRecordCount=PAGE,
                      returnGeometry="false", f="json")
        data = get(params, attempts=3, label=f"[{year} months {m0}-{m1} offset={offset}]")
        feats = data.get("features", [])
        rows += [f["attributes"] for f in feats]
        if not feats or (len(feats) < PAGE and not data.get("exceededTransferLimit")):
            break
        offset += len(feats)
    return pd.DataFrame(rows)


def fetch_slice_adaptive(year, m0, m1, stats) -> pd.DataFrame:
    """Try the slice; if the server times out, split the months in half and try again."""
    try:
        frame = fetch_slice(year, m0, m1, stats)
        print(f"  {year} months {m0:>2}-{m1:<2} done ({len(frame):,} ports)", flush=True)
        return frame
    except SlowQuery as exc:
        if m0 == m1:
            raise RuntimeError(f"Even one month ({year}-{m0:02d}) times out: {exc}")
        mid = (m0 + m1) // 2
        print(f"  {year} months {m0}-{m1} too slow for the server; splitting at month {mid}", flush=True)
        return pd.concat([fetch_slice_adaptive(year, m0, mid, stats),
                          fetch_slice_adaptive(year, mid + 1, m1, stats)], ignore_index=True)


def fetch_totals() -> pd.DataFrame:
    """Sum each slice on the server (small, fast requests), then add the slices up here."""
    stats = [sum_stat(f) for f in SUM_FIELDS]
    stats.append({"statisticType": "count", "onStatisticField": "portid", "outStatisticFieldName": "n_days"})
    slices = [(y, m0, m1) for y in YEARS for m0, m1 in ((1, 6), (7, 12))]
    print(f"Fetching {len(slices)} half-year slices, {WORKERS} at a time...")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        frames = list(pool.map(lambda sl: fetch_slice_adaptive(*sl, stats), slices))
    allrows = pd.concat(frames, ignore_index=True)
    allrows["portid"] = allrows["portid"].astype(str)
    df = allrows.groupby("portid", as_index=False).sum(numeric_only=True)
    # names are optional: group-by-name is rejected, but max() per port in ONE month may work
    try:
        name_stats = [{"statisticType": "max", "onStatisticField": f, "outStatisticFieldName": f"{f}_max"}
                      for f in NAME_FIELDS]
        names = fetch_slice(YEARS[-1], 6, 6, name_stats)
        names["portid"] = names["portid"].astype(str)
        df = df.merge(names, on="portid", how="left")
    except RuntimeError as exc:
        print(f"  names not available from the server ({str(exc)[:100]}...); will use the static ports table.")
    return df


def attach_static_names(out):
    """Fill portname / country / ISO3 from the static ports table (pull_ports_static.py)."""
    path = INTERIM / "portwatch_ports_database.csv"
    if not path.exists():
        print("WARNING: no names available. Run pull_ports_static.py (data/interim/portwatch_ports_database.csv "
              "needed), then rerun this script; names are left empty for now.")
        return out
    static = pd.read_csv(path, dtype=str)
    low = {c.lower(): c for c in static.columns}
    static = static.drop_duplicates(low["portid"]).set_index(low["portid"])
    for name in NAME_FIELDS:
        if name.lower() in low:
            out[name] = out[name].fillna(out["portid"].map(static[low[name.lower()]]))
    print(f"Names after static table: {out['portname'].notna().sum()}/{len(out)} ports have a name")
    return out


def main():
    RAW.mkdir(parents=True, exist_ok=True); INTERIM.mkdir(parents=True, exist_ok=True)
    df = fetch_totals()
    if df.empty:
        raise RuntimeError("No rows returned; check the layer URL and the YEARS range")
    df.to_csv(RAW / "portwatch_ports_totals_raw.csv", index=False)
    print(f"Raw: {len(df):,} ports, columns: {list(df.columns)}")

    # Match the sum_* columns case-insensitively (the server may change the case).
    lookup = {c.lower(): c for c in df.columns}
    def col(name):
        return df[lookup[name.lower()]].fillna(0)
    missing = [f for f in SUM_FIELDS if f"sum_{f}".lower() not in lookup]
    if missing:
        raise RuntimeError(f"Server did not return sums for: {missing}; columns: {list(df.columns)}")

    out = pd.DataFrame({"portid": df[lookup["portid"]].astype(str)})
    for name in NAME_FIELDS:
        src = lookup.get(f"{name}_max".lower())
        out[name] = df[src] if src else pd.NA
    out["tons_import_total"] = sum(col(f"sum_import_{t}") for t in TYPES)
    out["tons_export_total"] = sum(col(f"sum_export_{t}") for t in TYPES)
    out["tons_total"] = out["tons_import_total"] + out["tons_export_total"]
    out["tons_tanker"] = col("sum_import_tanker") + col("sum_export_tanker")
    out["portcalls_total"] = col("sum_portcalls")
    out["portcalls_tanker"] = col("sum_portcalls_tanker")
    if "n_days" in lookup:
        out["n_days"] = df[lookup["n_days"]]
    out["rank_total"] = out["tons_total"].rank(ascending=False, method="first").astype(int)
    out["rank_tanker"] = out["tons_tanker"].rank(ascending=False, method="first").astype(int)
    out = out.sort_values("rank_total").reset_index(drop=True)
    if out["portname"].isna().any():
        out = attach_static_names(out)

    destination = INTERIM / "portwatch_ports_totals.parquet"
    try:
        out.to_parquet(destination, index=False)
    except ImportError:
        destination = INTERIM / "portwatch_ports_totals.csv"
        out.to_csv(destination, index=False)
        print("pyarrow not installed: saved CSV instead")
    print(f"Saved {len(out):,} ports to {destination}")

    print(f"\nPortWatch ports: {len(out):,}; duplicate portids: {out['portid'].duplicated().sum()}")
    print("Top 15 by estimated total tons, 2019-2025 (sanity check):")
    print(out.head(15)[["rank_total", "portid", "portname", "country", "tons_total"]].to_string(index=False))
    print("\nTop 15 by estimated tanker tons:")
    print(out.nsmallest(15, "rank_tanker")[["rank_tanker", "portid", "portname", "country", "tons_tanker"]]
          .to_string(index=False))
    top100 = out.nsmallest(60, "rank_total")
    print(f"\nTop 60 by total cover {top100['tons_total'].sum() / out['tons_total'].sum():.1%} of global estimated tons")


if __name__ == "__main__":
    main()