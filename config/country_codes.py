"""Country code table: match UN Comtrade country codes to IMF PortWatch countries.

Deliverable 2, task 2. Joins on ISO3. Every Comtrade code (reporter or partner,
still valid in 2019 or later) and every PortWatch country gets one row, with a
status that says whether it matched and, if not, why.

Run from the repo root:
  python scripts/country_codes.py            # uses cached raw JSON if present
  python scripts/country_codes.py --refresh  # re-download from APIs
Needs: pandas, pyarrow, requests.

Outputs:
  data/raw/comtrade_reporters.json, data/raw/comtrade_partners.json,
  data/raw/portwatch_countries.json          - API responses as downloaded (git-ignored)
  data/processed/country_codes.csv           - the crosswalk (git-ignored)
  data/interim/country_codes.parquet         - cleaned Parquet in data/interim/ (git-ignored)
  config/country_codes.csv                   - reference crosswalk for git commit
"""

import argparse
import json
import re
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
PROCESSED = ROOT / "data" / "processed"
CONFIG = ROOT / "config"

COMTRADE_REF = "https://comtradeapi.un.org/files/v1/app/reference/{}"
PORTWATCH_COUNTRIES = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "PortWatch_countries_database/FeatureServer/0/query"
)

# Codes that expired before the study window are dropped (USSR, pre-1990 Germany, ...).
WINDOW_START = pd.Timestamp("2019-01-01")

# Matches ISO3 can't make. Comtrade reports Taiwan as "Other Asia, nes" (490).
MANUAL_MATCHES = {490: "TWN"}

# PortWatch territories whose Comtrade code expired; their trade is reported by the parent.
# Left unmatched on purpose: mapping them to the parent is an analysis choice.
FOLDED_INTO_PARENT = {
    "GUF": "France (251) since 1997",
    "GLP": "France (251) since 1997",
    "REU": "France (251) since 1997",
    "MYT": "France (251) since 2014",
    "VIR": "USA (842) since 1981",
}

# Comtrade's non-country codes: World, Bunkers, Free Zones, Special Categories, Areas nes.
COMTRADE_SPECIAL_CODES = {0, 837, 838, 839, 899}


def fetch_json(url, out_path, params=None, refresh=False):
    if out_path.exists() and not refresh:
        print(f"Using cached {out_path.relative_to(ROOT)} (use --refresh to re-pull)")
        return json.loads(out_path.read_text(encoding="utf-8"))
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    out_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def load_comtrade(refresh=False):
    """One row per Comtrade code, combining the reporter and partner lists."""
    frames = []
    for kind, fname in [("reporter", "Reporters.json"), ("partner", "partnerAreas.json")]:
        data = fetch_json(COMTRADE_REF.format(fname), RAW / f"comtrade_{kind}s.json", refresh=refresh)
        df = pd.DataFrame(data["results"])
        # Partner file capitalizes its keys (PartnerCode); normalize both.
        df.columns = [c[0].lower() + c[1:] for c in df.columns]
        df = df.rename(columns={
            f"{kind}Code": "comtrade_code",
            f"{kind}Desc": "comtrade_name",
            f"{kind}CodeIsoAlpha3": "comtrade_iso3",
            "isGroup": "is_group",
            "entryExpiredDate": "expired",
        })
        df[f"in_{kind}s"] = True
        frames.append(df[["comtrade_code", "comtrade_name", "comtrade_iso3",
                          "is_group", "expired", f"in_{kind}s"]])

    reporters, partners = frames
    ct = reporters.merge(partners, on="comtrade_code", how="outer", suffixes=("", "_p"))
    for col in ["comtrade_name", "comtrade_iso3", "is_group", "expired"]:
        ct[col] = ct[col].fillna(ct[f"{col}_p"])
    ct = ct.drop(columns=[c for c in ct.columns if c.endswith("_p")])

    ct["comtrade_code"] = ct["comtrade_code"].astype(int)
    ct["comtrade_name"] = ct["comtrade_name"].str.strip()
    ct["comtrade_iso3"] = ct["comtrade_iso3"].str.strip()
    ct["in_reporters"] = ct["in_reporters"].fillna(False).astype(bool)
    ct["in_partners"] = ct["in_partners"].fillna(False).astype(bool)
    ct["is_group"] = ct["is_group"].fillna(False).astype(bool)

    expired = pd.to_datetime(ct["expired"])
    ct = ct[expired.isna() | (expired >= WINDOW_START)].drop(columns="expired")
    return ct


def load_portwatch(refresh=False):
    data = fetch_json(
        PORTWATCH_COUNTRIES,
        RAW / "portwatch_countries.json",
        params={"where": "1=1", "outFields": "country,ISO3,economy_type",
                "returnGeometry": "false", "f": "json"},
        refresh=refresh,
    )
    if "error" in data or data.get("exceededTransferLimit"):
        raise RuntimeError(f"PortWatch query incomplete: {data.get('error')}")
    pw = pd.DataFrame([f["attributes"] for f in data["features"]])
    return pw.rename(columns={"country": "portwatch_country", "ISO3": "portwatch_iso3",
                              "economy_type": "portwatch_economy_type"})


def is_comtrade_aggregate(row):
    return (row["is_group"]
            or row["comtrade_code"] in COMTRADE_SPECIAL_CODES
            or "nes" in row["comtrade_name"].split()
            or not re.fullmatch(r"[A-Z]{3}", str(row["comtrade_iso3"])))


def build_table(ct, pw):
    ct = ct.copy()
    ct["match_iso3"] = ct["comtrade_iso3"]
    ct["match_method"] = "iso3"
    for code, iso3 in MANUAL_MATCHES.items():
        ct.loc[ct["comtrade_code"] == code, ["match_iso3", "match_method"]] = [iso3, "manual"]
    ct.loc[ct.apply(is_comtrade_aggregate, axis=1) & (ct["match_method"] != "manual"),
           "match_iso3"] = None

    # Several live Comtrade codes can share an ISO3 (USA 840/842, France 250/251).
    # The one in the reporter list is the code trade data uses; the rest are aliases.
    ct["is_alias"] = False
    for iso3, grp in ct.groupby("match_iso3"):
        if len(grp) == 1:
            continue
        primary = grp[grp["in_reporters"]]
        if len(primary) != 1:
            raise RuntimeError(f"Can't pick one Comtrade code for {iso3}:\n{grp}")
        ct.loc[grp.index.difference(primary.index), "is_alias"] = True

    primary_ct = ct[~ct["is_alias"]]
    table = primary_ct.merge(pw, left_on="match_iso3", right_on="portwatch_iso3",
                             how="outer", indicator=True)
    aliases = ct[ct["is_alias"]].assign(_merge="alias")
    table = pd.concat([table, aliases], ignore_index=True)

    def status_and_note(row):
        if row["_merge"] == "both":
            note = "Taiwan; Comtrade reports it as Other Asia, nes" if row["match_method"] == "manual" else ""
            return "matched", note
        if row["_merge"] == "alias":
            return "alias", f"Duplicate code for {row['match_iso3']}; use the reporter-list code"
        if row["_merge"] == "right_only":
            if row["portwatch_economy_type"] != "country":
                return "portwatch_only", "PortWatch regional/global aggregate"
            parent = FOLDED_INTO_PARENT.get(row["portwatch_iso3"])
            if parent:
                return "portwatch_only", f"Reported by Comtrade under {parent}"
            return "portwatch_only", "No Comtrade code with this ISO3"
        if pd.isna(row["match_iso3"]):
            return "comtrade_only", "Comtrade aggregate or special code"
        return "comtrade_only", "Not in PortWatch countries (e.g. landlocked)"

    table[["status", "note"]] = table.apply(status_and_note, axis=1, result_type="expand")
    table.loc[table["status"] != "matched", "match_method"] = "none"
    table["comtrade_code"] = table["comtrade_code"].astype("Int64")

    cols = ["status", "match_method", "comtrade_code", "comtrade_name", "comtrade_iso3",
            "in_reporters", "in_partners", "portwatch_country", "portwatch_iso3",
            "portwatch_economy_type", "note"]
    order = {"matched": 0, "portwatch_only": 1, "comtrade_only": 2, "alias": 3}
    return (table[cols]
            .sort_values(["status", "comtrade_name", "portwatch_country"],
                         key=lambda s: s.map(order) if s.name == "status" else s)
            .reset_index(drop=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="Re-download reference JSONs even if cached")
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    INTERIM.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    CONFIG.mkdir(parents=True, exist_ok=True)

    table = build_table(load_comtrade(refresh=args.refresh), load_portwatch(refresh=args.refresh))

    out_processed = PROCESSED / "country_codes.csv"
    out_interim = INTERIM / "country_codes.parquet"
    out_config = CONFIG / "country_codes.csv"

    table.to_csv(out_processed, index=False, encoding="utf-8")
    table.to_parquet(out_interim, index=False)
    table.to_csv(out_config, index=False, encoding="utf-8")

    print(f"Wrote {out_processed.relative_to(ROOT)}, {out_interim.relative_to(ROOT)}, and {out_config.relative_to(ROOT)} ({len(table)} rows)")
    print(table.groupby(["status", "note"], dropna=False).size().to_string())

    unmatched = table[table["status"].isin(["portwatch_only", "comtrade_only"])
                      & ~table["note"].isin(["PortWatch regional/global aggregate",
                                             "Comtrade aggregate or special code"])]
    print("\nReal countries/territories that did not match:")
    for _, r in unmatched.iterrows():
        name = r["portwatch_country"] if r["status"] == "portwatch_only" else r["comtrade_name"]
        iso3 = r["portwatch_iso3"] if r["status"] == "portwatch_only" else r["comtrade_iso3"]
        print(f"  {r['status']:15} {iso3}  {name}")


if __name__ == "__main__":
    main()