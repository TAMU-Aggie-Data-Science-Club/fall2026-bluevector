"""Crude share calibration: how much of PortWatch tanker volume is oil (OD matrix, Part 3).

What it does
    PortWatch counts ALL liquid-bulk tankers (crude, products, chemicals, LNG/LPG), and
    counts every transit in both directions, laden or in ballast. EIA publishes how much oil
    actually passes each chokepoint. This script compares the two to get a "crude share" that
    Part 4 (build_od_matrix / od_matrix.py) multiplies into PortWatch tanker volumes.

    For each chokepoint:  crude_share = EIA oil flow (mbd) / PortWatch tanker flow (mbd)
      * PortWatch mbd = mean daily capacity_tanker (tonnes) x 7.3 barrels per tonne / 1,000,000
      * averaged over the BASELINE years (2019-2022, before the Red Sea crisis)
      * per-year ratios for 2019-2023 are listed in the notes column
    It also computes a global ratio: Comtrade crude barrels (country_flows.csv) divided by
    PortWatch tanker barrels at the selected ports (port_totals.csv), and writes one
    `global_default` row. That default is a decision the group should confirm (see below).

Run from the repository root
    python scripts/od_matrix/od_crude_share.py                 # default = global ratio if available
    python scripts/od_matrix/od_crude_share.py --default 0.65  # set the global_default by hand

Inputs (data/interim/)
    eia_chokepoint_flows.parquet            from scripts/data_pull/eia_chokepoints.py
    portwatch_chokepoints_daily.parquet     from scripts/data_pull/pull_chokepoints.py (or the .csv it falls back to)
    country_flows.csv                       from scripts/od_matrix/od_country_flows.py   (optional)
    port_totals.csv                         Part 2; long format (portid, direction, tons_per_year)
                                            or wide format (export_tanker_tons_yr, import_tanker_tons_yr)  (optional)

Output (data/interim/)
    crude_share.csv   scope, eia_mbd, portwatch_tanker_mbd, crude_share, notes
                      scope = `global_default` or a PortWatch chokepoint ID (chokepoint5 = Malacca, ...)

Needs: pandas, pyarrow (pip install pandas pyarrow). No API key.

Things to know before trusting the numbers
    * EIA's table is total oil (crude + petroleum products). It has no crude-only split for
      2020 onward, so this "crude share" is really an OIL share of tanker volume.
    * PortWatch capacity_tanker is carrying capacity of transiting tankers, laden or not, in both
      directions. EIA is oil actually carried. Ratios therefore come out lower than the true laden share.
    * EIA's Suez figure includes the SUMED pipeline, which is not ship traffic.
    * EIA table values are hardcoded in scripts/data_pull/eia_chokepoints.py (2019 from the June 2024
      edition, 2020 onward from a later one). Mixed editions are not exactly comparable.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "data" / "interim"

BARRELS_PER_TONNE = 7.3                 # DELIVERABLE3 base assumption
CALIBRATION_YEARS = [2019, 2020, 2021, 2022, 2023]   # shown per year in the notes
BASELINE_YEARS = [2019, 2020, 2021, 2022]            # pre-Red Sea window used for the ratio
MIN_DAYS = 300                          # warn if a PortWatch year has fewer observed days

# EIA table row -> PortWatch chokepoint ID (IDs from config/edges.csv, chokepoint_portid)
EIA_TO_PORTID = {
    "Strait of Malacca": ("chokepoint5", "Malacca"),
    "Strait of Hormuz": ("chokepoint6", "Hormuz"),
    "Suez Canal and SUMED Pipeline": ("chokepoint1", "Suez"),
    "Bab el-Mandeb": ("chokepoint4", "Bab el-Mandeb"),
    "Cape of Good Hope": ("chokepoint7", "Cape of Good Hope"),
}
SPECIFIC_NOTES = {
    "chokepoint1": "EIA figure includes the SUMED pipeline (not ship traffic), so this ratio runs high.",
}


def read_table(stem: str, required: bool = True):
    """Read data/interim/<stem>.parquet, falling back to .csv. Returns None if optional and missing."""
    for ext, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        path = INTERIM / f"{stem}{ext}"
        if path.exists():
            return reader(path)
    if required:
        sys.exit(f"Missing input: data/interim/{stem}.parquet. Run its script first (see the header of this file).")
    return None


def eia_by_year() -> dict:
    """{portid: {year: eia_mbd}} for the chokepoints we calibrate."""
    eia = read_table("eia_chokepoint_flows")
    eia.columns = [str(c) for c in eia.columns]
    if "location" not in eia.columns:
        sys.exit(f"eia_chokepoint_flows has no `location` column. Columns: {list(eia.columns)}")
    out = {}
    for location, (portid, _name) in EIA_TO_PORTID.items():
        row = eia[eia["location"].str.strip() == location]
        if row.empty:
            print(f"  warning: EIA table has no row '{location}'. Rows: {list(eia['location'])}")
            continue
        out[portid] = {
            y: float(row.iloc[0][str(y)]) for y in CALIBRATION_YEARS
            if str(y) in eia.columns and pd.notna(row.iloc[0][str(y)])
        }
    return out


def portwatch_by_year() -> tuple[dict, dict]:
    """({portid: {year: tanker_mbd}}, {portid: {year: days_observed}}) from daily capacity_tanker."""
    pw = read_table("portwatch_chokepoints_daily")
    need = {"date", "portid", "capacity_tanker"}
    if not need.issubset(pw.columns):
        sys.exit(f"portwatch_chokepoints_daily is missing {sorted(need - set(pw.columns))}. Columns: {list(pw.columns)}")
    pw = pw.copy()
    pw["date"] = pd.to_datetime(pw["date"])
    pw["year"] = pw["date"].dt.year
    pw["capacity_tanker"] = pd.to_numeric(pw["capacity_tanker"], errors="coerce")
    # Mean of the days we have. A missing day is not zero.
    g = pw.dropna(subset=["capacity_tanker"]).groupby(["portid", "year"])["capacity_tanker"]
    mean_t, days = g.mean(), g.count()
    mbd, nd = {}, {}
    for (portid, year), tonnes in mean_t.items():
        mbd.setdefault(portid, {})[int(year)] = tonnes * BARRELS_PER_TONNE / 1e6
        nd.setdefault(portid, {})[int(year)] = int(days[(portid, year)])
    return mbd, nd


def chokepoint_rows() -> list[dict]:
    eia = eia_by_year()
    pw_mbd, pw_days = portwatch_by_year()
    rows = []
    for _location, (portid, name) in EIA_TO_PORTID.items():
        if portid not in eia:
            continue
        if portid not in pw_mbd:
            print(f"  warning: no PortWatch data for {portid} ({name}). IDs present: {sorted(pw_mbd)}")
            continue
        per_year, notes_years = {}, []
        for y in CALIBRATION_YEARS:
            if y in eia[portid] and y in pw_mbd[portid] and pw_mbd[portid][y] > 0:
                per_year[y] = (eia[portid][y], pw_mbd[portid][y])
                notes_years.append(f"{y}:{eia[portid][y] / pw_mbd[portid][y]:.2f}")
                if pw_days[portid][y] < MIN_DAYS:
                    notes_years[-1] += f"(only {pw_days[portid][y]} days)"
        base = [per_year[y] for y in BASELINE_YEARS if y in per_year]
        if not base:
            print(f"  warning: no overlapping baseline years for {portid} ({name}); skipped")
            continue
        eia_mbd = sum(b[0] for b in base) / len(base)
        pw_tanker = sum(b[1] for b in base) / len(base)
        share = eia_mbd / pw_tanker
        notes = [f"{name}; mean of {BASELINE_YEARS[0]}-{BASELINE_YEARS[-1]}; per-year ratio {' '.join(notes_years)}"]
        if portid in SPECIFIC_NOTES:
            notes.append(SPECIFIC_NOTES[portid])
        if share > 1:
            notes.append("WARNING: ratio above 1 (EIA flow larger than PortWatch tanker volume); check units and IDs.")
        rows.append(dict(scope=portid, eia_mbd=round(eia_mbd, 3), portwatch_tanker_mbd=round(pw_tanker, 3),
                         crude_share=round(share, 4), notes=" | ".join(notes)))
    return rows


def global_ratio():
    """Comtrade crude barrels / PortWatch tanker barrels at the selected ports, or None."""
    flows = read_table("country_flows", required=False)
    totals = read_table("port_totals", required=False)
    if flows is None or totals is None:
        return None
    flows = flows.rename(columns={"barrels": "barrels_per_year"})
    if "barrels_per_year" not in flows.columns:
        return None
    # Accept Part 2 in long format (Evan's od_matrix.py) or the wide format in DELIVERABLE3.
    if {"direction", "tons_per_year"}.issubset(totals.columns):
        export_tons = totals.loc[totals["direction"] == "export", "tons_per_year"].sum()
    elif "export_tanker_tons_yr" in totals.columns:
        export_tons = totals["export_tanker_tons_yr"].sum()
    else:
        print("  warning: port_totals.csv format not recognised; global ratio skipped")
        return None
    if export_tons <= 0:
        return None
    flow_bbl = float(flows["barrels_per_year"].sum())
    tanker_bbl = float(export_tons) * BARRELS_PER_TONNE
    return dict(
        eia_mbd=round(flow_bbl / 365 / 1e6, 3),
        portwatch_tanker_mbd=round(tanker_bbl / 365 / 1e6, 3),
        crude_share=round(flow_bbl / tanker_bbl, 4),
        notes=("Global: Comtrade country_flows barrels / PortWatch export tanker barrels at the selected ports "
               "(eia_mbd here is the Comtrade flow in mbd, not EIA)."),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--default", type=float, help="Set the global_default crude share by hand (0 to 1)")
    args = ap.parse_args()

    rows = chokepoint_rows()
    if not rows:
        sys.exit("No chokepoint ratios could be computed. Check the warnings above.")
    glob = global_ratio()

    if args.default is not None:
        if not 0 < args.default <= 1:
            sys.exit("--default must be between 0 and 1")
        default = dict(scope="global_default", eia_mbd="", portwatch_tanker_mbd="", crude_share=args.default,
                       notes="Set by hand with --default. Group decision.")
    elif glob is not None:
        default = dict(scope="global_default", **glob)
    else:
        shares = sorted(r["crude_share"] for r in rows)
        median = shares[len(shares) // 2]
        default = dict(scope="global_default", eia_mbd="", portwatch_tanker_mbd="", crude_share=median,
                       notes="Median of the chokepoint ratios (country_flows.csv or port_totals.csv not available yet).")

    out = pd.DataFrame([default] + rows, columns=["scope", "eia_mbd", "portwatch_tanker_mbd", "crude_share", "notes"])
    INTERIM.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM / "crude_share.csv"
    out.to_csv(out_path, index=False)
    print(f"Saved {out_path}\n")
    print(out.drop(columns="notes").to_string(index=False))
    print(f"\nglobal_default = {default['crude_share']}. Confirm this with the group; "
          "od_matrix.py still has CRUDE_SHARE = 0.6 as a placeholder until it reads this file.")
    if default["crude_share"] > 1:
        print("WARNING: the default is above 1; a share above 1 is not physical. Check the inputs.")


if __name__ == "__main__":
    main()
