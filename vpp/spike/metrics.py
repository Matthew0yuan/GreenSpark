"""Scoring spike forecasts (predictions from vpp.spike.models).

Every metric is computed per task (days_ahead) and period (validation / test, by target trading day, from
EVAL_START) on one comparable sample: rows with a label and a prediction from every compared model.
  ap, auc          average precision (area under the PR curve) and ROC AUC: ranking only
  brier, logloss   probability quality (calibrated probabilities; score baselines have none)
  budget k         flag the top k share of the period's target intervals by score: precision (spike rate
                   among flagged), recall (share of spike intervals flagged), false alarms
  threshold        a probability threshold chosen on validation (best F1), frozen for test
  events           runs of spike intervals, gaps <= EVENT_GAP merged, counted once; an event is warned by a
                   task if any of its intervals in that task's rows is flagged
Intervals are 30 min; bootstrap resamples whole target trading days (or weeks), never single intervals.

Run:  python -m vpp.spike.metrics [--delayed]   -> reports/spike/*.csv
"""

import sys

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from vpp.data import PROCESSED, ROOT
from vpp.spike import protocol as pc

OUT = ROOT / "reports" / "spike"
PROB_MODELS = ["B0", "B1", "B2", "B3", "B4", "B5_weather", "B5_aemo_price", "B5_aemo_error", "B5_all", "existing_C",
               "C_strict", "C+stpasa", "C+outage", "C+stpasa+outage", "C+price_extra", "C_strict_w12", "C+stpasa+outage_w12"]
SCORE_MODELS = ["aemo_price", "yesterday", "existing_price"]
# calibrated probabilities only where they beat the raw ones on validation log loss (2026-10-17: B0, B1);
# for B2 and up Platt scaling on the previous six months made validation Brier and log loss slightly worse
USE_CAL = {"B0", "B1"}
LEAD_BINS = [0, 6, 12, 23, 30, 36, 47]
HOURS = {"night 00-06": (0, 6), "morning 06-10": (6, 10), "midday 10-16": (10, 16), "evening 16-21": (16, 21), "late 21-24": (21, 24)}


def score_of(pred: pd.DataFrame) -> pd.Series:
    """The probability (or score) a model is judged on: calibrated for USE_CAL models, raw otherwise."""
    if "p_cal" not in pred:
        return pred["p_raw"]
    return pred["p_cal"].fillna(pred["p_raw"]).where(pred["model"].isin(USE_CAL), pred["p_raw"])


def wide(pred: pd.DataFrame, models: list[str], label: str = "spike") -> pd.DataFrame:
    """One row per (task, target interval) with a column per model's score; only rows every model scores."""
    p = pred[pred["model"].isin(models)].copy()
    p["score"] = score_of(p)
    w = p.pivot_table(index="row", columns="model", values="score", aggfunc="first")
    meta = pred.drop_duplicates("row").set_index("row")[["issue_date", "issue_time", "target_time", "target_date", "days_ahead",
                                                          "lead_hours", "slot", "rtp", "max5", "spike", "spike300", "any5"]]
    w = meta.join(w)
    w = w[(w["target_date"] >= pc.EVAL_START) & w[label].notna()]
    w["period"] = pc.period_of(w["target_date"])
    return w


def comparable(w: pd.DataFrame, models: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rows scored by every model, and the excluded rows counted by reason."""
    miss = w[models].isna()
    reasons = miss.groupby([w["days_ahead"], w["period"]]).sum()
    return w[~miss.any(axis=1)], reasons


def events(rtp: pd.Series, threshold: float = pc.SPIKE, gap: int = pc.EVENT_GAP) -> pd.Series:
    """Event id per 30-min interval (NaN outside events) from the full price series."""
    s = (rtp >= threshold).to_numpy()
    ids = np.full(len(s), np.nan)
    k, last = -1, -10 ** 9
    for i in np.flatnonzero(s):
        if i - last > gap + 1:
            k += 1
        ids[i], last = k, i
    return pd.Series(ids, index=rtp.index)


def budget_flags(score: pd.Series, k: float) -> pd.Series:
    """Top k share of rows by score (ties broken by order)."""
    n = max(1, int(round(k * len(score))))
    return score.rank(method="first", ascending=False) <= n


def basic(y: pd.Series, s: pd.Series, prob: bool) -> dict:
    out = {"n": len(y), "spikes": int(y.sum()), "rate": y.mean(),
           "ap": average_precision_score(y, s) if y.sum() else np.nan,
           "auc": roc_auc_score(y, s) if 0 < y.sum() < len(y) else np.nan}
    if prob:
        p = s.clip(1e-6, 1 - 1e-6)
        out["brier"] = ((p - y) ** 2).mean()
        out["logloss"] = -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()
    return out


def event_recall(w: pd.DataFrame, flags: pd.Series, ev: pd.Series) -> dict:
    e = ev.reindex(w["target_time"]).to_numpy()
    df = pd.DataFrame({"event": e, "flag": flags.to_numpy(), "issue_time": w["issue_time"].to_numpy(),
                       "target_time": w["target_time"].to_numpy()}).dropna(subset=["event"])
    if df.empty:
        return {"events": 0, "events_warned": 0, "event_recall": np.nan, "warn_lead_h_median": np.nan}
    g = df.groupby("event")
    warned = g["flag"].any()
    first = df[df["flag"]].groupby("event").agg(issue=("issue_time", "min"))
    start = g["target_time"].min()
    lead = ((start.reindex(first.index) - first["issue"]).dt.total_seconds() / 3600)
    return {"events": int(len(warned)), "events_warned": int(warned.sum()), "event_recall": warned.mean(),
            "warn_lead_h_median": lead.median() if len(lead) else np.nan}


def table(w: pd.DataFrame, models: list[str], ev: pd.Series, label: str = "spike", thresholds: dict | None = None) -> pd.DataFrame:
    rows = []
    for (d, period), g in w.groupby(["days_ahead", "period"]):
        y = g[label]
        for m in models:
            s = g[m]
            rec = {"days_ahead": d, "period": period, "model": m, **basic(y, s, m not in SCORE_MODELS)}
            rec["events"] = event_recall(g, pd.Series(False, index=g.index), ev)["events"]
            for k in pc.BUDGETS:
                f = budget_flags(s, k)
                rec[f"prec@{k:.0%}"] = y[f].mean()
                rec[f"recall@{k:.0%}"] = y[f].sum() / max(y.sum(), 1)
                rec[f"false_alarms@{k:.0%}"] = int((f & (y == 0)).sum())
                er = event_recall(g, f, ev)
                rec[f"event_recall@{k:.0%}"] = er["event_recall"]
                rec[f"warn_lead_h@{k:.0%}"] = er["warn_lead_h_median"]
            if thresholds and (d, m) in thresholds:
                thr = thresholds[(d, m)]
                f = s >= thr
                rec.update(threshold=thr, thr_flagged=int(f.sum()), thr_precision=y[f].mean() if f.any() else np.nan,
                           thr_recall=y[f].sum() / max(y.sum(), 1), thr_false_alarms=int((f & (y == 0)).sum()),
                           thr_event_recall=event_recall(g, f, ev)["event_recall"])
            rows.append(rec)
    return pd.DataFrame(rows)


def best_f1_thresholds(w: pd.DataFrame, models: list[str], label: str = "spike") -> dict:
    """Probability threshold with the best F1 on validation, per task and model (frozen for test)."""
    out = {}
    v = w[w["period"] == "validation"]
    for d, g in v.groupby("days_ahead"):
        y = g[label].to_numpy()
        for m in models:
            s = g[m].to_numpy()
            order = np.argsort(-s)
            tp = np.cumsum(y[order])
            k = np.arange(1, len(s) + 1)
            f1 = 2 * tp / (k + y.sum())
            i = int(np.argmax(f1))
            out[(d, m)] = s[order][i]
    return out


def by_group(w: pd.DataFrame, models: list[str], key: pd.Series, label: str = "spike") -> pd.DataFrame:
    rows = []
    for (d, period, k), g in w.groupby([w["days_ahead"], w["period"], key]):
        y = g[label]
        rec = {"days_ahead": d, "period": period, "group": k, "n": len(g), "spikes": int(y.sum()), "rate": y.mean()}
        for m in models:
            rec[f"ap_{m}"] = average_precision_score(y, g[m]) if y.sum() else np.nan
        rows.append(rec)
    return pd.DataFrame(rows)


def bootstrap_ap(w: pd.DataFrame, a: str, b: str, label: str = "spike", n: int = 2000, block: str = "day", seed: int = 0) -> tuple:
    """AP(a) - AP(b) with a 95% interval from resampling whole target days (or 7-day blocks)."""
    rng = np.random.default_rng(seed)
    key = w["target_date"] if block == "day" else (w["target_date"] - w["target_date"].min()).dt.days // 7
    groups = [g.index.to_numpy() for _, g in w.groupby(key)]
    y, sa, sb = w[label].to_numpy(), w[a].to_numpy(), w[b].to_numpy()
    pos = {i: w.index.get_indexer(ix) for i, ix in enumerate(groups)}
    diffs = []
    for _ in range(n):
        pick = np.concatenate([pos[i] for i in rng.integers(0, len(groups), len(groups))])
        if y[pick].sum() == 0:
            continue
        diffs.append(average_precision_score(y[pick], sa[pick]) - average_precision_score(y[pick], sb[pick]))
    d = average_precision_score(y, sa) - average_precision_score(y, sb)
    return d, float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def bootstrap_logloss(w: pd.DataFrame, a: str, b: str, label: str = "spike", n: int = 2000, seed: int = 0) -> tuple:
    """Mean log loss(b) - log loss(a) (> 0: a is better) with a day-block 95% interval."""
    def ll(p, y):
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return -(y * np.log(p) + (1 - y) * np.log(1 - p))
    per_day = pd.DataFrame({"d": w["target_date"], "x": ll(w[b].to_numpy(), w[label].to_numpy()) - ll(w[a].to_numpy(), w[label].to_numpy())})
    s = per_day.groupby("d")["x"].agg(["sum", "size"])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), (n, len(s)))
    boot = s["sum"].to_numpy()[idx].sum(1) / s["size"].to_numpy()[idx].sum(1)
    return s["sum"].sum() / s["size"].sum(), float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def reliability(w: pd.DataFrame, models: list[str], label: str = "spike") -> pd.DataFrame:
    bins = [0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 1.0]
    rows = []
    for (d, period), g in w.groupby(["days_ahead", "period"]):
        for m in models:
            b = pd.cut(g[m], bins, include_lowest=True)
            r = g.groupby(b, observed=True).agg(n=(label, "size"), mean_p=(m, "mean"), observed=(label, "mean"))
            rows.append(r.reset_index().rename(columns={m: "bin"}).assign(days_ahead=d, period=period, model=m))
    return pd.concat(rows, ignore_index=True)
