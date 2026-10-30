"""B0-B5 walk-forward spike classifiers and baselines on the point-in-time table (vpp.spike.features).

Per task (days_ahead 0 = lead 0-22.5h, 1 = lead 23-46.5h) and month M from OOS_START: train on target days
before M - EMBARGO_DAYS (never after TRAIN_END), predict the issues of M. The models only differ in their
feature groups; LightGBM settings are those of the existing classifier (vpp.spike_risk), not tuned.
  B0  constant: the spike rate of the training rows
  B1  time           B2  + price history     B3  + system (AEMO pre-dispatch quantities, ST PASA)
  B4  + outages and revisions                B5_* B4 + one more group; B5_all all of them
Every model also gets a calibrated probability: Platt scaling fit on its own out-of-sample predictions of
the previous CAL_MONTHS months (target days before M - EMBARGO_DAYS, not after TRAIN_END).

Baselines that are scores, not probabilities (ranked only): AEMO's pre-dispatch energy price in the same
run, same-slot price of the last complete day, and the existing price regression and spike classifier,
re-run through their own code (vpp.forecast / vpp.spike_risk: issued "08:15" with the 08:00 run and
day1/day2 weather, so they see slightly more than this protocol allows).

Run:  python -m vpp.spike.models [--delayed]   -> data/processed/spike_predictions[_delayed].parquet
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from vpp.data import PROCESSED
from vpp.spike import protocol as pc
from vpp.spike.features import GROUPS
from vpp.spike_risk import CLF_PARAMS

LADDER = {
    "B1": ["time"], "B2": ["time", "price"], "B3": ["time", "price", "system"], "B4": ["time", "price", "system", "outage"],
    "B5_weather": ["time", "price", "system", "outage", "weather"],
    "B5_aemo_price": ["time", "price", "system", "outage", "aemo_price"],
    "B5_aemo_error": ["time", "price", "system", "outage", "aemo_error"],
    "B5_all": ["time", "price", "system", "outage", "weather", "aemo_price", "aemo_error"],
}
# Round 2 (added 2026-10-17 after the validation tables of round 1, see reports/spike/protocol.md): the
# existing classifier's features rebuilt under this protocol, then the new sources on top of it; and the
# same with a rolling 12-month training window (the spike rate fell from 6.4% before 2025-04 to 2.1-2.7%).
EXTRA = {
    "C_strict": ["existing"], "C+stpasa": ["existing", "stpasa"], "C+outage": ["existing", "outage_only"],
    "C+stpasa+outage": ["existing", "stpasa", "outage_only"], "C+price_extra": ["existing", "price_extra"],
}
WINDOWED = {"C_strict_w12": "C_strict", "C+stpasa+outage_w12": "C+stpasa+outage"}
PARAMS = {**CLF_PARAMS, "n_jobs": 1}
WORKERS = os.cpu_count() or 1
LABEL = "spike"


def cols_of(groups: list[str]) -> list[str]:
    return [c for g in groups for c in GROUPS[g]]


def months(table: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.date_range(pc.OOS_START, table["issue_date"].max(), freq="MS")


def train_mask(data: pd.DataFrame, month: pd.Timestamp, window_months: int | None = None) -> pd.Series:
    end = min(month - pd.Timedelta(days=pc.EMBARGO_DAYS), pc.TRAIN_END + pd.Timedelta(days=1))
    m = (data["target_date"] < end) & data[LABEL].notna()
    if window_months:
        m &= data["target_date"] >= end - pd.DateOffset(months=window_months)
    return m


def walk_forward(data: pd.DataFrame, name: str, cols: list[str] | None, label: str = LABEL,
                 weight: float | str = 1.0, window_months: int | None = None) -> pd.DataFrame:
    """Raw out-of-sample probabilities of one model for every month. cols=None is B0."""
    def one(month: pd.Timestamp) -> pd.DataFrame | None:
        test = (data["issue_date"] >= month) & (data["issue_date"] < month + pd.offsets.MonthBegin(1))
        if not test.any():
            return None
        tr = train_mask(data, month, window_months) & data[label].notna()
        y = data.loc[tr, label].astype(int)
        if cols is None:
            p = np.full(test.sum(), y.mean())
        else:
            w = (len(y) - y.sum()) / y.sum() if weight == "balanced" else float(weight)
            clf = lgb.LGBMClassifier(**PARAMS).fit(data.loc[tr, cols], y, sample_weight=np.where(y == 1, w, 1.0))
            p = clf.predict_proba(data.loc[test, cols])[:, 1]
        return pd.DataFrame({"row": data.index[test], "p_raw": p, "refit_month": month, "n_train": int(tr.sum()),
                             "train_last_day": data.loc[tr, "target_date"].max()})
    with ThreadPoolExecutor(WORKERS) as pool:
        out = pd.concat([x for x in pool.map(one, months(data)) if x is not None], ignore_index=True)
    return out.assign(model=name)


def calibrate(pred: pd.DataFrame, data: pd.DataFrame, label: str = LABEL) -> pd.Series:
    """Platt scaling per month on the model's own earlier out-of-sample predictions."""
    p = pred.set_index("row")["p_raw"]
    y = data.loc[p.index, label]
    tday = data.loc[p.index, "target_date"]
    logit = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
    out = pd.Series(np.nan, index=p.index)
    for month, idx in pred.groupby("refit_month")["row"]:
        hist = ((tday < month - pd.Timedelta(days=pc.EMBARGO_DAYS)) & (tday >= month - pd.DateOffset(months=pc.CAL_MONTHS))
                & (tday <= pc.TRAIN_END) & y.notna())
        if y[hist].sum() < 10 or y[hist].nunique() < 2:
            continue
        lr = LogisticRegression(C=1e6).fit(logit[hist].to_numpy()[:, None], y[hist].astype(int))
        out[idx] = lr.predict_proba(logit[idx].to_numpy()[:, None])[:, 1]
    return out


def existing_scores(table: pd.DataFrame) -> pd.DataFrame:
    """The existing price regression (energy blend) and spike classifier C, through their own code, for both
    days_ahead; matched to this table's rows on (issue_date, target_time)."""
    from vpp import aemo_correction as ac
    from vpp import spike_risk as sr
    from vpp.forecast import energy_blend
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
    weather = pd.read_parquet(PROCESSED / "weather_30min.parquet")
    frame = ac.build_frame(df30, runs, weather)
    sig = sr.add_signals(frame, df30)
    parts = []
    for d in (0, 1):
        blend = energy_blend(frame, d, start=pc.OOS_START)
        price = (blend["energy_with_aemo"] + blend["energy_no_aemo"]) / 2
        clf = sr.walk_forward_risk(sig, d, start=pc.OOS_START)
        x = frame.loc[price.index, ["issue_date", "ts"]].assign(existing_price=price, existing_C=clf.reindex(price.index))
        parts.append(x)
    x = pd.concat(parts).rename(columns={"ts": "target_time"})
    key = table[["issue_date", "target_time"]].reset_index()
    return key.merge(x, on=["issue_date", "target_time"], how="left").set_index("index")[["existing_price", "existing_C"]]


def run(table: pd.DataFrame, with_existing: bool = True) -> pd.DataFrame:
    preds = []
    for d in (0, 1):
        data = table[table["days_ahead"] == d]
        jobs = {"B0": None, **{k: cols_of(v) for k, v in LADDER.items()}}
        for name, cols in jobs.items():
            p = walk_forward(data, name, cols)
            p["p_cal"] = calibrate(p, data).reindex(p["row"]).to_numpy()
            preds.append(p)
            print(f"days_ahead {d} {name}: {len(p):,} predictions", flush=True)
    pred = pd.concat(preds, ignore_index=True)
    scores = {"aemo_price": table["aemo_energy"], "yesterday": table["p_prev1"]}
    if with_existing:
        ex = existing_scores(table)
        scores.update(existing_price=ex["existing_price"], existing_C=ex["existing_C"])
    oos = table["issue_date"] >= pc.OOS_START
    for name, s in scores.items():
        pred = pd.concat([pred, pd.DataFrame({"row": table.index[oos], "p_raw": s[oos].to_numpy(), "model": name})], ignore_index=True)
    keep = ["issue_date", "issue_time", "target_time", "target_date", "days_ahead", "lead_hours", "slot", "rtp", "max5",
            "spike", "spike300", "any5"]
    return pred.join(table[keep], on="row")


def run_extra(table: pd.DataFrame) -> pd.DataFrame:
    """Round 2: EXTRA and WINDOWED (12-month window), same walk-forward and calibration as the ladder."""
    preds = []
    for d in (0, 1):
        data = table[table["days_ahead"] == d]
        jobs = [(k, cols_of(v), None) for k, v in EXTRA.items()] + [(k, cols_of(EXTRA[v]), 12) for k, v in WINDOWED.items()]
        for name, cols, window in jobs:
            p = walk_forward(data, name, cols, window_months=window)
            p["p_cal"] = calibrate(p, data).reindex(p["row"]).to_numpy()
            preds.append(p)
            print(f"days_ahead {d} {name}: {len(p):,} predictions", flush=True)
    keep = ["issue_date", "issue_time", "target_time", "target_date", "days_ahead", "lead_hours", "slot", "rtp", "max5",
            "spike", "spike300", "any5"]
    return pd.concat(preds, ignore_index=True).join(table[keep], on="row")


def run_variants(table: pd.DataFrame, model: str) -> pd.DataFrame:
    """For one model: positive-class weights 3 and negatives/positives of the training rows, and four more
    LightGBM seeds (how much AP moves from row/column subsampling alone)."""
    global PARAMS
    groups = {**LADDER, **EXTRA}[model]
    base, preds = PARAMS, []
    for d in (0, 1):
        data = table[table["days_ahead"] == d]
        jobs = [(f"{model}_w3", 3.0, 0), (f"{model}_wbal", "balanced", 0)] + [(f"{model}_seed{s}", 1.0, s) for s in (1, 2, 3, 4)]
        for name, wt, seed in jobs:
            PARAMS = {**base, "random_state": seed}
            p = walk_forward(data, name, cols_of(groups), weight=wt)
            p["p_cal"] = calibrate(p, data).reindex(p["row"]).to_numpy()
            preds.append(p)
            print(f"days_ahead {d} {name}", flush=True)
    PARAMS = base
    keep = ["issue_date", "issue_time", "target_time", "target_date", "days_ahead", "lead_hours", "slot", "rtp", "max5",
            "spike", "spike300", "any5"]
    return pd.concat(preds, ignore_index=True).join(table[keep], on="row")


def main() -> None:
    delayed = "--delayed" in sys.argv
    suffix = "_delayed" if delayed else ""
    table = pd.read_parquet(PROCESSED / f"spike_table{suffix}.parquet")
    if "--variants" in sys.argv:
        for model in sys.argv[sys.argv.index("--variants") + 1:]:
            run_variants(table, model).to_parquet(PROCESSED / f"spike_predictions_variants_{model.replace('+', '_')}.parquet", index=False)
        return
    if "--extra" in sys.argv:  # round 2, appended to the round-1 predictions of the same run
        out = PROCESSED / f"spike_predictions{suffix}.parquet"
        old = pd.read_parquet(out)
        new = run_extra(table)
        new["model_version"] = new["model"] + f"|spike>={pc.SPIKE:.0f}|train_end={pc.TRAIN_END.date()}|{suffix or 'central'}"
        pd.concat([old[~old["model"].isin(new["model"].unique())], new], ignore_index=True).to_parquet(out, index=False)
        print(f"appended {len(new):,} predictions")
        return
    pred = run(table, with_existing=not delayed)
    pred["model_version"] = pred["model"] + f"|spike>={pc.SPIKE:.0f}|train_end={pc.TRAIN_END.date()}|{suffix or 'central'}"
    pred.to_parquet(PROCESSED / f"spike_predictions{suffix}.parquet", index=False)
    print(f"saved {len(pred):,} predictions")


if __name__ == "__main__":
    main()
