"""spikev3 training, calibration and probability scores (analysis/2026-10-28/spikev3/PLAN.md sections 3.2 and 3.4).

walk_forward is vpp.spike.models.walk_forward with the training cut-off as an argument instead of a module constant
(same LightGBM settings, embargo, refit months and unit sample weights, so it reproduces it row for row).
calibrate fits, for each refit month M, a calibrator on the model's own out-of-sample predictions with target days in
[M - 12 months, M - EMBARGO) and not after the cut-off: "raw" (none), "platt" or "isotonic".
"""

import os
from concurrent.futures import ThreadPoolExecutor

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

from vpp.spike import protocol as pc
from vpp.spike_risk import CLF_PARAMS

TRAIN_END = pd.Timestamp("2026-06-30")
CAL_MONTHS = 12
EPS = 1e-6
METHODS = ("raw", "platt", "isotonic")


def walk_forward(data: pd.DataFrame, name: str, cols: list[str], train_end: pd.Timestamp = TRAIN_END,
                 label: str = "spike", seed: int = 0, oos_start: pd.Timestamp = pc.OOS_START) -> pd.DataFrame:
    """Raw out-of-sample probabilities, one refit per month from oos_start (one days_ahead slice of the table)."""
    params = {**CLF_PARAMS, "n_jobs": 1, "random_state": seed}

    def one(month: pd.Timestamp) -> pd.DataFrame | None:
        test = (data["issue_date"] >= month) & (data["issue_date"] < month + pd.offsets.MonthBegin(1))
        if not test.any():
            return None
        end = min(month - pd.Timedelta(days=pc.EMBARGO_DAYS), train_end + pd.Timedelta(days=1))
        tr = (data["target_date"] < end) & data[label].notna()
        y = data.loc[tr, label].astype(int)
        clf = lgb.LGBMClassifier(**params).fit(data.loc[tr, cols], y, sample_weight=np.where(y == 1, 1.0, 1.0))
        return pd.DataFrame({"row": data.index[test], "p_raw": clf.predict_proba(data.loc[test, cols])[:, 1],
                             "refit_month": month, "n_train": int(tr.sum())})

    months = pd.date_range(oos_start, data["issue_date"].max(), freq="MS")
    with ThreadPoolExecutor(os.cpu_count() or 1) as pool:
        out = pd.concat([x for x in pool.map(one, months) if x is not None], ignore_index=True)
    return out.assign(model=name)


def calibrate(pred: pd.DataFrame, data: pd.DataFrame, method: str, train_end: pd.Timestamp = TRAIN_END,
              label: str = "spike") -> np.ndarray:
    """Calibrated probabilities in pred's row order (raw where a month has < 10 positives in its window)."""
    p = pred["p_raw"].to_numpy(float)
    if method == "raw":
        return p.copy()
    y = data.loc[pred["row"], label].to_numpy(float)
    tday = data.loc[pred["row"], "target_date"].to_numpy()
    out = p.copy()
    for month, idx in pred.groupby("refit_month").indices.items():
        lo = (pd.Timestamp(month) - pd.DateOffset(months=CAL_MONTHS)).to_datetime64()
        hi = min(pd.Timestamp(month) - pd.Timedelta(days=pc.EMBARGO_DAYS), train_end + pd.Timedelta(days=1)).to_datetime64()
        h = (tday >= lo) & (tday < hi) & np.isfinite(y)
        if y[h].sum() < 10 or len(np.unique(y[h])) < 2:
            continue
        if method == "platt":
            lg = lambda q: np.log(np.clip(q, EPS, 1 - EPS) / (1 - np.clip(q, EPS, 1 - EPS)))  # noqa: E731
            lr = LogisticRegression(C=1e6).fit(lg(p[h])[:, None], y[h].astype(int))
            out[idx] = lr.predict_proba(lg(p[idx])[:, None])[:, 1]
        elif method == "isotonic":
            iso = IsotonicRegression(y_min=EPS, y_max=1 - EPS, out_of_bounds="clip").fit(p[h], y[h])
            out[idx] = iso.predict(p[idx])
        else:
            raise ValueError(method)
    return out


# ---------------------------------------------------------------- scores

def logloss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def blocks(days: pd.Series, block: str) -> np.ndarray:
    """Block id per row: the target trading day, or 7-day blocks counted from the first day."""
    d = pd.DatetimeIndex(days)
    return d.normalize().asi8 if block == "day" else ((d - d.min()).days // 7).to_numpy()


def paired(x: np.ndarray, days: pd.Series, block: str, n: int, seed: int = 0) -> tuple[float, float, float]:
    """Mean of a per-row difference x with a 95% interval from resampling whole blocks (ratio of block sums)."""
    df = pd.DataFrame({"b": blocks(days, block), "x": x}).groupby("b")["x"].agg(["sum", "size"])
    s, c = df["sum"].to_numpy(), df["size"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(s), (n, len(s)))
    boot = s[idx].sum(1) / c[idx].sum(1)
    return float(s.sum() / c.sum()), float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def reliability(p: np.ndarray, y: np.ndarray, days: pd.Series, n: int = 1000, seed: int = 0) -> pd.DataFrame:
    """10 bins by predicted probability (deciles of p): mean p, observed rate and its day-block 95% interval."""
    edges = np.unique(np.quantile(p, np.linspace(0, 1, 11)))
    b = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    rows = []
    rng = np.random.default_rng(seed)
    for k in range(len(edges) - 1):
        m = b == k
        g = pd.DataFrame({"d": blocks(days[m], "day"), "y": y[m]}).groupby("d")["y"].agg(["sum", "size"])
        s, c = g["sum"].to_numpy(), g["size"].to_numpy()
        idx = rng.integers(0, len(s), (n, len(s)))
        boot = s[idx].sum(1) / c[idx].sum(1)
        rows.append({"bin": k, "n": int(m.sum()), "mean_p": float(p[m].mean()), "observed": float(y[m].mean()),
                     "obs_lo": float(np.percentile(boot, 2.5)), "obs_hi": float(np.percentile(boot, 97.5))})
    return pd.DataFrame(rows)


def summary(p: np.ndarray, y: np.ndarray, base_rate: float) -> dict:
    """Log loss, Brier, Brier skill against a constant base rate, AP, mean p against the observed rate, and the
    precision and recall of flagging the top 5% of rows."""
    brier = float(((p - y) ** 2).mean())
    ref = float(((base_rate - y) ** 2).mean())
    k = max(1, int(round(0.05 * len(p))))
    top = np.argsort(-p, kind="stable")[:k]
    return {"n": len(y), "spikes": int(y.sum()), "rate": float(y.mean()), "mean_p": float(p.mean()),
            "logloss": float(logloss(p, y).mean()), "brier": brier, "brier_skill": 1 - brier / ref,
            "ap": float(average_precision_score(y, p)) if y.sum() else np.nan,
            "prec@5%": float(y[top].mean()), "recall@5%": float(y[top].sum() / max(y.sum(), 1))}
