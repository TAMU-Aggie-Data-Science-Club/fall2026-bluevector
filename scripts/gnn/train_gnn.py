"""Train simulated-plus-real and PortWatch-only GNNs with pre-event backtests.

Run: python -m scripts.gnn.train_gnn --epochs 180 --seeds 7 19 43
All datasets, checkpoints, predictions and run manifests stay under data/processed.
The temporal experiment predicts next-week ALL-tanker chokepoint counts, not
unobserved crude volumes. Simulator pretraining is a separate, retrospective
prior: its OD/calibration vintage must not be described as a historical forecast.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import random
import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader
from scripts.gnn.gnn_model import EdgeGNN
from scripts.gnn.gnn_dataset import ShippingGraphDataset, collate_disjoint

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/processed'
EVENTS = {
    'ever_given_2021': ('2021-01-01', '2021-03-19', '2021-04-09'),
    'panama_drought_2023': ('2023-05-01', '2023-08-04', '2023-12-29'),
    'red_sea_2024': ('2023-08-01', '2024-03-01', '2024-06-28'),
}


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(2)


def graph_inputs():
    edges = pd.read_csv(ROOT / 'config/edges.csv').sort_values('edge_id').reset_index(drop=True)
    nodes = pd.read_csv(ROOT / 'config/nodes.csv').sort_values('node_id').reset_index(drop=True)
    ids = {v: i for i, v in enumerate(nodes.node_id)}
    ei = torch.tensor([[ids[v] for v in edges.from_node], [ids[v] for v in edges.to_node]])
    xn = np.zeros((len(nodes), 6), np.float32)
    xn[:, 0] = nodes.node_id.str.startswith('port_')
    xn[:, 1] = 1 - xn[:, 0]
    for j, name, divisor in [(2, 'lat', 90), (3, 'lon', 180)]:
        if name in nodes:
            xn[:, j] = pd.to_numeric(nodes[name], errors='raise') / divisor
    # No solver-derived OD demands enter the real-only experiment.
    return edges, torch.tensor(xn), ei


def weekly_counts(edges):
    path = OUT / 'chokepoint_weekly_attributes.parquet'
    if not path.exists():
        from scripts.graph.build_edge_attributes import load_chokepoint_daily, load_crude_shares, build_weekly_chokepoint_series
        daily = load_chokepoint_daily()
        weekly, _ = build_weekly_chokepoint_series(daily, edges[edges.edge_type.eq('chokepoint')], load_crude_shares())
        OUT.mkdir(parents=True, exist_ok=True)
        weekly.to_parquet(path, index=False)
    raw = pd.read_parquet(OUT / 'chokepoint_weekly_attributes.parquet')
    raw['week_ending'] = pd.to_datetime(raw.week_ending)
    if raw.duplicated(['week_ending', 'edge_id']).any():
        raise ValueError('Duplicate chokepoint-week labels')
    if 'n_days_observed' in raw:
        raw = raw[raw.n_days_observed == 7]  # partial weeks are not weekly totals
    counts = raw.pivot(index='week_ending', columns='edge_id', values='weekly_tanker_transits')
    counts = counts.reindex(pd.date_range(counts.index.min(), counts.index.max(), freq='W-FRI'))
    return counts.reindex(columns=edges.edge_id).astype(float)


def make_temporal(edges, xn, ei, counts, event):
    val_start, test_start, test_end = map(pd.Timestamp, EVENTS[event])
    # Red Sea validation ends before the disruption; no H1 labels enter this experiment.
    val_end = pd.Timestamp('2023-10-31') if event == 'red_sea_2024' else test_start - pd.Timedelta(days=1)
    if event == 'price_embeddings':
        val_end = pd.Timestamp('2022-12-30')
    train_rows = counts.index < val_start
    means = counts.loc[train_rows].mean().fillna(1).clip(lower=1).to_numpy(np.float32)
    lag = counts.shift(1)
    ma4 = counts.shift(1).rolling(4, min_periods=4).mean()
    seasonal = counts.shift(52).fillna(counts.shift(1).expanding().mean())
    cp = edges.edge_type.eq('chokepoint').to_numpy()
    # Scaling is fitted solely before this event's validation start.
    length = edges.length_nm.to_numpy(np.float32)
    static = np.zeros((len(edges), 14), np.float32)
    for j, typ in enumerate(['lane', 'chokepoint', 'connector']):
        static[:, j] = edges.edge_type.eq(typ)
    static[:, 3] = length / 10000
    static[:, 4] = length / (13 * 24 * 30)
    static[:, 5] = means / 1000
    # Current tariffs are excluded from historical forecasts.
    static[:, 6] = 0
    static[:, 7] = 1
    static[:, 9] = means / 1000
    x, y, masks, dates = [], [], [], []
    for i, date in enumerate(counts.index):
        available = cp & np.isfinite(lag.iloc[i]) & np.isfinite(ma4.iloc[i]) & np.isfinite(seasonal.iloc[i])
        mask = available & np.isfinite(counts.iloc[i])
        if not mask.any():
            continue
        feat = static.copy()
        for j, table in [(11, lag), (12, ma4), (13, seasonal)]:
            feat[:, j] = np.nan_to_num(table.iloc[i].to_numpy(np.float32) / means)
        target = np.zeros((len(edges), 4), np.float32)
        target[:, 1] = np.nan_to_num(counts.iloc[i].to_numpy(np.float32) / means)
        loss_mask = np.zeros_like(target, dtype=bool)
        loss_mask[:, 1] = mask
        x.append(feat); y.append(target); masks.append(loss_mask); dates.append(date)
    dates = pd.DatetimeIndex(dates)
    x, y, masks = map(torch.tensor, [np.stack(x), np.stack(y), np.stack(masks)])
    def subset(idx):
        idx = np.flatnonzero(idx)
        return TemporalDataset([str(d.date()) for d in dates[idx]], ['observed'] * len(idx), ei,
                               xn.unsqueeze(0).repeat(len(idx), 1, 1), x[idx], y[idx], masks[idx])
    sets = [subset(dates < val_start), subset((dates >= val_start) & (dates <= val_end)),
            subset((dates >= test_start) & (dates <= test_end)), subset(np.ones(len(dates), dtype=bool))]
    assert all(len(s) for s in sets), 'Empty temporal split'
    return sets, means, lag, seasonal


class TemporalDataset(ShippingGraphDataset):
    # Same batch protocol as G1, with three additional, explicitly lagged inputs.
    def __init__(self, ids, sources, ei, xn, xe, y, mask):
        self.scenario_ids, self.sources, self.edge_index = ids, sources, ei
        self.x_node, self.x_edge, self.y_edge, self.loss_mask = xn, xe, y, mask
        assert xe.shape[-1] == 14 and y.shape == mask.shape
        assert all(torch.isfinite(t).all() for t in [xn, xe, y])


def fit(model, train, val, epochs, seed):
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(train, batch_size=32, shuffle=True, generator=generator, collate_fn=collate_disjoint)
    valid = next(iter(DataLoader(val, batch_size=len(val), collate_fn=collate_disjoint)))
    opt = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=0.0001)
    best, state, patience, trace = float('inf'), None, 0, []
    for epoch in range(epochs):
        model.train(); losses = []
        for b in loader:
            pred, _ = model(b['x_node'], b['x_edge'], b['edge_index'])
            pred = pred.reshape_as(b['y_edge'])
            mask = b['loss_mask']
            loss = torch.nn.functional.smooth_l1_loss(pred[mask], b['y_edge'][mask])
            opt.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
            opt.step(); losses.append(loss.item())
        model.eval()
        with torch.no_grad():
            pred, _ = model(valid['x_node'], valid['x_edge'], valid['edge_index'])
            pred = pred.reshape_as(valid['y_edge'])
            score = torch.nn.functional.smooth_l1_loss(pred[valid['loss_mask']], valid['y_edge'][valid['loss_mask']]).item()
        trace.append({'epoch': epoch + 1, 'train_loss': float(np.mean(losses)), 'validation_loss': score, 'source': 'modeled'})
        if score < best - 1e-6:
            best, state, patience = score, copy.deepcopy(model.state_dict()), 0
        else:
            patience += 1
        if patience >= 25:
            break
    model.load_state_dict(state)
    return trace


def pretrain(model, edges, xn, ei, seed, epochs):
    sim = pd.read_parquet(OUT / 'gnn_simulated_scenarios.parquet')
    events = pd.read_parquet(OUT / 'scenario_edge_flows.parquet')
    baseline = events.query("scenario_id == 'baseline'").set_index('edge_id').reindex(edges.edge_id)
    base_transits = baseline.tanker_transits.to_numpy(np.float32).clip(1)
    names = sorted(sim.scenario_id.unique())
    singles = [n for n in names if 'compound' not in n and n not in EVENTS]
    order = np.random.default_rng(seed).permutation(singles)
    nv = max(1, len(order) // 5)
    train_names, val_names = set(order[nv:]), set(order[:nv])
    training = sim[sim.scenario_id.isin(train_names)]
    scale = np.array([max(training.oil_volume_bbl.quantile(.95), 1), 1,
                      max(training.utilization.quantile(.95), 1), max(training.delay_days.quantile(.95), 1)], np.float32)
    xs, ys, names, tables = [], [], [], []
    combined = pd.concat([sim, events[~events.scenario_id.eq('baseline')]], ignore_index=True)
    for name, group in combined.groupby('scenario_id'):
        g = group.set_index('edge_id').reindex(edges.edge_id)
        x = np.zeros((len(edges), 14), np.float32)
        for j, typ in enumerate(['lane', 'chokepoint', 'connector']):
            x[:, j] = edges.edge_type.eq(typ)
        x[:, 3] = edges.length_nm / 10000
        x[:, 4] = edges.length_nm / (13 * 24 * 30)
        x[:, 5] = base_transits / 1000
        x[:, 6] = edges.get('toll_usd_per_bbl', pd.Series(0., index=edges.index)).fillna(0)
        x[:, 7] = g.throttle_factor
        x[:, 8] = baseline.oil_volume_bbl.to_numpy(np.float32) / scale[0]
        x[:, 9] = base_transits / 1000
        x[:, 10] = baseline.utilization.to_numpy(np.float32)
        x[:, 11:14] = 1
        y = g[['oil_volume_bbl', 'tanker_transits', 'utilization', 'delay_days']].to_numpy(np.float32) / scale
        y[:, 1] /= base_transits
        xs.append(x); ys.append(y); names.append(name); tables.append(g)
    def subset(selected):
        idx = [i for i, n in enumerate(names) if n in selected]
        xe, y = torch.tensor(np.stack(xs)[idx]), torch.tensor(np.stack(ys)[idx])
        return TemporalDataset([names[i] for i in idx], ['simulated'] * len(idx), ei,
                               xn.unsqueeze(0).repeat(len(idx), 1, 1), xe, y, torch.ones_like(y, dtype=torch.bool))
    trace = fit(model, subset(train_names), subset(val_names), epochs, seed)
    torch.save({'state_dict': model.state_dict(), 'target_scale': scale, 'baseline_transits': base_transits,
                'edge_ids': edges.edge_id.tolist(), 'train_scenarios': sorted(train_names),
                'validation_scenarios': sorted(val_names)}, OUT / f'gnn_simulator_seed{seed}.pt')
    reports, exports = [], []
    model.eval()
    with torch.no_grad():
        for name, x, table in zip(names, xs, tables):
            if name in train_names or name in val_names:
                continue
            pred, _ = model(xn, torch.tensor(x), ei)
            values = pred.numpy() * scale
            values[:, 1] *= base_transits
            actual = table[['oil_volume_bbl', 'tanker_transits', 'utilization', 'delay_days']].to_numpy()
            for j, target in enumerate(['oil_volume_bbl', 'tanker_transits', 'utilization', 'delay_days']):
                error = values[:, j] - actual[:, j]
                reports.append(dict(scenario_id=name, seed=seed, target=target, n=len(edges),
                                    mae=float(np.abs(error).mean()), rmse=float(np.sqrt(np.mean(error ** 2))),
                                    source='gnn', evaluation='solver imitation; not real validation'))
            # A separate scenario surrogate, not the real-only temporal forecaster.
            # Reconcile cargo and utilization identities; no directional conservation claim.
            out = table.reset_index().copy()
            out['tanker_transits'] = values[:, 1]
            cargo = np.divide(baseline.oil_volume_bbl.to_numpy(), baseline.tanker_transits.to_numpy(),
                              out=np.full(len(edges), 1e6), where=baseline.tanker_transits.to_numpy() > 0)
            out['oil_volume_bbl'] = values[:, 1] * cargo
            out['utilization'] = np.divide(values[:, 1], out.capacity_weekly.to_numpy(),
                                         out=np.zeros(len(edges)), where=out.capacity_weekly.to_numpy() > 0)
            out['delay_days'] = np.where(out.throttle_factor.to_numpy() > 0, values[:, 3], 0)
            params = json.loads((ROOT / 'config/flow_params.json').read_text())
            out['cost_per_barrel'] = (out.free_flow_days + out.delay_days) * params['daily_tanker_rate'] / cargo + edges.toll_usd_per_bbl.fillna(0).to_numpy()
            out['delta_volume_bbl'] = out.oil_volume_bbl.to_numpy() - baseline.oil_volume_bbl.to_numpy()
            out['delta_transits'] = values[:, 1] - baseline.tanker_transits.to_numpy()
            out['share_rerouted'] = np.maximum(0, -out.delta_volume_bbl.to_numpy() / baseline.oil_volume_bbl.to_numpy().clip(1))
            # Aggregate connector volume counts each routed barrel twice (load + discharge).
            demand = baseline.loc[edges.edge_type.to_numpy() == 'connector', 'oil_volume_bbl'].sum() / 2
            routed = out.loc[out.edge_type.eq('connector'), 'oil_volume_bbl'].sum() / 2
            out['stranded_barrels'] = max(0., demand - routed)
            out['anomaly_z'] = 0.  # unavailable for unobserved routes; not a measurement
            out['source'] = 'gnn'
            out['seed'] = seed
            exports.append(out)
    pd.DataFrame(reports).to_csv(OUT / f'gnn_synthetic_evaluation_seed{seed}.csv', index=False)
    pd.concat(exports).to_parquet(OUT / f'gnn_scenario_predictions_seed{seed}.parquet', index=False)
    return trace


def train_price_embeddings(edges, xn, ei, counts, epochs):
    # The representation is frozen before every price calibration and test week.
    EVENTS['price_embeddings'] = ('2022-08-05', '2023-01-06', '2024-06-28')
    (train, val, _, history), means, _, _ = make_temporal(edges, xn, ei, counts, 'price_embeddings')
    del EVENTS['price_embeddings']
    seed_all(7)
    model = EdgeGNN(edge_in=14, hidden=32)
    trace = fit(model, train, val, epochs, 7)
    model.eval(); rows = []
    with torch.no_grad():
        for sample in history:
            _, emb = model(sample['x_node'], sample['x_edge'], ei)
            rows.append(dict(week_or_scenario_id=pd.Timestamp(sample['scenario_id']),
                             **{f'gnn_emb_{i}': float(emb[i]) for i in range(8)}))
    pd.DataFrame(rows).assign(source='gnn').to_parquet(OUT / 'gnn_price_embeddings.parquet', index=False)
    pd.DataFrame(trace).to_csv(OUT / 'gnn_price_embedding_loss.csv', index=False)
    torch.save({'state_dict': model.state_dict(), 'transit_scale': means,
                'train_end': train.scenario_ids[-1], 'validation_end': val.scenario_ids[-1]},
               OUT / 'gnn_price_embedding.pt')
    print('Price embeddings frozen after validation ending', val.scenario_ids[-1], flush=True)


def check_temporal_splits(edges, xn, ei, counts):
    for event in EVENTS:
        original, scale, _, _ = make_temporal(edges, xn, ei, counts, event)
        altered = counts.copy()
        altered.loc[altered.index >= pd.Timestamp(EVENTS[event][1])] *= 1000
        changed, changed_scale, _, _ = make_temporal(edges, xn, ei, altered, event)
        assert np.array_equal(scale, changed_scale)
        for a, b in zip(original[:2], changed[:2]):
            assert torch.equal(a.x_edge, b.x_edge) and torch.equal(a.y_edge, b.y_edge)
        assert original[0].scenario_ids[-1] < original[1].scenario_ids[0] < original[2].scenario_ids[0]
    print('PASS: future observations cannot alter training, validation or normalization')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--epochs', type=int, default=180)
    ap.add_argument('--pretrain-epochs', type=int, default=150)
    ap.add_argument('--mode', choices=['both', 'real_only'], default='both')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--prepare-only', action='store_true')
    ap.add_argument('--price-embeddings-only', action='store_true')
    ap.add_argument('--seeds', type=int, nargs='+', default=[7, 19, 43])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    edges, xn, ei = graph_inputs(); counts = weekly_counts(edges)
    if args.check:
        check_temporal_splits(edges, xn, ei, counts)
        return
    if args.prepare_only:
        print('Prepared observed weekly counts:', counts.shape)
        return
    train_price_embeddings(edges, xn, ei, counts, args.epochs)
    if args.price_embeddings_only:
        return
    rows, manifests = [], []
    for seed in args.seeds:
        seed_all(seed)
        pretrained = EdgeGNN(edge_in=14, hidden=32)
        if args.mode == 'both':
            sim_trace = pretrain(pretrained, edges, xn, ei, seed, args.pretrain_epochs)
            pd.DataFrame(sim_trace).to_csv(OUT / f'gnn_pretrain_seed{seed}.csv', index=False)
        for event in EVENTS:
            (train, val, test, _), means, lag, seasonal = make_temporal(edges, xn, ei, counts, event)
            for mode in (['real_only', 'simulated_plus_real'] if args.mode == 'both' else ['real_only']):
                seed_all(seed)
                model = EdgeGNN(edge_in=14, hidden=32)
                if mode == 'simulated_plus_real':
                    model.load_state_dict(pretrained.state_dict())
                trace = fit(model, train, val, args.epochs, seed)
                pd.DataFrame(trace).to_csv(OUT / f'gnn_{mode}_{event}_seed{seed}_loss.csv', index=False)
                torch.save({'state_dict': model.state_dict(), 'transit_scale': means, 'edge_ids': edges.edge_id.tolist()}, OUT / f'gnn_{mode}_{event}_seed{seed}.pt')
                model.eval()
                with torch.no_grad():
                    for sample in test:
                        pred, _ = model(sample['x_node'], sample['x_edge'], ei)
                        date = pd.Timestamp(sample['scenario_id'])
                        for k in torch.where(sample['loss_mask'][:, 1])[0].tolist():
                            rows.append(dict(event=event, model=mode, seed=seed, week_ending=date,
                                             edge_id=edges.edge_id.iloc[k], actual=float(counts.loc[date].iloc[k]),
                                             prediction=float(pred[k, 1] * means[k]), persistence=float(lag.loc[date].iloc[k]),
                                             seasonal=float(seasonal.loc[date].iloc[k]), source='gnn'))
                manifests.append(dict(event=event, model=mode, seed=seed, train_end=train.scenario_ids[-1],
                                      validation_start=val.scenario_ids[0], validation_end=val.scenario_ids[-1],
                                      test_start=test.scenario_ids[0], test_end=test.scenario_ids[-1],
                                      best_validation_loss=min(t['validation_loss'] for t in trace), epochs=len(trace), source='gnn', training_sources='observed' if mode == 'real_only' else 'simulated,observed'))
                print(event, mode, seed, 'validation loss', manifests[-1]['best_validation_loss'], flush=True)
        pd.DataFrame(rows).to_parquet(OUT / 'gnn_temporal_predictions.parquet', index=False)
        pd.DataFrame(manifests).to_csv(OUT / 'gnn_run_manifest.csv', index=False)
    from scripts.gnn.evaluate_gnn import main as evaluate
    evaluate()


if __name__ == '__main__':
    main()
