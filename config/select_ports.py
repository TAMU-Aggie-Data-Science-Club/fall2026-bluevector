"""Select the ~100 ports for the network (crude-oil focused, tankers only in v1).

Run from the repository root:  python config/select_ports.py
Needs:  pip install pandas pyarrow
Run these first:
  python config/pull_ports_totals.py         (required: tons and ranks per port)
  python config/pull_ports_static.py         (recommended: lat, lon, LOCODE, continent, names)

FORMULA (v2, changed from the DATA.md "Port selection" formula, which was 60 by total
tons + 40 by tanker tons; see PARAMS and the comparison this script prints):
  1. Pool = every PortWatch port, minus EXCLUDE_*, after merging the terminals in MERGE.
  2. Take the top N_TANKER ports by tanker tons (import + export, 2019-2025).
  3. Take the top N_TOTAL ports by total tons, but only ports whose tanker tons are at
     least MIN_TANKER_SHARE of their total (keeps pure iron ore / coal / container ports
     out: the model is tankers only).
  4. Add INCLUDE_* ports, de-duplicate, then fill up to N_PORTS by combined rank:
     TANKER_WEIGHT * rank_tanker + (1 - TANKER_WEIGHT) * rank_total (lower is better).
Coverage = selected tons / global PortWatch tons, for total and tanker volume and by region.
All numbers are my starting defaults: tune them in PARAMS below.

Outputs:
  config/ports_selected.csv                    the selected ports (a small reference list, safe to commit)
  data/interim/ports_selected_coverage.csv     coverage report by region
The first columns match config/nodes.csv from Deliverable 2; wpi_index is left empty.
`member_portids` lists every PortWatch portid merged into a node (needed to pull its daily series).
Tons are PortWatch ESTIMATES, and "tanker" is not crude-only (it includes products, chemicals, LNG/LPG).
"""
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
INTERIM = REPO / "data/interim"
OUT = REPO / "config/ports_selected.csv"
COVERAGE = INTERIM / "ports_selected_coverage.csv"

# --- Formula parameters (my starting defaults: tune freely) ---------------------------
PARAMS = dict(n_ports=100, n_tanker=70, n_total=20, min_tanker_share=0.25, tanker_weight=0.9)
# The original DATA.md formula, kept only for the comparison printed at the end.
V1 = dict(n_ports=100, n_tanker=40, n_total=60, min_tanker_share=0.0, tanker_weight=0.5)

# --- Manual overrides. Use PortWatch portids or exact port names. ---------------------
# MERGE: terminals of one port complex -> one node. {"member_portid": "representative_portid"}.
#        The member's tons are added to the representative and the member is removed.
MERGE = {
    "port1429": "port824",    # Zhoushan -> Ningbo (26 km apart; Ningbo-Zhoushan)
    "port526": "port1091",    # Juaymah -> Ras Tanura (21 km apart, adjacent Saudi terminals)
    "port134": "port933",     # Beaumont -> Port Arthur (19 km apart, same Texas waterway)
    "port33": "port805",      # Aliaga -> Nemrut Bay (5 km apart, same Izmir-area complex)
}
# INCLUDE: always selected (they count toward n_ports), even if the formula would not pick them.
INCLUDE_IDS = []
INCLUDE_NAMES = ["Sikka", "Bonny", "Kharg Island"]   # Indian refinery port, Nigerian crude, Iranian crude
# EXCLUDE: never selected and left out of the ranking.
EXCLUDE_IDS = ["fso120"]   # "Malaysia - Offshore Oil Terminal 6": floating storage / ship-to-ship area off Johor
EXCLUDE_NAMES = []
# If the industry_top1/2/3 columns in ports_selected.csv confirm these are LNG terminals (the model is
# crude-only), uncomment:  EXCLUDE_NAMES = ["Ras Laffan", "Sabine Pass"]
DUP_KM = 100  # report selected same-country port pairs closer than this (just a report, never auto-merged)

INDUSTRY = ["industry_top1", "industry_top2", "industry_top3"]  # from the static ports table, if present
SUM_COLS = ["tons_import_total", "tons_export_total", "tons_total", "tons_tanker",
            "portcalls_total", "portcalls_tanker"]

# Well-known oil ports for a name-based sanity check (my own list, NOT authoritative;
# PortWatch names may differ, so a miss means "look manually").
EXPECTED = ["singapore", "rotterdam", "houston", "corpus christi", "ras tanura", "fujairah",
            "basra", "novorossiysk", "primorsk", "ust-luga", "ceyhan", "botas", "ningbo", "qingdao",
            "dalian", "zhoushan", "ulsan", "yeosu", "chiba", "sikka", "vadinar", "antwerp",
            "algeciras", "bonny", "louisiana", "port arthur", "jubail", "yanbu", "kharg", "ahmadi",
            "jose", "es sider", "brega", "zawiya", "luanda", "soyo", "cabinda", "forcados", "qua iboe",
            "mundra", "paradip", "kochi"]


def read_table(stem):
    for ext in (".parquet", ".csv"):
        path = INTERIM / f"{stem}{ext}"
        if path.exists():
            df = pd.read_parquet(path) if ext == ".parquet" else pd.read_csv(path)
            df["portid"] = df["portid"].astype(str)
            return df
    return None


def attach_static(everything):
    """Add lat, lon, LOCODE, continent (and fill missing names) from the static ports table."""
    static = read_table("portwatch_ports_database")
    for c in ("lat", "lon", "LOCODE", "continent", *INDUSTRY):
        everything[c] = pd.NA
    if static is None:
        print("NOTE: data/interim/portwatch_ports_database.csv not found; lat/lon/LOCODE/continent "
              "left empty and region coverage skipped. Run pull_ports_static.py first.")
        return everything
    low = {c.lower(): c for c in static.columns}
    keep = {low[k]: v for k, v in {"lat": "lat", "lon": "lon", "locode": "LOCODE", "continent": "continent",
                                   "portname": "_portname", "country": "_country", "iso3": "_ISO3",
                                   **{c: c for c in INDUSTRY}}.items()
            if k in low}
    static = static[["portid"] + list(keep)].rename(columns=keep).drop_duplicates("portid")
    for c in ("lat", "lon", "LOCODE", "continent", "_portname", "_country", "_ISO3", *INDUSTRY):
        if c not in static.columns:
            static[c] = pd.NA
    everything = everything.drop(columns=["lat", "lon", "LOCODE", "continent", *INDUSTRY]).merge(static, on="portid", how="left")
    for col in ("portname", "country", "ISO3"):  # the totals file may have no names: use the static table's
        everything[col] = everything[col].fillna(everything["_" + col]) if col in everything.columns else everything["_" + col]
    return everything.drop(columns=["_portname", "_country", "_ISO3"])


def ids_from(everything, ids, names, what):
    """portids from explicit ids plus exact (case-insensitive) port names; warns about misses."""
    found = []
    for pid in ids:
        if pid in set(everything["portid"]):
            found.append(pid)
        else:
            print(f"WARNING: {what} portid {pid!r} not found")
    lower = everything["portname"].fillna("").str.lower()
    for name in names:
        hit = everything[lower == name.lower()]
        if hit.empty:
            print(f"WARNING: {what} name {name!r} matches no port exactly")
        else:
            best = hit.sort_values("tons_tanker", ascending=False).iloc[0]
            if len(hit) > 1:
                print(f"NOTE: {what} name {name!r} matches {len(hit)} ports; using {best['portid']} "
                      f"(most tanker tons)")
            found.append(best["portid"])
    return found


def apply_merges(df):
    df = df.copy()
    df["member_portids"] = df["portid"]
    for member, rep in MERGE.items():
        ids = set(df["portid"])
        if member not in ids or rep not in ids or member == rep:
            print(f"WARNING: MERGE {member!r} -> {rep!r} skipped (id missing or identical)")
            continue
        im, ir = df.index[df["portid"] == member][0], df.index[df["portid"] == rep][0]
        for c in [c for c in SUM_COLS if c in df.columns]:
            df.loc[ir, c] = df.loc[ir, c] + df.loc[im, c]
        df.loc[ir, "member_portids"] = df.loc[ir, "member_portids"] + ";" + df.loc[im, "member_portids"]
        print(f"Merged {df.loc[im, 'portname']} ({member}) into {df.loc[ir, 'portname']} ({rep})")
        df = df.drop(index=im)
    return df.reset_index(drop=True)


def add_ranks(df):
    df = df.copy()
    df["rank_total"] = df["tons_total"].rank(ascending=False, method="first").astype(int)
    df["rank_tanker"] = df["tons_tanker"].rank(ascending=False, method="first").astype(int)
    df["tanker_share"] = (df["tons_tanker"] / df["tons_total"]).where(df["tons_total"] > 0)
    return df


def select(df, p, forced=()):
    """Apply the formula with parameters p. Returns (all ports with columns, chosen ports)."""
    df = add_ranks(df)
    w = p["tanker_weight"]
    df["combined_rank"] = w * df["rank_tanker"] + (1 - w) * df["rank_total"]
    eligible = df["tanker_share"].fillna(0) >= p["min_tanker_share"]
    top_tanker = df["rank_tanker"] <= p["n_tanker"]
    elig_rank = df.loc[eligible, "tons_total"].rank(ascending=False, method="first")
    top_total = df.index.isin(elig_rank[elig_rank <= p["n_total"]].index)
    is_forced = df["portid"].isin(list(forced))

    df["selected_by"] = ""
    df.loc[top_total, "selected_by"] = "top_total"
    df.loc[top_tanker, "selected_by"] = "top_tanker"
    df.loc[top_total & top_tanker, "selected_by"] = "both"
    df.loc[is_forced, "selected_by"] = "include"
    chosen_mask = is_forced | top_tanker | top_total
    chosen = df[chosen_mask]

    n_fill = p["n_ports"] - len(chosen)
    if n_fill > 0:
        rest = df[~chosen_mask & eligible].sort_values(["combined_rank", "rank_tanker"]).head(n_fill).copy()
        rest["selected_by"] = "fill_combined_rank"
        chosen = pd.concat([chosen, rest])
    elif n_fill < 0:  # more picks than slots: drop the worst combined-rank non-forced ports
        print(f"NOTE: {len(chosen)} picks exceed n_ports={p['n_ports']}; dropping {-n_fill} worst by combined rank")
        keep_forced = chosen[chosen["portid"].isin(list(forced))]
        others = chosen[~chosen["portid"].isin(list(forced))].sort_values(["combined_rank", "rank_tanker"])
        chosen = pd.concat([keep_forced, others.head(p["n_ports"] - len(keep_forced))])
    return df, chosen.sort_values("rank_tanker").reset_index(drop=True)


def coverage(everything, chosen):
    g_total, g_tanker = everything["tons_total"].sum(), everything["tons_tanker"].sum()
    rows = [{"region": "GLOBAL", "ports_selected": len(chosen),
             "share_of_total_tons": chosen["tons_total"].sum() / g_total,
             "share_of_tanker_tons": chosen["tons_tanker"].sum() / g_tanker,
             "region_share_of_selection": 1.0}]
    if everything["continent"].notna().any():
        for region, grp in everything.groupby(everything["continent"].fillna("(unknown)")):
            sel = chosen[chosen["portid"].isin(grp["portid"])]
            gt, gk = grp["tons_total"].sum(), grp["tons_tanker"].sum()
            rows.append({"region": region, "ports_selected": len(sel),
                         "share_of_total_tons": sel["tons_total"].sum() / gt if gt else float("nan"),
                         "share_of_tanker_tons": sel["tons_tanker"].sum() / gk if gk else float("nan"),
                         "region_share_of_selection": len(sel) / len(chosen)})
    return pd.DataFrame(rows)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def near_duplicates(chosen):
    c = chosen.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    pairs = []
    for i in range(len(c)):
        for j in range(i + 1, len(c)):
            if c.loc[i, "ISO3"] != c.loc[j, "ISO3"]:
                continue
            d = haversine_km(float(c.loc[i, "lat"]), float(c.loc[i, "lon"]), float(c.loc[j, "lat"]), float(c.loc[j, "lon"]))
            if d <= DUP_KM:
                pairs.append((d, c.loc[i], c.loc[j]))
    if pairs:
        print(f"\nSame-country selected ports within {DUP_KM} km (possible same port complex; "
              "to merge, add to MERGE: {member: representative}):")
        for d, a, b in sorted(pairs, key=lambda t: t[0]):
            print(f"  {d:5.0f} km  {a['portname']} ({a['portid']})  <->  {b['portname']} ({b['portid']})")


def name_check(everything, chosen):
    print("\nSanity check: well-known oil ports by name (name matching only, verify manually):")
    names = everything["portname"].fillna("").str.lower()
    sel_ids = set(chosen["portid"])
    for word in EXPECTED:
        hits = everything[names.str.contains(word, regex=False)]
        if hits.empty:
            print(f"  {word:16} no PortWatch name contains this (may be named differently)")
            continue
        for _, r in hits.sort_values("rank_tanker").head(3).iterrows():
            flag = "SELECTED    " if r["portid"] in sel_ids else "not selected"
            print(f"  {word:16} {flag} {str(r['portname'])[:34]:34} rank_total={r['rank_total']:<5} "
                  f"rank_tanker={r['rank_tanker']}")


def main():
    totals = read_table("portwatch_ports_totals")
    if totals is None:
        raise SystemExit("Run python config/pull_ports_totals.py first (data/interim/portwatch_ports_totals.*)")
    base = attach_static(totals.drop(columns=[c for c in ("rank_total", "rank_tanker") if c in totals.columns]))

    # Comparison baseline: the original formula on the unmodified data.
    v1_all, v1_chosen = select(base, V1)

    # v2: apply overrides, then the formula.
    exclude = set(ids_from(base, EXCLUDE_IDS, EXCLUDE_NAMES, "EXCLUDE"))
    forced = ids_from(base, INCLUDE_IDS, INCLUDE_NAMES, "INCLUDE")
    pool = apply_merges(base[~base["portid"].isin(exclude)])
    everything, chosen = select(pool, PARAMS, forced)

    out = pd.DataFrame({
        "node_id": "port_" + chosen["portid"], "node_type": "port", "name": chosen["portname"],
        "country_iso3": chosen["ISO3"], "lat": chosen["lat"], "lon": chosen["lon"],
        "portwatch_portid": chosen["portid"], "wpi_index": "", "unlocode": chosen["LOCODE"],
        "rank_total": chosen["rank_total"], "rank_tanker": chosen["rank_tanker"],
        "tons_total": chosen["tons_total"].round().astype("int64"),
        "tons_tanker": chosen["tons_tanker"].round().astype("int64"),
        "country": chosen["country"], "continent": chosen["continent"],
        **{c: chosen[c] for c in INDUSTRY},
        "tanker_share": chosen["tanker_share"].round(4), "combined_rank": chosen["combined_rank"].round(1),
        "selected_by": chosen["selected_by"], "member_portids": chosen["member_portids"],
        "share_of_global_total": chosen["tons_total"] / everything["tons_total"].sum(),
        "share_of_global_tanker": chosen["tons_tanker"] / everything["tons_tanker"].sum(),
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    INTERIM.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    cov = coverage(everything, chosen)
    cov.to_csv(COVERAGE, index=False)

    print(f"\nFormula parameters: {PARAMS}")
    print(f"Static match: {chosen['lat'].notna().sum()}/{len(chosen)} selected ports have coordinates, "
          f"{chosen['LOCODE'].notna().sum()}/{len(chosen)} have a LOCODE")
    print(f"Selected {len(out)} ports -> {OUT}")
    print(out["selected_by"].value_counts().to_string())
    print(f"\nPortWatch ports in the ranking: {len(everything):,}")
    pd.set_option("display.width", 220)
    print("\nCoverage (share of PortWatch estimated tons):")
    print(cov.to_string(index=False, float_format=lambda x: f"{x:.1%}"))
    print("\nThe selected ports (by tanker rank):")
    cols = ["rank_tanker", "rank_total", "portwatch_portid", "name", "country_iso3", "continent",
            "tanker_share", "industry_top1", "industry_top2", "selected_by"]
    print(out[cols].to_string(index=False))

    # What changed against the original 60/40 formula
    old_ids, new_ids = set(v1_chosen["portid"]), set(chosen["portid"])
    label = base.set_index("portid")["portname"]
    print(f"\nChange vs original formula (60 total / 40 tanker): {len(old_ids - new_ids)} dropped, "
          f"{len(new_ids - old_ids)} added, {len(old_ids & new_ids)} kept")
    print("  Dropped:", ", ".join(sorted(str(label.get(i, i)) for i in old_ids - new_ids)) or "-")
    print("  Added:  ", ", ".join(sorted(str(label.get(i, i)) for i in new_ids - old_ids)) or "-")
    v1c = coverage(v1_all, v1_chosen).iloc[0]
    print(f"  Coverage original: total {v1c['share_of_total_tons']:.1%}, tanker {v1c['share_of_tanker_tons']:.1%}"
          f"  ->  new: total {cov.iloc[0]['share_of_total_tons']:.1%}, tanker {cov.iloc[0]['share_of_tanker_tons']:.1%}")

    name_check(everything, chosen)
    near_duplicates(chosen)
    dupes = out[out.duplicated("name", keep=False)]
    if len(dupes):
        print("\nWARNING: duplicate port names in the selection (check for terminals of one port):")
        print(dupes[["portwatch_portid", "name", "country_iso3", "tons_total"]].to_string(index=False))


if __name__ == "__main__":
    main()