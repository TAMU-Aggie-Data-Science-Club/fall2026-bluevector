"""Calculate annual PortWatch tanker totals and shares for selected ports.

Run from the repository root:
    python scripts/od_port_shares.py
"""

from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PORTS_PATH = REPO / "config" / "ports_selected.csv"
DAILY_PATH = REPO / "data" / "interim" / "portwatch_ports_daily_selected.parquet"
OUTPUT_DIR = REPO / "data" / "interim"

BASELINE_START = "2019-01-01"
BASELINE_END = "2023-10-31"
AVERAGE_DAYS_PER_YEAR = 365.25


def read_inputs():
    if not PORTS_PATH.exists():
        raise FileNotFoundError(f"Selected-port list not found: {PORTS_PATH}")
    if not DAILY_PATH.exists():
        raise FileNotFoundError(
            f"Daily PortWatch file not found: {DAILY_PATH}\n"
            "Create it with: python config/pull_portwatch_transits_2019_2025.py "
            "--ports config/ports_selected.csv"
        )

    selected = pd.read_csv(PORTS_PATH, dtype=str)
    daily = pd.read_parquet(DAILY_PATH)

    selected_columns = {"country_iso3", "portwatch_portid", "member_portids"}
    daily_columns = {"date", "portid", "ISO3", "import_tanker", "export_tanker"}

    if not selected_columns.issubset(selected.columns):
        raise ValueError(f"Selected-port file is missing columns: {selected_columns - set(selected.columns)}")
    if not daily_columns.issubset(daily.columns):
        raise ValueError(f"Daily file is missing columns: {daily_columns - set(daily.columns)}")
    if selected["portwatch_portid"].nunique() != 100:
        raise ValueError(
            "Expected 100 unique selected ports; found "
            f"{selected['portwatch_portid'].nunique()}"
        )

    return selected, daily


def expand_selected_ports(selected):
    ports = selected[
        ["country_iso3", "portwatch_portid", "member_portids"]
    ].copy()

    ports["member_portids"] = ports["member_portids"].fillna("").str.split(";")
    members = ports.explode("member_portids")
    members["member_portids"] = members["member_portids"].str.strip()
    members = members[members["member_portids"] != ""]

    members = members.rename(
        columns={
            "country_iso3": "iso3",
            "portwatch_portid": "portid",
            "member_portids": "source_portid",
        }
    )

    return members[["iso3", "portid", "source_portid"]]


def prepare_daily(selected, daily):
    members = expand_selected_ports(selected)

    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily["portid"] = daily["portid"].astype(str)

    start = pd.Timestamp(BASELINE_START)
    end = pd.Timestamp(BASELINE_END)
    daily = daily[daily["date"].between(start, end)]

    daily = daily.rename(columns={"portid": "source_portid"})
    daily = daily.merge(members, on="source_portid", how="inner")

    for column in ["import_tanker", "export_tanker"]:
        daily[column] = pd.to_numeric(daily[column], errors="coerce").fillna(0)

    return daily


def build_port_totals(selected, daily):
    daily = prepare_daily(selected, daily)
    daily_long = daily.melt(
        id_vars=["iso3", "portid"],
        value_vars=["import_tanker", "export_tanker"],
        var_name="direction",
        value_name="tons",
    )
    daily_long["direction"] = daily_long["direction"].str.replace(
        "_tanker", "", regex=False
    )

    observed_totals = (
        daily_long.groupby(["iso3", "portid", "direction"], as_index=False)["tons"]
        .sum()
    )

    baseline_start = pd.Timestamp(BASELINE_START)
    baseline_end = pd.Timestamp(BASELINE_END)
    baseline_years = (
        (baseline_end - baseline_start).days + 1
    ) / AVERAGE_DAYS_PER_YEAR

    wide_totals = observed_totals.pivot(
        index=["iso3", "portid"], columns="direction", values="tons"
    ).reset_index()
    wide_totals.columns.name = None

    selected_ports = selected[
        ["node_id", "name", "country_iso3", "portwatch_portid"]
    ].drop_duplicates(subset=["portwatch_portid"])
    selected_ports = selected_ports.rename(
        columns={
            "name": "portname",
            "country_iso3": "iso3",
            "portwatch_portid": "portid",
        }
    )
    totals = selected_ports.merge(wide_totals, on=["iso3", "portid"], how="left")
    for direction in ["export", "import"]:
        if direction not in totals:
            totals[direction] = 0.0
        totals[direction] = totals[direction].fillna(0) / baseline_years

    totals = totals.rename(
        columns={
            "export": "export_tanker_tons_yr",
            "import": "import_tanker_tons_yr",
        }
    )
    totals["export_tanker_bbl_yr"] = totals["export_tanker_tons_yr"] * 7.3
    totals["import_tanker_bbl_yr"] = totals["import_tanker_tons_yr"] * 7.3
    totals = totals[
        [
            "portid",
            "node_id",
            "portname",
            "iso3",
            "export_tanker_tons_yr",
            "import_tanker_tons_yr",
            "export_tanker_bbl_yr",
            "import_tanker_bbl_yr",
        ]
    ].sort_values(["iso3", "portid"]).reset_index(drop=True)

    share_totals = totals.melt(
        id_vars=["iso3", "portid"],
        value_vars=["export_tanker_tons_yr", "import_tanker_tons_yr"],
        var_name="direction",
        value_name="tons_per_year",
    )
    share_totals["direction"] = share_totals["direction"].str.replace(
        "_tanker_tons_yr", "", regex=False
    )
    country_direction_totals = share_totals.groupby(["iso3", "direction"])[
        "tons_per_year"
    ].transform("sum")
    shares = share_totals.copy()
    shares["share"] = 0.0
    positive = country_direction_totals > 0
    shares.loc[positive, "share"] = (
        shares.loc[positive, "tons_per_year"] / country_direction_totals[positive]
    )
    shares = shares[["iso3", "portid", "direction", "tons_per_year", "share"]]
    shares = shares.sort_values(["iso3", "portid", "direction"]).reset_index(drop=True)

    return totals, shares


def main():
    selected, daily = read_inputs()
    totals, shares = build_port_totals(selected, daily)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    totals_path = OUTPUT_DIR / "port_totals.csv"
    shares_path = OUTPUT_DIR / "port_shares.csv"
    totals.to_csv(totals_path, index=False)
    shares.to_csv(shares_path, index=False)

    print(f"Baseline: {BASELINE_START} through {BASELINE_END}")
    print(f"Selected ports: {selected['portwatch_portid'].nunique()}")
    print(f"Wrote {len(totals)} port totals to {totals_path}")
    print(f"Wrote {len(shares)} port-direction shares to {shares_path}")


if __name__ == "__main__":
    main()
