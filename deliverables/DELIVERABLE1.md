# 🚢 First Deliverable: Source Documentation and Validation

**📅 Due: Sunday, October 11, 2026**

## 🎯 What this is

Before we build anything, we make sure our data is real, free to use, and says what we think it says. Each subteam looks at its own two sources and records what it finds in the table in [`DATA.md`](DATA.md).

You do **not** need to know these data sources already. The steps below tell you where to look and what to write down. ⭐ Deeper checks are marked **optional**.

## 📦 What your subteam hands in

1. **The blank cells in your rows of the `DATA.md` table**, filled in.
2. **The exact field names** for the data we need from each of your sources (listed below).
3. **One quick check per source**, with pass or fail and one line of why.
4. **A formula check:** for each formula listed under your subteam, `ok` or `needs change: <what and why>`.
5. **One pull request per subteam**, into the shared integration branch. See [`GITHUB_GUIDE.md`](GITHUB_GUIDE.md) and [Where your pull request goes](#-where-your-pull-request-goes).

⭐ *Optional extras:* the deeper checks listed under each subteam.

## 👥 Subteams

| Subteam | Members | Core source | Second source |
|---|---|---|---|
| ⚓ **A · Ports & traffic** | Valerie and Thomas | IMF PortWatch | NGA World Port Index |
| 🌍 **B · Trade flows** | Shreeyasha and Evan | UN Comtrade | JODI-Oil |
| 🛢️ **C · Prices & oil flows** | Vyom and Elijah | EIA | World Bank |
| 🧭 **D · Routes & ships** | Zachary and Alan | searoute | Global Fishing Watch |

These are initial subteams for the starting deliverables (Weeks 1–2).

## 📊 What data do we need from each source?

### ⚓ A · Ports & traffic (Valerie and Thomas)

| Source | What we need | What we use it for |
|---|---|---|
| **PortWatch: ports** | Date; port ID, name, country; port calls by ship type (tanker, container, dry bulk, general cargo, Ro-Ro); estimated import tons and export tons by ship type | Choosing the ~100 ports; port attributes; oil demand totals |
| **PortWatch: chokepoints** | Date; chokepoint ID, name, location; transit calls by ship type; estimated trade volume by ship type (if provided). Priority: Suez, Bab-el-Mandeb, Cape of Good Hope, Gibraltar, Hormuz, Malacca, Panama | Traffic on key edges; the real data we validate against |
| **NGA World Port Index** | Port name; UN/LOCODE (if present); latitude and longitude; country; channel depth and maximum vessel size (if available) | Matching the same port across sources; vessel-size limits |

### 🌍 B · Trade flows (Shreeyasha and Evan)

| Source | What we need | What we use it for |
|---|---|---|
| **UN Comtrade** | Reporter country, partner country, flow direction (export or import), period (year or month), commodity HS 2709 (crude oil), net weight (kg), trade value (USD). Pull both exporter-reported and importer-reported figures | Building the oil demand matrix (how much oil goes from where to where) |
| **JODI-Oil** | Monthly crude exports and imports by country | Cross-checking Comtrade |

### 🛢️ C · Prices & oil flows (Vyom and Elijah)

| Source | What we need | What we use it for |
|---|---|---|
| **EIA: prices** | Daily Brent spot price (USD per barrel) | The price the model predicts |
| **EIA: chokepoint flows** | Oil flowing through each chokepoint (million barrels per day) | Working out how much of tanker traffic is oil; checking the model |
| **World Bank** | Monthly Brent, Dubai, and WTI prices | Cross-checking the Brent series |

### 🧭 D · Routes & ships (Zachary and Alan)

| Source | What we need | What we use it for |
|---|---|---|
| **searoute** | Sea distance (nautical miles) and route shape between each pair of nodes | Edge length and map lines; minimum transit time |
| **Global Fishing Watch** | Vessel ID (MMSI or IMO) and ship class; port-visit start and end times; port visited (name, country, coordinates); visit confidence; optional AIS-gap events | How long real tankers take between ports; delay |

> 💡 The field names above are descriptive. Part of your job is to find the **exact** names, written the way the source writes them.

## 📬 Where your pull request goes

The whole first deliverable shares **one issue** and **one integration branch**, `issue-N/integration` (`N` is the issue number). Each subteam has its own branch off it and opens its own pull request **into the integration branch, not `main`**.

| Subteam | Branch name |
|---|---|
| ⚓ A · Ports & traffic | `issue-N/ports-traffic` |
| 🌍 B · Trade flows | `issue-N/trade-flows` |
| 🛢️ C · Prices & oil flows | `issue-N/prices-oil-flows` |
| 🧭 D · Routes & ships | `issue-N/routes-ships` |

## 🔐 Keep API keys private

Comtrade, EIA, and Global Fishing Watch need keys or tokens. **Never paste a key into any file in the repo**, including `DATA.md`. In the table, write `free key required`, not the key itself.

- Keep keys in a `.env` file (it is git-ignored) or in an environment variable.
- The repo scans every push for secrets (Gitleaks). If a key is pushed, tell the team right away, then revoke it and create a new one. Deleting the file is not enough, because git remembers it.

## 🛠️ How to do your part

All four subteams follow the same six steps. Subteam-specific details come after.

1. 🔎 **Open your source's website.** The links are in the **Origin / URL** column of the `DATA.md` table.
2. 📝 **Fill in your blank cells in `DATA.md`.** The column guide in `DATA.md` explains each one. In plain words:
   - **Coverage:** which regions, years, and ship types does the source include?
   - **Update frequency and lag:** how often does it refresh, and how old is the newest data?
   - **Quota / API key:** do you need to sign up or get a key, and are there limits?
3. 🏷️ **Write the exact field names** for the data in the tables above.
   > 💡 Look for a "data dictionary" or "documentation" page. If there isn't one, download a small sample and copy the column headers.
4. 🧪 **Do one quick check:** download or view a small sample and see whether the data we need is really there. Write **pass** or **fail** plus one line of why. For example: `pass: tanker transit counts by date are available for Suez`.
5. 📐 **Verify your formulas.** Your subteam's section lists each formula in plain words, with the columns it needs. For each one:
   - Check that the columns exist in your data, using the exact field names from step 3.
   - Try the formula on a few rows. A spreadsheet is fine.
   - Write `ok`, or `needs change: <what and why>`. If it needs a change, also fix it in the **Derived metrics** table in `DATA.md`.
6. 📬 **Open one pull request** for your subteam. Teammates share one branch, so decide who opens it.

If you get stuck, write `TBD: <reason>` and move on. A clear "I couldn't find this" is useful.

## 🗂️ What your subteam does

Each subteam has **required** steps, a **formulas to verify** table, and ⭐ **optional** extras.

### ⚓ A · Ports & traffic

**Required**
1. **PortWatch:** find the ports data and the chokepoints data. Write down the exact field names for port calls, import and export tons, and chokepoint transit counts, by ship type.
2. **Quick check:** open the Suez and Cape of Good Hope data. Do you see tanker transit counts by date? Write pass or fail.
3. **NGA World Port Index:** find the download and its field list. Write down the fields for port name, UN/LOCODE, latitude, longitude, and country, and whether channel depth and maximum vessel size are included.
4. **Quick check:** does the port index list major ports you know, such as Rotterdam, Singapore, and Houston? Write pass or fail.
5. **Verify these formulas:**

| Formula | In plain words | Columns you need |
|---|---|---|
| Port ranking | Add up import tons + export tons for each port over 2019–2025, once for all ship types and once for tankers only. Take the top ~60 overall and the top ~40 by tankers | Port ID; import tons and export tons by ship type; date |
| Capacity | The "full" level: a high percentile (95th–99th) of weekly transit counts before the Red Sea crisis (for example, 2019 to October 2023) | Chokepoint or port ID; transit or port calls by ship type; date |
| Utilization | This week's count ÷ capacity | Same as capacity |
| Anomaly | How unusual a week is: (this week's value − the average for the same week of the year) ÷ the usual spread (standard deviation) | Weekly value; date |
| Average vessel size | Estimated trade volume (tons) ÷ number of transits, by ship type | Estimated trade volume; transit calls |

⭐ **Optional**
- Plot Suez vs. Cape of Good Hope tanker transits around December 2023. Does the Cape rise while Suez falls?
- Match PortWatch ports to the port index and report how many match.

### 🌍 B · Trade flows

**Required**
1. **Comtrade:** sign up for the free API key. Find the fields for reporter, partner, flow direction, period, commodity code, weight, and value.
2. **Pull crude oil (HS 2709) for 2019–2025.** Sort by weight and list the 20 biggest exporters and the 20 biggest importers.
3. Note which years are missing, if any.
4. **Quick check:** can you get crude oil data with all the fields above? Write pass or fail.
5. **JODI-Oil:** find the data page. Write down the fields for crude exports and imports by country, and how often it updates.
6. **Verify these formulas:**

| Formula | In plain words | Columns you need |
|---|---|---|
| Weight to barrels | Barrels = weight in kg ÷ 1,000 × about 7.3 | Net weight (kg) |
| Oil demand between two ports | Country-to-country flow × the share of the exporting country's tanker exports that leave from this port × the share of the importing country's tanker imports that arrive at that port. Then scale so each port's total matches PortWatch | Reporter, partner, net weight (Comtrade); tanker export and import tons by port (PortWatch, from subteam A) |

   Also check that Comtrade's country names or codes can be matched to the countries in PortWatch.

⭐ **Optional**
- Compare exporter-reported and importer-reported figures for the same trade and flag mismatches over 20%.
- Compare Comtrade totals with PortWatch tanker volumes by country (with subteam A).

### 🛢️ C · Prices & oil flows

**Required**
1. **EIA:** sign up for the free API key. Find the daily Brent price series and write down its exact field names and units.
2. Pull daily Brent from 2019 to now. **Quick check:** is the latest date recent, and are there any long gaps? Write pass or fail.
3. Find the EIA chokepoint oil flow data. Write down the units and which years it covers.
4. **World Bank:** download the monthly commodity prices file. Find the Brent column and write down its name and units.
5. **Verify these formulas:**

| Formula | In plain words | Columns you need |
|---|---|---|
| Barrels from tons | PortWatch tons × about 7.3 barrels per tonne (check this number against a public source) | PortWatch trade volume (tons) |
| Crude share | How much of the tanker volume at a chokepoint is oil: EIA oil flow ÷ PortWatch tanker volume for the same chokepoint, converted to the same units | EIA chokepoint flow (million barrels per day); PortWatch tanker volume at that chokepoint (from subteam A) |
| Weekly Brent | The average of the daily Brent prices in each week | Date; Brent price (USD per barrel) |

⭐ **Optional**
- Compare World Bank monthly Brent with the average of EIA's daily Brent for the same months.
- Find public sources for the cost-per-barrel inputs in `DATA.md`: tanker daily cost, barrels per cargo, and canal toll (low, base, high).

### 🧭 D · Routes & ships

**Required**
1. **searoute:** install it (`pip install searoute`) and follow its README to get the distance between a pair of ports.
2. **Quick check:** try 3 port pairs you know. Do the distances look reasonable? Search for a published distance to compare. Write pass or fail.
3. **Global Fishing Watch:** register and get an API token. Write down any quotas, and note that the license is non-commercial.
4. Find the port-visit data. Write down the exact fields for vessel ID, ship class, visit start and end times, port, and confidence.
5. **Quick check:** look up port visits for 3–5 tankers you can name. Do the visits make sense? Write pass or fail.
6. **Verify these formulas:**

| Formula | In plain words | Columns you need |
|---|---|---|
| Minimum transit days | Distance in nautical miles ÷ (speed in knots × 24). Start with 12–14 knots | searoute distance (nm) |
| Observed transit days | For one tanker: the start time of its visit to port B − the end time of its visit to port A, for consecutive visits | Vessel ID; visit start; visit end; port |
| Delay | Typical transit days this week − typical transit days for the same port pair before the Red Sea crisis | Observed transit days by week and port pair |

   Try the first two on one of your port pairs and one of your tankers. Do the answers look reasonable?

⭐ **Optional**
- Test about 20–30 tankers instead of 3–5.
- Compare searoute distances with published distances for about 10 port pairs.

## ❓ Frequently asked questions

### What is the difference between "prices" and "trade flows"?

They are different questions about the same oil.

| | 🌍 **Trade flows** (subteam B) | 🛢️ **Prices & oil flows** (subteam C) |
|---|---|---|
| Question | *How much* oil moves between which countries? | *What does* a barrel cost? And how much passes each chokepoint? |
| Looks like | "Country X sent about N million tonnes to Country Y" | "Brent was $P per barrel on this day" |
| Source | Comtrade | EIA and World Bank |
| Used for | Building the demand matrix: how much oil must travel, and where | The price we predict at the end, and checking the model |

Trade flows tell the model **what has to move**. Prices tell us **what we are predicting**.

The word "flow" shows up in three places, so here is the difference:

| Name | Source | What it means |
|---|---|---|
| Trade flows | Comtrade | Oil traded from one country to another |
| Chokepoint oil flows | EIA | Oil passing through a chokepoint such as Suez |
| Ship traffic | PortWatch | Counts and estimated tons of ships at ports and chokepoints |

### What is the difference between "ports & traffic" and "routes & ships"?

- ⚓ **Ports & traffic (subteam A)** is about **how busy each place is.** It counts ships and tons at ports and chokepoints, by ship type, over time. It is aggregate data and says nothing about individual ships.
- 🧭 **Routes & ships (subteam D)** is about **how ships get from A to B.** It covers the distance and shape of the sea path (searoute), and how long real tankers actually take between ports (Global Fishing Watch).

An analogy: ports & traffic is counting the cars that pass through each toll booth. Routes & ships is the road map, plus tracking individual cars to see how long their trips take.

| | ⚓ **Ports & traffic** | 🧭 **Routes & ships** |
|---|---|---|
| Question | How many ships and tons pass through each place? | How far apart are places, and how long do ships take? |
| Example | "N tanker calls at a port this week" | "The sea path is D nautical miles; this tanker took T days" |
| Becomes | Port and edge **traffic** attributes; the data we validate against | Edge **length** and **transit time and delay** attributes |
| Level of detail | Aggregate counts | Individual ships (a sample) |

## ✅ Done checklist

- [ ] Every blank cell in your rows is filled in (or says `TBD: reason`)
- [ ] Exact field names are written down
- [ ] Each source has one quick check with pass or fail and one line of why
- [ ] Each formula for your subteam is marked `ok` or `needs change: <why>`
- [ ] No API keys or tokens are in any file
- [ ] Your subteam's pull request is open, and its number is in the table
- [ ] ⭐ *Optional:* the deeper checks

🚨 If **PortWatch** or **Comtrade** fails a check, say so right away. The project depends on both.