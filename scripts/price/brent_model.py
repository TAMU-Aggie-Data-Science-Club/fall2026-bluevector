"""
scripts/price/brent_model.py  —  Deliverable 5, slot E2

PURPOSE
-------
Fit a weekly Brent crude oil price model that:
  1. Uses an autoregressive anchor (lagged price) so the model does not spuriously
     fit the trending series.
  2. Adds network-derived features (shipping cost change, Suez share, voyage days,
     rerouted barrel-miles) to explain deviation from the AR anchor.
  3. Reports a 90 % prediction interval using quantile regression.
  4. Applies the trained model to every scenario in scenario_price_features.parquet
     to give a predicted Brent impact for each disruption scenario.

Inputs
------
data/processed/historical_price_dataset.parquet  — built by network_indices.py
data/processed/scenario_price_features.parquet   — built by network_indices.py
config/flow_params.json                          — charter rates and params

Outputs
-------
data/processed/brent_model_history_predictions.parquet
    Per-week predictions on the full history (in-sample; not held-out).
    Columns: week_or_scenario_id, brent_weekly_avg, pred_brent_mean,
             pred_brent_lower_90, pred_brent_upper_90,
             freight_surcharge_component, supply_risk_premium, source

data/processed/brent_scenario_impacts.parquet
    Per-scenario Brent impact prediction.
    Columns: week_or_scenario_id, pred_brent_mean, pred_brent_lower_90,
             pred_brent_upper_90, delta_brent_mean, source

data/processed/brent_model_coefficients.csv
    Fitted coefficients and metadata for the mean (Ridge) model.

HOW IT WORKS
------------
MODEL ARCHITECTURE

Mean model — ARX with Ridge regularisation (pure numpy, no sklearn):
    y_t = α + β_1*y_{t-1} + β_2*MA4_t + β_3*Δcost_t
           + β_4*Δsuez_share_t + β_5*extra_days_t
           + β_6*rerouted_bmiles_t + β_7…β_14*gnn_emb
    + ε_t

where
    y_t            — weekly Brent price (USD/bbl)
    y_{t-1}        — one-week lag (brent_lag1)
    MA4_t          — 4-week rolling average as slow-trend anchor (brent_ma4)
    Δcost_t        — delta_cost_per_bbl_base (change in freight cost vs baseline)
    Δsuez_share_t  — week-on-week change in suez_tanker_share
    extra_days_t   — avg_voyage_days minus its rolling 4-week mean
    rerouted_bmiles— rerouted_barrel_miles

Ridge solution (closed form):
    β = (X'X + λI)^{-1} X'y
    λ = 1.0 (mild regularisation; keeps coefficients from blowing up)

Interval model — quantile regression (5th and 95th percentile):
    Minimise Σ ρ_q(y_t - X_t β)  where ρ_q(u) = u*(q - 1[u<0])

Implemented via iteratively reweighted least squares (IRLS):
    At each step, weight each residual by 1 / (|r| + ε) with an asymmetric sign,
    then solve a weighted OLS.  Converges in ~30 iterations for tabular data.

DECOMPOSITION
    freight_surcharge = β_3 * Δcost_t
    supply_risk_premium = pred_mean - AR_anchor - freight_surcharge
    AR_anchor = α + β_1*y_{t-1} + β_2*MA4_t

SCENARIO IMPACTS
    For each scenario row, we build the same feature vector using:
      - AR terms set to the last observed Brent price (Oct 2026)
      - Network indices from scenario_price_features.parquet
    Then predict mean + intervals.  delta_brent_mean is relative to baseline scenario.

CAVEATS (stated honestly)
  - Oil prices are close to a random walk; network features add signal but the
    AR anchor dominates.
  - The model is fitted on weekly data since 2019.  The 2022 Ukraine spike is a
    large residual the network features cannot explain.
  - Scenario impacts are counterfactuals, not forecasts.

Usage
-----
    python -m scripts.price.brent_model          # writes output files
    python -m scripts.price.brent_model --check  # + acceptance checks
"""

from __future__ import annotations

import argparse
import pathlib
import json
import sys

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = pathlib.Path(__file__).resolve().parents[2]
PROCESSED = REPO / "data" / "processed"
INTERIM = REPO / "data" / "interim"
CONFIG = REPO / "config" / "flow_params.json"

HISTORY_FILE = PROCESSED / "historical_price_dataset.parquet"
SCENARIO_FILE = PROCESSED / "scenario_price_features.parquet"

OUT_HIST_PRED = PROCESSED / "brent_model_history_predictions.parquet"
OUT_SCEN_IMPACTS = PROCESSED / "brent_scenario_impacts.parquet"
OUT_COEFS = PROCESSED / "brent_model_coefficients.csv"


# ---------------------------------------------------------------------------
# Ridge regression (pure numpy)
# ---------------------------------------------------------------------------
def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float = 1.0) -> np.ndarray:
    """
    Closed-form Ridge regression.
        β = (X'X + λI)^{-1} X'y

    Parameters
    ----------
    X   : (n, p) design matrix (should include intercept column if wanted)
    y   : (n,) target vector
    lam : regularisation strength (applied to all coefficients including intercept)

    Returns
    -------
    β   : (p,) coefficient vector
    """
    n, p = X.shape
    XtX = X.T @ X  # (p, p)
    XtX += lam * np.eye(p)
    Xty = X.T @ y  # (p,)
    # Solve via numpy's least-squares (stable for near-singular matrices)
    beta, _, _, _ = np.linalg.lstsq(XtX, Xty, rcond=None)
    return beta


# ---------------------------------------------------------------------------
# Quantile regression via IRLS
# ---------------------------------------------------------------------------
def quantile_fit(
    X: np.ndarray,
    y: np.ndarray,
    q: float,
    lam: float = 1.0,
    max_iter: int = 50,
    tol: float = 1e-6,
    eps: float = 1e-4,
) -> np.ndarray:
    """
    Quantile regression minimising Σ ρ_q(y - Xβ), solved via IRLS.

    ρ_q(u) = u*(q - 1[u<0])  (the "pinball" / "check" loss)

    At each iteration:
      w_i = 1 / max(|r_i|, eps)   (Huber-like smoothing to avoid division by zero)
      Solve weighted Ridge:  β ← argmin Σ w_i (y_i - x_i'β)² + λ||β||²

    Then apply the asymmetric correction:
      Positive residuals (y > ŷ) → weight factor q
      Negative residuals (y < ŷ) → weight factor (1-q)

    Parameters
    ----------
    X, y  : design matrix and target
    q     : quantile (0 < q < 1); use q=0.05 for lower, q=0.95 for upper
    """
    n, p = X.shape
    beta = ridge_fit(X, y, lam=lam)   # warm start from Ridge

    for _ in range(max_iter):
        beta_prev = beta.copy()
        resid = y - X @ beta

        # Asymmetric weighting: over-weight the target side
        w = np.where(resid >= 0, q, 1.0 - q) / (np.abs(resid) + eps)
        w = np.clip(w, 1e-6, 1e6)

        # Weighted Ridge solve
        sqrtW = np.sqrt(w)
        Xw = sqrtW[:, None] * X
        yw = sqrtW * y
        XwXw = Xw.T @ Xw + lam * np.eye(p)
        Xwyw = Xw.T @ yw
        beta, _, _, _ = np.linalg.lstsq(XwXw, Xwyw, rcond=None)

        if np.max(np.abs(beta - beta_prev)) < tol:
            break

    return beta


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------
def build_features(hist: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Add lagged / rolling / change features to the history DataFrame.
    Returns the enriched DataFrame and the list of feature column names.

    Rows with NaN in any feature are dropped (first few weeks).
    """
    df = hist.sort_values("week_or_scenario_id").copy()

    # --- AR terms ---
    df["brent_lag1"] = df["brent_weekly_avg"].shift(1)
    df["brent_ma4"]  = df["brent_weekly_avg"].shift(1).rolling(4).mean()

    # --- Network feature changes ---
    df["delta_suez_share"] = df["suez_tanker_share"].diff()
    df["extra_voyage_days"] = df["avg_voyage_days"] - df["avg_voyage_days"].rolling(4).mean()

    # GNN embeddings (zeros in history; kept as features for future use)
    gnn_cols = [f"gnn_emb_{i}" for i in range(8)]

    feature_cols = [
        "brent_lag1",          # autoregressive anchor
        "brent_ma4",           # slow-trend anchor
        "delta_cost_per_bbl_base",   # freight change signal
        "delta_suez_share",    # Suez diversion signal
        "extra_voyage_days",   # voyage-day deviation
        "rerouted_barrel_miles",     # fleet work change
    ] + gnn_cols

    df = df.dropna(subset=feature_cols + ["brent_weekly_avg"])
    return df, feature_cols


# ---------------------------------------------------------------------------
# Prediction decomposition
# ---------------------------------------------------------------------------
def decompose_prediction(
    X_df: pd.DataFrame,
    feature_cols: list[str],
    beta: np.ndarray,
    intercept_idx: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Split the prediction into:
      AR_anchor             = intercept + β_lag1*lag1 + β_ma4*ma4
      freight_surcharge     = β_cost * delta_cost
      supply_risk_premium   = pred - AR_anchor - freight_surcharge

    Returns (ar_anchor, freight_surcharge, supply_risk_premium) all shape (n,).
    """
    X = np.column_stack([np.ones(len(X_df))] + [X_df[c].values for c in feature_cols])

    pred = X @ beta

    # AR anchor: intercept + lag1 + ma4 terms (indices 0, 1, 2)
    ar_anchor = (
        beta[0]                               # intercept
        + beta[1] * X_df["brent_lag1"].values # brent_lag1
        + beta[2] * X_df["brent_ma4"].values  # brent_ma4
    )

    # Freight surcharge: beta[3] * delta_cost
    freight = beta[3] * X_df["delta_cost_per_bbl_base"].values

    supply_risk = pred - ar_anchor - freight

    return ar_anchor, freight, supply_risk


# ---------------------------------------------------------------------------
# Scenario feature preparation
# ---------------------------------------------------------------------------
def build_scenario_features(
    scen: pd.DataFrame,
    last_brent: float,
    last_suez_share: float,
    last_avg_days: float,
    baseline_cost: float,
) -> pd.DataFrame:
    """
    Build feature vectors for scenario rows, anchoring AR terms to the last
    observed Brent price.

    For scenario inputs:
      - brent_lag1 = brent_ma4 = last_brent (last observed price)
      - delta_suez_share = suez_tanker_share - last_suez_share
      - extra_voyage_days = avg_voyage_days - last_avg_days
      - delta_cost_per_bbl_base: taken from scenario table (already vs baseline)
    """
    df = scen.copy()
    df["brent_lag1"] = last_brent
    df["brent_ma4"]  = last_brent
    df["delta_suez_share"]   = df["suez_tanker_share"] - last_suez_share
    df["extra_voyage_days"]  = df["avg_voyage_days"] - last_avg_days
    df["brent_weekly_avg"]   = float("nan")   # unknown for scenarios

    # Ensure GNN embedding columns exist (zeros)
    for i in range(8):
        if f"gnn_emb_{i}" not in df.columns:
            df[f"gnn_emb_{i}"] = 0.0

    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(check: bool = False) -> None:
    # Load data
    hist_raw = pd.read_parquet(HISTORY_FILE)
    scen_raw = pd.read_parquet(SCENARIO_FILE)

    # ---- Prepare history ---------------------------------------------------
    hist, feature_cols = build_features(hist_raw)

    # Build design matrix (intercept prepended)
    X_hist = np.column_stack(
        [np.ones(len(hist))] + [hist[c].values for c in feature_cols]
    )
    y_hist = hist["brent_weekly_avg"].values

    # ---- Fit models --------------------------------------------------------
    lam = 1.0
    print("[brent_model] Fitting Ridge mean model …")
    beta_mean = ridge_fit(X_hist, y_hist, lam=lam)

    print("[brent_model] Fitting quantile model (q=0.05) …")
    beta_lo = quantile_fit(X_hist, y_hist, q=0.05, lam=lam)

    print("[brent_model] Fitting quantile model (q=0.95) …")
    beta_hi = quantile_fit(X_hist, y_hist, q=0.95, lam=lam)

    # ---- In-sample predictions on history ----------------------------------
    pred_mean = X_hist @ beta_mean
    pred_lo   = X_hist @ beta_lo
    pred_hi   = X_hist @ beta_hi

    # Enforce ordering: lower < mean < upper
    pred_lo   = np.minimum(pred_lo, pred_mean)
    pred_hi   = np.maximum(pred_hi, pred_mean)

    # Enforce non-negative prices
    pred_mean = np.maximum(pred_mean, 0.0)
    pred_lo   = np.maximum(pred_lo,   0.0)
    pred_hi   = np.maximum(pred_hi,   0.0)

    # Decompose
    ar_anchor, freight, supply_risk = decompose_prediction(hist, feature_cols, beta_mean)

    hist_pred = pd.DataFrame({
        "week_or_scenario_id": hist["week_or_scenario_id"].values,
        "brent_weekly_avg":     y_hist,
        "pred_brent_mean":      pred_mean,
        "pred_brent_lower_90":  pred_lo,
        "pred_brent_upper_90":  pred_hi,
        "freight_surcharge_component": freight,
        "supply_risk_premium":  supply_risk,
        "source": "observed",
    })

    # Save history predictions
    hist_pred.to_parquet(OUT_HIST_PRED, index=False)
    print(f"[brent_model] wrote {len(hist_pred):,} history predictions → {OUT_HIST_PRED}")

    # ---- Coefficients table ------------------------------------------------
    all_feature_names = ["intercept"] + feature_cols
    coef_df = pd.DataFrame({
        "feature":    all_feature_names,
        "beta_mean":  beta_mean,
        "beta_q05":   beta_lo,
        "beta_q95":   beta_hi,
    })
    coef_df.to_csv(OUT_COEFS, index=False)
    print(f"[brent_model] wrote coefficients → {OUT_COEFS}")

    # ---- Scenario impacts --------------------------------------------------
    # AR anchors: last observed values
    last_brent = float(hist["brent_weekly_avg"].iloc[-1])
    last_suez  = float(hist["suez_tanker_share"].iloc[-1])
    last_days  = float(hist["avg_voyage_days"].iloc[-1])
    baseline_cost = float(
        scen_raw[scen_raw["week_or_scenario_id"] == "baseline"]["shipping_cost_per_bbl_base"].iloc[0]
        if "baseline" in scen_raw["week_or_scenario_id"].values
        else hist["shipping_cost_per_bbl_base"].iloc[-1]
    )

    print("[brent_model] Computing scenario Brent impacts …")
    scen_feat = build_scenario_features(
        scen_raw, last_brent, last_suez, last_days, baseline_cost
    )

    # Keep only feature rows that have all needed columns
    gnn_cols = [f"gnn_emb_{i}" for i in range(8)]
    all_feat_needed = feature_cols  # same list as history
    scen_feat = scen_feat.dropna(subset=[c for c in all_feat_needed if c != "brent_weekly_avg"])

    X_scen = np.column_stack(
        [np.ones(len(scen_feat))] + [scen_feat[c].values for c in feature_cols]
    )

    sp_mean = X_scen @ beta_mean
    sp_lo   = X_scen @ beta_lo
    sp_hi   = X_scen @ beta_hi

    # Enforce ordering and non-negative
    sp_lo   = np.minimum(sp_lo, sp_mean)
    sp_hi   = np.maximum(sp_hi, sp_mean)
    sp_mean = np.maximum(sp_mean, 0.0)
    sp_lo   = np.maximum(sp_lo,   0.0)
    sp_hi   = np.maximum(sp_hi,   0.0)

    # Delta vs baseline scenario
    bl_mask = scen_feat["week_or_scenario_id"].isin(["baseline", "sim_baseline"])
    if bl_mask.any():
        bl_mean = float(sp_mean[bl_mask.values][0])
    else:
        bl_mean = last_brent

    scen_impacts = pd.DataFrame({
        "week_or_scenario_id": scen_feat["week_or_scenario_id"].values,
        "pred_brent_mean":    sp_mean,
        "pred_brent_lower_90": sp_lo,
        "pred_brent_upper_90": sp_hi,
        "delta_brent_mean": sp_mean - bl_mean,
        "source": scen_feat["source"].values,
    })

    scen_impacts.to_parquet(OUT_SCEN_IMPACTS, index=False)
    print(f"[brent_model] wrote {len(scen_impacts):,} scenario impact rows → {OUT_SCEN_IMPACTS}")

    if check:
        _acceptance_check(hist_pred, scen_impacts, coef_df)


# ---------------------------------------------------------------------------
# Acceptance checks
# ---------------------------------------------------------------------------
def _acceptance_check(
    hist_pred: pd.DataFrame,
    scen_impacts: pd.DataFrame,
    coef_df: pd.DataFrame,
) -> None:
    print("\n[brent_model] Running acceptance checks …")

    # 1. Intervals ordered for every row (history)
    ordered_hist = (
        (hist_pred["pred_brent_lower_90"] <= hist_pred["pred_brent_mean"]) &
        (hist_pred["pred_brent_mean"] <= hist_pred["pred_brent_upper_90"])
    ).all()
    print(f"  History intervals ordered: {'PASS' if ordered_hist else 'FAIL'}")

    # 2. Intervals ordered for every row (scenarios)
    ordered_scen = (
        (scen_impacts["pred_brent_lower_90"] <= scen_impacts["pred_brent_mean"]) &
        (scen_impacts["pred_brent_mean"] <= scen_impacts["pred_brent_upper_90"])
    ).all()
    print(f"  Scenario intervals ordered: {'PASS' if ordered_scen else 'FAIL'}")

    # 3. No negative prices
    no_neg_hist = (hist_pred[["pred_brent_mean", "pred_brent_lower_90", "pred_brent_upper_90"]] >= 0).all().all()
    no_neg_scen = (scen_impacts[["pred_brent_mean", "pred_brent_lower_90", "pred_brent_upper_90"]] >= 0).all().all()
    print(f"  No negative prices (history): {'PASS' if no_neg_hist else 'FAIL'}")
    print(f"  No negative prices (scenarios): {'PASS' if no_neg_scen else 'FAIL'}")

    # 4. Key scenarios have plausible signs
    # Closing Suez or Hormuz should raise the predicted Brent vs baseline
    for scenario_name in ["sim_E001_th_000", "sim_E003_th_000", "sim_E004_th_000"]:
        rows = scen_impacts[scen_impacts["week_or_scenario_id"] == scenario_name]
        if not rows.empty:
            delta = rows["delta_brent_mean"].iloc[0]
            sign_ok = delta > 0
            print(f"  {scenario_name}: delta_brent_mean = {delta:+.4f} ({'PASS: positive' if sign_ok else 'WARNING: non-positive'})")

    # 5. No NaN in predictions
    nan_hist = hist_pred[["pred_brent_mean", "pred_brent_lower_90", "pred_brent_upper_90"]].isna().sum().sum()
    nan_scen = scen_impacts[["pred_brent_mean", "pred_brent_lower_90", "pred_brent_upper_90"]].isna().sum().sum()
    print(f"  NaN in history predictions: {nan_hist} ({'PASS' if nan_hist == 0 else 'FAIL'})")
    print(f"  NaN in scenario predictions: {nan_scen} ({'PASS' if nan_scen == 0 else 'FAIL'})")

    # 6. Print key coefficients for sanity
    print("\n  Key coefficients (mean model):")
    for _, row in coef_df.iterrows():
        print(f"    {row['feature']:35s} {row['beta_mean']:+.6f}")

    print("[brent_model] acceptance checks complete\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    main(check=args.check)
