"""Fit the port-to-port crude oil OD matrix with iterative proportional fitting (IPF).

Run from the repository root:
    python config/od_matrix.py            # real inputs from data/interim/
    python config/od_matrix.py --sample   # made-up inputs, to test the method
Then check the result:
    python config/od_checks.py            # or: python config/od_checks.py --sample

Inputs (data/interim/):
    country_flows.csv   exporter_iso3, importer_iso3, barrels_per_year   (Part 1)
    port_shares.csv     iso3, portid, direction (export/import), share   (Part 2)
    port_totals.csv     portid, direction (export/import), tons_per_year (Part 2)
                        Tanker tons per year over the baseline period. This format
                        is an assumption: DELIVERABLE3 does not define it yet.

Outputs (data/interim/, or data/interim/sample/ with --sample):
    od_matrix.csv       origin_portid, destination_portid, barrels_per_year
    od_port_fit.csv     one row per port and direction: target, seed, fitted, status

Method:
    1. Seed (the initial guess). Split each country-to-country flow across port pairs:
           seed[i, j] = flow[country(i), country(j)] * export_share[i] * import_share[j]
    2. Targets. Each port's PortWatch tanker tons per year * CRUDE_SHARE * BARRELS_PER_TONNE.
    3. Drop what can't be fitted. IPF only rescales cells that are already non-zero, so a
       port with a target but no seed flow (or seed flow but no target) is left out and
       reported in od_port_fit.csv.
    4. Balance. Every row sum and every column sum of a matrix add up to the same grand
       total, so total export targets must equal total import targets. They don't in the
       data (our ports also trade with ports outside the network), so both sides are
       scaled to their mean. The printed balance factors show how big that change is.
    5. IPF. Scale every row so it sums to its export target, then every column so it sums
       to its import target. Fixing columns breaks rows a little, so repeat until the
       largest gap is below TOLERANCE.

If IPF does not converge, more iterations will not help: the country pairs Comtrade
reports cannot carry the port targets (for example, a port's target is larger than
everything its country's trade partners can absorb). Setting SEED_FILL above zero gives
every empty cross-country port pair a small starting value, so IPF can use it. That makes
the fit possible but puts barrels on trades Comtrade never reported, and the script
prints how many. Domestic pairs always stay zero.
Needs: pandas, numpy.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
SAMPLE_DIR = INTERIM / "sample"

BARRELS_PER_TONNE = 7.3   # DELIVERABLE3 base assumption
CRUDE_SHARE = 0.6         # PLACEHOLDER until Part 3 calibrates it against EIA chokepoint flows
TOLERANCE = 1e-6
MAX_ITERATIONS = 1000
SEED_FILL = 0.0           # off. Else a fraction of the mean seed cell, e.g. 1e-4 (see Method)


def make_sample():
    """Small made-up inputs in the agreed formats. Port and country names are fake on purpose."""
    country_flows = pd.DataFrame(
        [("SAU", "CHN", 500e6), ("SAU", "USA", 200e6), ("SAU", "NLD", 150e6),
         ("USA", "CHN", 100e6), ("USA", "NLD", 120e6)],
        columns=["exporter_iso3", "importer_iso3", "barrels_per_year"],
    )
    port_shares = pd.DataFrame(
        [("SAU", "portA1", "export", 0.7), ("SAU", "portA2", "export", 0.3),
         ("USA", "portU1", "export", 0.6), ("USA", "portU2", "export", 0.4),
         ("USA", "portU1", "import", 0.5), ("USA", "portU2", "import", 0.5),
         ("CHN", "portC1", "import", 1.0), ("NLD", "portN1", "import", 1.0),
         ("NOR", "portX1", "export", 1.0)],  # NOR has no country flows: can't be fitted
        columns=["iso3", "portid", "direction", "share"],
    )
    # Tons chosen so the seed does NOT already match, giving IPF something to fix.
    port_totals = pd.DataFrame(
        [("portA1", "export", 150e6), ("portA2", "export", 90e6),
         ("portU1", "export", 25e6), ("portU2", "export", 30e6),
         ("portU1", "import", 20e6), ("portU2", "import", 35e6),
         ("portC1", "import", 140e6), ("portN1", "import", 70e6),
         ("portX1", "export", 40e6)],
        columns=["portid", "direction", "tons_per_year"],
    )
    return country_flows, port_shares, port_totals


def load_inputs(folder):
    country_flows = pd.read_csv(folder / "country_flows.csv")
    # DELIVERABLE3 calls the column `barrels`; od_country_flows.py writes `barrels_per_year`.
    country_flows = country_flows.rename(columns={"barrels": "barrels_per_year"})
    port_shares = pd.read_csv(folder / "port_shares.csv", dtype={"portid": str})
    port_totals = pd.read_csv(folder / "port_totals.csv", dtype={"portid": str})
    return country_flows, port_shares, port_totals


def build_seed(country_flows, port_shares):
    """Long table of seed cells: every exporter port x every importer port of each country flow."""
    exporters = port_shares.loc[port_shares["direction"] == "export", ["iso3", "portid", "share"]]
    importers = port_shares.loc[port_shares["direction"] == "import", ["iso3", "portid", "share"]]
    seed = (
        country_flows
        .merge(exporters.rename(columns={"iso3": "exporter_iso3", "portid": "origin_portid",
                                         "share": "export_share"}), on="exporter_iso3")
        .merge(importers.rename(columns={"iso3": "importer_iso3", "portid": "destination_portid",
                                         "share": "import_share"}), on="importer_iso3")
    )
    seed["barrels_per_year"] = (
        seed["barrels_per_year"] * seed["export_share"] * seed["import_share"]
    )

    # Country flows with no port on one side disappear in the merges above. Say how much.
    kept = seed[["exporter_iso3", "importer_iso3"]].drop_duplicates()
    lost = country_flows.merge(kept, how="left", indicator=True)
    lost = lost[lost["_merge"] == "left_only"]
    if not lost.empty:
        share = lost["barrels_per_year"].sum() / country_flows["barrels_per_year"].sum()
        print(f"{len(lost)} country flows have no port on one side and are not in the seed "
              f"({share:.1%} of country-flow barrels).")
    return seed[["origin_portid", "destination_portid", "barrels_per_year"]]


def build_targets(port_totals):
    """Port target barrels per year, one Series per direction, indexed by portid."""
    barrels = port_totals["tons_per_year"] * CRUDE_SHARE * BARRELS_PER_TONNE
    targets = port_totals.assign(barrels_per_year=barrels)
    export_target = targets[targets["direction"] == "export"].set_index("portid")["barrels_per_year"]
    import_target = targets[targets["direction"] == "import"].set_index("portid")["barrels_per_year"]
    return export_target, import_target


def fill_seed(seed, origins, destinations, port_shares):
    """Give every empty cross-country port pair a small value, so IPF is able to use it."""
    country = port_shares.drop_duplicates("portid").set_index("portid")["iso3"]
    origin_iso3 = country.reindex(origins).to_numpy(dtype=object)[:, None]
    destination_iso3 = country.reindex(destinations).to_numpy(dtype=object)[None, :]
    cross_country = (pd.notna(origin_iso3) & pd.notna(destination_iso3)
                     & (origin_iso3 != destination_iso3))
    filled = seed.copy()
    filled[(seed == 0) & cross_country] = SEED_FILL * seed[seed > 0].mean()
    return filled


def ipf(seed, row_goal, col_goal):
    """Alternately scale rows and columns of `seed` to hit `row_goal` and `col_goal`.

    Returns the fitted matrix, the number of iterations, and the final largest relative gap.
    Rows and columns whose goal is zero stay zero.
    """
    matrix = seed.copy()
    gap = np.inf
    for iteration in range(1, MAX_ITERATIONS + 1):
        row_sums = matrix.sum(axis=1)
        matrix *= np.divide(row_goal, row_sums, out=np.zeros_like(row_sums),
                            where=row_sums > 0)[:, None]
        col_sums = matrix.sum(axis=0)
        matrix *= np.divide(col_goal, col_sums, out=np.zeros_like(col_sums),
                            where=col_sums > 0)[None, :]
        # Columns are exact after the last step, so only the rows can still be off.
        row_sums = matrix.sum(axis=1)
        fitted = row_goal > 0
        gap = np.max(np.abs(row_sums[fitted] - row_goal[fitted]) / row_goal[fitted])
        if gap < TOLERANCE:
            break
    return matrix, iteration, gap


def fit(country_flows, port_shares, port_totals):
    seed_long = build_seed(country_flows, port_shares)
    export_target, import_target = build_targets(port_totals)

    origins = sorted(set(seed_long["origin_portid"]) | set(export_target.index))
    destinations = sorted(set(seed_long["destination_portid"]) | set(import_target.index))
    seed = (
        seed_long.groupby(["origin_portid", "destination_portid"])["barrels_per_year"].sum()
        .unstack(fill_value=0.0)
        .reindex(index=origins, columns=destinations, fill_value=0.0)
        .to_numpy()
    )
    row_target = export_target.reindex(origins).fillna(0.0).to_numpy()
    col_target = import_target.reindex(destinations).fillna(0.0).to_numpy()
    reported = seed > 0   # the port pairs that come from a Comtrade country flow
    if SEED_FILL > 0:
        seed = fill_seed(seed, origins, destinations, port_shares)
    seed_row_sums, seed_col_sums = seed.sum(axis=1), seed.sum(axis=0)

    # Drop rows and columns that can't be fitted. Dropping a column can empty a row
    # (and the reverse), so repeat until nothing changes.
    work = seed.copy()
    while True:
        keep_rows = (work.sum(axis=1) > 0) & (row_target > 0)
        keep_cols = (work.sum(axis=0) > 0) & (col_target > 0)
        before = work.sum()
        work[~keep_rows, :] = 0.0
        work[:, ~keep_cols] = 0.0
        if work.sum() == before:
            break
    if not keep_rows.any() or not keep_cols.any():
        raise RuntimeError("No port has both seed flow and a target; check that the "
                           "portids in port_shares.csv and port_totals.csv match.")

    export_total =row_target[keep_rows].sum()
    import_total = col_target[keep_cols].sum()
    common_total = (export_total + import_total) / 2
    row_goal = np.where(keep_rows, row_target * common_total / export_total, 0.0)
    col_goal = np.where(keep_cols, col_target * common_total / import_total, 0.0)
    print(f"Targets of fittable ports: exports {export_total:,.0f}, imports {import_total:,.0f} "
          f"barrels/year.\nBalanced both to {common_total:,.0f}: export targets x"
          f"{common_total / export_total:.3f}, import targets x{common_total / import_total:.3f}.")

    matrix, iterations, gap = ipf(work, row_goal, col_goal)
    if gap < TOLERANCE:
        print(f"IPF converged in {iterations} iterations (largest gap {gap:.1e}).")
    else:
        print(f"WARNING: IPF did not converge in {iterations} iterations (largest gap {gap:.1e}). "
              "The country pairs in the seed probably can't carry these port targets; "
              "see SEED_FILL at the top of this script.")
    if SEED_FILL > 0:
        print(f"Seed fill {SEED_FILL:g}: {matrix[~reported].sum() / matrix.sum():.1%} of fitted "
              "barrels are on port pairs Comtrade does not report.")

    od = (
        pd.DataFrame(matrix, index=pd.Index(origins, name="origin_portid"),
                     columns=pd.Index(destinations, name="destination_portid"))
        .stack().rename("barrels_per_year").reset_index()
    )
    od = (od[od["barrels_per_year"] > 0]
          .sort_values("barrels_per_year", ascending=False)
          .reset_index(drop=True))

    port_fit = pd.concat([
        port_fit_table("export", origins, row_target, row_goal, seed_row_sums,
                       matrix.sum(axis=1), keep_rows),
        port_fit_table("import", destinations, col_target, col_goal, seed_col_sums,
                       matrix.sum(axis=0), keep_cols),
    ], ignore_index=True)
    return od, port_fit


def port_fit_table(direction, portids, target, goal, seed_sums, fitted_sums, kept):
    table = pd.DataFrame({
        "portid": portids,
        "direction": direction,
        "target_barrels_per_year": target,   # from PortWatch, before balancing
        "goal_barrels_per_year": goal,       # after balancing: what IPF aims for
        "seed_barrels_per_year": seed_sums,
        "fitted_barrels_per_year": fitted_sums,
    })
    table["status"] = np.select(
        [kept, target <= 0, seed_sums <= 0],
        ["fitted", "no_target", "no_seed_flow"],
        default="dropped_with_partners",  # had both, but every partner port was dropped
    )
    return table


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true",
                        help="use small made-up inputs and write to data/interim/sample/")
    args = parser.parse_args()

    if args.sample:
        out_dir = SAMPLE_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        country_flows, port_shares, port_totals = make_sample()
        # Save the made-up inputs too, so the check script (and you) can read them.
        country_flows.to_csv(out_dir / "country_flows.csv", index=False)
        port_shares.to_csv(out_dir / "port_shares.csv", index=False)
        port_totals.to_csv(out_dir / "port_totals.csv", index=False)
    else:
        out_dir = INTERIM
        country_flows, port_shares, port_totals = load_inputs(INTERIM)

    od, port_fit = fit(country_flows, port_shares, port_totals)
    od.to_csv(out_dir / "od_matrix.csv", index=False)
    port_fit.to_csv(out_dir / "od_port_fit.csv", index=False)

    not_fitted = port_fit[port_fit["status"] != "fitted"]
    if not not_fitted.empty:
        print(f"\n{len(not_fitted)} port totals could not be fitted:")
        print(not_fitted[["portid", "direction", "status", "target_barrels_per_year"]]
              .to_string(index=False))
    print(f"\nWrote {len(od):,} port pairs to {(out_dir / 'od_matrix.csv').relative_to(ROOT)}")
    print("Largest port pairs:")
    print(od.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
