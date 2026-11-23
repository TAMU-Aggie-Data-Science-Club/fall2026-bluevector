"""
evaluate_model.py

Error of the GNN's predictions. The model outputs the 22 column edge table
(source == "gnn"); we compare it to a truth table in the same format
(observed / simulated / modeled) by joining on (scenario_id, edge_id).

Only the state and change columns are scored. The static ones (length_nm,
capacity_weekly, free_flow_days) and the id columns don't matter.

    python evaluate_model.py --pred gnn_predictions.parquet --truth truth_edges.parquet \
        --splits tensors/splits.json --subset heldout --out eval/

"""
import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

KEYS = ["scenario_id", "edge_id"]
CONTRACT_COLUMNS = [
    "scenario_id", "edge_id", "from_node", "to_node", "edge_type",
    "chokepoint_portid", "searoute_passage", "length_nm",
    "throttle_factor", "oil_volume_bbl", "tanker_transits", "capacity_weekly",
    "utilization", "free_flow_days", "delay_days", "anomaly_z",
    "cost_per_barrel", "stranded_barrels",
    "delta_volume_bbl", "delta_transits", "share_rerouted", "source",
]
PRIMARY = ["oil_volume_bbl", "tanker_transits", "utilization", "delay_days"]    # what the model is trained on
SECONDARY = ["anomaly_z", "cost_per_barrel", "stranded_barrels",
             "delta_volume_bbl", "delta_transits", "share_rerouted"]
SCORED = PRIMARY + SECONDARY
DELTA_COLS = ["delta_volume_bbl", "delta_transits"]
# at baseline these are zero by definition (used for the "nothing changes" guess)
ZERO_AT_BASELINE = DELTA_COLS + ["share_rerouted", "stranded_barrels"]

EVENT_TAGS = {"ever_given_2021": "ever_given", "panama_drought_2023": "panama_drought",
              "red_sea_2024": "red_sea"}


def read_table(path):
    path = Path(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def check_columns(df, name):
    assert list(df.columns) == CONTRACT_COLUMNS, (
        f"{name} table doesn't match the 22 column contract. "
        f"missing: {[c for c in CONTRACT_COLUMNS if c not in df.columns]}, "
        f"extra: {[c for c in df.columns if c not in CONTRACT_COLUMNS]} (order matters too)")


def get_event(scenario_id, event_map):
    if event_map and scenario_id in event_map:
        return event_map[scenario_id]
    s = scenario_id.lower()
    for event, tag in EVENT_TAGS.items():
        if tag in s:
            return event
    return "other"


def ids_in_subset(splits_path, subset):
    """scenario ids + week strings for a split, from build_tensors' splits.json"""
    splits = json.loads(Path(splits_path).read_text())
    ids = set()
    for stage in ["pretrain", "finetune"]:
        ids |= {str(x) for x in splits.get(stage, {}).get(subset, [])}
    return ids


def get_metrics(y, p, is_delta=False):
    ok = np.isfinite(y) & np.isfinite(p)
    y, p = y[ok], p[ok]
    res = {"n": len(y), "mae": np.nan, "rmse": np.nan, "bias": np.nan,
           "wape": np.nan, "r2": np.nan, "truth_mean": np.nan}
    if is_delta:
        res["sign_acc"] = np.nan
    if len(y) == 0:
        return res

    err = p - y
    abs_truth = np.abs(y).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    res["mae"] = np.abs(err).mean()
    res["rmse"] = np.sqrt((err ** 2).mean())
    res["bias"] = err.mean()                    # pred - truth
    if abs_truth > 0:
        res["wape"] = np.abs(err).sum() / abs_truth
    if ss_tot > 0:
        res["r2"] = 1 - (err ** 2).sum() / ss_tot
    res["truth_mean"] = y.mean()

    if is_delta:
        # did we get the direction right, only counting edges that actually moved
        moved = np.abs(y) > 0.01 * y.std()
        if moved.any():
            res["sign_acc"] = (np.sign(p[moved]) == np.sign(y[moved])).mean()
    return res


def metric_table(j, group_col, has_naive):
    rows = []
    if group_col is None:
        groups = [("all", j)]
    else:
        groups = list(j.groupby(group_col))
    for name, d in groups:
        for col in SCORED:
            y = d[col + "_true"].to_numpy(float)
            p = d[col + "_pred"].to_numpy(float)
            m = get_metrics(y, p, is_delta=col in DELTA_COLS)
            if has_naive:
                naive_mae = get_metrics(y, d[col + "_naive"].to_numpy(float))["mae"]
                m["mae_naive"] = naive_mae
                m["skill_mae"] = 1 - m["mae"] / naive_mae if naive_mae and naive_mae > 0 else np.nan
            tier = "primary" if col in PRIMARY else "secondary"
            rows.append({"group": name, "column": col, "tier": tier, **m})
    return pd.DataFrame(rows)


def consistency_checks(pred, baseline):
    """are the predictions even internally sensible?"""
    res = {"n_pred_rows": len(pred)}
    res["nonfinite_rate"] = {c: float((~np.isfinite(pred[c].astype(float))).mean()) for c in SCORED}

    has_cap = pred["capacity_weekly"] > 0
    util_err = (pred.tanker_transits[has_cap] / pred.capacity_weekly[has_cap] - pred.utilization[has_cap]).abs()
    res["utilization_max_abs_err_vs_transits_over_capacity"] = float(util_err.max()) if len(util_err) else None
    res["utilization_inconsistent_rate"] = float((util_err > 1e-3).mean()) if len(util_err) else None

    res["share_rerouted_below_0_rate"] = float((pred.share_rerouted < 0).mean())
    res["share_rerouted_above_1_rate"] = float((pred.share_rerouted > 1).mean())
    for c in ["oil_volume_bbl", "tanker_transits", "delay_days", "utilization"]:
        res[f"{c}_negative_rate"] = float((pred[c] < 0).mean())
    res["throttle_outside_0_1_rate"] = float(((pred.throttle_factor < 0) | (pred.throttle_factor > 1)).mean())
    res["source_values"] = sorted(map(str, pred["source"].unique()))

    if baseline is not None:
        b = baseline.set_index("edge_id")
        base_vol = pred["edge_id"].map(b["oil_volume_bbl"])
        base_tr = pred["edge_id"].map(b["tanker_transits"])
        dv_err = (pred.oil_volume_bbl - base_vol - pred.delta_volume_bbl).abs()
        dt_err = (pred.tanker_transits - base_tr - pred.delta_transits).abs()
        res["delta_volume_inconsistent_rate"] = float((dv_err > 1e-3 * (base_vol.abs() + 1)).mean())
        res["delta_transits_inconsistent_rate"] = float((dt_err > 1e-3 * (base_tr.abs() + 1)).mean())
        # share_rerouted should be (baseline - volume) / baseline, floored at 0
        expected = ((base_vol - pred.oil_volume_bbl) / base_vol.replace(0, np.nan)).clip(lower=0).fillna(0)
        res["share_rerouted_inconsistent_rate"] = float(((expected - pred.share_rerouted).abs() > 1e-3).mean())
    return res


def fmt(v):
    if isinstance(v, (float, np.floating)):
        return "" if not np.isfinite(v) else f"{v:.4g}"
    return str(v)


def md_table(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(fmt(v) for v in row) + " |")
    return "\n".join(lines)


def write_report(path, args, cover, overall, by_event, by_type, cons, has_naive):
    cols = ["column", "n", "mae", "rmse", "bias", "wape", "r2"]
    if has_naive:
        cols.append("skill_mae")
    prim = overall["tier"] == "primary"

    lines = ["# Model performance", "",
             f"- predictions: `{args.pred}`",
             f"- truth: `{args.truth}`",
             f"- subset: `{args.subset}`" + (f" (from `{args.splits}`)" if args.splits else ""), "",
             "## Coverage",
             f"- truth rows scored: {cover['truth_rows']}",
             f"- truth rows with no prediction: {cover['missing_pred']} ({cover['missing_pred_rate']:.2%})",
             f"- prediction rows with no truth (ignored): {cover['extra_pred']}", "",
             "## Overall, primary targets", md_table(overall[prim][cols]), "",
             "## Overall, secondary columns", md_table(overall[~prim][cols]), "",
             "## By event, primary targets",
             md_table(by_event[by_event.tier == "primary"][["group"] + cols]), "",
             "## By edge type, primary targets",
             md_table(by_type[by_type.tier == "primary"][["group"] + cols]), "",
             "## Consistency checks on the predictions", "```", json.dumps(cons, indent=2), "```", "",
             "bias = mean(pred - truth), wape = sum|err| / sum|truth|."]
    if has_naive:
        lines.append("skill_mae = 1 - MAE / MAE of guessing the baseline (above 0 means we beat it).")
    else:
        lines.append("No baseline scenario in the truth table so there are no skill scores.")
    lines.append("Rows with no truth value are skipped. Observed truth is usually chokepoint edges only.")
    Path(path).write_text("\n".join(lines))


def run(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pred = read_table(args.pred)
    truth = read_table(args.truth)
    check_columns(pred, "prediction")
    check_columns(truth, "truth")
    for df in (pred, truth):
        df["scenario_id"] = df["scenario_id"].astype(str)
        df["edge_id"] = df["edge_id"].astype(str)
    assert not pred.duplicated(KEYS).any(), "duplicate (scenario_id, edge_id) in predictions"
    assert not truth.duplicated(KEYS).any(), "duplicate (scenario_id, edge_id) in truth"
    if not (pred["source"] == "gnn").all():
        print("WARNING: some prediction rows have source != 'gnn'")

    event_map = None
    if args.event_map:
        em = pd.read_csv(args.event_map)
        event_map = dict(zip(em["scenario_id"].astype(str), em["event"].astype(str)))

    # baseline run, used for the "nothing changes" guess and the delta checks
    baseline = None
    if args.baseline_id in set(truth["scenario_id"]):
        baseline = truth[truth["scenario_id"] == args.baseline_id]
    else:
        print(f"note: no '{args.baseline_id}' scenario in truth, skipping skill scores")

    # only score the split we asked for, so we don't accidentally grade on training data
    if args.splits:
        ids = ids_in_subset(args.splits, args.subset)
        assert ids, f"nothing listed for '{args.subset}' in {args.splits}"
        truth = truth[truth["scenario_id"].isin(ids)]
        assert len(truth), "no truth rows left, scenario_id / week keys don't match splits.json"
    else:
        print("note: no --splits, scoring every row in the truth table (training data too)")

    # join truth with predictions
    t = truth[KEYS + ["edge_type", "chokepoint_portid", "source"] + SCORED]
    t = t.rename(columns={c: c + "_true" for c in SCORED}).rename(columns={"source": "truth_source"})
    p = pred[KEYS + SCORED].rename(columns={c: c + "_pred" for c in SCORED})
    j = t.merge(p, on=KEYS, how="left")

    pred_keys = set(zip(pred.scenario_id, pred.edge_id))
    truth_keys = set(zip(truth.scenario_id, truth.edge_id))
    cover = {
        "truth_rows": len(t),
        "missing_pred": int(j[[c + "_pred" for c in SCORED]].isna().all(axis=1).sum()),
        "extra_pred": len(pred_keys - truth_keys),
    }
    cover["missing_pred_rate"] = cover["missing_pred"] / max(cover["truth_rows"], 1)

    j["event"] = j["scenario_id"].map(lambda s: get_event(s, event_map))
    j["chokepoint"] = np.where(j["edge_type"] == "chokepoint", j["chokepoint_portid"].astype(str), "")

    has_naive = baseline is not None
    if has_naive:
        b = baseline.set_index("edge_id")
        for c in SCORED:
            j[c + "_naive"] = 0.0 if c in ZERO_AT_BASELINE else j["edge_id"].map(b[c])

    overall = metric_table(j, None, has_naive)
    by_event = metric_table(j, "event", has_naive)
    by_type = metric_table(j, "edge_type", has_naive)
    by_source = metric_table(j, "truth_source", has_naive)
    by_chokepoint = metric_table(j[j["chokepoint"] != ""], "chokepoint", has_naive)
    cons = consistency_checks(pred, baseline)

    overall.to_csv(out / "metrics_overall.csv", index=False)
    by_group = pd.concat([
        by_event.assign(group_type="event"),
        by_type.assign(group_type="edge_type"),
        by_source.assign(group_type="truth_source"),
        by_chokepoint.assign(group_type="chokepoint"),
    ])
    by_group.to_csv(out / "metrics_by_group.csv", index=False)
    (out / "consistency.json").write_text(json.dumps(cons, indent=2))
    write_report(out / "report.md", args, cover, overall, by_event, by_type, cons, has_naive)

    show = ["column", "n", "mae", "rmse", "wape", "r2"] + (["skill_mae"] if has_naive else [])
    print(overall[overall.tier == "primary"][show].to_string(index=False, float_format=lambda v: f"{v:.4g}"))
    print(f"\ncoverage: {cover}")
    print(f"saved metrics_overall.csv, metrics_by_group.csv, consistency.json, report.md to {out}/")


def self_test():
    """fake truth + a 'model' that is truth with 10% noise, just to check the pipeline runs"""
    rng = np.random.default_rng(0)
    n_edges = 40
    edge_ids = [f"e{i}" for i in range(n_edges)]
    edge_type = ["chokepoint"] * 5 + ["lane"] * (n_edges - 5)
    chk_id = [f"chk{i}" for i in range(5)] + [""] * (n_edges - 5)
    capacity = rng.uniform(50, 500, n_edges)
    base_transits = rng.uniform(10, 100, n_edges)

    pieces = []
    for sid in ["baseline", "sim_1", "sim_2", "red_sea_2024_a", "ever_given_2021"]:
        throttle = np.ones(n_edges) if sid == "baseline" else rng.uniform(0.3, 1, n_edges)
        transits = base_transits * throttle
        volume = transits * 1e5
        base_volume = base_transits * 1e5
        pieces.append(pd.DataFrame({
            "scenario_id": sid, "edge_id": edge_ids, "from_node": "a", "to_node": "b",
            "edge_type": edge_type, "chokepoint_portid": chk_id, "searoute_passage": "",
            "length_nm": 100.0, "throttle_factor": throttle, "oil_volume_bbl": volume,
            "tanker_transits": transits, "capacity_weekly": capacity,
            "utilization": transits / capacity, "free_flow_days": 1.0,
            "delay_days": (1 - throttle) * 5, "anomaly_z": (throttle - 1) * 3,
            "cost_per_barrel": 1 + (1 - throttle), "stranded_barrels": 0.0,
            "delta_volume_bbl": volume - base_volume, "delta_transits": transits - base_transits,
            "share_rerouted": np.clip((base_volume - volume) / base_volume, 0, None),
            "source": "simulated"}))
    truth = pd.concat(pieces, ignore_index=True)

    pred = truth.copy()
    for c in SCORED:
        pred[c] = pred[c] * rng.normal(1, 0.1, len(pred)) + rng.normal(0, 1e-3, len(pred))
    pred["utilization"] = pred.tanker_transits / pred.capacity_weekly
    pred["share_rerouted"] = pred.share_rerouted.clip(lower=0)
    pred["source"] = "gnn"

    with tempfile.TemporaryDirectory() as d:
        truth.to_csv(f"{d}/truth.csv", index=False)
        pred.to_csv(f"{d}/pred.csv", index=False)
        splits = {"pretrain": {"heldout": ["red_sea_2024_a", "ever_given_2021"]}, "finetune": {"heldout": []}}
        Path(f"{d}/splits.json").write_text(json.dumps(splits))
        args = argparse.Namespace(pred=f"{d}/pred.csv", truth=f"{d}/truth.csv", splits=f"{d}/splits.json",
                                  subset="heldout", baseline_id="baseline", event_map=None, out=f"{d}/out")
        run(args)
        m = pd.read_csv(f"{d}/out/metrics_overall.csv")
        assert (m.loc[m.column == "tanker_transits", "n"] == 2 * n_edges).all()
        assert (m.loc[m.tier == "primary", "wape"] < 0.2).all(), "10% noise should give wape around 0.08"
        groups = pd.read_csv(f"{d}/out/metrics_by_group.csv")["group"].values
        assert "ever_given_2021" in groups
    print("self-test passed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="gnn_predictions.parquet", help="model output (22 column table)")
    ap.add_argument("--truth", default="truth_edges.parquet", help="observed/simulated table (22 columns)")
    ap.add_argument("--splits", default=None, help="splits.json from build_tensors.py")
    ap.add_argument("--subset", default="heldout", choices=["train", "val", "heldout"])
    ap.add_argument("--baseline-id", default="baseline")
    ap.add_argument("--event-map", default=None, help="optional csv with scenario_id,event")
    ap.add_argument("--out", default="eval")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
    else:
        run(args)


if __name__ == "__main__":
    main()