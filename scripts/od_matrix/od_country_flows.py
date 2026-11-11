"""Create baseline country-to-country crude oil flows for the maritime network.

Run from the repository root:
    python scripts/data_pull/comtrade_script.py
    python scripts/od_matrix/od_country_flows.py

Inputs:
    data/interim/comtrade_all_annual.parquet
    data/interim/comtrade_sea_annual.parquet
    config/country_codes.csv
    config/ports_selected.csv

Outputs:
    data/interim/country_flows.csv
    data/interim/pipeline_adjustments.csv

Method:
    * Restrict to 2019-2022, HS 2709, bilateral total-customs records.
    * Prefer importer-reported (M) figures to exporter-reported (X) figures.
    * Use valid sea-only reports when available; otherwise deduct pipeline
      modes 9110/9100 if reported by the same reporting direction; otherwise
      retain the all-mode figure as an explicitly labeled proxy.
    * Average available annual values to obtain barrels/year.

"""

from pathlib import Path

import numpy as np
import pandas as pd


DATA_DIR = Path("data")
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
INTERIM_DIR = DATA_DIR / "interim"
BASELINE_YEARS = range(2019, 2023)


def filter_bilateral(df):
    return df.loc[
        (df["partnerCode"] != 0)
        & (df["partner2Code"] == 0)
        & (df["customsCode"] == "C00")
        & (df["flowCode"].isin(["X", "M"]))
    ].copy()


def map_trade_flows(df, country_mapping):
    df = df.copy()
    code_to_iso3 = country_mapping.set_index("comtrade_code")["portwatch_iso3"]
    df["reporter_iso3"] = df["reporterCode"].map(code_to_iso3)
    df["partner_iso3"] = df["partnerCode"].map(code_to_iso3)
    df["exporter_iso3"] = np.where(
        df["flowCode"] == "X", df["reporter_iso3"], df["partner_iso3"]
    )
    df["importer_iso3"] = np.where(
        df["flowCode"] == "M", df["reporter_iso3"], df["partner_iso3"]
    )
    return df


def filter_selected_countries(df, selected_countries):
    return df.loc[
        df["exporter_iso3"].isin(selected_countries)
        & df["importer_iso3"].isin(selected_countries)
        & (df["exporter_iso3"] != df["importer_iso3"])
    ].copy()


def main():
    # Load the four specified inputs.
    comtrade_all = pd.read_parquet(INTERIM_DIR / "comtrade_all_annual.parquet")
    comtrade_sea = pd.read_parquet(INTERIM_DIR / "comtrade_sea_annual.parquet")
    country_codes = pd.read_csv(CONFIG_DIR / "country_codes.csv")
    ports_selected = pd.read_csv(CONFIG_DIR / "ports_selected.csv")

    # Restrict to the 2019–2022 baseline and bilateral total-customs records.
    comtrade_all_base = comtrade_all[
        comtrade_all["refYear"].isin(BASELINE_YEARS)
    ].copy()
    comtrade_sea_base = comtrade_sea[
        comtrade_sea["refYear"].isin(BASELINE_YEARS)
    ].copy()
    all_bilateral = filter_bilateral(comtrade_all_base)
    sea_bilateral = filter_bilateral(comtrade_sea_base)

    # Map Comtrade numeric codes to PortWatch ISO3 codes.
    country_mapping = (
        country_codes.loc[
            country_codes["status"] == "matched",
            ["comtrade_code", "portwatch_iso3"],
        ]
        .dropna()
        .drop_duplicates()
        .copy()
    )
    country_mapping["comtrade_code"] = country_mapping["comtrade_code"].astype(int)
    country_mapping = country_mapping[country_mapping["comtrade_code"] != 899]

    all_mapped = map_trade_flows(all_bilateral, country_mapping)
    sea_mapped = map_trade_flows(sea_bilateral, country_mapping)
    selected_countries = set(ports_selected["country_iso3"].dropna().unique())
    all_selected = filter_selected_countries(all_mapped, selected_countries)
    sea_selected = filter_selected_countries(sea_mapped, selected_countries)

    # Use TOTAL MOT (0) for all-mode totals, avoiding double-counting modes.
    all_selected_total = all_selected.loc[all_selected["motCode"] == 0].copy()
    keys = ["exporter_iso3", "importer_iso3", "refYear"]
    group_cols = keys + ["flowCode"]

    annual_reports = (
        all_selected_total.groupby(group_cols, as_index=False)
        .agg(barrels=("barrels", "sum"))
    )
    annual_comparison = (
        annual_reports.pivot(index=keys, columns="flowCode", values="barrels")
        .rename(columns={"M": "import_barrels", "X": "export_barrels"})
        .reset_index()
    )
    annual_comparison["reconciled_barrels"] = (
        annual_comparison["import_barrels"]
        .combine_first(annual_comparison["export_barrels"])
    )

    # Reconcile sea-reported volumes using the same importer-first preference.
    sea_annual_reports = (
        sea_selected.groupby(group_cols, as_index=False)
        .agg(sea_barrels=("barrels", "sum"))
    )
    sea_comparison = (
        sea_annual_reports.pivot(index=keys, columns="flowCode", values="sea_barrels")
        .rename(columns={"M": "sea_import_barrels", "X": "sea_export_barrels"})
        .reset_index()
    )
    sea_comparison["reconciled_sea_barrels"] = (
        sea_comparison["sea_import_barrels"]
        .combine_first(sea_comparison["sea_export_barrels"])
    )
    comparison = annual_comparison.merge(
        sea_comparison, on=keys, how="left", validate="one_to_one"
    )

    # Pipeline modes: prefer specific 9110 to broader 9100 within a reporting flow.
    pipeline_modes = all_selected.loc[
        all_selected["motCode"].isin([9100, 9110])
    ].copy()
    pipeline_modes["mode_priority"] = np.where(
        pipeline_modes["motCode"] == 9110, 0, 1
    )
    pipeline_modes = (
        pipeline_modes.sort_values("mode_priority")
        .drop_duplicates(subset=group_cols, keep="first")
    )
    pipeline_modes = pipeline_modes[
        group_cols + ["motCode", "barrels"]
    ].rename(columns={
        "barrels": "pipeline_barrels",
        "motCode": "pipeline_motCode",
    })

    # Match pipeline volume to the direction used for the all-mode total.
    flows = comparison.copy()
    flows["selected_flow"] = np.where(
        flows["import_barrels"].notna(), "M", "X"
    )
    pipeline_matched = flows[keys + ["selected_flow"]].merge(
        pipeline_modes,
        left_on=keys + ["selected_flow"],
        right_on=keys + ["flowCode"],
        how="left",
        validate="one_to_one",
    )
    flows["pipeline_barrels"] = pipeline_matched["pipeline_barrels"].to_numpy()
    flows["pipeline_motCode"] = pipeline_matched["pipeline_motCode"].to_numpy()

    # Prefer valid sea reports; otherwise subtract valid pipeline observations;
    # otherwise retain the all-mode volume as a proxy.
    flows["has_valid_sea"] = (
        flows["reconciled_sea_barrels"].notna()
        & (flows["reconciled_sea_barrels"] >= 0)
        & (flows["reconciled_sea_barrels"] <= flows["reconciled_barrels"])
    )
    flows["has_valid_pipeline"] = (
        flows["pipeline_barrels"].notna()
        & (flows["pipeline_barrels"] >= 0)
        & (flows["pipeline_barrels"] <= flows["reconciled_barrels"])
    )
    flows["adjusted_barrels"] = np.select(
        [flows["has_valid_sea"], flows["has_valid_pipeline"]],
        [
            flows["reconciled_sea_barrels"],
            flows["reconciled_barrels"] - flows["pipeline_barrels"],
        ],
        default=flows["reconciled_barrels"],
    )
    flows["source_method"] = np.select(
        [flows["has_valid_sea"], flows["has_valid_pipeline"]],
        ["sea_reported", "all_minus_pipeline"],
        default="all_mode_proxy",
    )

    # Average observed annual values for each bilateral pair.
    country_flows = (
        flows.groupby(["exporter_iso3", "importer_iso3"], as_index=False)
        .agg(
            barrels_per_year=("adjusted_barrels", "mean"),
            source_method=(
                "source_method",
                lambda x: x.iloc[0] if x.nunique() == 1 else "mixed",
            ),
            years_available=("refYear", "nunique"),
        )
    )

    # Audit rows with a valid reported pipeline observation (including rows
    # where the final method uses sea data instead of a pipeline deduction).
    pipeline_adjustments = flows.loc[
        flows["has_valid_pipeline"],
        [
            "exporter_iso3", "importer_iso3", "refYear",
            "reconciled_barrels", "pipeline_barrels", "pipeline_motCode",
            "adjusted_barrels", "source_method",
        ],
    ].copy()
    pipeline_adjustments = pipeline_adjustments.rename(
        columns={"reconciled_barrels": "all_mode_barrels"}
    )

    # Validation checks.
    assert country_flows["exporter_iso3"].notna().all()
    assert country_flows["importer_iso3"].notna().all()
    assert country_flows["exporter_iso3"].isin(selected_countries).all()
    assert country_flows["importer_iso3"].isin(selected_countries).all()
    assert country_flows["barrels_per_year"].notna().all()
    assert (country_flows["barrels_per_year"] >= 0).all()
    assert not (
        (all_selected["reporterCode"] == 899)
        | (all_selected["partnerCode"] == 899)
    ).any()
    assert (
        country_flows["exporter_iso3"] != country_flows["importer_iso3"]
    ).all()
    print("All validation checks passed!")

    # Final CSVs
    country_flows[
        ["exporter_iso3", "importer_iso3", "barrels_per_year", "source_method"]
    ].to_csv(INTERIM_DIR / "country_flows.csv", index=False)
    pipeline_adjustments.to_csv(
        INTERIM_DIR / "pipeline_adjustments.csv", index=False
    )
    print("Files saved successfully.")


if __name__ == "__main__":
    main()
