"""spikev3 rolling updates (analysis/2026-10-28/spikev3/PLAN.md section 7). Monthly refits whose training and calibration
windows may run past the 2026-06-30 cut-off ("production style"); every month M still uses only labels of target days
before M - EMBARGO_DAYS.

  fit_month / walk_forward   raw probabilities from all history (as vpp.spikev3.models.walk_forward when train_end is
                             the cut-off), the last `window` days, or all history weighted 0.5 ** (age / half_life)
  calibrate                  Platt on the model's own out-of-sample predictions of the last `window` days
  base_rate                  the spike rate of the last `window` days, one constant per month
Windows that hold fewer than MIN_POS spike rows are extended back STEP at a time (calibration and base rate: at most
MAX_CAL_DAYS; calibration stays raw if it is still short).
"""

import os
from concurrent.futures import ThreadPoolExecutor

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from vpp.spike import protocol as pc
from vpp.spike_risk import CLF_PARAMS
from vpp.spikev3 import models as md

NO_CUT = pd.Timestamp("2100-01-01")
MIN_POS = 10
STEP = pd.Timedelta(days=30)
MAX_CAL_DAYS = 365


def cutoff(month: pd.Timestamp, train_end: pd.Timestamp = NO_CUT, window_end: pd.Timestamp | None = None) -> pd.Timestamp:
    """End (exclusive) of the target days month M may learn from; window_end overrides it (leak audit)."""
    if window_end is not None:
        return window_end
    return min(month - pd.Timedelta(days=pc.EMBARGO_DAYS), train_end + pd.Timedelta(days=1))


def window_mask(days: pd.Series, y: pd.Series, end: pd.Timestamp, window: int | None,
                max_days: int | None = None) -> tuple[pd.Series, int | None]:
    """Rows with a label before end; with window, the last window days, extended back until MIN_POS spikes."""
    known = (days < end) & y.notna()
    if window is None:
        return known, None
    first = days[known].min()
    start = end - pd.Timedelta(days=window)
    while True:
        m = known & (days >= start)
        if y[m].sum() >= MIN_POS or start <= first or (max_days and (end - start).days >= max_days):
            return m, (end - start).days
        start = start - STEP if not max_days else max(start - STEP, end - pd.Timedelta(days=max_days))


def fit_month(data: pd.DataFrame, cols: list[str], month: pd.Timestamp, *, window: int | None = None,
              half_life: float | None = None, train_end: pd.Timestamp = NO_CUT, window_end: pd.Timestamp | None = None,
              label: str = "spike", seed: int = 0) -> pd.DataFrame | None:
    test = (data["issue_date"] >= month) & (data["issue_date"] < month + pd.offsets.MonthBegin(1))
    if not test.any():
        return None
    end = cutoff(month, train_end, window_end)
    tr, days = window_mask(data["target_date"], data[label], end, window)
    y = data.loc[tr, label].astype(int)
    if half_life is None:
        w = np.where(y == 1, 1.0, 1.0)
    else:
        w = 0.5 ** ((end - data.loc[tr, "target_date"]).dt.days.to_numpy() / half_life)
    clf = lgb.LGBMClassifier(**{**CLF_PARAMS, "n_jobs": 1, "random_state": seed}).fit(data.loc[tr, cols], y, sample_weight=w)
    return pd.DataFrame({"row": data.index[test], "p_raw": clf.predict_proba(data.loc[test, cols])[:, 1],
                         "refit_month": month, "n_train": int(tr.sum()), "train_pos": int(y.sum()), "train_days": days})


def walk_forward(data: pd.DataFrame, name: str, cols: list[str], **kw) -> pd.DataFrame:
    months = pd.date_range(pc.OOS_START, data["issue_date"].max(), freq="MS")
    with ThreadPoolExecutor(os.cpu_count() or 1) as pool:
        out = pd.concat([x for x in pool.map(lambda m: fit_month(data, cols, m, **kw), months) if x is not None],
                        ignore_index=True)
    return out.assign(model=name)


def calibrate(pred: pd.DataFrame, data: pd.DataFrame, window: int, train_end: pd.Timestamp = NO_CUT,
              window_end: dict | None = None, label: str = "spike") -> tuple[np.ndarray, dict]:
    """Platt per refit month on the model's own predictions of the last `window` days before the month's cut-off;
    returns the probabilities in pred's order and the window length (days) used per month (None: left raw)."""
    p = pred["p_raw"].to_numpy(float)
    y = pd.Series(data.loc[pred["row"], label].to_numpy(float))
    tday = pd.Series(pd.to_datetime(data.loc[pred["row"], "target_date"].to_numpy()))
    out, used = p.copy(), {}
    lg = lambda q: np.log(np.clip(q, md.EPS, 1 - md.EPS) / (1 - np.clip(q, md.EPS, 1 - md.EPS)))  # noqa: E731
    for month, idx in pred.groupby("refit_month").indices.items():
        end = cutoff(pd.Timestamp(month), train_end, (window_end or {}).get(pd.Timestamp(month)))
        h, days = window_mask(tday, y, end, window, MAX_CAL_DAYS)
        h = h.to_numpy()
        if y[h].sum() < MIN_POS or y[h].nunique() < 2:
            used[pd.Timestamp(month)] = None
            continue
        lr = LogisticRegression(C=1e6).fit(lg(p[h])[:, None], y[h].astype(int))
        out[idx] = lr.predict_proba(lg(p[idx])[:, None])[:, 1]
        used[pd.Timestamp(month)] = days
    return out, used


def base_rate(data: pd.DataFrame, window: int, label: str = "spike") -> pd.DataFrame:
    """One constant per issue month: the spike rate of the last `window` days before its cut-off."""
    months = pd.date_range(pc.OOS_START, data["issue_date"].max(), freq="MS")
    rows = []
    for m in months:
        test = (data["issue_date"] >= m) & (data["issue_date"] < m + pd.offsets.MonthBegin(1))
        if not test.any():
            continue
        h, days = window_mask(data["target_date"], data[label], cutoff(m), window, MAX_CAL_DAYS)
        rate = float(data.loc[h, label].mean())
        rows.append(pd.DataFrame({"row": data.index[test], "p_raw": rate, "refit_month": m, "train_days": days}))
    return pd.concat(rows, ignore_index=True)
