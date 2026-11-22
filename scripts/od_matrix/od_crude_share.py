"""Crude share calibration: how much of PortWatch tanker tonnage is crude oil (OD matrix, Part 3).

What it does
    PortWatch counts ALL liquid-bulk tankers (crude, products, chemicals, LNG/LPG), and
    capacity_tanker is an estimate of the cargo carried (payload share x DWT), in both
    directions, laden or in ballast. EIA publishes how much *crude* passes each chokepoint,
    and Comtrade publishes how much crude each country exports. This script uses those two
    to derive the crude share of tanker tonnage for the two different universes the project
    multiplies by it.

    A. Chokepoint universe (used by est_oil_volume on chokepoint edges)
           crude_share = EIA crude flow (mbd) / PortWatch tanker volume (mbd)
       averaged over 2020-2022 (EIA publishes the crude split from 2020; the baseline ends
       before the Red Sea crisis). Per-year ratios go in the notes column.

    B. Port universe (the OD matrix `global_default`)
       The OD base is port tanker tonnage, so the numerator has to be crude *at those ports*.
       A country's crude does not all load at the ports we selected, so each country's crude
       and imports are weighted by the share of that country's tanker tonnage that sits in
       the 100 selected ports (from PortWatch's all-port table, 1,955 ports):

           w_c         = selected-port tanker tons of country c / all-port tanker tons of c
           crude_calls = sum_c (crude exports_c + crude imports_c) x w_c
           share       = crude_calls / (selected export + import tanker tons x 7.3)

       Both sides count a voyage twice (load and discharge), so the ratio is consistent.
       Result: 0.526, i.e. 0.53. Cross-check by a completely different route (Comtrade
       crude vs HS 2710 products x petroleum's 0.85 share of tanker tonnage) also gives
       0.526; see .planning/crude-share.md for that derivation.

Run from the repository root
    python scripts/od_matrix/od_crude_share.py                 # compute both, write the file
    python scripts/od_matrix/od_crude_share.py --default 0.53  # override the port-side value

Inputs (data/interim/)
    eia_chokepoint_flows.parquet            scripts/data_pull/eia_chokepoints.py
                                            (commodity = total | crude | products)
    portwatch_chokepoints_daily.parquet      scripts/data_pull/pull_chokepoints.py
    country_flows.csv                        scripts/od_matrix/od_country_flows.py   (optional)
    port_totals.csv                          scripts/od_matrix/od_port_shares.py     (optional)
    portwatch_ports_totals.parquet           PortWatch all-port tonnage (optional)
                                            (tons_tanker is cumulative over n_days, so it is
                                             annualised by n_days/365.25)

Output (data/interim/)
    crude_share.csv   scope, eia_mbd, portwatch_tanker_mbd, crude_share, notes
                      scope = `global_default` (port universe) or a PortWatch chokepoint ID
                      (chokepoint5 = Malacca, chokepoint6 = Hormuz, chokepoint1 = Suez,
                       chokepoint4 = Bab el-Mandeb, chokepoint7 = Cape of Good Hope,
                       chokepoint2 = Panama)

Things to know before trusting the numbers
    * The two scopes answer different questions and are NOT interchangeable. Crude makes
      roughly 1.3 chokepoint transits per tonne while products make roughly 0.5, so
      chokepoint tonnage is crude-enriched about twice over versus port tonnage. Using the
      chokepoint value in the OD inflates the matrix by ~35 %.
    * EIA's Suez row includes the SUMED pipeline, which is not ship traffic.
    * EIA's Panama row is a fiscal year (October-September).
    * The port-side value assumes crude and products use a country's ports in proportion to
      that country's tanker tonnage; there is no port-level commodity data in this project.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "data" / "interim"

BARRELS_PER_TONNE = 7.3                 # DELIVERABLE3 base assumption
CALIBRATION_YEARS = [2020, 2021, 2022, 2023]   # crude split exists from 2020; shown per year
BASELINE_YEARS = [2020, 2021, 2022]            # pre-Red Sea window used for the ratio
MIN_DAYS = 300                          # warn if a PortWatch year has fewer observed days

# EIA table row -> PortWatch chokepoint ID (IDs from config/edges.csv, chokepoint_portid)
EIA_TO_PORTID = {
    "Strait of Malacca": ("chokepoint5", "Malacca"),
    "Strait of Hormuz": ("chokepoint6", "Hormuz"),
    "Suez Canal and SUMED Pipeline": ("chokepoint1", "Suez"),
    "Bab el-Mandeb": ("chokepoint4", "Bab el-Mandeb"),
    "Cape of Good Hope": ("chokepoint7", "Cape of Good Hope"),
    "Panama Canal": ("chokepoint2", "Panama"),
}
SPECIFIC_NOTES = {
    "chokepoint1": "EIA figure includes the SUMED pipeline (not ship traffic).",
    "chokepoint2": "EIA Panama row is a fiscal year (Oct-Sep).",
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


def eia_by_year(commodity: str = "crude") -> dict:
    """{portid: {year: eia_mbd}} from the EIA table, for one commodity tag."""
    eia = read_table("eia_chokepoint_flows")
    eia.columns = [str(c) for c in eia.columns]
    if "location" not in eia.columns:
        sys.exit(f"eia_chokepoint_flows has no `location` column. Columns: {list(eia.columns)}")
    if "commodity" in eia.columns:
        eia = eia[eia["commodity"].astype(str).str.strip().str.lower() == commodity]
    elif commodity != "total":
        sys.exit("eia_chokepoint_flows has no `commodity` column; re-run "
                 "scripts/data_pull/eia_chokepoints.py to add the crude/products rows.")
    out = {}
    for location, (portid, _name) in EIA_TO_PORTID.items():
        row = eia[eia["location"].astype(str).str.strip() == location]
        if row.empty:
            print(f"  warning: EIA table has no {commodity} row '{location}'.")
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
    """Crude-only ratio per chokepoint: EIA crude mbd / PortWatch tanker mbd."""
    eia = eia_by_year("crude")
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
        notes = [f"{name}; EIA crude / PortWatch tanker; mean of {BASELINE_YEARS[0]}-{BASELINE_YEARS[-1]}; "
                 f"per-year ratio {' '.join(notes_years)}"]
        if portid in SPECIFIC_NOTES:
            notes.append(SPECIFIC_NOTES[portid])
        if share > 1:
            notes.append("WARNING: crude above PortWatch tanker tonnage for this chokepoint; check the inputs.")
        rows.append(dict(scope=portid, eia_mbd=round(eia_mbd, 3), portwatch_tanker_mbd=round(pw_tanker, 3),
                         crude_share=round(share, 4), notes=" | ".join(notes)))
    return rows


def port_side_share():
    """Crude share of PortWatch tanker tonnage at the 100 selected ports, or None.

    Each country's crude is weighted by the share of that country's tanker tonnage that sits
    in the selected ports, so crude that loads at a port we did not select is not counted.
    """
    flows = read_table("country_flows", required=False)
    totals = read_table("port_totals", required=False)
    all_ports = read_table("portwatch_ports_totals", required=False)
    if flows is None or totals is None or all_ports is None:
        print("  note: country_flows.csv, port_totals.csv or portwatch_ports_totals.parquet missing; "
              "port-side share skipped.")
        return None
    flows = flows.rename(columns={"barrels": "barrels_per_year"})
    if not {"exporter_iso3", "importer_iso3", "barrels_per_year"}.issubset(flows.columns):
        return None
    if not {"export_tanker_tons_yr", "import_tanker_tons_yr"}.issubset(totals.columns):
        print("  warning: port_totals.csv format not recognised; port-side share skipped")
        return None

    ap = all_ports.copy()
    days = pd.to_numeric(ap.get("n_days"), errors="coerce")
    ap["years"] = (days / 365.25).where(days > 0)
    ap["tons_yr"] = pd.to_numeric(ap["tons_tanker"], errors="coerce") / ap["years"]
    all_t = ap.dropna(subset=["tons_yr"]).groupby("ISO3")["tons_yr"].sum()

    sel_t = totals.groupby("iso3")[["export_tanker_tons_yr", "import_tanker_tons_yr"]].sum().sum(axis=1)
    crude_exp = flows.groupby("exporter_iso3")["barrels_per_year"].sum()
    crude_imp = flows.groupby("importer_iso3")["barrels_per_year"].sum()
    crude = crude_exp.add(crude_imp.reindex(crude_exp.index).fillna(0.0), fill_value=0.0)

    weight = (sel_t / all_t).reindex(crude.index).fillna(0.0).clip(0.0, 1.0)
    crude_calls = float((crude * weight).sum())
    denominator = float(sel_t.sum()) * BARRELS_PER_TONNE
    if denominator <= 0:
        return None

    covered = float((crude * weight).sum() / crude.sum()) if crude.sum() > 0 else 0.0
    return dict(
        eia_mbd=round(crude_calls / 365 / 1e6, 3),          # Comtrade crude at the selected ports, mbd
        portwatch_tanker_mbd=round(denominator / 365 / 1e6, 3),
        crude_share=round(crude_calls / denominator, 4),
        notes=("Port universe: Comtrade crude exports+imports of the 100 ports' countries, each country "
               f"weighted by the share of its all-port tanker tonnage that sits in the selected ports "
               f"(crude coverage {covered:.1%}), divided by selected-port tanker tonnage x 7.3. "
               "eia_mbd here is the Comtrade crude volume reaching those ports, not EIA."),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--default", type=float, help="Set the port-side global_default crude share by hand (0 to 1)")
    args = ap.parse_args()

    rows = chokepoint_rows()
    if not rows:
        sys.exit("No chokepoint ratios could be computed. Check the warnings above.")
    port = port_side_share()

    if args.default is not None:
        if not 0 < args.default <= 1:
            sys.exit("--default must be between 0 and 1")
        default = dict(scope="global_default", eia_mbd="", portwatch_tanker_mbd="", crude_share=args.default,
                       notes="Set by hand with --default. Group decision; applies to the port universe only.")
    elif port is not None:
        default = dict(scope="global_default", **port)
    else:
        shares = sorted(r["crude_share"] for r in rows)
        median = shares[len(shares) // 2]
        default = dict(scope="global_default", eia_mbd="", portwatch_tanker_mbd="", crude_share=median,
                       notes=("Median of the chokepoint ratios (port-side inputs missing). WARNING: this is the "
                              "chokepoint universe, which is crude-enriched; prefer re-running with the port "
                              "inputs so the OD base and the share agree."))

    out = pd.DataFrame([default] + rows, columns=["scope", "eia_mbd", "portwatch_tanker_mbd", "crude_share", "notes"])
    INTERIM.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM / "crude_share.csv"
    out.to_csv(out_path, index=False)
    print(f"Saved {out_path}\n")
    print(out.drop(columns="notes").to_string(index=False))
    print(f"\nglobal_default (port universe) = {default['crude_share']}. "
          f"Chokepoint rows are the crude share of chokepoint tonnage and are used per edge by "
          f"build_edge_attributes.py; do not substitute one for the other.")


if __name__ == "__main__":
    main()
