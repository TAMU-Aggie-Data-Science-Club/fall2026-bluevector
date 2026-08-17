# Data

This file explains **DeadZone's** suggested data sources.

> **`DATA.md` is tracked in git. The `data/` folder is not.** AIS bulk data is huge — never commit it. Clone the repo, then populate `data/` locally.

## Suggested sources (starting point)

| Source | Origin / URL | Access method | License | Sensitivity | Notes |
|--------|--------------|---------------|---------|-------------|-------|
| MarineCadastre (US AIS) | https://marinecadastre.gov/ais/ | Free bulk download by year/month | Public | None | US coastal + territorial waters — well-documented, easy to start |
| Global Fishing Watch | https://globalfishingwatch.org/data-download/ | Registered download | CC-BY-NC (research) | None | Global commercial AIS, cleaned. Registration required. |
| UN Comtrade | https://comtrade.un.org/ | REST API | Attribution — free tier | None | Bilateral trade flows by commodity + country — the economic side of the model |
| World Bank Commodity Prices | https://www.worldbank.org/en/research/commodity-markets | Monthly XLS | Public | None | Historical monthly commodity prices for calibrating the economic model |
| Natural Earth (coastlines, borders) | https://www.naturalearthdata.com/ | Public download | Public domain | None | Base layers for the map |

## How to think about using each source

- **AIS data is huge.** A month of global AIS is tens of GB. Down-sample to a bounding box around your choke point + a coarse H3 grid before doing anything else.
- **Spoofing / noise.** AIS transmissions are sometimes spoofed or missing. Don't chase every anomaly — filter obvious junk (impossibly fast vessels, land-locked ships).
- **Comtrade lag.** Trade data lags by months. Any real-time claim is unsupportable — frame the economic model as counterfactual, not forecast.
- **License.** Global Fishing Watch data is CC-BY-NC — attribute properly and don't ship it in a commercial product.

Choosing and vetting a source is a **judgment call** — surface it to a PM rather than deciding a major data direction alone.

## Local layout convention

```
data/
├── raw/          # AIS bulk files as downloaded — huge, NEVER commit
├── interim/      # per-vessel trajectories, H3-gridded traffic tables
└── processed/    # lane graph edges + weights + goods flow tables
```

Because `data/` isn't in git, the **pipeline that fetches and builds these folders** is what must be committed and reproducible — not the data itself.
