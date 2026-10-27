"""
UN Comtrade crude oil (HS 2709), annual 2019–2025.

HOW TO RUN (from the repository root):
1. pip install dotenv
2. pip install comtradeapicall
3. pip install -r requirements.txt
4. Create .env file and set SUBSCRIPTION_KEY.
5. Run python scripts/comtrade_script.py

Raw annual API responses (2019-2025) are cached in data/raw/.
Four cleaned CSVs and two Parquet tables are written to data/processed/.
Reruns use cached responses and make no API calls unless files are missing.
To refresh a cached year, delete the corresponding raw CSV and rerun.
"""

# Import Packages for API Calls
from pathlib import Path
import os

import pandas as pd
import comtradeapicall
from dotenv import load_dotenv


RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
BARRELS_PER_TONNE = 7.3

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

    sea_codes = set(sea_reporters["reporterCode"])
    all_codes = set(all_reporters["reporterCode"])
    print(f"All-mode reporters: {len(all_codes)}")
    print(f"Sea-mode reporters: {len(sea_codes)}")
    print(f"All-mode reporters without sea records: {len(all_codes - sea_codes)}")
    print(f"Sea reporters not in all-modes dataset: {len(sea_codes - all_codes)}")
    print(f"Outputs saved in: {PROCESSED_DIR.resolve()}")


if __name__ == "__main__":
    main()
