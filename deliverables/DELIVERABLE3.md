# 🕸️ Third Deliverable: Graph Build + OD Matrix

**📅 Due: Tuesday, November 3, 2026**

## 🎯 What this is

Deliverable 2 gave us the map (38 junctions, 54 edges), the ranked ports, and the data pipelines. Now we build the two things the flow model needs:

1. **The graph:** the full network of ports, junctions, and lanes, with lengths and attributes.
2. **The OD matrix:** an estimate of how much crude oil moves from each port to each other port.

Next deliverable, the **flow model**, routes the OD matrix over the graph. We are **not** building the flow model yet.

## 👥 Two groups

| Group | Builds |
|---|---|
| 🧭 **Graph** | `nodes.csv`, `edges.csv` |
| 🛢️ **OD matrix** | `od_matrix` |

Each group has four parts. **Pick your parts yourselves.** Every part can be built and tested on its own.

## 🧭 Graph group

**Hand in:** `nodes.csv`, `edges.csv`, attribute tables, and the scripts that build them.


### Nodes.csv
1. **Assemble the nodes.csv.** Join the ~100 ranked ports to the PortWatch port table, add the junctions, and add node attributes (country, tanker import and export tons, exporter or importer role).

### Edges.csv
2. **Attach ports to junctions with connector edges.** Each port gets up to 3 connectors to its nearest junctions, using these rules:
   - A connector must not cross a chokepoint (it would bypass the chokepoint edge). Ports sitting on a chokepoint, like Algeciras, are attached to one side by hand.
   - Skip a connector that isn't at least ~5% shorter than reaching that junction through one you already have.
   - Ports are start or end points only, never stop-overs. The routing step enforces this later.
3. **Fill in lengths.** Use searoute for `length_nm` on every edge, with `speed_knot` set explicitly (12–14), and add minimum transit days.
4. **Add static attributes:** edge type, chokepoint IDs, vessel-size limits and tolls (Suez, Panama), and a capacity proxy per chokepoint (a high percentile of weekly tanker counts before the Red Sea crisis).
5. **Add dynamic attributes** for each chokepoint edge: weekly tanker transits, estimated volume, utilization, and anomaly. Tag everything `observed` or `modeled`.

### Validation
7. **Check the network:** connected, alternatives exist for each chokepoint (Hormuz and Bosporus have none), distances close to direct searoute, and junctions in the sea.

### 💡 Four parts

Each part is built against sample inputs in the agreed formats. Real inputs are plugged in at the end.

| Part | Builds | Tests against | Output |
|---|---|---|---|
| **Part 1** | Step 1: the nodes table and node attributes | The ranked port list and port table | `nodes.csv` |
| **Part 2** | Steps 2–3: the connector script, `edges.csv` assembly, and lengths | A 20-port sample and the core edge file | `edges.csv` |
| **Part 3** | Steps 4–5: static attributes, capacity proxy, and dynamic chokepoint attributes | The chokepoint daily data and other sources too | fill/add in additional columns in `edges.csv` |
| **Part 4** | Step 6: the network checks | The current `junctions.csv` and `edges.csv` | A check script and a results note |

## 🛢️ OD matrix group

**Hand in:** `od_matrix.csv` and the **scripts** and **tables** that build it.

1. **Country flows.** From Comtrade, build country-to-country crude flows (HS 2709) in barrels for a baseline period before the Red Sea crisis. Use both exporter and importer reports, and note large mismatches.
2. **Sea adjustment.** Use all-modes data minus a documented list of pipeline pairs (for example Canada to the US, Russia to Europe), and sea-only data where a country reports it.
3. **Port shares.** From PortWatch, work out each port's share of its country's tanker exports and imports.
4. **Crude share.** "Tanker" isn't "crude." Calibrate a crude share against EIA's chokepoint flows, and write the assumption down.
5. **Split and fit.** Split each country flow across port pairs using the shares, then scale (iterative proportional fitting) until port totals match PortWatch.
6. **Sanity checks:** totals match, nothing negative, and the biggest port pairs look sensible.

### 💡 Four parts

| Part | Builds | Tests against | Output |
|---|---|---|---|
| **Part 1** | Steps 1–2: country flows and the sea adjustment | Comtrade data already pulled | `country_flows.csv` |
| **Part 2** | Step 3: port shares and port totals | PortWatch daily ports data | `port_shares.csv`, `port_totals.csv` |
| **Part 3** | Step 4: the crude share and the barrels conversion | EIA chokepoint flows and PortWatch chokepoint data | `crude_share` notes and a table |
| **Part 4** | Steps 5–6: the fitting script and the checks | Made-up small country flows and shares | `od_matrix.csv` and a check script |

## 🧱 Base assumptions

These are our starting assumptions. We can change them later.

- **Keys:** PortWatch `portid` for ports, `ISO3` for countries.
- **Edge types:** `lane`, `chokepoint`, `connector`.
- **Ship speed:** 12 to 14 knots (set `speed_knot` explicitly in searoute).
- **Barrels:** 1 tonne of crude is about 7.3 barrels.
- **Baseline period:** before the Red Sea crisis (up to October 2023).
- **Crude share:** tanker volume is not crude volume, so a crude share is applied and written down.
- **OD matrix columns:** `origin_portid`, `destination_portid`, `barrels_per_year`. Tables passed between parts: `country_flows` (`exporter_iso3`, `importer_iso3`, `barrels`) and `port_shares` (`iso3`, `portid`, `direction`, `share`).
- **Files:** raw in `data/raw/`, cleaned in `data/interim/`, reference lists in `config/`. Nothing under `data/` is committed.

## 🔐 Reproducible and safe

Someone with a fresh clone and their own keys should be able to rebuild your files. Keep keys in `.env`, never commit a key, list new packages in your pull request, and add a short "how to run" note to each script.

## 📬 How to submit

This deliverable uses issue 3 and the integration branch `issue-3/integration`. Each group has one branch:

| Group | Branch |
|---|---|
| 🧭 Graph | `issue-3/graph` |
| 🛢️ OD matrix | `issue-3/od-matrix` |

Everyone pushes to the group's branch (pull before you push), and **one person per group** opens a pull request into `issue-3/integration`. See [`GITHUB_GUIDE.md`](GITHUB_GUIDE.md).

## ✅ Done checklist

**🧭 Graph**
- [ ] `nodes.csv` and `edges.csv` committed, with connectors, lengths, and attributes
- [ ] Chokepoint attribute tables (capacity, weekly counts, utilization, anomaly)
- [ ] Network checks run and results written down

**🛢️ OD matrix**
- [ ] `country_flows.csv`, `port_shares.csv`, and the crude share written down
- [ ] `od_matrix.csv` whose port totals match PortWatch
- [ ] Pipeline pair list and sea-data coverage noted

**Both**
- [ ] No keys or data files committed
- [ ] Pull request open into `issue-3/integration`

## ⚠️ Known risks

- **Tanker volume isn't crude volume,** so the crude share is an assumption.
- **Comtrade sea-only data is thin,** and its license is internal use only.
- **The port split is an estimate.** Check the biggest pairs against the Global Fishing Watch sample if you have it.
- **Connector rules were tuned on a 28-port sample,** so re-check them on the final 100.
