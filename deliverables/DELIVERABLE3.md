# 🕸️ Third Deliverable: Graph Build + OD Matrix

**📅 Due: Tuesday, November 3, 2026**

## 🎯 What this is

Deliverable 2 gave us the map (38 junctions, 54 edges), the ranked ports, and the data pipelines. Now we build the two things the flow model needs:

1. **The graph:** the full network of ports, junctions, and lanes, with lengths and attributes.
2. **The OD matrix:** an estimate of how much crude oil moves from each port to each other port.

Next deliverable, the **flow model**, routes the OD matrix over the graph. We are **not** building the flow model yet.

## 👥 Two groups

| Group | Builds | 
| ----- | ----- | 
| 🧭 **Graph** | `nodes.csv`, `edges.csv` | 
| 🛢️ **OD matrix** | `od_matrix.csv` + intermediate tables (`country_flows.csv`, `port_totals.csv`, `port_shares.csv`, `crude_share.csv`) | 

Each group has four parts. **Pick your parts yourselves.** Every part has an explicit input/output schema so it can be built and tested on its own.

---

## 🧭 Graph group

**Hand in:** `nodes.csv`, `edges.csv`, attribute tables, and the scripts that build them.

### Nodes.csv

1. **Assemble `nodes.csv`.** Join the ~100 ranked ports to the PortWatch port table, add the junctions, and add node attributes (country, tanker import and export tons, exporter or importer role).

### Edges.csv

2. **Attach ports to junctions with connector edges.** Each port gets up to 3 connectors to its nearest junctions, using these rules:
   * A connector must not cross a chokepoint (it would bypass the chokepoint edge). Ports sitting on a chokepoint, like Algeciras, are attached to one side by hand.
   * Skip a connector that isn't at least ~5% shorter than reaching that junction through one you already have.
   * Ports are start or end points only, never stop-overs. The routing step enforces this later.
3. **Fill in lengths.** Use searoute for `length_nm` on every edge, with `speed_knot` set explicitly (12–14), and add minimum transit days.
4. **Add static attributes:** edge type, chokepoint IDs, vessel-size limits and tolls (Suez, Panama), and a capacity proxy per chokepoint (a high percentile of weekly tanker counts before the Red Sea crisis).
5. **Add dynamic attributes** for each chokepoint edge: weekly tanker transits, estimated volume, utilization, and anomaly. Tag everything `observed` or `modeled`.

### Validation

6. **Check the network:** connected, alternatives exist for each chokepoint (Hormuz and Bosporus have none), distances close to direct searoute, and junctions in the sea.

### 💡 Four parts

Each part is built against sample inputs in the agreed formats. Real inputs are plugged in at the end.

| Part | Builds | Tests against | Output | 
| ----- | ----- | ----- | ----- | 
| **Part 1** | Step 1: the nodes table and node attributes | The ranked port list and port table | `nodes.csv` | 
| **Part 2** | Steps 2–3: the connector script, `edges.csv` assembly, and lengths | A 20-port sample and the core edge file | `edges.csv` | 
| **Part 3** | Steps 4–5: static attributes, capacity proxy, and dynamic chokepoint attributes | The chokepoint daily data and other sources too | Fill/add additional columns in `edges.csv` | 
| **Part 4** | Step 6: the network checks | The current `junctions.csv` and `edges.csv` | A check script and a results note | 

---

## 🛢️ OD matrix group

**Hand in:** pipeline scripts that build `country_flows.csv`, `port_totals.csv`, `port_shares.csv`, `crude_share.csv`, and the final `od_matrix.csv` (saved under `data/interim/` and `data/processed/`), plus a summary note/check script.

### 💡 Four parts (Who builds what & how to test independently)

Every member can build and test their script immediately using the agreed schemas below.

| Part | Owner | What to Build | Inputs | Outputs (`data/interim/`) | Independent Test / Acceptance Check | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **Part 1: Country Flows & Sea Adjustment** | Member 1 | Script `scripts/od_country_flows.py`: reconcile Comtrade `M` vs `X` (2019–2022 baseline), map to `ISO3`, filter to countries in `ports_selected.csv`, apply pipeline deductions & `motCode=2100` where valid. | `data/interim/comtrade_all_annual.parquet`, `comtrade_sea_annual.parquet`, `config/country_codes.csv`, `config/ports_selected.csv` | `country_flows.csv` (`exporter_iso3`, `importer_iso3`, `barrels_per_year`, `source_method`) + `pipeline_adjustments.csv` | Top bilateral pairs include `SAU -> CHN`, `SAU -> JPN`, `RUS -> CHN/IND`, `IRQ -> IND/CHN`; no `Areas, nes (899)` rows remain; overland `CAN -> USA` pipeline volume is reduced. | 
| **Part 2: Port Totals & Port Shares** | Member 2 | Script `scripts/od_port_shares.py`: aggregate baseline PortWatch daily data for the 100 selected ports (summing merged `member_portids`), output annual port totals and within-country export/import shares. | `data/interim/portwatch_ports_daily_selected.parquet` (or `portwatch_ports_totals.parquet`), `config/ports_selected.csv` | `port_totals.csv` & `port_shares.csv` (`iso3`, `portid`, `direction`, `tons_per_year`, `share`) | For every `iso3` and `direction` (`export`, `import`), `share` values are in `[0, 1]` and sum to `1.000` (when the country has `> 0` flow). All 100 selected ports appear. | 
| **Part 3: Crude Share Calibration** | Member 3 | Script `scripts/od_crude_share.py`: compare EIA annual chokepoint flows (`mb/d`) against PortWatch chokepoint tanker volumes (`mb/d`) and global Comtrade crude vs PortWatch tanker totals. | `data/interim/eia_chokepoint_flows.parquet`, `data/interim/portwatch_chokepoints_daily.parquet`, `country_flows.csv`, `port_totals.csv` | `crude_share.csv` (`scope`, `eia_mbd`, `portwatch_tanker_mbd`, `crude_share`, `notes`) | Produces a clear table of chokepoint ratios (Hormuz, Malacca, Suez, Bab-el-Mandeb, Cape) and defines a global crude share. | 
| **Part 4: IPF Fitting & OD Sanity Checks** | Member 4 | Script `scripts/build_od_matrix.py`: build seed matrix, scale port totals by `crude_share`, run IPF (RAS) until convergence, and run sanity checks. | `country_flows.csv`, `port_shares.csv`, `port_totals.csv`, `crude_share.csv` *(can test early on synthetic 5-port toy CSVs)* | `data/processed/od_matrix.csv` & `data/interim/od_matrix.parquet` (`origin_portid`, `destination_portid`, `barrels_per_year`, `barrels_per_day`) | IPF converges in `< 50` iterations; max marginal error `< 1%`; no `NaN` or negative values; prints top 20 port-to-port corridors. | 

### How the OD Matrix Pipeline Works (Step-by-Step)

#### 1. Country-to-Country Maritime Crude Flows (`country_flows.csv`)
* **Baseline window:** Average annual crude oil trade (HS 2709) over the pre-Red Sea crisis baseline (**2019–2022**, or Jan 2019 – Oct 2023 annualized).
* **Reconciling Exporter (`X`) vs. Importer (`M`) reports:** Because major Gulf exporters (e.g., Saudi Arabia, UAE) mask destinations under `Areas, nes (899)` and some exporters (e.g., Russia, Venezuela, Iran) have missing reporting years, use **Importer-reported (`M`)** flows as the primary bilateral truth, falling back to **Exporter-reported (`X`)** only when the importer does not report. Join Comtrade numeric codes to `ISO3` via `config/country_codes.csv`.
* **Sea-only adjustment:** Filter to countries that have at least one port in `config/ports_selected.csv`. Where reliable `motCode=2100` (sea) data exists, use it; otherwise start from `all-modes` (`motCode=0`) and subtract/down-weight documented overland pipeline flows (e.g., `CAN -> USA`, `MEX -> USA`, `RUS -> CHN`, `RUS -> DEU/POL`, `KAZ -> RUS/CHN`).
* **Output schema (`data/interim/country_flows.csv`):**
  * `exporter_iso3`, `importer_iso3`, `barrels_per_year`, `source_method` (`importer_M`, `exporter_X`, `sea_adjusted`)

#### 2. Port Totals & Within-Country Port Shares (`port_totals.csv` & `port_shares.csv`)
From PortWatch daily port data (`data/interim/portwatch_ports_daily_selected.parquet`) and `config/ports_selected.csv`, we build two tables restricted to our **~100 selected model ports** over the baseline window:
* **Handling merged complexes:** Use `member_portids` from `config/ports_selected.csv` (e.g., `port1091;port526` for Ras Tanura + Juaymah) so daily tonnes from merged sub-terminals are summed into their parent `portid`.
* **Table A — `port_totals.csv` (Physical Marginals per Port):**
  * For each selected port, compute the mean annual `export_tanker` tonnes and mean annual `import_tanker` tonnes across the baseline years.
  * Convert to all-tanker barrels using `barrels = tonnes * 7.3`. (Part 4 multiplies these by `crude_share` to get the target crude oil marginals for IPF.)
  * **Columns (`data/interim/port_totals.csv`):** `portid`, `node_id`, `portname`, `iso3`, `export_tanker_tons_yr`, `import_tanker_tons_yr`, `export_tanker_bbl_yr`, `import_tanker_bbl_yr`
* **Table B — `port_shares.csv` (Within-Country Allocation Weights):**
  * For each country, sum the export and import tanker tonnes **across the selected ports in that country**:
    `country_export_tons = SUM(port_export_tons)`
    `country_import_tons = SUM(port_import_tons)`
  * Compute each port's share of its country's selected-port exports and imports:
    `port_export_share = port_export_tons / country_export_tons`
    `port_import_share = port_import_tons / country_import_tons`
  * By construction, for every country, the export shares sum to `1.0` and the import shares sum to `1.0`. (If a country has no exports, set its export shares to `0`.)
  * **Columns (`data/interim/port_shares.csv`):** `iso3`, `portid`, `direction` (`export` or `import`), `tons_per_year`, `share`

#### 3. Crude Share Calibration (`crude_share.csv`)
* **Why we need it:** PortWatch's `export_tanker` and `import_tanker` fields include all liquid bulk tankers (crude oil, refined products, chemicals, LNG/LPG). EIA's chokepoint table (`data/interim/eia_chokepoint_flows.parquet`) and Comtrade (`HS 2709`) measure **crude/petroleum**.
* **How to compute it:**
  1. From `portwatch_chokepoints_daily.parquet`, compute average daily tanker cargo tonnes at each major chokepoint (`capacity_tanker` or `import_tanker + export_tanker` proxy) and convert to million barrels per day (`mbd = tonnes_per_day * 7.3 / 1,000,000`).
  2. Compare against EIA's published annual chokepoint oil flows (`mbd`) for 2019–2023:
     `crude_share = EIA_chokepoint_flow_mbd / PortWatch_tanker_flow_mbd`
  3. Also compute a **global macro crude share** by dividing total global maritime Comtrade crude barrels (`country_flows.csv`) by total selected-port PortWatch tanker barrels (`port_totals.csv`).
  4. Document a single default `CRUDE_SHARE` (e.g., `0.60`–`0.70`) and optional regional/chokepoint factors.
* **Columns (`data/interim/crude_share.csv`):** `scope` (`global_default` or chokepoint ID), `eia_mbd`, `portwatch_tanker_mbd`, `crude_share`, `notes`

#### 4. Building & Fitting the OD Matrix (`od_matrix.csv`)
This step combines outputs from Parts 1, 2, and 3 into a port-to-port matrix:
1. **Seed Matrix (Naive Port Split):** For every origin port and destination port (where countries don't match), split the bilateral country flow (`barrels_per_year` from `country_flows.csv`) using the export and import port shares (`port_shares.csv`):
   `seed_flow = country_to_country_flow * origin_port_export_share * destination_port_import_share`

2. **Target Port Marginals:** Using `port_totals.csv` and `crude_share.csv`, compute each port's target annual crude oil exports and imports:
   `target_port_export = export_tanker_bbl_yr * crude_share`
   `target_port_import = import_tanker_bbl_yr * crude_share`
   *(Note: Scale target imports globally so the sum of all network imports exactly matches the sum of all network exports.)*

3. **Iterative Proportional Fitting (IPF / RAS Algorithm):** Alternate row and column scaling until the matrix row sums match `target_port_export` and column sums match `target_port_import` (within a `0.0001` tolerance or max 100 iterations):
   * **Row step (match origin port exports):**
     `flow = flow * (target_port_export / current_sum_of_port_exports)`
   * **Column step (match destination port imports):**
     `flow = flow * (target_port_import / current_sum_of_port_imports)`
   *(Add a tiny `1e-9` floor on active country pairs or drop ports with zero target flow to avoid division by zero.)*

4. **Sanity Checks:** Verify no negative or `NaN` flows, row/column sums match target port totals within `< 1%`, and print the top 20 port-to-port crude corridors (e.g., `Ras Tanura -> Ningbo`, `Ras Tanura -> Ulsan`, `Houston -> Rotterdam`).
* **Columns (`data/processed/od_matrix.csv` & `.parquet`):** `origin_portid`, `destination_portid`, `origin_node_id`, `destination_node_id`, `origin_iso3`, `destination_iso3`, `barrels_per_year`, `barrels_per_day`

---

## 🧱 Base assumptions

These are our starting assumptions. We can change them later.

* **Keys:** PortWatch `portid` for ports (`node_id = port_<portid>`), `ISO3` for countries.
* **Edge types:** `lane`, `chokepoint`, `connector`.
* **Ship speed:** 12 to 14 knots (set `speed_knot` explicitly in searoute).
* **Barrels:** 1 tonne of crude is about 7.3 barrels.
* **Baseline period:** Pre-Red Sea crisis (**2019–2022** for annual tables, or Jan 2019 – Oct 2023 annualized).
* **Crude share:** Tanker volume is not crude volume, so an explicit `crude_share` is calibrated in `crude_share.csv` and applied to PortWatch tanker totals.
* **Schemas passed between OD matrix parts:**
  * `country_flows.csv`: `exporter_iso3`, `importer_iso3`, `barrels_per_year`, `source_method`
  * `port_totals.csv`: `portid`, `node_id`, `portname`, `iso3`, `export_tanker_tons_yr`, `import_tanker_tons_yr`, `export_tanker_bbl_yr`, `import_tanker_bbl_yr`
  * `port_shares.csv`: `iso3`, `portid`, `direction` (`export` or `import`), `tons_per_year`, `share`
  * `crude_share.csv`: `scope`, `eia_mbd`, `portwatch_tanker_mbd`, `crude_share`, `notes`
  * `od_matrix.csv`: `origin_portid`, `destination_portid`, `origin_node_id`, `destination_node_id`, `origin_iso3`, `destination_iso3`, `barrels_per_year`, `barrels_per_day`
* **Files:** raw in `data/raw/`, intermediate tables in `data/interim/`, final graph/OD outputs in `data/processed/`, small reference lists in `config/`. Nothing under `data/` is committed.

## 🔐 Reproducible and safe

Someone with a fresh clone and their own keys should be able to rebuild your files. Keep keys in `.env`, never commit a key, list new packages in your pull request, and add a short "how to run" note to each script.

## 📬 How to submit

This deliverable uses issue 3 and the integration branch `issue-3/integration`. Each group has one branch:

| Group | Branch | 
| ----- | ----- | 
| 🧭 Graph | `issue-3/graph` | 
| 🛢️ OD matrix | `issue-3/od-matrix` | 

Everyone pushes to the group's branch (pull before you push), and **one person per group** opens a pull request into `issue-3/integration`. See [`GITHUB_GUIDE.md`](GITHUB_GUIDE.md).

## ✅ Done checklist

**🧭 Graph**

* [ ] `nodes.csv` and `edges.csv` committed, with connectors, lengths, and attributes
* [ ] Chokepoint attribute tables (capacity, weekly counts, utilization, anomaly)
* [ ] Network checks run and results written down

**🛢️ OD matrix**

* [ ] `scripts/od_country_flows.py` builds `country_flows.csv` (reconciling `M` vs `X`, removing `Areas, nes`, and adjusting overland pipeline pairs)
* [ ] `scripts/od_port_shares.py` builds `port_totals.csv` and `port_shares.csv` (summing merged `member_portids`, with shares summing to 1 per country and direction)
* [ ] `scripts/od_crude_share.py` builds `crude_share.csv` calibrating PortWatch tanker volume against EIA chokepoint oil flows
* [ ] `scripts/build_od_matrix.py` builds `od_matrix.csv` via IPF so row/column sums match scaled PortWatch port totals, with top corridors verified

**Both**

* [ ] No keys or data files committed
* [ ] Pull request open into `issue-3/integration`

## ⚠️ Known risks

* **Tanker volume isn't crude volume,** so the crude share is an assumption.
* **Comtrade sea-only data is thin,** and its license is internal use only.
* **The port split is an estimate.** Check the biggest pairs against the Global Fishing Watch sample if you have it.
* **Connector rules were tuned on a 28-port sample,** so re-check them on the final 100.