# 🗺️ Second Deliverable: Node and Edge Selection + Initial Data Pipeline

**📅 Due: Sunday, October 25, 2026**

## 🎯 What this is

Deliverable 1 showed our data is usable. Now we use it to:

1. **Choose the map we model:** about 100 key ports, 15–20 sea junctions, and the lanes between them.
2. **Get the data flowing:** scripts that fetch each source and save clean files that anyone can rebuild.

We are **not** building the graph yet. That is the next step.

## 👥 Two groups

| Group | Members | Sources |
|---|---|---|
| 🧭 **Network & traffic** | Valerie, Thomas, Zachary, Alan | PortWatch, NGA World Port Index, searoute, Global Fishing Watch |
| 🛢️ **Trade & price** | Shreeyasha, Evan, Vyom, Elijah | Comtrade, EIA, World Bank, JODI-Oil |

Split the work inside your group however you like.

## 🧭 Network & traffic

**Hand in:** a list of nodes, a list of edges, and pipeline scripts.

1. **Choose about 100 ports.** Rank PortWatch ports by volume, using the **Port selection** formula in `DATA.md`. Report what share of global and tanker volume they cover, and check that no obvious major oil ports are missing.
2. **Match ports to the port index** (NGA World Port Index) and report the match rate.
3. **Choose 15–20 junctions:** sea crossing points such as the Red Sea, Cape of Good Hope, Hormuz, Malacca, Gibraltar, and the ends of the Panama Canal.
4. **Build the edge list.** An edge is one sea lane between two nodes. Make a table with one row per edge: where it starts, where it ends, whether it is a chokepoint edge, and its length.
   - **Port to junction:** connect each port to its nearest junction (example: Singapore to the Strait of Malacca junction).
   - **Junction to junction:** connect junctions that ships travel between directly (example: Gulf of Aden to the Red Sea).
   - **Chokepoints:** a chokepoint is itself an edge between two junctions, so mark it as one (example: the Suez Canal edge runs from the Red Sea junction to the Mediterranean junction).
   - **Length:** use searoute to get the distance in nautical miles between the two ends of each edge.
5. **Check the network.** Every port should be reachable from every other, and each key chokepoint should have an alternative route (mark it "none" if there isn't one, for example Hormuz).
6. **Write the pipelines** for PortWatch, the port index, and searoute distances. Add a Global Fishing Watch tanker sample if your token works.

Commit the node and edge lists as small CSV files in a `config/` folder. They are reference lists, not data.

### 💡 Suggested steps

These are suggestions in a sensible order. Adapt them as you see fit.

**Part 1. Choose the ports**

1. **Pull the PortWatch ports data** for 2019–2025 with a script. It returns at most 1,000 rows per page, so loop through pages. Save the raw files and a cleaned Parquet.
2. **Rank the ports** using the **Port selection** formula in `DATA.md`:
   - For each `portid`, add up import + export tons across ship types, once for all ship types and once for tankers only.
   - Take the top ~60 overall and the top ~40 by tankers, remove duplicates, and fill up to 100 with the next best.
3. **Report coverage:** what share of global tons (total and tanker) do your 100 ports cover? Which regions are included?
4. **Match to the port index.** Join PortWatch ports to the NGA World Port Index, using UN/LOCODE first, then nearest coordinates plus name. Report the match rate. Singapore appears as separate terminal records, so expect some manual review.
5. **Review the final 100 by eye.** Are any obvious oil ports missing, such as Gulf, US Gulf Coast, or major Asian import ports? Add them and say why.

**Part 2. Choose the junctions and edges**

1. **Pick 15–20 junctions:** sea crossing points such as the Red Sea, Gulf of Aden, Cape of Good Hope, Strait of Hormuz, Strait of Malacca, Gibraltar, and the two ends of the Panama Canal. Check that each key chokepoint exists in PortWatch (priority: Suez, Bab-el-Mandeb, Cape of Good Hope, Gibraltar, Hormuz, Malacca, Panama).
2. **Draft the edges in a spreadsheet:** one row per edge with `from`, `to`, and `type` (lane or chokepoint). Include port-to-junction, junction-to-junction, and chokepoint edges.
3. **Add lengths with searoute.** Write one script that reads the edge table, asks searoute for the distance between the two ends of each edge, and saves `length_nm`. Pass `speed_knot` explicitly (the default is 24, our assumption is 12–14).
4. **Check the network is connected:** every port should be able to reach every other port. A quick NetworkX check works.
5. **Check alternative routes** for each key chokepoint, so that closing it reroutes traffic. searoute can test this: closing Suez with `restrictions=['northwest','suez']` gives the Cape route.

| If this closes | Alternative to check |
|---|---|
| Suez | Around the Cape of Good Hope |
| Bab-el-Mandeb | Around the Cape of Good Hope |
| Panama | The Suez route, or around South America |
| Malacca | Other Southeast Asian straits (if included) |
| Hormuz | **Probably none by sea.** Mark it "no sea alternative" |

**Part 3. Build the pipelines**

For each source, write a script that fetches the data and saves it as Parquet. Each script should run from a fresh clone.

| Source | What the script saves |
|---|---|
| PortWatch ports and chokepoints | Daily series for the selected ports and chokepoints, 2019 to present |
| NGA World Port Index | Port reference table |
| searoute | Edge lengths for the edge list |
| Global Fishing Watch *(if your token works)* | Port visits for a tanker sample |

**Commit two small files** in a `config/` folder. They are reference lists, not data (the folder name is a suggestion):

- `config/nodes.csv`: `node_id`, `node_type` (`port` or `junction`), `name`, `country_iso3`, `lat`, `lon`, `portwatch_portid`, `wpi_index`, `unlocode`, `rank_total`, `rank_tanker`, `tons_total`, `tons_tanker`
- `config/edges.csv`: `edge_id`, `from_node`, `to_node`, `edge_type` (`lane` or `chokepoint`), `chokepoint_portid`, `length_nm`, `alternative_group`

## 🛢️ Trade & price

**Hand in:** pipeline scripts and a country code table.

1. **Comtrade crude oil (HS 2709), 2019–2025.** Pull both exporter-reported and importer-reported flows. Pull all modes of transport first, then sea-only (`motCode=2100`), and note which reporters have sea-only data. Convert weight to barrels in one documented place.
2. **Country code table.** Match Comtrade's country codes to PortWatch's countries, and list any that do not match.
3. **EIA.** Pull daily Brent (with a weekly average) and the chokepoint oil flow table.
4. **World Bank.** Pull the monthly Brent, Dubai, and WTI prices.
5. ⭐ **JODI-Oil (optional).** Treat the `0` values it returns for unreported data as missing.

### 💡 Suggested steps

These are suggestions in a sensible order. Adapt them as you see fit.

**Part 1. Comtrade crude oil**

1. **Pull crude oil (HS 2709)** for 2019–2025, all reporters, as **both exporter-reported and importer-reported** flows. Start with one year to confirm the fields, then loop over the rest. Save the raw responses and a cleaned Parquet table.
2. **Pull all modes of transport first.** Then repeat with `motCode=2100` (sea) and record which reporters have sea-only data. Many do not report it.
3. **Convert weight to barrels** in the cleaning step: barrels = kg ÷ 1,000 × 7.3. Keep the factor in one place so it can be changed.
4. **Build the country code table.** Match Comtrade's numeric country codes to PortWatch's country names and `ISO3`, and list any that do not match.
5. **Note the gaps:** missing years, missing major exporters, and large mismatches between the two reports.

**Part 2. EIA, World Bank, and JODI-Oil**

1. **EIA Brent:** pull daily Brent from 2019 and add a weekly average (the average of the daily prices in each week). Use the free API key from your `.env` file.
2. **EIA chokepoint flows:** save the chokepoint oil flow table with its units. It is annual, so keep the year columns clear.
3. **World Bank:** save the monthly Brent, Dubai, and WTI prices.
4. ⭐ **JODI-Oil (optional):** the API needs an account and handles about one month per request. It returns `0` for unreported data, so treat zeros as missing, not as real zeros.

**Part 3. Make it reproducible**

Someone with a fresh clone and their own API keys should be able to rebuild every file by running your scripts. Cache API responses in `data/raw/` so reruns don't use your quota, list any new Python packages in your pull request, and add a short "how to run" note to each script.

## 🧱 Baseline conventions

Use these for now. We can change them later.

- **Port key:** PortWatch `portid`.
- **Dates:** `YYYY-MM-DD`.
- **Where files go:** raw downloads in `data/raw/`, cleaned Parquet in `data/interim/`. Nothing under `data/` is committed.

## 🔐 Reproducible and safe

Someone with a fresh clone and their own API keys should be able to rebuild your files by running your scripts.

- Keep keys in environment variables or a `.env` file. **Never commit a key.**
- List any new Python packages in your pull request.
- Add a short "how to run" note to each script.

## 📬 How to submit

This deliverable uses issue 2 and the integration branch `issue-2/integration`. Each group has one branch off it:

| Group | Branch |
|---|---|
| 🧭 Network & traffic | `issue-2/network-traffic` |
| 🛢️ Trade & price | `issue-2/trade-price` |

1. **Everyone in the group pushes their work to the group's branch.** Pull before you push so you don't overwrite each other. If the branch doesn't exist yet, one person creates it from `issue-2/integration`.
2. **One person from each group opens a pull request** from the group's branch into `issue-2/integration` (not `main`).

Follow [`GITHUB_GUIDE.md`](GITHUB_GUIDE.md). A good midpoint goal: draft port list and first pipelines running by **Friday, October 23**.

## ✅ Done checklist

**🧭 Network & traffic**
- [ ] About 100 ports chosen, with a coverage report
- [ ] 15–20 junctions and an edge list, saved as CSV files in `config/`
- [ ] Network connected, and alternative routes checked for key chokepoints
- [ ] PortWatch, port index, and searoute pipelines run from a fresh clone

**🛢️ Trade & price**
- [ ] Comtrade, EIA, and World Bank pipelines run from a fresh clone
- [ ] Country code table, with unmatched countries listed

**Both**
- [ ] No API keys or data files committed
- [ ] Your group's work is pushed to its branch, and one pull request is open into `issue-2/integration`
