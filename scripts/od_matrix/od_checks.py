"""Sanity checks for the OD matrix built by od_matrix.py.

Run from the repository root, after od_matrix.py:
    python scripts/od_matrix/od_checks.py            # checks data/interim/
    python scripts/od_matrix/od_checks.py --sample   # checks data/interim/sample/

Inputs (same folder):
    od_matrix.csv, od_port_fit.csv   from od_matrix.py
    country_flows.csv, port_shares.csv
    config/ports_selected.csv        (real run only: are all ports in the network?)

Each check prints PASS or FAIL. Lines marked INFO are for reading, not pass/fail.
The script exits with status 1 if any check fails, so it can gate a pipeline.

INFO: unfitted ports, grand totals, how far IPF moved each country pair away from
Comtrade, and the largest port pairs to eyeball.
Needs: pandas, numpy.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "data" / "interim"
SAMPLE_DIR = INTERIM / "sample"
PORTS_SELECTED = ROOT / "config" / "ports_selected.csv"

OD_COLUMNS = ["origin_portid", "destination_portid", "barrels_per_year"]
TOTAL_TOLERANCE = 1e-4   # relative; looser than the fit's own 1e-6 because the CSV rounds
SHARE_TOLERANCE = 1e-6

failures = []


def report(name, passed, detail=""):
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
    if not passed:
        failures.append(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true", help="check data/interim/sample/")
    args = parser.parse_args()
    folder = SAMPLE_DIR if args.sample else INTERIM

    od = pd.read_csv(folder / "od_matrix.csv", dtype={"origin_portid": str,
                                                       "destination_portid": str})
    port_fit = pd.read_csv(folder / "od_port_fit.csv", dtype={"portid": str})
    country_flows = (pd.read_csv(folder / "country_flows.csv")
                     .rename(columns={"barrels": "barrels_per_year"}))
    port_shares = pd.read_csv(folder / "port_shares.csv", dtype={"portid": str})

    report("columns", list(od.columns) == OD_COLUMNS, f"got {list(od.columns)}")
    report("no missing values", not od.isna().any().any(),
           f"{int(od.isna().any(axis=1).sum())} rows with gaps")

    negative =od[od["barrels_per_year"] < 0]
    report("nothing negative", negative.empty, f"{len(negative)} negative cells")

    duplicates =od.duplicated(["origin_portid", "destination_portid"]).sum()
    report("no duplicate port pairs", duplicates == 0, f"{duplicates} duplicates")

    port_country =port_shares.drop_duplicates("portid").set_index("portid")["iso3"]
    od["origin_iso3"] = od["origin_portid"].map(port_country)
    od["destination_iso3"] = od["destination_portid"].map(port_country)
    self_loops = (od["origin_portid"] == od["destination_portid"]).sum()
    report("no port ships to itself", self_loops == 0, f"{self_loops} self-loops")
    domestic = od[od["origin_iso3"] == od["destination_iso3"]]
    report("no domestic pairs", domestic.empty,
           f"{len(domestic)} pairs, e.g. {domestic[OD_COLUMNS].head(3).values.tolist()}")

    # Recomputed from the matrix here, not trusted from the fit table.
    fitted = port_fit[port_fit["status"] == "fitted"].set_index(["portid", "direction"])
    sums = pd.concat([
        od.groupby("origin_portid")["barrels_per_year"].sum()
          .rename_axis("portid").to_frame().assign(direction="export"),
        od.groupby("destination_portid")["barrels_per_year"].sum()
          .rename_axis("portid").to_frame().assign(direction="import"),
    ]).reset_index().set_index(["portid", "direction"])["barrels_per_year"]
    goal = fitted["goal_barrels_per_year"]
    actual = sums.reindex(goal.index).fillna(0.0)
    gap = (actual - goal).abs() / goal
    worst = gap.idxmax() if len(gap) else None
    report("port totals match targets", len(gap) > 0 and gap.max() <= TOTAL_TOLERANCE,
           f"largest gap {gap.max():.1e} at {worst}" if len(gap) else "no fitted ports")
    unexpected = sums.index.difference(goal.index)
    report("only fitted ports carry flow", len(unexpected) == 0,
           f"{list(unexpected)[:5]}")

    share_sums =port_shares.groupby(["iso3", "direction"])["share"].sum()
    over = share_sums[share_sums > 1 + SHARE_TOLERANCE]
    report("port shares add up to at most 1", over.empty, over.round(3).to_dict())

    # Skipped for the sample: its ports are made up.
    if not args.sample:
        selected = set(pd.read_csv(PORTS_SELECTED, dtype=str)["portwatch_portid"])
        outside = (set(od["origin_portid"]) | set(od["destination_portid"])) - selected
        report("all ports in ports_selected.csv", not outside, f"{sorted(outside)[:5]}")

    print()
    not_fitted = port_fit[port_fit["status"] != "fitted"]
    print(f"INFO  {len(not_fitted)} port totals not fitted "
          f"({not_fitted['target_barrels_per_year'].sum():,.0f} target barrels/year):")
    if not not_fitted.empty:
        print(not_fitted[["portid", "direction", "status", "target_barrels_per_year"]]
              .to_string(index=False))
    print(f"INFO  grand totals, barrels/year: OD matrix {od['barrels_per_year'].sum():,.0f}; "
          f"Comtrade country flows {country_flows['barrels_per_year'].sum():,.0f}; "
          f"PortWatch export targets {port_fit.loc[port_fit['direction'] == 'export', 'target_barrels_per_year'].sum():,.0f}; "
          f"import targets {port_fit.loc[port_fit['direction'] == 'import', 'target_barrels_per_year'].sum():,.0f}")

    # INFO: IPF fits port totals, so country-pair totals drift away from Comtrade.
    by_country = (od.groupby(["origin_iso3", "destination_iso3"])["barrels_per_year"].sum()
                  .rename("od_barrels").reset_index())
    drift = country_flows.merge(
        by_country, how="outer",
        left_on=["exporter_iso3", "importer_iso3"],
        right_on=["origin_iso3", "destination_iso3"],
    )
    drift["exporter_iso3"] = drift["exporter_iso3"].fillna(drift["origin_iso3"])
    drift["importer_iso3"] = drift["importer_iso3"].fillna(drift["destination_iso3"])
    drift = drift.fillna({"barrels_per_year": 0.0, "od_barrels": 0.0})
    drift["ratio_od_to_comtrade"] = np.where(
        drift["barrels_per_year"] > 0, drift["od_barrels"] / drift["barrels_per_year"], np.nan)
    off_comtrade = drift.loc[drift["barrels_per_year"] == 0, "od_barrels"].sum()
    print(f"INFO  {off_comtrade / od['barrels_per_year'].sum():.1%} of OD barrels are on "
          "country pairs Comtrade does not report (non-zero only if SEED_FILL is on)")
    drift = drift.sort_values("barrels_per_year", ascending=False)
    print("\nINFO  largest country pairs, Comtrade vs OD matrix (ratio 1.0 = unchanged by IPF):")
    print(drift[["exporter_iso3", "importer_iso3", "barrels_per_year", "od_barrels",
                 "ratio_od_to_comtrade"]].head(15).to_string(index=False))

    top =od.head(15)[OD_COLUMNS + ["origin_iso3", "destination_iso3"]]
    if not args.sample and PORTS_SELECTED.exists():
        names = pd.read_csv(PORTS_SELECTED, dtype=str).set_index("portwatch_portid")["name"]
        top = top.assign(origin_name=top["origin_portid"].map(names),
                         destination_name=top["destination_portid"].map(names))
    print("\nINFO  largest port pairs (do these look sensible?):")
    print(top.to_string(index=False))

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
