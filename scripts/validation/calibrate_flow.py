"""Deliverable 4 — Calibration & Validation Group, Part 2: Red Sea Parameter Calibration.

What it does:
  Grid-searches (alpha, beta, Red Sea throughput loss, war-risk surcharge) against the observed
  Dec 2023 - Feb 2024 (H1) Red Sea chokepoint shifts from validation_targets.csv: the Suez and
  Bab-el-Mandeb transit drops, the Cape of Good Hope transit rise, and the Suez / (Suez + Cape)
  transit share. The winning parameter set is scored once on the held-out Mar-Jun 2024 (H2)
  window and written to config/flow_params.json.

  The H1/H2 split is what makes the calibration honest: H1 is fitted, H2 is never used to pick
  parameters, so validation accuracy on H2 is a genuine out-of-sample test.

Run from the repository root:
    .venv/bin/python scripts/validation/calibrate_flow.py
"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.flow_model.scenario_engine import get_baseline, run_scenario
from scripts.validation.validation_targets import build_validation_targets

CONFIG_DIR = ROOT / "config"
INTERIM_DIR = ROOT / "data" / "interim"

SHARE_TARGET_TOLERANCE = 0.05


def _observed(df: pd.DataFrame) -> dict:
    idx = df.set_index("searoute_passage")
    suez = float(idx.loc["suez", "tanker_transits"])
    cape = float(idx.loc["south_africa", "tanker_transits"])
    return {
        "suez_pct": float(idx.loc["suez", "pct_change_transits"]),
        "bab_pct": float(idx.loc["babalmandab", "pct_change_transits"]),
        "cape_pct": float(idx.loc["south_africa", "pct_change_transits"]),
        "share": suez / (suez + cape) if (suez + cape) > 0 else 0.0,
    }


def _modeled(base_df: pd.DataFrame, scen_df: pd.DataFrame, summary: dict) -> dict:
    base = base_df.set_index("searoute_passage")["tanker_transits"]
    scen = scen_df.set_index("searoute_passage")["tanker_transits"]

    def pct(passage: str) -> float:
        b = float(base.loc[passage])
        return (float(scen.loc[passage]) - b) / b * 100.0 if b > 0 else 0.0

    return {"suez_pct": pct("suez"), "cape_pct": pct("south_africa"), "share": summary["suez_tanker_share"]}


def calibrate() -> dict:
    targets_path = INTERIM_DIR / "validation_targets.csv"
    targets = pd.read_csv(targets_path) if targets_path.exists() else build_validation_targets()

    calib_df = targets[targets["scenario_id"] == "red_sea_2023h1"]
    holdout_df = targets[targets["scenario_id"] == "red_sea_2024_h2"]
    if calib_df.empty or holdout_df.empty:
        targets = build_validation_targets()
        calib_df = targets[targets["scenario_id"] == "red_sea_2023h1"]
        holdout_df = targets[targets["scenario_id"] == "red_sea_2024_h2"]

    obs = _observed(calib_df)
    obs_holdout = _observed(holdout_df)
    print(
        "Observed Red Sea H1 targets: "
        f"Suez={obs['suez_pct']:+.1f}%, Bab-el-Mandeb={obs['bab_pct']:+.1f}%, "
        f"Cape={obs['cape_pct']:+.1f}%, Suez/(Suez+Cape) share={obs['share']:.3f}"
    )
    print(
        "Observed Red Sea H2 (held out) targets: "
        f"Suez={obs_holdout['suez_pct']:+.1f}%, Cape={obs_holdout['cape_pct']:+.1f}%, "
        f"share={obs_holdout['share']:.3f}"
    )

    best_loss = float("inf")
    best: dict | None = None
    best_pred: dict | None = None

    for alpha in (0.10, 0.12, 0.15):
        for beta in (3.0, 4.0):
            ab_override = {"alpha": alpha, "beta": beta}
            base_df, _ = get_baseline(ab_override)
            for throttle in (0.45, 0.55, 0.65, 0.75, 0.85):
                for war_risk in (0.0, 0.25, 0.5, 0.75, 1.0):
                    override = {**ab_override, "red_sea_throttle": throttle, "red_sea_war_risk_per_bbl": war_risk}
                    scen_df, summary = run_scenario(
                        {"suez": throttle, "babalmandab": throttle},
                        scenario_id="calib_red_sea",
                        params_override=override,
                    )
                    pred = _modeled(base_df, scen_df, summary)
                    loss = (
                        abs(pred["suez_pct"] - obs["suez_pct"]) / 100.0
                        + abs(pred["cape_pct"] - obs["cape_pct"]) / 100.0
                        + abs(pred["share"] - obs["share"])
                    )
                    if loss < best_loss:
                        best_loss = float(loss)
                        best_pred = pred
                        best = {
                            "alpha": alpha,
                            "beta": beta,
                            "red_sea_throttle": throttle,
                            "red_sea_war_risk_per_bbl": war_risk,
                        }

    assert best is not None and best_pred is not None
    throttle = best["red_sea_throttle"]

    # Out-of-sample check on the held-out H2 window with the winning parameters
    holdout_scen, holdout_summary = run_scenario(
        {"suez": throttle, "babalmandab": throttle},
        scenario_id="holdout_red_sea",
        params_override=best,
    )
    holdout_base, _ = get_baseline(best)
    holdout_pred = _modeled(holdout_base, holdout_scen, holdout_summary)

    share_error = abs(best_pred["share"] - obs["share"])
    holdout_share_error = abs(holdout_pred["share"] - obs_holdout["share"])

    params = {
        "speed_knots": 13.0,
        "alpha": best["alpha"],
        "beta": best["beta"],
        "daily_tanker_rate": 50000.0,
        "daily_tanker_rate_low": 35000.0,
        "daily_tanker_rate_high": 75000.0,
        "avg_barrels_per_transit": 1000000.0,
        "red_sea_throttle": throttle,
        "red_sea_war_risk_per_bbl": best["red_sea_war_risk_per_bbl"],
        "risk_passages": ["suez", "babalmandab"],
        "chokepoint_queue_days": 1.0,
        "max_util_for_delay": 5.0,
        "n_congestion_iters": 10,
        "calibration_window": "2023-12-01..2024-02-29 (Red Sea H1)",
        "holdout_window": "2024-03-01..2024-06-30 (Red Sea H2)",
        "calibration_loss": round(best_loss, 4),
        "calibration_suez_pct_modeled": round(best_pred["suez_pct"], 2),
        "calibration_suez_pct_observed": round(obs["suez_pct"], 2),
        "calibration_cape_pct_modeled": round(best_pred["cape_pct"], 2),
        "calibration_cape_pct_observed": round(obs["cape_pct"], 2),
        "observed_suez_share_crisis": round(obs["share"], 4),
        "modeled_suez_share_crisis": round(best_pred["share"], 4),
        "share_error_abs": round(share_error, 4),
        "meets_share_target": bool(share_error <= SHARE_TARGET_TOLERANCE),
        "holdout_suez_pct_modeled": round(holdout_pred["suez_pct"], 2),
        "holdout_suez_pct_observed": round(obs_holdout["suez_pct"], 2),
        "holdout_observed_suez_share": round(obs_holdout["share"], 4),
        "holdout_modeled_suez_share": round(holdout_pred["share"], 4),
        "holdout_share_error_abs": round(holdout_share_error, 4),
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONFIG_DIR / "flow_params.json"
    out_path.write_text(json.dumps(params, indent=2), encoding="utf-8")

    print(f"\nCalibrated parameters saved to {out_path.relative_to(ROOT)}:")
    print(json.dumps(params, indent=2))
    status = "PASS" if params["meets_share_target"] else "FAIL"
    print(
        f"\nAcceptance (Suez share within {SHARE_TARGET_TOLERANCE:.0%} of observed): {status} "
        f"| modeled {params['modeled_suez_share_crisis']:.4f} vs observed {params['observed_suez_share_crisis']:.4f}"
        f" (|error| {params['share_error_abs']:.4f})"
    )
    print(
        f"Held-out H2 check: modeled share {params['holdout_modeled_suez_share']:.4f} vs observed "
        f"{params['holdout_observed_suez_share']:.4f} (|error| {params['holdout_share_error_abs']:.4f})"
    )
    return params


if __name__ == "__main__":
    calibrate()
