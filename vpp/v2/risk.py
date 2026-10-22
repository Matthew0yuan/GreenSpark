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

Round 3, D (reports/v2_plan.md, section 9.2): how often a danger day is a spike day (a half-hour at or above SPIKE
from 09:00), for two calls and two thresholds.
  calls       "same day": the 09:00 model above; "day before": the same model's day-ahead version (days_ahead=1),
              issued at 09:00 the day before (HISTORY_D1), its risk over the same 09:00-07:30
  thresholds  "365": the month's, as above; "recent": the 80% quantile of the risks of the RECENT target days before,
              each day at its own (only calls already issued)
Pass (before the cutoff): the lift - spike-day rate on danger days less on normal days - has a month-block interval
above 0 (P1), and in at least P2_SHARE of the months with both kinds of day the danger days' rate is the higher (P2).
The recent threshold is also run through V1-V3.

Run:  python -m vpp.v2.risk [--rebuild]
      python -m vpp.v2.risk --danger [--rebuild]
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
HISTORY_D1 = PROCESSED / "spike_risk_history_0900_d1.parquet"  # the day-ahead version (round 3, D)
HISTORY_START = pd.Timestamp("2024-04-01 08:00")
GATE = {"V1": (0.8, 1.25)}
RECENT = 28                   # target days behind the recent threshold [fixed, as the price level's]
RECENT_SENS = 90              # reported only
P2_SHARE = 0.75               # [fixed]
REPORTS = bt.REPORTS


def history(rebuild: bool = False, days_ahead: int = 0) -> pd.DataFrame:
    """The spike-risk model issued at 09:00 for the same trading day (days_ahead=0) or the next (1), walked forward
    from HISTORY_START (cached)."""
    path = HISTORY if days_ahead == 0 else HISTORY_D1
    if path.exists() and not rebuild:
        return pd.read_parquet(path)
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
    weather = pd.read_parquet(PROCESSED / "weather_30min.parquet")
    f = sr.add_signals(ac.build_frame(df30, runs, weather, issue=ISSUE), df30)
    p = sr.walk_forward_risk(f, days_ahead, start=HISTORY_START)
    out = f.loc[p.index, ["issue_date", "ts", "days_ahead", "target_date", "actual_energy"]].assign(p_spike=p)
    out.loc[(out["ts"] < out["issue_date"] + ISSUE).to_numpy(), "p_spike"] = np.nan
    out = out.sort_values("ts").reset_index(drop=True)
    out.to_parquet(path)
    return out


def day_risk(hist: pd.DataFrame) -> pd.Series:
    """Each trading day's morning risk: 1 - prod(1 - p) over the half-hours from 09:00."""
    p = hist.dropna(subset=["p_spike"])
    p = p[(p["ts"] - p["target_date"] >= pd.Timedelta(hours=8) + FIRST * pd.Timedelta(minutes=30)).to_numpy()]
    return 1.0 - p.groupby("target_date")["p_spike"].apply(lambda x: float(np.prod(1.0 - x.to_numpy())))


def recent_flags(risk: pd.Series, days: int = RECENT) -> pd.Series:
    """1 where a day's risk is at least the 80% quantile of the risks of the `days` target days before it (NaN without
    them all): only calls issued before the day's own."""
    thr = risk.shift(1).rolling(days, min_periods=days).quantile(1 - DANGER_SHARE, interpolation="linear")
    return (risk >= thr).astype(float).where(thr.notna() & risk.notna())


class Model:
    """The spike tables of every month, and the day-level inputs: spikes, danger.
    threshold "365": a danger day's risk is at least the month's threshold (the 365 days before the month), the
    estimation window's days classed at it too; "recent": each day at its own recent threshold (recent_flags).
    days_ahead: the morning call issued the same day (0) or the day before (1)."""

    def __init__(self, hist: pd.DataFrame | None = None, threshold: str = "365", recent: int = RECENT,
                 days_ahead: int = 0):
        from vpp.v2 import value as vl
        self.rtp = vl.prices()                                     # (trading day x slot) RTP
        self.spikes = (self.rtp >= SPIKE).to_numpy()
        self.days = self.rtp.index
        self.risk = day_risk(history(days_ahead=days_ahead) if hist is None else hist).reindex(self.days)
        self.mode = threshold
        self.flags = recent_flags(self.risk, recent).to_numpy() if threshold == "recent" else None
        seen = np.cumsum(self.spikes, axis=1) > 0
        self.regime = np.c_[np.zeros(len(self.days), bool), seen[:, :-1]]   # a spike in a half-hour before t
        rest = np.cumsum(self.spikes[:, ::-1], axis=1)[:, ::-1] > 0
        self.outcome = rest                                                 # a spike in t..47
        self._tables: dict = {}

    def window(self, month: pd.Period) -> np.ndarray:
        """Rows of the days that estimate month's tables: the 365 days before it, never after TRAIN_END."""
        end = min(month.start_time, CUTOFF)
        known = self.risk.notna().to_numpy() if self.flags is None else np.isfinite(self.flags)
        return np.flatnonzero((self.days >= end - WINDOW) & (self.days < end) & (self.days <= TRAIN_END) & known)

    def threshold(self, month: pd.Period) -> float:
        return float(np.quantile(self.risk.to_numpy()[self.window(month)], 1 - DANGER_SHARE))

    def flag(self, i: int, month: pd.Period) -> int | None:
        """1 if day row i is a danger day (at month's threshold, or its own recent one), 0 if not, None if unknown."""
        if self.flags is not None:
            return int(self.flags[i]) if np.isfinite(self.flags[i]) else None
        r = self.risk.iloc[i]
        return int(r >= self.threshold(month)) if np.isfinite(r) else None

    def danger(self, day: pd.Timestamp) -> int | None:
        """1 on a danger day, 0 otherwise, None without the morning model."""
        day = pd.Timestamp(day).normalize()
        if day not in self.days:
            return None
        return self.flag(self.days.get_loc(day), day.to_period("M"))

    def classes(self, month: pd.Period, rows: np.ndarray) -> np.ndarray:
        """Danger classes of the given day rows, at month's threshold (or each at its own recent one)."""
        if self.flags is not None:
            return self.flags[rows].astype(int)
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
        for i in days:
            d = model.flag(i, month)
            if d is None:
                continue
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


def hit_days(model: Model) -> pd.DataFrame:
    """Every backtest day with a call: danger (0/1), spike day (a half-hour at or above SPIKE from 09:00), the
    number of such half-hours, month and period."""
    rows = []
    for i in np.flatnonzero((model.days >= "2025-04-01") & (model.days <= "2026-10-15")):
        d = model.flag(i, model.days[i].to_period("M"))
        if d is None:
            continue
        n = int(model.spikes[i, FIRST:].sum())
        rows.append({"day": model.days[i], "danger": d, "spike": n > 0, "n_spike": n})
    x = pd.DataFrame(rows)
    x["month"] = pd.PeriodIndex(x["day"], freq="M")
    x["period"] = bt.period_of(x["day"])
    return x


def lift(x: pd.DataFrame) -> float:
    """Spike-day rate on danger days less on normal days (NaN without both)."""
    g = x.groupby("danger")["spike"].mean()
    return float(g.get(1, np.nan) - g.get(0, np.nan))


def hits(x: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per period: the hit table (see the module docstring); per month: each kind of day's spike-day rate."""
    rows = []
    for p, y in x.groupby("period"):
        dg, nm = y[y["danger"] == 1], y[y["danger"] == 0]
        m = y.groupby(["month", "danger"])["spike"].mean().unstack("danger")
        both = m.dropna() if {0, 1} <= set(m.columns) else m.iloc[:0]
        row = {"period": p, "days": len(y), "danger_share": len(dg) / len(y),
               "hit_danger": dg["spike"].mean(), "hit_normal": nm["spike"].mean(), "lift": lift(y),
               "recall": (y["spike"] & (y["danger"] == 1)).sum() / max(1, y["spike"].sum()),
               "spikes_per_danger_day": dg["n_spike"].mean(), "spikes_per_normal_day": nm["n_spike"].mean(),
               "months": y["month"].nunique(), "months_both": len(both),
               "months_danger_higher": int((both[1] > both[0]).sum()) if len(both) else 0}
        row["lift_lo"], row["lift_hi"] = _month_boot(y, lift)
        rows.append(row)
    res = pd.DataFrame(rows).set_index("period")
    months = x.groupby(["month", "danger"]).agg(days=("spike", "size"), hit=("spike", "mean")).reset_index()
    return res, months


def danger_pass(res: pd.DataFrame) -> dict:
    v = res.loc["validation"]
    return {"P1": v["lift_lo"] > 0,
            "P2": v["months_both"] > 0 and v["months_danger_higher"] >= P2_SHARE * v["months_both"]}


def check_overlap() -> tuple[float, int]:
    """Largest difference between the extended history and the dispatcher's 09:00 file where both exist."""
    h = history().dropna(subset=["p_spike"]).set_index("ts")["p_spike"]
    o = pd.read_parquet(sr.OUT_0900).dropna(subset=["p_spike"]).set_index("ts")["p_spike"]
    common = h.index.intersection(o.index)
    return float((h.reindex(common) - o.reindex(common)).abs().max()), len(common)


def danger_checks() -> dict:
    """Round 3's checks: the day-ahead calls issued at 09:00 the day before, for targets after the issue; the recent
    threshold of a day unchanged when its own and later risks change; R1's validation unchanged at "365"."""
    h1 = history(days_ahead=1).dropna(subset=["p_spike"])
    issued = bool(((h1["target_date"] - h1["issue_date"]) == pd.Timedelta(days=1)).all()
                  and (h1["ts"] >= h1["issue_date"] + ISSUE).all())
    risk = day_risk(history())
    rng = np.random.default_rng(0)
    timing = True
    for k in rng.integers(RECENT + 1, len(risk) - 1, 5):
        thr = risk.shift(1).rolling(RECENT).quantile(1 - DANGER_SHARE)
        changed = risk.copy()
        changed.iloc[k:] = rng.uniform(0, 1, len(risk) - k)
        timing &= thr.iloc[k] == changed.shift(1).rolling(RECENT).quantile(1 - DANGER_SHARE).iloc[k]
    saved = pd.read_csv(REPORTS / "v2_risk_validation.csv", index_col="period")
    res, _ = validate(Model())
    same = np.allclose(res[saved.columns].loc[saved.index].to_numpy(float), saved.to_numpy(float), rtol=0, atol=1e-12)
    return {"day-ahead calls issued the day before at 09:00": issued,
            "recent threshold uses only earlier calls": bool(timing), "R1 at '365' as committed": bool(same)}


def danger_main(rebuild: bool) -> None:
    for days_ahead in (0, 1):
        history(rebuild, days_ahead)
    variants = [("same day", 0, "365", None), ("same day", 0, "recent", RECENT), ("day before", 1, "365", None),
                ("day before", 1, "recent", RECENT), ("same day", 0, "recent", RECENT_SENS),
                ("day before", 1, "recent", RECENT_SENS)]
    tables, months = [], []
    for call, ahead, thr, n in variants:
        label = thr if n is None else f"recent {n}"
        model = Model(threshold=thr, recent=n or RECENT, days_ahead=ahead)
        res, mon = hits(hit_days(model))
        ok = danger_pass(res)
        tables.append(res.assign(call=call, threshold=label, P1=ok["P1"], P2=ok["P2"],
                                 chosen_on=n != RECENT_SENS).reset_index())
        months.append(mon.assign(call=call, threshold=label))
    table = pd.concat(tables).set_index(["call", "threshold", "period"])
    table.to_csv(REPORTS / "v2_danger_hits.csv")
    pd.concat(months).to_csv(REPORTS / "v2_danger_months.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(table.round(3).to_string())
    res, gate = validate(Model(threshold="recent"))
    res.to_csv(REPORTS / "v2_risk_validation_recent.csv")
    print("\nR1 with the recent threshold (same day)\n" + res.round(4).T.to_string())
    print("gate (before the cutoff):", ", ".join(f"{k} {'pass' if v else 'FAIL'}" for k, v in gate.items()))
    print("\nchecks:", ", ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in danger_checks().items()))


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--danger" in argv:
        danger_main("--rebuild" in argv)
        return
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
