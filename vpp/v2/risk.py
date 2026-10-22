"""R1: the intraday spike judgment (reports/v2_plan.md, section 7.2), from the project's own models.

Signals
  danger   the 09:00 spike-risk model (vpp.spike_risk: each half-hour's probability of reaching $200, monthly
           walk-forward, trained to TRAIN_END) folded into a day's risk 1 - prod(1 - p) over 09:00-07:30; the
           top 20% of the estimation window's days are "danger days". The model is extended back to 2024-04 by the
           same walk-forward so every month from 2025-04 has a full window (HISTORY; must equal the dispatcher's
           file where they overlap)
  regime   whether a half-hour of today that has already ended settled at or above SPIKE ($150)
Estimate  at each half-hour t from 09:00, P(a half-hour at or above SPIKE in t..07:30), per cell (t, danger,
          regime): Beta with the prior at the slot's frequency over all days (strength PRIOR days), data the 365
          days before the month (never after TRAIN_END). Output: the posterior mean, its 80% interval, and where
          it comes from in log-odds - the slot's base, the danger day, today's spikes so far.
Tests (before the cutoff, each month from its own window, scored every hour from 09:00): V1 predicted / actual
in 0.8-1.25; V2 daily Brier against the slot-only rate, month-block interval below 0; V3 the realised rate with
today's spike vs without, and danger vs not, compared within the same half-hour, month-block interval above 0.

Run:  python -m vpp.v2.risk [--rebuild]
"""

import sys

import numpy as np
import pandas as pd
from scipy.stats import beta

from vpp import aemo_correction as ac
from vpp import backtest as bt
from vpp import dispatch as dp
from vpp import spike_risk as sr
from vpp.data import PROCESSED
from vpp.evaluate import CUTOFF, TRAIN_END
from vpp.forecast import ISSUE

SPIKE = 150.0                 # $/MWh: a spike half-hour (the offers' lowest price) [fixed]
DANGER_SHARE = 0.20           # the top share of days by morning risk that are danger days [fixed]
PRIOR = 20.0                  # prior strength, days [fixed]
WINDOW = pd.Timedelta(days=365)
FIRST = 2                     # 09:00, when the morning model is issued
SCORED = np.arange(FIRST, dp.N, 2)  # every hour from 09:00
HISTORY = PROCESSED / "spike_risk_history_0900.parquet"
HISTORY_START = pd.Timestamp("2024-04-01 08:00")
GATE = {"V1": (0.8, 1.25)}
REPORTS = bt.REPORTS


def history(rebuild: bool = False) -> pd.DataFrame:
    """The 09:00 spike-risk model, walked forward from HISTORY_START (cached)."""
    if HISTORY.exists() and not rebuild:
        return pd.read_parquet(HISTORY)
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
    weather = pd.read_parquet(PROCESSED / "weather_30min.parquet")
    f = sr.add_signals(ac.build_frame(df30, runs, weather, issue=ISSUE), df30)
    p = sr.walk_forward_risk(f, 0, start=HISTORY_START)
    out = f.loc[p.index, ["issue_date", "ts", "days_ahead", "target_date", "actual_energy"]].assign(p_spike=p)
    out.loc[(out["ts"] < out["issue_date"] + ISSUE).to_numpy(), "p_spike"] = np.nan
    out = out.sort_values("ts").reset_index(drop=True)
    out.to_parquet(HISTORY)
    return out


def day_risk(hist: pd.DataFrame) -> pd.Series:
    """Each trading day's morning risk: 1 - prod(1 - p) over the half-hours from 09:00."""
    p = hist.dropna(subset=["p_spike"])
    return 1.0 - p.groupby("target_date")["p_spike"].apply(lambda x: float(np.prod(1.0 - x.to_numpy())))


class Model:
    """The spike tables of every month, and the day-level inputs: spikes, danger."""

    def __init__(self, hist: pd.DataFrame | None = None):
        from vpp.v2 import value as vl
        self.rtp = vl.prices()                                     # (trading day x slot) RTP
        self.spikes = (self.rtp >= SPIKE).to_numpy()
        self.days = self.rtp.index
        self.risk = day_risk(history() if hist is None else hist).reindex(self.days)
        seen = np.cumsum(self.spikes, axis=1) > 0
        self.regime = np.c_[np.zeros(len(self.days), bool), seen[:, :-1]]   # a spike in a half-hour before t
        rest = np.cumsum(self.spikes[:, ::-1], axis=1)[:, ::-1] > 0
        self.outcome = rest                                                 # a spike in t..47
        self._tables: dict = {}

    def window(self, month: pd.Period) -> np.ndarray:
        """Rows of the days that estimate month's tables: the 365 days before it, never after TRAIN_END."""
        end = min(month.start_time, CUTOFF)
        return np.flatnonzero((self.days >= end - WINDOW) & (self.days < end) & (self.days <= TRAIN_END)
                              & self.risk.notna().to_numpy())

    def threshold(self, month: pd.Period) -> float:
        return float(np.quantile(self.risk.to_numpy()[self.window(month)], 1 - DANGER_SHARE))

    def danger(self, day: pd.Timestamp) -> int | None:
        """1 on a danger day, 0 otherwise, None without the morning model."""
        r = self.risk.get(pd.Timestamp(day).normalize())
        if r is None or not np.isfinite(r):
            return None
        return int(r >= self.threshold(pd.Timestamp(day).to_period("M")))

    def classes(self, month: pd.Period, rows: np.ndarray) -> np.ndarray:
        """Danger classes of the given day rows, at month's threshold."""
        return (self.risk.to_numpy()[rows] >= self.threshold(month)).astype(int)

    def tables(self, month: pd.Period) -> dict:
        """Beta posteriors per slot: base (t), danger (t, D), cell (t, D, R) as (a, b) arrays."""
        if month in self._tables:
            return self._tables[month]
        w = self.window(month)
        D, R, Y = self.classes(month, w), self.regime[w], self.outcome[w]
        base = (Y.sum(axis=0) + 0.5) / (len(w) + 1.0)                     # the slot's frequency
        a0, b0 = PRIOR * base, PRIOR * (1 - base)
        out = {"base": base, "days": len(w), "danger": np.zeros((dp.N, 2, 2)), "cell": np.zeros((dp.N, 2, 2, 2))}
        for d in (0, 1):
            m = D == d
            k, n = Y[m].sum(axis=0), m.sum()
            out["danger"][:, d] = np.c_[a0 + k, b0 + n - k]
            for r in (0, 1):
                mr = m[:, None] & (R == r)
                k, n = (Y & mr).sum(axis=0), mr.sum(axis=0)
                out["cell"][:, d, r] = np.c_[a0 + k, b0 + n - k]
        self._tables[month] = out
        return out

    def judge(self, day: pd.Timestamp, t: int, regime: int | None = None, danger: int | None = None) -> dict:
        """The judgment at half-hour t of day: P(a spike in t..07:30) with its 80% interval, and its parts.
        regime / danger default to the day's (regime from the half-hours that ended before t)."""
        day = pd.Timestamp(day).normalize()
        tb = self.tables(day.to_period("M"))
        i = self.days.get_loc(day)
        regime = int(self.regime[i, t]) if regime is None else regime
        danger = self.danger(day) if danger is None else danger
        base = float(tb["base"][t])
        out = {"t": t, "regime": regime, "danger": danger, "base": base}
        if danger is None or t < FIRST:
            out.update(p=base, lo=np.nan, hi=np.nan, p_danger=base)
            return out
        ad, bd_ = tb["danger"][t, danger]
        a, b = tb["cell"][t, danger, regime]
        out.update(p=a / (a + b), lo=float(beta.ppf(0.1, a, b)), hi=float(beta.ppf(0.9, a, b)), p_danger=ad / (ad + bd_))
        return out


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def scores(model: Model, months) -> pd.DataFrame:
    """Every scored decision of the given months: prediction, the slot-only rate, interval, outcome, signals."""
    rows = []
    for month in months:
        tb = model.tables(month)
        days = np.flatnonzero((model.days >= month.start_time) & (model.days <= month.end_time))
        thr = model.threshold(month)
        for i in days:
            r = model.risk.iloc[i]
            if not np.isfinite(r):
                continue
            d = int(r >= thr)
            for t in SCORED:
                reg = int(model.regime[i, t])
                a, b = tb["cell"][t, d, reg]
                rows.append({"day": model.days[i], "month": month, "t": t, "danger": d, "regime": reg,
                             "p": a / (a + b), "lo": beta.ppf(0.1, a, b), "hi": beta.ppf(0.9, a, b),
                             "base": tb["base"][t], "y": bool(model.outcome[i, t])})
    return pd.DataFrame(rows)


def _month_boot(frame: pd.DataFrame, stat, n: int = 4000, seed: int = 0) -> tuple[float, float]:
    """95% interval of stat(frame) resampling whole calendar months."""
    groups = [g for _, g in frame.groupby("month")]
    rng = np.random.default_rng(seed)
    vals = [stat(pd.concat([groups[k] for k in rng.integers(0, len(groups), len(groups))])) for _ in range(n)]
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def within_slot_gap(frame: pd.DataFrame, col: str) -> float:
    """The realised rate with col = 1 less with col = 0, compared within each half-hour, weighted by the col = 1
    decisions (later half-hours have fewer left to spike in, so slots are not pooled)."""
    g = frame.groupby(["t", col])["y"].agg(["mean", "size"]).unstack(col)
    if ("size", 1) not in g or ("size", 0) not in g:
        return np.nan
    g = g.dropna()
    w = g[("size", 1)]
    return float(((g[("mean", 1)] - g[("mean", 0)]) * w).sum() / w.sum()) if w.sum() else np.nan


def validate(model: Model) -> tuple[pd.DataFrame, dict]:
    months = pd.period_range("2025-04", "2026-10", freq="M")
    sc = scores(model, months)
    sc["period"] = bt.period_of(sc["day"])
    out, rows = {}, []
    for p, x in sc.groupby("period"):
        brier = x.assign(dm=(x["p"] - x["y"]) ** 2 - (x["base"] - x["y"]) ** 2).groupby("day")["dm"].mean()
        bdays = pd.DataFrame({"month": pd.PeriodIndex(brier.index, freq="M"), "v": brier.to_numpy()})
        row = {"period": p, "decisions": len(x), "days": x["day"].nunique(),
               "V1_ratio": x["p"].sum() / max(1, x["y"].sum()),
               "V2_brier_gain": brier.mean(), "V2_lo": np.nan, "V2_hi": np.nan,
               "V3_regime_gap": within_slot_gap(x, "regime"), "V3_danger_gap": within_slot_gap(x, "danger"),
               "coverage_80": float(((x["y"].groupby(x["month"]).mean() >= x["lo"].groupby(x["month"]).mean())
                                    & (x["y"].groupby(x["month"]).mean() <= x["hi"].groupby(x["month"]).mean())).mean())}
        row["V2_lo"], row["V2_hi"] = _month_boot(bdays, lambda f: f["v"].mean())
        row["V3_regime_lo"], row["V3_regime_hi"] = _month_boot(x, lambda f: within_slot_gap(f, "regime"))
        row["V3_danger_lo"], row["V3_danger_hi"] = _month_boot(x, lambda f: within_slot_gap(f, "danger"))
        rows.append(row)
    res = pd.DataFrame(rows).set_index("period")
    v = res.loc["validation"]
    out = {"V1": GATE["V1"][0] <= v["V1_ratio"] <= GATE["V1"][1], "V2": v["V2_hi"] < 0,
           "V3 regime": v["V3_regime_lo"] > 0, "V3 danger": v["V3_danger_lo"] > 0}
    return res, out


def check_overlap() -> tuple[float, int]:
    """Largest difference between the extended history and the dispatcher's 09:00 file where both exist."""
    h = history().dropna(subset=["p_spike"]).set_index("ts")["p_spike"]
    o = pd.read_parquet(sr.OUT_0900).dropna(subset=["p_spike"]).set_index("ts")["p_spike"]
    common = h.index.intersection(o.index)
    return float((h.reindex(common) - o.reindex(common)).abs().max()), len(common)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    hist = history("--rebuild" in argv)
    diff, n = check_overlap()
    print(f"morning model extended to {hist['target_date'].min().date()}; where it overlaps the dispatcher's file "
          f"({n} half-hours) the largest difference is {diff:.2e}")
    model = Model(hist)
    res, gate = validate(model)
    res.to_csv(REPORTS / "v2_risk_validation.csv")
    pd.set_option("display.width", 200)
    print(res.round(4).T.to_string())
    print("gate (before the cutoff):", ", ".join(f"{k} {'pass' if v else 'FAIL'}" for k, v in gate.items()))
    print("=> integrate into decisions" if all(gate.values()) else "=> descriptive only, not into decisions")


if __name__ == "__main__":
    main()
