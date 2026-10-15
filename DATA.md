#!/usr/bin/env python3
"""Run in a checkout of TAMU-Aggie-Data-Science-Club/fall2026-bluevector on issue-1/ports-traffic.
Updates DATA.md in place; does not commit or push."""
from pathlib import Path
p=Path('DATA.md')
if not p.exists(): raise SystemExit('Run in the repository root (DATA.md not found).')
s=p.read_text(encoding='utf8')
if '## Ports & traffic (Subteam A) — Deliverable 1 validation' in s: raise SystemExit('Already applied.')
lines=s.splitlines()
updates={
'| 1 · Essential | IMF PortWatch |':{
7:'Ports: `date`, `portid`, `portname`, `country`, `portcalls_container`, `portcalls_dry_bulk`, `portcalls_general_cargo`, `portcalls_roro`, `portcalls_tanker`, `import_container`, `import_dry_bulk`, `import_general_cargo`, `import_roro`, `import_tanker`, `export_container`, `export_dry_bulk`, `export_general_cargo`, `export_roro`, `export_tanker`. Chokepoints: `date`, `portid`, `portname`, `n_container`, `n_dry_bulk`, `n_general_cargo`, `n_roro`, `n_tanker`, `n_total`, `capacity_tanker`, `capacity`',
8:'Global; daily series from 2019 onward; container, dry bulk, general cargo, Ro-Ro, tanker; Oct 2026 sample (not continuously verified)',
9:'Daily observations; updated generally weekly; lag variable (previous sample ~5–8 days)',
10:'Public ArcGIS REST/download; no key needed for checked public endpoint; limits TBD: verify for production',
11:'pass: Suez and Cape of Good Hope date-indexed `n_tanker` fields identified; port samples include Aqaba, Rotterdam and Singapore',
13:'Retains Valerie’s confirmed fields; expands exports and chokepoints. `capacity_tanker` is estimated aggregate carrying capacity (tonnes), NOT a historical transit-count capacity. Tanker volumes include non-crude.',
},
'| 6 · Recommended | NGA World Port Index |':{
7:'Newer CSV: `World Port Index Number`; `Main Port Name`; `Alternate Port Name`; `UN/LOCODE`; `Country Code`; `Latitude`; `Longitude`; `Channel Depth (m)`; `Maximum Vessel Length (m)`; `Maximum Vessel Beam (m)`; `Maximum Vessel Draft (m)`; headers may vary by edition',
8:'Worldwide port reference, not vessel traffic time series; port/terminal granularity and missing dimensions may vary; edition/year TBD',
9:'Periodically revised reference dataset; current published edition date/lag TBD',
10:'Public NGA download; no API key for downloads; download quota TBD',
11:'pass with caveat: Rotterdam and Houston identified; Singapore represented by component port/terminal records requiring name matching',
13:'Use coordinates, names and UN/LOCODE where available to match to PortWatch. Verify exact CSV headings, missing identifiers and depth/vessel-limit units against the downloaded edition.',
}}
for needle,values in updates.items():
 idx=next((i for i,line in enumerate(lines) if line.startswith(needle)),None)
 if idx is None: raise SystemExit(f'Missing register row: {needle}')
 chunks=[v.strip() for v in lines[idx].split('|')]
 if len(chunks)!=16: raise SystemExit(f'Unexpected table format ({len(chunks)} segments): {needle}')
 for col,value in values.items(): chunks[col+1]=value
 lines[idx]='| '+' | '.join(chunks[1:-1])+' |'
s='\n'.join(lines)
addition='''## Ports & traffic (Subteam A) — Deliverable 1 validation (October 2026)

This records the two required source checks and five formula reviews without replacing the rest of the shared source register. No raw data is committed. Spot checks reflect the October 2026 work, not a live-data guarantee.

### Source checks

- **PortWatch — pass:** Suez and Cape of Good Hope daily chokepoint data includes date-indexed tanker transit counts (`n_tanker`); port data includes ship-class port calls and estimated imports/exports. Earlier samples included Aqaba, Rotterdam, and Singapore. Sample cutoffs previously noted were 2026-10-02 (ports) and 2026-10-04 (chokepoints). Recheck freshness when downloading.
- **NGA World Port Index — pass with caveat:** Rotterdam and Houston appear in WPI; Singapore may appear as individual terminals rather than one directly matching PortWatch port record. Coordinate/name matching and manual review are necessary. Field names may differ between current NGA CSV and older ArcGIS service editions.
- **Field meanings:** `n_tanker` counts transit events; `capacity_tanker` describes estimated aggregate cargo-carrying tonnage associated with transits, **not** a physical throughput limit. The historical *capacity proxy* must be computed from past transit counts instead.

References: [PortWatch](https://portwatch.imf.org/), [PortWatch ArcGIS chokepoints](https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0), [NGA WPI](https://msi.nga.mil/Publications/WPI). Verify edition-specific column spellings and units against the exact file used.

### Formula checks (five required)

| Formula | Status | Formula review and test |
|---|---|---|
| Port ranking | **ok (method)** | For each `portid`, sum `import_* + export_*` across ship types over 2019–2025; rank all classes and tanker-only. Choose ~60 top by all types and ~40 by tanker, deduplicate and fill to ~100. Import/export should be summed as estimated tonnage, not interpreted as percentages. |
| Capacity proxy | **ok with correction** | Weekly aggregate of daily `n_tanker`, then historical 95th–99th percentile in a documented pre-Red-Sea-crisis baseline (for example 2019–October 2023). Do **not** substitute PortWatch `capacity_tanker` measured in tonnes for count-per-week capacity. |
| Utilization | **ok** | `weekly_n_tanker / historical_percentile_weekly_n_tanker`; numerical sanity check 80/100 = 0.80 (unitless). May exceed 1 because proxy is not a fixed engineering limit. |
| Anomaly | **ok with zero-spread edge case** | `(weekly_count - same_week_of_year_baseline_mean) / same_week_of_year_baseline_stddev`; numerical sanity check (80−100)/10 = −2. Use matching week definitions and return undefined if baseline standard deviation is zero or sample insufficient. |
| Average vessel size | **ok as a proxy** | `capacity_tanker / n_tanker` in estimated tonnes of carrying capacity per transit, not actual cargo or vessel dimensions. Numerical sanity check 240,000/3 = 80,000 tonnes/transit; undefined if transit count is zero. |

**Validation boundary:** The numerical values in the formula checks are deliberately illustrative, not observed PortWatch values. No complete 2019–2025 top-100 ranking or computed historical percentiles are claimed. Confirm all dataset versions and endpoint limits, reproduce the calculations on downloaded source samples, and obtain PM review of any licensing or source-adoption uncertainty.

'''
marker='## Local layout convention'
if marker not in s:raise SystemExit('Insertion heading missing')
s=s.replace(marker,addition+marker)
p.write_text(s+'\n',encoding='utf8')
print(f'Updated {p}. Review with: git diff -- DATA.md')
