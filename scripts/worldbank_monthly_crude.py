"""World Bank Pink Sheet: monthly crude oil prices (Brent, Dubai, WTI, average).

What it does
    Reads CMO-Historical-Data-Monthly.xlsx (sheet "Monthly Prices"), keeps the four
    crude oil columns from 2019-01 onward, and saves a clean Parquet table.

Output
    data/interim/worldbank_monthly_crude.parquet
    columns: month (YYYY-MM-DD, first of month), avg_usd_per_bbl, brent_usd_per_bbl,
             dubai_usd_per_bbl, wti_usd_per_bbl   (nominal USD per barrel)

How to run (from the repo root)
    # Option A: use the file you already downloaded
    python scripts/worldbank_monthly_crude.py --file path/to/CMO-Historical-Data-Monthly.xlsx

    # Option B: download it (needs the current URL in WB_XLSX_URL below)
    python scripts/worldbank_monthly_crude.py

    # Re-download even if a cached copy exists
    python scripts/worldbank_monthly_crude.py --refresh

Needs
    pip install requests pandas pyarrow openpyxl
    No API key. No key is read or stored by this script.

Notes
    * The World Bank changes the download link with every monthly release. If Option B
      fails with a 404, open https://www.worldbank.org/en/research/commodity-markets,
      copy the "Monthly prices (XLS)" link, and paste it into WB_XLSX_URL.
    * ".." in the sheet means "not available". It becomes a missing value (NaN).
    * Dates look like 2026M09 and become 2026-09-01.
    * The raw xlsx is cached in data/raw/worldbank/. Nothing under data/ is committed (it is git-ignored).
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import requests

# Update this link from the World Bank commodity markets page when a new release is out.
WB_XLSX_URL = (
    "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)
SHEET_NAME = "Monthly Prices"
START_DATE = "2019-01-01"

ROOT = Path(__file__).resolve().parents[1]  # repo root, so it runs from anywhere
RAW_DIR = ROOT / "data" / "raw" / "worldbank"
INTERIM_DIR = ROOT / "data" / "interim"
RAW_FILE = RAW_DIR / "CMO-Historical-Data-Monthly.xlsx"
OUT_FILE = INTERIM_DIR / "worldbank_monthly_crude.parquet"

# Header text in the sheet -> clean column name
COLUMNS = {
    "Crude oil, average": "avg_usd_per_bbl",
    "Crude oil, Brent": "brent_usd_per_bbl",
    "Crude oil, Dubai": "dubai_usd_per_bbl",
    "Crude oil, WTI": "wti_usd_per_bbl",
}

# Values seen in the Oct 2026 release, used only as a printed sanity check (warns, never fails).
# The World Bank can revise recent months, so a small mismatch is not necessarily a bug.
SPOT_CHECK = {
    "2026-08-01": {"brent_usd_per_bbl": 90.9, "dubai_usd_per_bbl": 79.7, "wti_usd_per_bbl": 82.7},
    "2026-09-01": {"brent_usd_per_bbl": 116.8, "dubai_usd_per_bbl": 93.3, "wti_usd_per_bbl": 96.9},
}

DATE_CODE = re.compile(r"^\d{4}M\d{2}$")


def norm(text) -> str:
    """Collapse whitespace/newlines and strip footnote markers like '**' or ' a/'."""
    s = " ".join(str(text).split())
    s = re.sub(r"\s*(\*+|[a-z]/)$", "", s)
    return s.strip()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
    if resp.status_code != 200:
        sys.exit(
            f"Download failed (HTTP {resp.status_code}). The link has probably changed: "
            "update WB_XLSX_URL, or download the file by hand and use --file."
        )
    dest.write_bytes(resp.content)
    print(f"Downloaded {len(resp.content) / 1e6:.1f} MB to {dest}")


def parse(xlsx_path: Path) -> pd.DataFrame:
    raw = pd.read_excel(xlsx_path, sheet_name=SHEET_NAME, header=None, dtype=object)

    # Find the header row by content, not by a fixed row number.
    header_row = None
    for i in range(min(20, len(raw))):
        if any(norm(v) == "Crude oil, Brent" for v in raw.iloc[i].tolist()):
            header_row = i
            break
    if header_row is None:
        sys.exit('Could not find a "Crude oil, Brent" header in the first 20 rows. '
                 "The sheet layout may have changed; open the file and check.")

    # Map each wanted header to its column position.
    positions = {}
    for j, v in enumerate(raw.iloc[header_row].tolist()):
        name = norm(v)
        if name in COLUMNS and COLUMNS[name] not in positions:
            positions[COLUMNS[name]] = j
    missing = set(COLUMNS.values()) - set(positions)
    if missing:
        sys.exit(f"Missing columns in the sheet: {sorted(missing)}")

    # Data rows are the ones whose first cell looks like 2026M09.
    first = raw.iloc[:, 0].map(lambda v: norm(v) if pd.notna(v) else "")
    is_data = first.map(lambda s: bool(DATE_CODE.match(s)))
    data = raw[is_data].copy()
    codes = first[is_data]

    out = pd.DataFrame({"month": pd.to_datetime(codes.str[:4] + "-" + codes.str[5:] + "-01")})
    for clean, j in positions.items():
        # ".." and any other text become NaN
        out[clean] = pd.to_numeric(data.iloc[:, j], errors="coerce").astype(float).values
    out = out.reset_index(drop=True)
    out = out[out["month"] >= pd.Timestamp(START_DATE)].reset_index(drop=True)
    return out[["month", "avg_usd_per_bbl", "brent_usd_per_bbl", "dubai_usd_per_bbl", "wti_usd_per_bbl"]]


def check(df: pd.DataFrame) -> None:
    print(f"\nRows: {len(df)}   first month: {df['month'].min().date()}   latest month: {df['month'].max().date()}")

    expected = pd.date_range(df["month"].min(), df["month"].max(), freq="MS")
    skipped = sorted(set(expected) - set(df["month"]))
    print("Missing months:", [d.strftime("%Y-%m") for d in skipped] or "none")

    nulls = df.drop(columns="month").isna().sum()
    print("Missing values per column:", nulls.to_dict())

    for month, vals in SPOT_CHECK.items():
        row = df[df["month"] == pd.Timestamp(month)]
        if row.empty:
            continue
        for col, want in vals.items():
            got = float(row[col].iloc[0])
            flag = "ok" if abs(got - want) < 0.05 else "WARNING (revised or wrong column?)"
            print(f"Spot check {month[:7]} {col}: file={got} expected={want} -> {flag}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, help="Use a local copy of the xlsx instead of downloading")
    ap.add_argument("--refresh", action="store_true", help="Re-download even if cached")
    args = ap.parse_args()

    if args.file:
        xlsx = args.file
        if not xlsx.exists():
            sys.exit(f"File not found: {xlsx}")
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        if xlsx.resolve() != RAW_FILE.resolve():
            RAW_FILE.write_bytes(xlsx.read_bytes())  # keep a cached copy under data/raw
    else:
        if args.refresh or not RAW_FILE.exists():
            download(WB_XLSX_URL, RAW_FILE)
        else:
            print(f"Using cached file {RAW_FILE} (use --refresh to re-download)")
        xlsx = RAW_FILE

    df = parse(xlsx)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_FILE, index=False)
    print(f"Saved {OUT_FILE}")
    check(df)


if __name__ == "__main__":
    main()
