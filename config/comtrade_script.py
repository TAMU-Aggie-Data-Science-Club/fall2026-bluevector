"""
UN Comtrade crude oil (HS 2709), annual 2019–2025.

HOW TO RUN (from the repository root):
1. pip install python-dotenv
2. pip install comtradeapicall
3. pip install -r requirements.txt
4. Create .env file and set SUBSCRIPTION_KEY.
5. Run python scripts/comtrade_script.py

Raw annual API responses (2019-2025) are cached in data/raw/.
Cleaned Parquet tables are written to data/interim/ (and data/processed/ alongside CSVs).
Reruns use cached responses and make no API calls unless files are missing.
To refresh a cached year, delete the corresponding raw CSV and rerun.
"""

# Import Packages for API Calls
from pathlib import Path
import os

import pandas as pd
import comtradeapicall
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"
RAW_DIR.mkdir(parents=True, exist_ok=True)
INTERIM_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
BARRELS_PER_TONNE = 7.3

# Major global crude exporters to check for reporting coverage (Comtrade numeric code -> Name)
MAJOR_EXPORTERS = {
    682: "Saudi Arabia",
    643: "Russia",
    364: "Iran",
    368: "Iraq",
    784: "United Arab Emirates",
    414: "Kuwait",
    842: "USA",
    124: "Canada",
    579: "Norway",
    76: "Brazil",
    566: "Nigeria",
    24: "Angola",
    398: "Kazakhstan",
    484: "Mexico",
    862: "Venezuela",
}

# Comtrade non-country partner/reporter codes (World, Bunkers, Free Zones, Special Categories, Areas nes)
SPECIAL_CODES = {0, 837, 838, 839, 899}

# Common params for Crude Oil commodities between 2019-2025
COMMON_PARAMS = dict(
    typeCode="C",
    clCode="HS",
    reporterCode=None,
    cmdCode="2709",
    flowCode="X,M",
    partnerCode=None,
    partner2Code=None,
    customsCode=None,
    format_output="JSON",
    aggregateBy=None,
    breakdownMode="plus",
    countOnly=None,
    includeDesc=True,
)

REQUIRED_COLUMNS = {
    "refYear", "reporterCode", "reporterDesc", "partnerCode",
    "flowCode", "netWgt", "customsCode", "partner2Code",
}

KEEP_COLUMNS = [
    "refYear", "period", "reporterCode", "reporterDesc",
    "partnerCode", "partnerDesc", "partner2Code", "partner2Desc",
    "flowCode", "flowDesc", "cmdCode", "customsCode",
    "motCode", "motDesc", "mosCode", "netWgt", "primaryValue",
    "isNetWgtEstimated", "isReported", "isAggregate",
]


def validate_response(df, label):
    if df is None or df.empty:
        raise RuntimeError(f"{label}: empty response")

    missing = REQUIRED_COLUMNS - set(df.columns)

    if missing:
        raise RuntimeError(
            f"{label}: missing columns: {sorted(missing)}"
        )

    years_found = set(
        pd.to_numeric(df["refYear"], errors="coerce")
        .dropna()
        .astype(int)
    )

    expected_years = set(range(2019, 2026))

    if years_found != expected_years:
        raise RuntimeError(
            f"{label}: Expected {expected_years}, "
            f"but found {years_found}"
        )

    if not set(df["flowCode"].dropna()).issubset({"X", "M"}):
        raise RuntimeError(f"{label}: unexpected trade flow codes")


# Fetch annual data for a given mot_code and label, caching results to avoid repeated API calls.
def fetch_annual(mot_code, label):
    cache_path = RAW_DIR / f"comtrade_{label}_annual.csv"

    if cache_path.exists():
        print(f"Loading cached {label} data...")
        data = pd.read_csv(cache_path, low_memory=False)

    else:
        print(f"Downloading {label} data...")

        load_dotenv()
        subscription_key = os.getenv("SUBSCRIPTION_KEY")

        if not subscription_key:
            raise ValueError(
                "SUBSCRIPTION_KEY is missing. Add it to .env."
            )

        data = comtradeapicall.getFinalData(
            subscription_key,
            freqCode="A",
            period="2019,2020,2021,2022,2023,2024,2025",
            motCode=mot_code,
            **COMMON_PARAMS,
        )

        validate_response(data, label)

        temporary_path = cache_path.with_suffix(".tmp")
        data.to_csv(temporary_path, index=False)
        temporary_path.replace(cache_path)

    validate_response(data, label)

    print(f"{label}: {len(data):,} rows")
    return data


# Add "barrels" column and keep only relevant columns
def preprocess_trade(raw):
    cleaned = raw[KEEP_COLUMNS].copy()
    cleaned["barrels"] = (
        pd.to_numeric(cleaned["netWgt"], errors="coerce")
        / 1000
        * BARRELS_PER_TONNE
    )
    return cleaned


# Create a list of unique reporter countries with their codes and descriptions
def reporter_list(cleaned):
    return (
        cleaned[["reporterCode", "reporterDesc"]]
        .drop_duplicates()
        .sort_values(["reporterDesc", "reporterCode"])
        .reset_index(drop=True)
    )


def report_gaps(df):
    """Part 1, Step 5: Note missing years, missing major exporters, and mirror mismatches."""
    print("\n--- COMTRADE GAP ANALYSIS (2019-2025) ---")
    expected_years = set(range(2019, 2026))

    # 1. Missing years per reporter
    yr_by_rep = df.groupby("reporterCode")["refYear"].apply(lambda s: set(s.dropna().astype(int)))
    incomplete_reps = {code: sorted(expected_years - yrs) for code, yrs in yr_by_rep.items() if yrs != expected_years}
    print(f"1. Reporters missing at least one year (out of {len(yr_by_rep)}): {len(incomplete_reps)}")

    # 2. Major exporters coverage
    print("2. Major crude oil exporters coverage (Flow = X):")
    exp_df = df[df["flowCode"] == "X"]
    exp_years = exp_df.groupby("reporterCode")["refYear"].apply(lambda s: sorted(set(s.dropna().astype(int))))
    for code, name in MAJOR_EXPORTERS.items():
        yrs = exp_years.get(code, [])
        missing = sorted(expected_years - set(yrs))
        status = "Complete (2019-2025)" if not missing else ("MISSING ALL YEARS" if not yrs else f"Missing: {missing}")
        print(f"   - {name:22} (code {code}): {status}")

    # 3a. Note unallocated / masked exports to 'Areas, nes' (899)
    nes_exports = df[
        (df["flowCode"] == "X")
        & (df["partnerCode"] == 899)
        & (df["partner2Code"].fillna(0) == 0)
        & (df["motCode"].fillna(0) == 0)
        & (df["customsCode"].fillna("C00") == "C00")
    ]
    if not nes_exports.empty:
        top_nes = (
            nes_exports.groupby(["reporterDesc", "refYear"], as_index=False)["barrels"]
            .sum()
            .sort_values("barrels", ascending=False)
            .head(3)
        )
        print("3a. Masked export destinations reported as 'Areas, nes' (code 899):")
        for _, r in top_nes.iterrows():
            print(f"   - {int(r['refYear'])}: {r['reporterDesc']} -> Areas, nes | {r['barrels']:,.0f} bbl")

    # 3b. True country-to-country bilateral mirror mismatches (excluding World=0 and special codes)
    bilateral = df[
        (~df["partnerCode"].isin(SPECIAL_CODES))
        & (~df["reporterCode"].isin(SPECIAL_CODES))
        & (df["partner2Code"].fillna(0) == 0)
        & (df["motCode"].fillna(0) == 0)
        & (df["customsCode"].fillna("C00") == "C00")
    ].copy()

    exp_side = (
        bilateral[bilateral["flowCode"] == "X"]
        .groupby(["refYear", "reporterCode", "reporterDesc", "partnerCode", "partnerDesc"], as_index=False)["barrels"]
        .sum()
        .rename(columns={
            "reporterCode": "exporterCode", "reporterDesc": "exporterDesc",
            "partnerCode": "importerCode", "partnerDesc": "importerDesc",
            "barrels": "exporter_reported_bbl",
        })
    )
    imp_side = (
        bilateral[bilateral["flowCode"] == "M"]
        .groupby(["refYear", "partnerCode", "partnerDesc", "reporterCode", "reporterDesc"], as_index=False)["barrels"]
        .sum()
        .rename(columns={
            "partnerCode": "exporterCode", "partnerDesc": "exporterDesc_imp",
            "reporterCode": "importerCode", "reporterDesc": "importerDesc_imp",
            "barrels": "importer_reported_bbl",
        })
    )

    mirror = exp_side.merge(imp_side, on=["refYear", "exporterCode", "importerCode"], how="outer")
    mirror["exporterDesc"] = mirror["exporterDesc"].fillna(mirror["exporterDesc_imp"])
    mirror["importerDesc"] = mirror["importerDesc"].fillna(mirror["importerDesc_imp"])
    mirror["exporter_reported_bbl"] = mirror["exporter_reported_bbl"].fillna(0.0)
    mirror["importer_reported_bbl"] = mirror["importer_reported_bbl"].fillna(0.0)
    mirror["abs_diff_bbl"] = (mirror["exporter_reported_bbl"] - mirror["importer_reported_bbl"]).abs()
    top_mismatches = mirror.sort_values("abs_diff_bbl", ascending=False).head(5)

    print("3b. Top 5 country-to-country bilateral mirror mismatches (Exporter X vs Importer M, in barrels):")
    for _, r in top_mismatches.iterrows():
        exp_name = r["exporterDesc"] if pd.notna(r.get("exporterDesc")) else f"Code {int(r['exporterCode'])}"
        imp_name = r["importerDesc"] if pd.notna(r.get("importerDesc")) else f"Code {int(r['importerCode'])}"
        print(
            f"   - {int(r['refYear'])}: {exp_name} -> {imp_name} | "
            f"Exp: {r['exporter_reported_bbl']:,.0f} bbl vs Imp: {r['importer_reported_bbl']:,.0f} bbl "
            f"(Diff: {r['abs_diff_bbl']:,.0f} bbl)"
        )


def main():
    # Raw records retain all returned aggregation levels. Do not sum them
    # indiscriminately: filter customsCode, partner2Code, partnerCode as needed.

    # Raw annual data for all modes and sea mode only, 2019-2025
    all_raw = fetch_annual(None, "all")
    sea_raw = fetch_annual("2100", "sea")

    # Preprocess: Add barrels column and keep only relevant columns
    all_annual = preprocess_trade(all_raw)
    sea_annual = preprocess_trade(sea_raw)

    # Reporter country lists for all modes and sea mode only, 2019-2025
    all_reporters = reporter_list(all_annual)
    sea_reporters = reporter_list(sea_annual)

    # Save processed data to CSV and Parquet files
    outputs = {
        "comtrade_all_annual": all_annual,
        "comtrade_sea_annual": sea_annual,
        "annual_reporters_all": all_reporters,
        "annual_reporters_sea": sea_reporters,
    }
    for name, df in outputs.items():
        df.to_csv(PROCESSED_DIR / f"{name}.csv", index=False)

    all_annual.to_parquet(PROCESSED_DIR / "comtrade_all_annual.parquet", index=False)
    sea_annual.to_parquet(PROCESSED_DIR / "comtrade_sea_annual.parquet", index=False)

    # Also save Parquet tables to data/interim/ per baseline convention
    all_annual.to_parquet(INTERIM_DIR / "comtrade_all_annual.parquet", index=False)
    sea_annual.to_parquet(INTERIM_DIR / "comtrade_sea_annual.parquet", index=False)

    sea_codes = set(sea_reporters["reporterCode"])
    all_codes = set(all_reporters["reporterCode"])
    print(f"All-mode reporters: {len(all_codes)}")
    print(f"Sea-mode reporters: {len(sea_codes)}")
    print(f"All-mode reporters without sea records: {len(all_codes - sea_codes)}")
    print(f"Sea reporters not in all-modes dataset: {len(sea_codes - all_codes)}")
    print(f"Outputs saved in: {INTERIM_DIR.resolve()} and {PROCESSED_DIR.resolve()}")

    report_gaps(all_annual)


if __name__ == "__main__":
    main()