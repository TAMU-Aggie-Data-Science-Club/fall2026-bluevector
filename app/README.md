# Blue Vector on Streamlit Community Cloud

The entry point is `app/dashboard.py`. This release exposes the full flow solver, not live GNN or price inference. Its scenarios are conditional simulations, not measurements or validated causal forecasts of a crisis.

## Run locally

From the repository root, use Python 3.13:

```powershell
.venv/Scripts/python.exe -m pip install -r app/requirements.txt
.venv/Scripts/python.exe -m streamlit run app/dashboard.py
```

The pinned direct dependencies are the versions used for local preparation. Kepler.gl is optional and not installed by the cloud manifest; PyDeck is the supported default map.

## Prepare data without committing it

The full solver requires the existing OD matrix and weekly chokepoint attributes. Package them locally:

```powershell
.venv/Scripts/python.exe scripts/package_dashboard_data.py
```

This creates ignored `data/processed/dashboard_bundle.zip` and its `.sha256` file. No upload occurs. The archive contains exactly `data/interim/od_matrix.csv` and `data/processed/chokepoint_weekly_attributes.parquet`. Configuration and topology come from tracked `config/` files. No raw trade records, credentials or model checkpoints are bundled.

Have the data owner approve the bundle's hosting and access terms before upload: the OD matrix derives from Comtrade and the weekly series from PortWatch; private storage does not automatically authorize public redistribution. See `DATA.md`. Use a durable HTTPS download endpoint returning ZIP bytes directly, not a Drive preview page or an expiring signed URL. No storage provider has yet been selected or provisioned.

In Community Cloud's app settings, enter these two secrets:

```toml
DATA_BUNDLE_URL = "https://YOUR-APPROVED-HOST/versioned-dashboard-bundle.zip"
DATA_BUNDLE_SHA256 = "COPY_THE_64_CHARACTER_HASH_PRINTED_BY_THE_PACKAGER"
```

The app verifies the SHA-256 and archive members before restoring files under ignored `data/`. Restarted cloud instances can restore again. A changed hash downloads a new bundle. When replacing data, restart the app to clear cached graph/solver state. Keep the matching topology, flow parameters and data version together. Do not rely on cloud local disk as durable storage.

Missing or invalid data stops the dashboard explicitly. Solver failures also stop it; no simplified substitute engine runs silently. Never put secret URLs or API credentials into Git. `.streamlit/secrets.toml` is ignored. This dashboard needs no live EIA or Comtrade API keys.

## Deploy and keep one address

1. Publish the approved code to GitHub; local commits are invisible to Community Cloud. The current working branch is `issue-5/integration`; no merge to main is required to test that branch.
2. Sign into https://share.streamlit.io/ with a GitHub identity permitted to deploy this organization repository; organization authorization/admin help may be required.
3. Create app: repository `TAMU-Aggie-Data-Science-Club/fall2026-bluevector`, branch `issue-5/integration`, entry point `app/dashboard.py`. Choose Python 3.13 in advanced settings and enter the data secrets.
4. Reserve an available recognizable subdomain, for example `blue-vector-adsc.streamlit.app`. This example is not reserved or verified. Make visitor access public and test signed out.
5. Keep the same deployed app and subdomain. Subsequent pushes to its selected branch update it at that address. Changing the app's URL or deleting it can break a printed QR. No hosting provider guarantees permanence.
6. After deployment succeeds, create a static QR encoding the actual HTTPS app URL. Export SVG, include the readable URL under it, and test at final printed size on multiple phones. Do not print a QR for localhost, a guessed subdomain or an expiring URL.

Community Cloud sleeps inactive apps after 12 hours; visitors can wake them. Open and exercise the app before the poster session.

## Release checklist

- Data owner approves hosting/access and supplies a stable direct HTTPS URL.
- Code is published; compatible Python/dependencies install on Community Cloud.
- Logs show successful startup, verified data and full-solver execution.
- Baseline, Hormuz full closure and one partially throttled case run successfully.
- Map renders on desktop and mobile, and public visitors need no login.
- Verify every metric against saved model output before relying on it in a demonstration. Added edge flows are not unique rerouted cargo, and summed chokepoint transits are crossings rather than distinct ships.
- GNN/price poster results remain separate retrospective evaluations until explicitly integrated and tested.
- Reserve the final subdomain before generating the poster QR.

Official documentation: [deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy), [dependencies](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies), [secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), [updates and hibernation](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app).
