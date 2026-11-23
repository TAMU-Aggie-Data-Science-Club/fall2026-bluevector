"""Deliverable 4 — Calibration & Validation Group, Part 2: Red Sea calibration (v2, sourced inputs).

What changed from the fitted-throttle version
  * The Red Sea scenario no longer cuts throughput. Suez stayed open through Dec 2023 - Jun 2024, so
    the diversion was a price response; treating it as a capacity cut would replay the observation
    inside the constraint that is supposed to predict it. Throughput cuts stay for the two closures
    that were physical (Ever Given, Panama slots).
  * The war-risk premium is a sourced insurance rate (an AWRP as a share of hull value per transit),
    charged once per listed-area transit. It is not fitted and not scaled by any throttle.
  * That leaves alpha/beta as the only free parameters, so both the Suez drop and the Cape rise are
    predictions rather than fitted outputs.

What it still does
  Grid-searches (alpha, beta) on the observed Dec 2023 - Feb 2024 (H1) Red Sea chokepoint shifts
  from validation_targets.csv, scores the winner once on the held-out Mar - Jun 2024 (H2) window,
  and writes config/flow_params.json.

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

from scripts.flow_model.scenario_engine import (
    AWRP_RED_SEA_H1_USD_PER_BBL,
    AWRP_RED_SEA_H2_USD_PER_BBL,
    RED_SEA_RISK_PASSAGES,
    event_scenario_params,
    get_baseline,
    run_scenario,
)
from scripts.validation.validation_targets import build_validation_targets

CONFIG_DIR = ROOT / "config"
INTERIM_DIR = ROOT / "data" / "interim"

SHARE_TARGET_TOLERANCE = 0.05

# The Red Sea windows are price events: Suez stayed open, utilisation stayed near 0.33, and the BPR
# curve cannot move a route there. Ever Given is the capacity event, so that is the window where
# alpha/beta are identified. Observed shift is the blocked week against the 13 weeks before it.
EVER_GIVEN_WEEK = pd.Timestamp("2021-03-26")
ATTRIBUTES_PATH = ROOT / "data" / "processed" / "chokepoint_weekly_attributes.csv"


def _observed_ever_given() -> dict:
    d = pd.read_csv(ATTRIBUTES_PATH, parse_dates=["week_ending"])
    out = {}
    for passage, key in (("suez", "suez_pct"), ("south_africa", "cape_pct")):
        s = d[d["searoute_passage"] == passage].set_index("week_ending").sort_index()
        pre = s.loc[: EVER_GIVEN_WEEK - pd.Timedelta(days=14), "weekly_tanker_transits"].tail(13)
        week = float(s.loc[EVER_GIVEN_WEEK, "weekly_tanker_transits"])
        out[key] = (week / float(pre.mean()) - 1.0) * 100.0
    return out


def _congestion_grid(scen_params: dict) -> tuple[list, tuple | None, bool, dict]:
    """Grid alpha/beta on Ever Given and return (points, chosen, flat, observed)."""
    obs = _observed_ever_given()
    print(
        f"\nObserved Ever Given week {EVER_GIVEN_WEEK.date()} vs 13 weeks before: "
        f"Suez={obs['suez_pct']:+.1f}%, Cape={obs['cape_pct']:+.1f}%"
    )
    points = []
    for alpha in (0.15, 0.30, 0.60, 1.00):
        for beta in (3.0, 4.0, 6.0, 8.0):
            ov = {"alpha": alpha, "beta": beta}
            base_df, _ = get_baseline(ov)
            scen_df, summary = run_scenario(
                {"suez": 1.0 / 7.0},
                scenario_id="calib_ever_given",
                params_override={**ov, **scen_params["ever_given_2021"]},
            )
            pred = _modeled(base_df, scen_df, summary)
            loss = (
                abs(pred["suez_pct"] - obs["suez_pct"]) / 100.0
                + abs(pred["cape_pct"] - obs["cape_pct"]) / 100.0
            )
            print(
                f"  alpha={alpha:.2f} beta={beta:.1f} -> Suez {pred['suez_pct']:+.1f}% "
                f"Cape {pred['cape_pct']:+.1f}% | loss {loss:.4f}"
            )
            points.append((alpha, beta, float(loss), pred))
    # Flatness is measured against the DELIVERABLE4 default point, not against the spread of the grid:
    # the spread is widened by bad points at large alpha, and none of that makes a better alpha. If no
    # point beats (0.15, 4.0) by a hundredth of the loss, the curve is unidentified and the config stands.
    default = next((p for p in points if p[0] == 0.15 and p[1] == 4.0), points[-1])
    best = min(points, key=lambda p: p[2])
    flat = best[2] >= default[2] - 0.01
    # Report the prediction of the point that is actually kept, so a flat grid still records numbers.
    return points, (None if flat else best), flat, obs, default[3]


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
    scen_params = event_scenario_params()
    h1_params = scen_params["red_sea_2023h1"]
    h2_params = scen_params["red_sea_2024"]

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
    print(
        f"Sourced premium: H1 ${h1_params['awrp_usd_per_bbl']:.2f}/bbl, H2 "
        f"${h2_params['awrp_usd_per_bbl']:.2f}/bbl, charged on {RED_SEA_RISK_PASSAGES}. "
        "No throughput cut in either window."
    )

    points: list[tuple[float, float, float, dict]] = []

    for alpha in (0.10, 0.12, 0.15):
        for beta in (3.0, 4.0):
            ab_override = {"alpha": alpha, "beta": beta}
            base_df, _ = get_baseline(ab_override)
            scen_df, summary = run_scenario(
                {},
                scenario_id="calib_red_sea",
                params_override={**ab_override, **h1_params},
            )
            pred = _modeled(base_df, scen_df, summary)
            loss = (
                abs(pred["suez_pct"] - obs["suez_pct"]) / 100.0
                + abs(pred["cape_pct"] - obs["cape_pct"]) / 100.0
                + abs(pred["share"] - obs["share"])
            )
            print(
                f"  alpha={alpha:.2f} beta={beta:.1f} -> Suez {pred['suez_pct']:+.1f}% "
                f"Cape {pred['cape_pct']:+.1f}% share {pred['share']:.4f} | loss {loss:.4f}"
            )
            points.append((alpha, beta, float(loss), pred))

    current = json.loads((CONFIG_DIR / "flow_params.json").read_text(encoding="utf-8"))
    default = next((p for p in points if p[0] == 0.15 and p[1] == 4.0), points[-1])
    grid_is_flat = min(p[2] for p in points) >= default[2] - 0.01
    if grid_is_flat:
        # Every point returns the same answer because congestion never bites at observed utilisation
        # (the BPR delay is 0.02 days at Suez). Promoting whichever point sorts first would be an
        # arbitrary claim of calibration, so the config keeps the default point.
        chosen = next(
            (p for p in points if p[0] == float(current["alpha"]) and p[1] == float(current["beta"])),
            default,
        )
    else:
        chosen = min(points, key=lambda p: p[2])

    best = {"alpha": chosen[0], "beta": chosen[1]}
    best_loss, best_pred = chosen[2], chosen[3]
    assert best_pred is not None
    if grid_is_flat:
        print(
            "Red Sea grid is flat: alpha and beta are not identified on a price-only window. "
            f"Red Sea keeps alpha={best['alpha']:.2f}, beta={best['beta']:.1f}."
        )
    else:
        print(f"Red Sea grid selects alpha={best['alpha']:.2f}, beta={best['beta']:.1f}.")

    # Calibrate the congestion curve where a queue actually exists, then adopt that point.
    cg_points, cg_chosen, cg_flat, cg_obs, cg_default_pred = _congestion_grid(scen_params)
    if cg_chosen is not None:
        cg_alpha, cg_beta, cg_loss, cg_pred = cg_chosen
        print(f"Ever Given grid selects alpha={cg_alpha:.2f}, beta={cg_beta:.2f} (loss {cg_loss:.4f}).")
        best = {"alpha": cg_alpha, "beta": cg_beta}
    else:
        cg_loss = float("nan")
        cg_pred = {
            "suez_pct": cg_default_pred["suez_pct"],
            "cape_pct": cg_default_pred["cape_pct"],
        }
        print("Ever Given grid is flat too: congestion carries no weight at any alpha/beta tested.")

    # Out-of-sample check on the held-out H2 window, with the H2 premium (a sourced input, so this
    # stays a test of the routing response rather than of the premium).
    holdout_scen, holdout_summary = run_scenario(
        {},
        scenario_id="holdout_red_sea",
        params_override={**best, **h2_params},
    )
    holdout_base, _ = get_baseline(best)
    holdout_pred = _modeled(holdout_base, holdout_scen, holdout_summary)

    share_error = abs(best_pred["share"] - obs["share"])
    holdout_share_error = abs(holdout_pred["share"] - obs_holdout["share"])

    # Update the config in place rather than rebuilding it: the tariff windows, the cost divisor and
    # the toll basis are maintained by hand, and a calibration run must not silently drop them.
    params = json.loads((CONFIG_DIR / "flow_params.json").read_text(encoding="utf-8"))
    params.pop("chokepoint_queue_days", None)
    params.pop("allow_over_capacity", None)
    params.update(
        {
            "alpha": best["alpha"],
            "beta": best["beta"],
            "awrp_red_sea_h1_usd_per_bbl": AWRP_RED_SEA_H1_USD_PER_BBL,
            "awrp_red_sea_h2_usd_per_bbl": AWRP_RED_SEA_H2_USD_PER_BBL,
            "red_sea_risk_passages": RED_SEA_RISK_PASSAGES,
            "calibration_window": "2023-12-01..2024-02-29 (Red Sea H1)",
            "holdout_window": "2024-03-01..2024-06-30 (Red Sea H2)",
            "calibration_grid_flat": bool(grid_is_flat),
            "congestion_calibration_window": f"Ever Given, week {EVER_GIVEN_WEEK.date()}",
            "congestion_grid_flat": bool(cg_flat),
            "congestion_calibration_loss": None if cg_flat else round(cg_loss, 4),
            "congestion_suez_pct_observed": round(cg_obs["suez_pct"], 2),
            "congestion_cape_pct_observed": round(cg_obs["cape_pct"], 2),
            "congestion_suez_pct_modeled": round(cg_pred["suez_pct"], 2),
            "congestion_cape_pct_modeled": round(cg_pred["cape_pct"], 2),
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
            "holdout_cape_pct_modeled": round(holdout_pred["cape_pct"], 2),
            "holdout_cape_pct_observed": round(obs_holdout["cape_pct"], 2),
            "holdout_observed_suez_share": round(obs_holdout["share"], 4),
            "holdout_modeled_suez_share": round(holdout_pred["share"], 4),
            "holdout_share_error_abs": round(holdout_share_error, 4),
        }
    )

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
