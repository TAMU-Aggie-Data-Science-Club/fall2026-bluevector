"""Weekly Brent error-correction Ridge model and honest scenario sensitivities.

Only lagged, observable network features enter the price regression. Historical
route days and freight costs are not measured and cannot identify their Brent
coefficients. Scenario outputs are conditional associations, not causal premiums.
Run python -m scripts.price.backtest_price for the held-out scorecard.
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / 'data/processed'
BASE_FEATURES = ['brent_lag1']
NETWORK_FEATURES = BASE_FEATURES + ['brent_ma4_gap', 'suez_share_lag1', 'delta_suez_share_lag1']


def ridge_fit(X, y, lam=10.):
    penalty = np.eye(X.shape[1]) * lam
    penalty[0, 0] = 0  # never shrink the intercept toward a zero dollar price
    return np.linalg.solve(X.T @ X + penalty, X.T @ y)


def build_features(hist):
    df = hist.sort_values('week_or_scenario_id').copy()
    dates = pd.to_datetime(df.week_or_scenario_id)
    if dates.duplicated().any() or (dates.diff().dropna() != pd.Timedelta(days=7)).any():
        raise ValueError('Price features require unique consecutive Friday-ending weeks')
    df['brent_lag1'] = df.brent_weekly_avg.shift(1)
    df['brent_ma4_gap'] = df.brent_weekly_avg.shift(1).rolling(4).mean() - df.brent_lag1
    df['suez_share_lag1'] = df.suez_tanker_share.shift(1)
    df['delta_suez_share_lag1'] = df.suez_tanker_share.diff().shift(1)
    df['price_change'] = df.brent_weekly_avg - df.brent_lag1
    return df.dropna(subset=NETWORK_FEATURES + ['price_change']), NETWORK_FEATURES


class BrentRidge:
    def __init__(self, features, lam=10.):
        self.features, self.lam = list(features), lam

    def fit(self, frame):
        x = frame[self.features].to_numpy(float)
        self.mean, self.std = x.mean(0), x.std(0).clip(1e-8)
        self.beta = ridge_fit(self.design(frame), frame.price_change.to_numpy(float), self.lam)
        return self

    def design(self, frame):
        return np.column_stack([np.ones(len(frame)), (frame[self.features].to_numpy(float) - self.mean) / self.std])

    def predict(self, frame):
        return np.maximum(0, frame.brent_lag1.to_numpy(float) + self.design(frame) @ self.beta)


def load_history():
    wk = pd.read_parquet(PROCESSED / 'chokepoint_weekly_attributes.parquet')
    if 'n_days_observed' in wk:
        wk = wk[wk.n_days_observed == 7]
    pivot = wk.pivot(index='week_ending', columns='searoute_passage', values='weekly_tanker_transits')
    brent = pd.read_parquet(ROOT / 'data/interim/eia_brent_weekly.parquet').set_index('week_ending')
    idx = pd.date_range(max(pivot.index.min(), brent.index.min()), min(pivot.index.max(), brent.index.max()), freq='W-FRI')
    hist = brent.reindex(idx)[['brent_weekly_avg']].copy()
    hist['suez_tanker_share'] = pivot.suez / (pivot.suez + pivot.south_africa).replace(0, np.nan)
    hist['week_or_scenario_id'] = hist.index
    hist['source'] = 'observed'
    return hist.reset_index(drop=True)


def main():
    hist, features = build_features(load_history())
    # Separate current-vintage fit/calibration from the frozen historical backtest.
    # Reusing a 2023 radius for a newly refitted model would not be calibration.
    calibration = hist.tail(52)
    fitting = hist.iloc[:-52]
    model = BrentRidge(features).fit(fitting)
    residuals = np.sort(np.abs(calibration.brent_weekly_avg.to_numpy() - model.predict(calibration)))
    rank = min(len(residuals), int(np.ceil((len(residuals) + 1) * .9)))
    radius = float(residuals[rank - 1])
    pd.DataFrame([dict(fit_end=str(fitting.week_or_scenario_id.max()), calibration_start=str(calibration.week_or_scenario_id.min()),
                       calibration_end=str(calibration.week_or_scenario_id.max()), calibration_n=len(calibration),
                       radius_90=radius, source='modeled')]).to_csv(PROCESSED / 'scenario_price_calibration.csv', index=False)
    scenario_path = PROCESSED / 'scenario_price_features.parquet'
    if not scenario_path.exists():
        raise FileNotFoundError('Run network_indices first for scenario inputs')
    scen = pd.read_parquet(scenario_path)
    baseline = scen.loc[scen.week_or_scenario_id.eq('baseline')].iloc[0]
    x = scen.copy()
    x['brent_lag1'] = hist.brent_weekly_avg.iloc[-1]
    x['brent_ma4_gap'] = hist.brent_weekly_avg.tail(4).mean() - x.brent_lag1
    x['suez_share_lag1'] = x.suez_tanker_share
    x['delta_suez_share_lag1'] = x.suez_tanker_share - baseline.suez_tanker_share
    pred = model.predict(x)
    base_pred = pred[scen.week_or_scenario_id.eq('baseline').to_numpy()][0]
    result = pd.DataFrame(dict(week_or_scenario_id=scen.week_or_scenario_id,
        pred_brent_mean=pred, pred_brent_lower_90=np.maximum(0, pred - radius),
        pred_brent_upper_90=pred + radius, delta_brent_mean=pred - base_pred,
        source='modeled', interpretation='conditional association; not causal or a forecast',
        freight_surcharge_component=np.nan, supply_risk_premium=np.nan,
        causal_decomposition_identified=False,
        outside_historical_share_range=(x.suez_tanker_share < hist.suez_tanker_share.min()) | (x.suez_tanker_share > hist.suez_tanker_share.max())))
    assert (result.pred_brent_lower_90 <= result.pred_brent_mean).all()
    assert (result.pred_brent_mean <= result.pred_brent_upper_90).all()
    assert (result.pred_brent_lower_90 >= 0).all()
    result.to_parquet(PROCESSED / 'brent_scenario_impacts.parquet', index=False)
    pd.DataFrame({'feature': ['intercept'] + features, 'coefficient_standardized': model.beta}).to_csv(PROCESSED / 'brent_model_coefficients.csv', index=False)
    print(result[result.week_or_scenario_id.isin(['baseline', 'red_sea_2024', 'ever_given_2021', 'panama_drought_2023'])].to_string(index=False))


if __name__ == '__main__':
    main()
