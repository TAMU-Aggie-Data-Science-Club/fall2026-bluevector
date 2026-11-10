"""Build BlueVector Deliverable 3 Graph Part 1 nodes.csv.

Run from the repository root: python config/build_nodes.py
Uses only Python's standard library and the existing PortWatch ArcGIS source.
Inputs: config/ports_selected.csv, config/junctions.csv
Output: config/nodes.csv
"""
import csv
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SELECTED = ROOT / "config/ports_selected.csv"
JUNCTIONS = ROOT / "config/junctions.csv"
OUTPUT = ROOT / "config/nodes.csv"
URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/"
       "services/Daily_Ports_Data/FeatureServer/0/query")
PERIOD = "2019-2025"
EXTRA = ("import_tanker_tons", "export_tanker_tons", "tanker_role", "source_period")


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fetch_sums(portids):
    result = {}
    for start in range(0, len(portids), 8):
        group = portids[start:start + 8]
        where = ("year >= 2019 AND year <= 2025 AND portid IN ("
                 + ",".join("'" + p + "'" for p in group) + ")")
        stats = [
            {"statisticType": "sum", "onStatisticField": col,
             "outStatisticFieldName": "sum_" + col}
            for col in ("import_tanker", "export_tanker")
        ]
        params = {"where": where, "groupByFieldsForStatistics": "portid",
                  "outStatistics": json.dumps(stats), "returnGeometry": "false",
                  "f": "json"}
        last = None
        for attempt in range(3):
            try:
                with urlopen(URL + "?" + urlencode(params), timeout=90) as response:
                    data = json.load(response)
                if "error" in data:
                    raise RuntimeError(str(data["error"]))
                for feature in data.get("features", []):
                    attrs = feature["attributes"]
                    keymap = {k.lower().replace("\\_", "_"): v for k, v in attrs.items()}
                    pid = keymap["portid"]
                    result[pid] = (int(round(keymap["sum_import_tanker"] or 0)),
                                   int(round(keymap["sum_export_tanker"] or 0)))
                break
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                last = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        else:
            raise RuntimeError("PortWatch query failed for " + repr(group)) from last
    missing = sorted(set(portids) - set(result))
    if missing:
        raise ValueError("PortWatch omitted portids: " + ", ".join(missing))
    return result


def build():
    ports = read_csv(SELECTED)
    junctions = read_csv(JUNCTIONS)
    if len(ports) != 100 or len(junctions) != 38:
        raise ValueError("Expected 100 selected ports and 38 junctions")
    member_ids = sorted({p for row in ports for p in row["member_portids"].split(";")})
    sums = fetch_sums(member_ids)
    columns = list(ports[0]) + list(EXTRA)
    nodes = []
    for port in ports:
        members = port["member_portids"].split(";")
        imports = sum(sums[p][0] for p in members)
        exports = sum(sums[p][1] for p in members)
        row = {**port, "import_tanker_tons": imports, "export_tanker_tons": exports,
               "tanker_role": ("net_importer" if imports > exports else
                               "net_exporter" if exports > imports else "balanced"),
               "source_period": PERIOD}
        nodes.append(row)
    for junction in junctions:
        nodes.append({**{c: "" for c in columns},
                      **{k: v for k, v in junction.items() if k in columns}})
    ids = [r["node_id"] for r in nodes]
    if len(ids) != len(set(ids)) or len(nodes) != 138:
        raise ValueError("Duplicate node IDs or incorrect node count")
    for row in nodes:
        lat, lon = float(row["lat"]), float(row["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Invalid coordinates: " + row["node_id"])
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(nodes)
    print("Wrote", OUTPUT, "with", len(ports), "ports and", len(junctions), "junctions")
    print("Note: selected tons_tanker may differ slightly from current PortWatch sums",
          "because the published daily series can be revised.")


if __name__ == "__main__":
    build()
