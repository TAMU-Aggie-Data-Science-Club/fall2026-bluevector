"""Deliverable 4 — Calibration & Validation Group, Part 3: Event Validation & Benchmark Report.

What it does:
  Compares the Flow Model's disruption scenarios against observed PortWatch targets that were NOT
  used for calibration:
    - Red Sea Mar-Jun 2024 (held out from the Dec 2023 - Feb 2024 calibration window)
    - Ever Given peak 7-day blockage (Mar 23-29, 2021)
    - Panama Canal drought (Oct 2023 - Mar 2024)
  Outputs data/processed/validation_report.csv (the baseline scorecard that the GNN in
  Deliverable 5 must beat) plus per-chokepoint detail in validation_event_details.csv.

  Modeled transits are crude-only while PortWatch counts all tankers, so the comparison is made on
  the model's percentage shift applied to the observed pre-event baseline. That keeps the test
  scale-free without pretending the two series are the same quantity.

Run from the repository root:
    .venv/bin/python scripts/validation/validate_events.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.flow_model.scenario_engine import event_scenarios, get_baseline, load_params, run_scenario
from scripts.validation.validation_targets import build_validation_targets

INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"

# Held-out observed window -> model scenario that should reproduce it
EVAL_WINDOWS = {
    "red_sea_2024_h2": "red_sea_2024",
    "ever_given_2021_peak": "ever_given_2021",
    "panama_drought_2023": "panama_drought_2023",
}


def validate_events() -> pd.DataFrame:
    targets_path = INTERIM_DIR / "validation_targets.csv"
    obs_df = pd.read_csv(targets_path) if targets_path.exists() else build_validation_targets()

    params = load_params()
    scenarios = event_scenarios(params)
    base_df, _ = get_baseline(None)
    base_tr_map = base_df.set_index("edge_id")["tanker_transits"].to_dict()

    rows = []
    for window_id, scenario_id in EVAL_WINDOWS.items():
        mod_df, _ = run_scenario(scenarios[scenario_id], scenario_id=window_id, save_files=True)
        mod_map = mod_df.set_index("edge_id")

        ev_obs = obs_df[obs_df["scenario_id"] == window_id]
        for _, obs in ev_obs.iterrows():
            eid = obs["edge_id"]
            if eid not in mod_map.index:
                continue
            m_row = mod_map.loc[eid]

            obs_pre_tr = float(obs["pre_weekly_transits"])
            obs_cri_tr = float(obs["tanker_transits"])
            obs_pct = float(obs["pct_change_transits"])

            mod_base_tr = float(base_tr_map.get(eid, 0.0))
            mod_cri_tr = float(m_row["tanker_transits"])
            mod_pct = ((mod_cri_tr - mod_base_tr) / mod_base_tr * 100.0) if mod_base_tr > 0 else 0.0

            # Apply the model's percentage shift to the observed pre-event baseline
            implied_mod_cri_tr = obs_pre_tr * (1.0 + mod_pct / 100.0)
            abs_err_tr = abs(implied_mod_cri_tr - obs_cri_tr)
            ape = abs_err_tr / max(obs_cri_tr, 1.0)

            obs_delta = obs_cri_tr - obs_pre_tr
            mod_delta = implied_mod_cri_tr - obs_pre_tr
            dir_match = (np.sign(obs_delta) == np.sign(mod_delta)) or (abs(obs_pct) < 5.0 and abs(mod_pct) < 5.0)

            rows.append(
                {
                    "scenario_id": window_id,
                    "edge_id": eid,
                    "searoute_passage": obs["searoute_passage"],
                    "obs_pre_weekly_transits": round(obs_pre_tr, 2),
                    "obs_crisis_weekly_transits": round(obs_cri_tr, 2),
                    "modeled_crisis_weekly_transits": round(implied_mod_cri_tr, 2),
                    "obs_pct_change": round(obs_pct, 2),
                    "modeled_pct_change": round(mod_pct, 2),
                    "abs_error_transits": round(abs_err_tr, 2),
                    "ape_transits": round(ape, 4),
                    "direction_match": bool(dir_match),
                }
            )

    detail_df = pd.DataFrame(rows)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    detail_df.to_csv(PROCESSED_DIR / "validation_event_details.csv", index=False)

    # Aggregate scorecard by event
    summary = (
        detail_df.groupby("scenario_id", as_index=False)
        .agg(
            n_chokepoints=("edge_id", "count"),
            mae_transits=("abs_error_transits", "mean"),
            rmse_transits=("abs_error_transits", lambda x: float(np.sqrt(np.mean(x**2)))),
            mape_transits=("ape_transits", "mean"),
            direction_match_rate=("direction_match", "mean"),
        )
        .round(4)
    )
    pct_err = detail_df.assign(err=(detail_df["modeled_pct_change"] - detail_df["obs_pct_change"]).abs()).groupby(
        "scenario_id"
    )["err"].mean()
    summary["mae_pct_points"] = summary["scenario_id"].map(pct_err).round(2)

    summary.to_csv(PROCESSED_DIR / "validation_report.csv", index=False)

    print("Saved validation benchmark scorecard -> data/processed/validation_report.csv")
    print(summary.to_string(index=False))
    return summary


if __name__ == "__main__":
    validate_events()
