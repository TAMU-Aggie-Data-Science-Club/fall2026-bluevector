"""Frozen one-week price backtest: train <=2022, calibrate Jan-Oct 2023,
test Nov 2023-Jun 2024. Ridge strength is fixed before inspecting test results.
Intervals use a finite-sample 90% absolute-residual calibration quantile. This
is empirical uncertainty; serial dependence prevents an unconditional guarantee.
"""
import numpy as np
import pandas as pd
from scripts.price.brent_model import PROCESSED, BrentRidge, build_features, load_history, BASE_FEATURES, NETWORK_FEATURES


def radius90(error):
    values = np.sort(np.abs(np.asarray(error)))
    if len(values) < 10:
        raise ValueError('Insufficient independent calibration weeks')
    rank = min(len(values), int(np.ceil((len(values) + 1) * .9)))
    return float(values[rank - 1])


def paired_block_interval(model_error, baseline_error, repeats=2000):
    """Paired circular four-week block bootstrap of RMSE difference (model - RW)."""
    a, b = np.asarray(model_error), np.asarray(baseline_error)
    rng = np.random.default_rng(20261122)
    values = []
    for _ in range(repeats):
        starts = rng.integers(0, len(a), size=int(np.ceil(len(a) / 4)))
        idx = ((starts[:, None] + np.arange(4)) % len(a)).ravel()[:len(a)]
        values.append(np.sqrt(np.mean(a[idx] ** 2)) - np.sqrt(np.mean(b[idx] ** 2)))
    return np.quantile(values, [.025, .975])


def main():
    raw_history = load_history()
    hist, _ = build_features(raw_history)
    # An executable forecast-origin regression check, including the target week.
    cutoff = pd.Timestamp('2024-03-01')
    perturbed = raw_history.copy()
    future = pd.to_datetime(perturbed.week_or_scenario_id) >= cutoff
    perturbed.loc[future, 'brent_weekly_avg'] *= 10
    perturbed.loc[future, 'suez_tanker_share'] *= .1
    changed, _ = build_features(perturbed)
    mask = pd.to_datetime(hist.week_or_scenario_id) == cutoff
    other_mask = pd.to_datetime(changed.week_or_scenario_id) == cutoff
    expected_features = hist.loc[mask, NETWORK_FEATURES].to_numpy()
    altered_features = changed.loc[other_mask, NETWORK_FEATURES].to_numpy()
    assert np.array_equal(expected_features, altered_features), 'Future data leaked into price features'
    features_path = PROCESSED / 'gnn_price_embeddings.parquet'
    models = {'random_walk': None, 'ar1': BASE_FEATURES, 'blue_vector': NETWORK_FEATURES}
    if features_path.exists():
        embedding = pd.read_parquet(features_path).drop(columns='source', errors='ignore')
        hist = hist.merge(embedding, on='week_or_scenario_id', how='left', validate='one_to_one')
        emb_cols = [f'gnn_emb_{i}' for i in range(8)]
        models['blue_vector_gnn'] = NETWORK_FEATURES + emb_cols
    else:
        raise FileNotFoundError('Train the pre-2023 real-only GNN embeddings before this four-model comparison')
    # Common weeks for ALL models, including the baseline.
    hist = hist.dropna(subset=NETWORK_FEATURES + emb_cols)
    dates = pd.to_datetime(hist.week_or_scenario_id)
    train = hist[dates <= '2022-12-30']
    cal = hist[(dates >= '2023-01-06') & (dates <= '2023-10-27')]
    test = hist[(dates >= '2023-11-03') & (dates <= '2024-06-28')]
    if min(len(train), len(cal), len(test)) < 10:
        raise ValueError('Insufficient common train/calibration/test weeks')
    predictions, scores, intervals = [], [], []
    for name, features in models.items():
        model = None if features is None else BrentRidge(features).fit(train)
        pred_cal = cal.brent_lag1.to_numpy() if model is None else model.predict(cal)
        pred = test.brent_lag1.to_numpy() if model is None else model.predict(test)
        radius = radius90(cal.brent_weekly_avg.to_numpy() - pred_cal)
        lower, upper = np.maximum(0, pred - radius), pred + radius
        y, previous = test.brent_weekly_avg.to_numpy(), test.brent_lag1.to_numpy()
        err = pred - y
        scores.append(dict(model=name, n=len(test), mae=np.abs(err).mean(), rmse=np.sqrt(np.mean(err ** 2)),
                           direction_accuracy=np.mean(np.sign(pred - previous) == np.sign(y - previous)),
                           coverage_90=np.mean((lower <= y) & (y <= upper)), interval_width=np.mean(upper - lower),
                           source='modeled'))
        intervals.append(dict(model=name, calibration_n=len(cal), radius_90=radius, train_end=str(train.week_or_scenario_id.max()),
                              calibration_end=str(cal.week_or_scenario_id.max()), source='modeled'))
        predictions.append(pd.DataFrame(dict(model=name, week_or_scenario_id=test.week_or_scenario_id,
                           actual=y, previous=previous, pred_brent_mean=pred, pred_brent_lower_90=lower,
                           pred_brent_upper_90=upper, source='modeled')))
    baseline_error = predictions[0].pred_brent_mean.to_numpy() - predictions[0].actual.to_numpy()
    for score, frame in zip(scores, predictions):
        lo, hi = paired_block_interval(frame.pred_brent_mean.to_numpy() - frame.actual.to_numpy(), baseline_error)
        score['rmse_difference_ci95_low'], score['rmse_difference_ci95_high'] = lo, hi
    report = pd.DataFrame(scores)
    rw_rmse = float(report.loc[report.model.eq('random_walk'), 'rmse'].iloc[0])
    report['rmse_skill_vs_random_walk'] = 1 - report.rmse / rw_rmse
    report.to_csv(PROCESSED / 'price_backtest_report.csv', index=False)
    pd.concat(predictions).to_parquet(PROCESSED / 'price_backtest_predictions.parquet', index=False)
    pd.DataFrame(intervals).to_csv(PROCESSED / 'price_interval_calibration.csv', index=False)
    hist.assign(source='modeled').to_parquet(PROCESSED / 'price_model_design.parquet', index=False)
    print(report.to_string(index=False))


if __name__ == '__main__':
    main()
