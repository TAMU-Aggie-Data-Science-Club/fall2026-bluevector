"""Evaluate real weekly GNN predictions on identical chokepoint-week pairs.

MAPE excludes actual zeros; zero count and denominator are reported. Direction
accuracy refers to the change from last week, not a fitted event baseline.
"""
import numpy as np
import pandas as pd
from scripts.gnn.train_gnn import OUT, EVENTS
from scripts.flow_model.edge_metrics import SHARED_SCHEMA_COLS


def metrics(y, pred, previous):
    y, pred, previous = map(np.asarray, [y, pred, previous])
    error = pred - y
    nonzero = y > 0
    return dict(n=len(y), mae=float(np.abs(error).mean()), rmse=float(np.sqrt(np.mean(error ** 2))),
                mape=float(np.mean(np.abs(error[nonzero]) / y[nonzero])) if nonzero.any() else np.nan,
                mape_n=int(nonzero.sum()), zero_actual_n=int((~nonzero).sum()),
                direction_accuracy=float(np.mean(np.sign(pred - previous) == np.sign(y - previous))))


def main():
    raw = pd.read_parquet(OUT / 'gnn_temporal_predictions.parquet')
    # Average predictions across predeclared seeds, never pick the seed using test loss.
    key = ['event', 'model', 'week_ending', 'edge_id']
    ensemble = raw.groupby(key, as_index=False)[['actual', 'prediction', 'persistence', 'seasonal']].mean()
    rows = []
    for event, group in ensemble.groupby('event'):
        for model, g in group.groupby('model'):
            rows.append(dict(event=event, model=model, source='gnn', **metrics(g.actual, g.prediction, g.persistence)))
        baseline = group[group.model == 'real_only']
        for name in ['persistence', 'seasonal']:
            rows.append(dict(event=event, model=name, source='modeled', **metrics(baseline.actual, baseline[name], baseline.persistence)))
    # The solver is a conditional scenario estimate, not a lag-only forecast.
    # Match units by applying crude percentage changes to observed tanker means.
    scenario_path = OUT / 'scenario_edge_flows.parquet'
    if scenario_path.exists():
        solver = pd.read_parquet(scenario_path)
        base = solver[solver.scenario_id.eq('baseline')].set_index('edge_id').tanker_transits
        observations = pd.read_parquet(OUT / 'chokepoint_weekly_attributes.parquet')
        observations = observations[observations.n_days_observed.eq(7)]
        for event, group in ensemble[ensemble.model.eq('real_only')].groupby('event'):
            train_end = pd.Timestamp(EVENTS[event][0])
            pre = observations[pd.to_datetime(observations.week_ending) < train_end].groupby('edge_id').weekly_tanker_transits.mean()
            changed = solver[solver.scenario_id.eq(event)].set_index('edge_id').tanker_transits
            ratios = changed / base.replace(0, np.nan)
            implied = group.edge_id.map(pre * ratios)
            if implied.isna().any():
                raise ValueError('Missing common solver/observed comparison edges')
            rows.append(dict(event=event, model='physics_conditional', source='modeled',
                             **metrics(group.actual, implied, group.persistence)))
    report = pd.DataFrame(rows)
    persistence = report[report.model.eq('persistence')].set_index('event').rmse
    report['rmse_skill_vs_persistence'] = 1 - report.rmse / report.event.map(persistence)
    edge_rows = [dict(event=e, model=m, edge_id=k, source='gnn', **metrics(g.actual, g.prediction, g.persistence))
                 for (e, m, k), g in ensemble.groupby(['event', 'model', 'edge_id'])]
    pd.DataFrame(edge_rows).to_csv(OUT / 'gnn_evaluation_by_edge.csv', index=False)
    report.to_csv(OUT / 'gnn_evaluation_report.csv', index=False)
    ensemble.to_parquet(OUT / 'gnn_ensemble_predictions.parquet', index=False)
    per_seed = [dict(event=e, model=m, seed=s, source='gnn', **metrics(g.actual, g.prediction, g.persistence))
                for (e, m, s), g in raw.groupby(['event', 'model', 'seed'])]
    pd.DataFrame(per_seed).to_csv(OUT / 'gnn_seed_evaluation.csv', index=False)
    # Keep the downstream scenario contract separate from temporal forecasts.
    files = sorted(OUT.glob('gnn_scenario_predictions_seed*.parquet'))
    if files and 'simulated_plus_real' in raw.model.unique():
        scenarios = pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)
        # Only use seeds in the current declared run.
        scenarios = scenarios[scenarios.seed.isin(raw.seed.unique())]
        keys = ['scenario_id', 'edge_id']
        numeric = ['oil_volume_bbl', 'tanker_transits', 'utilization', 'delay_days',
                   'anomaly_z', 'cost_per_barrel', 'delta_volume_bbl', 'delta_transits',
                   'share_rerouted', 'stranded_barrels']
        reference = scenarios.drop_duplicates(keys).drop(columns=numeric + ['seed'])
        mean = scenarios.groupby(keys, as_index=False)[numeric].mean().merge(reference, on=keys, validate='one_to_one')
        baseline = pd.read_parquet(scenario_path).query("scenario_id == 'baseline'").set_index('edge_id')
        base_vol = mean.edge_id.map(baseline.oil_volume_bbl)
        mean['share_rerouted'] = np.maximum(0, -mean.delta_volume_bbl / base_vol.clip(lower=1))
        assert not mean.duplicated(keys).any()
        assert np.isfinite(mean[numeric].to_numpy()).all()
        assert (mean.loc[mean.throttle_factor.eq(0), ['oil_volume_bbl', 'tanker_transits']].to_numpy() == 0).all()
        mean[SHARED_SCHEMA_COLS].to_parquet(OUT / 'gnn_edge_predictions.parquet', index=False)
        simulated = pd.read_parquet(OUT / 'gnn_simulated_scenarios.parquet')
        targets = pd.concat([simulated, pd.read_parquet(scenario_path)], ignore_index=True)
        paired = mean.merge(targets, on=keys, suffixes=('_gnn', '_solver'), validate='one_to_one')
        synthetic_rows = []
        for target in ['oil_volume_bbl', 'tanker_transits', 'utilization', 'delay_days']:
            compound = paired[paired.scenario_id.str.contains('compound')]
            y, prediction = compound[f'{target}_solver'], compound[f'{target}_gnn']
            baseline_values = compound.edge_id.map(baseline[target])
            error, base_error = prediction - y, baseline_values - y
            rmse = float(np.sqrt(np.mean(error ** 2)))
            baseline_rmse = float(np.sqrt(np.mean(base_error ** 2)))
            synthetic_rows.append(dict(target=target, n=len(compound), mae=float(np.abs(error).mean()),
                                       rmse=rmse, wape=float(np.abs(error).sum() / np.abs(y).sum()),
                                       unchanged_baseline_rmse=baseline_rmse,
                                       rmse_skill_vs_unchanged_baseline=1 - rmse / baseline_rmse,
                                       source='gnn', evaluation='20 unseen compound scenarios; solver imitation only'))
        pd.DataFrame(synthetic_rows).to_csv(OUT / 'gnn_synthetic_evaluation_report.csv', index=False)
    print(report.to_string(index=False))


if __name__ == '__main__':
    main()
