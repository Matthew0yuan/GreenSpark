"""Price scenarios for a stochastic morning plan (step A of reports/q2b_plan.md, section 9): built and
checked for calibration before any optimiser uses them.

At the 09:00 plan of trading day D, scenario k is D's forecast plus the whole-day forecast error of one
past trading day a_k (an analog), slot by slot over 09:00 .. 07:30 (slots 2-47):
  energy  = forecast_D + (actual_a - forecast_a), clipped to the range of prices seen before D
  FCESS   = max(0, profile_D + (actual_a - profile_a)), profile = the 28-day average the dispatcher plans on
Whole days keep the errors' correlation through the day (an evening that runs high runs high for hours)
and across products. All S scenarios weigh the same.

Analogs come only from trading days before D, all complete by 08:00 on D. Their forecasts are the same
walk-forward 09:00 forecasts the dispatcher uses (vpp.forecast), extended back to HISTORY_START so that the
first validation days have a pool. Monthly refits do not depend on where the walk starts, so the overlap
must equal data/processed/forecast_dispatch_0900.parquet (checked in history()).

Methods (the chosen one has the lowest validation CRPS, energy, among those that pass the gate):
  recent          the S most recent days
  analog          the S days nearest to D in SIGNALS (z-scores over the pool), from any time before D
  analog365       the same, from the last 365 days only
  analog_vol      as analog, matching on SIGNALS + VOLATILITY       (added in round 2)
  analog365_vol   as analog365, matching on SIGNALS + VOLATILITY    (added in round 2)
  anen365_vol     analog365_vol's days, their actual prices instead of D's forecast + their errors (round 3)
  anen365_vol_level  the same, shifted by D's forecast mean less the analog's (round 3)
The forecast + error form adds another day's error path to D's smooth forecast; as that path is not lined
up with D's, it adds swings, and calm days got too volatile (round 2). The analog ensemble (round 3) takes
real days' paths instead, at the cost of D's own forecast shape.
SIGNALS, all public by 09:00 on D: our forecast's mean and maximum over 09:00 .. 07:30, AEMO's forecast
maximum energy requirement and its smallest capacity margin (08:00 run), the forecast maximum temperature,
yesterday's maximum price, and the FCESS price level (sum of the four 28-day profiles). VOLATILITY: our
forecast's 4-hour spread, yesterday's actual 4-hour spread and its mean over the last 7 days (09:00 .. 07:30).

Calibration, on the vpp.evaluate split (validation before CUTOFF; the test, from CUTOFF, is scored for the
chosen method only). Gate to step B, on validation. INSIDE_REF is what a perfectly calibrated ensemble of S
gives for "inside the 10-90% range" (72.6% for S = 20, not 80%: the range between interpolated quantiles of
20 members is narrower than the population's); TAIL_REF = 1 / (S + 1) is the share below (or above) all members:
  G1  half-hour energy prices: inside the 10-90% range INSIDE_REF +/- 0.05; below all and above all members
      each 0.5 .. 2 x TAIL_REF
  G2  CRPS below the point forecast's absolute error: 95% interval of the daily difference below 0
  G3  P(price >= $150): mean probability / observed frequency 0.8 .. 1.25, and Brier score below the point
      forecast's (95% interval of the daily difference below 0)
  G4  the day's maximum price, 17:00-21:00 mean and 4-hour spread (best 8 half-hours less worst 8): each
      inside INSIDE_REF +/- 0.10 of days; below all and above all each 0.5 .. 2 x TAIL_REF
FCESS is reported, not gated.
Round 1 (2026-10-20) set G1 at 0.75 .. 0.85 and G4 at 0.70 .. 0.90, centred on 80% by mistake, with no tail
tests; analog365 was chosen, failed G1 and G4, and its test results were printed. Round 2 centres the ranges
on INSIDE_REF with the same widths, adds the tail tests (stricter: on validation, coverage alone hid that the
actual 4-hour spread fell below all members on 20% of days, 45% of the calmest third) and the two volatility
methods; none passed G4. Round 3 adds the two analog-ensemble forms: both pass, and anen365_vol_level is
chosen. Every change was decided on validation; the test was printed in each round for that round's choice
(reports/findings.md, section 17).

Run:  python -m vpp.scenarios [--rebuild]
"""

import sys

import numpy as np
import pandas as pd

from vpp import aemo_correction as ac
from vpp import forecast as fo
from vpp.backtest import bootstrap_ci
from vpp.data import PROCESSED, ROOT, trading_date
from vpp.evaluate import period_of

REPORTS = ROOT / "reports"
HISTORY = PROCESSED / "forecast_history_0900.parquet"
SIGNALS_FILE = PROCESSED / "scenario_signals.parquet"
OUT = PROCESSED / "scenarios_0900.parquet"
HISTORY_START = pd.Timestamp("2024-07-01 08:00")  # first error day: its model trains on 2023-10 .. 2024-06 [fixed]
EVAL_START = fo.FORECAST_START                   # scenarios are scored from the first validation day
FCESS = fo.FCESS
FIRST = 2                                        # slot of 09:00, the plan's issue
S = 20                                           # scenarios per day: what a 48-slot MILP can carry [fixed]
SIGNALS = ["fc_mean", "fc_max", "aemo_req_max", "margin_min", "wx_tmax", "yday_max", "fcess_level"]
VOLATILITY = ["fc_spread4h", "yday_spread4h", "spread7"]
VOL = tuple(SIGNALS + VOLATILITY)
# name -> (pool, signals matched on, form of the scenario: see Bank.scenarios)
METHODS = {"recent": ("recent", (), "error"), "analog": ("all", tuple(SIGNALS), "error"),
           "analog365": ("365", tuple(SIGNALS), "error"), "analog_vol": ("all", VOL, "error"),
           "analog365_vol": ("365", VOL, "error"), "anen365_vol": ("365", VOL, "actual"),
           "anen365_vol_level": ("365", VOL, "actual_level")}
SPIKE = 150.0                                    # $/MWh: the offer rule's lowest price
EVENING = (18 - FIRST, 26 - FIRST)               # 17:00-21:00 in the sliced day
DAILY = ("max", "evening", "spread4h")
TAIL_REF = 1 / (S + 1)


def _calibrated_inside(n: int = 200_000, seed: int = 1) -> float:
    """Share of draws inside the 10-90% range of S other draws from the same distribution (Monte Carlo)."""
    z = np.random.default_rng(seed).standard_normal((n, S + 1))
    q10, q90 = np.quantile(z[:, :S], [0.1, 0.9], axis=1)
    return float(((z[:, S] >= q10) & (z[:, S] <= q90)).mean())


INSIDE_REF = _calibrated_inside()
GATE = {"G1": (INSIDE_REF - 0.05, INSIDE_REF + 0.05), "G3": (0.8, 1.25), "G4": (INSIDE_REF - 0.10, INSIDE_REF + 0.10),
        "tail": (0.5 * TAIL_REF, 2 * TAIL_REF)}


def history(rebuild: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Walk-forward 09:00 forecasts from HISTORY_START and each day's SIGNALS (cached)."""
    if HISTORY.exists() and SIGNALS_FILE.exists() and not rebuild:
        return pd.read_parquet(HISTORY), pd.read_parquet(SIGNALS_FILE)
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
    frame = ac.build_frame(df30, runs, issue=fo.ISSUE)
    fc = fo.build(start=HISTORY_START, issue=fo.ISSUE, frame=frame)
    # the overlap must equal the dispatcher's forecast
    old = pd.read_parquet(fo.OUT_0900).set_index("ts")["energy"]
    new = fc.set_index("ts")["energy"].reindex(old.index)
    gap = float((new - old).abs().max())
    print(f"history: {fc['target_date'].nunique()} days from {HISTORY_START:%Y-%m-%d}; "
          f"largest difference from the dispatcher's forecast on the overlap: {gap:.2e} $/MWh")
    if not gap < 1e-6:
        raise RuntimeError("the extended forecast differs from vpp.forecast's on the overlap")
    f = frame[frame["days_ahead"] == 0]
    by = f.groupby("target_date")
    sig = pd.DataFrame({
        "aemo_req_max": by["aemo_req_energy_mw"].max(),
        "margin_min": (f["aemo_available_injection_mw"] - f["aemo_req_energy_mw"]).groupby(f["target_date"]).min(),
        "wx_tmax": by["wx_tmax_day"].max(),
        "fcess_level": f[[f"prof28_{k}" for k in FCESS]].sum(axis=1).groupby(f["target_date"]).mean(),
    })
    daily_max = df30["energy"].groupby(trading_date(df30.index)).max()
    sig["yday_max"] = daily_max.reindex(sig.index - pd.Timedelta(days=1)).to_numpy()
    e = _pivot(fc, "energy")
    sig = sig.join(pd.DataFrame({"fc_mean": e.mean(axis=1), "fc_max": e.max(axis=1)}))
    fc.to_parquet(HISTORY)
    sig.to_parquet(SIGNALS_FILE)
    return fc, sig


def spread4h(x: np.ndarray) -> np.ndarray:
    """Best 8 half-hours less worst 8 along the last axis: what a 4-hour battery could buy low and sell high."""
    s = np.sort(x, axis=-1)
    return s[..., -8:].mean(axis=-1) - s[..., :8].mean(axis=-1)


def volatility(fc: pd.DataFrame) -> pd.DataFrame:
    """VOLATILITY signals per trading day, public by 09:00 on it (yesterday's trading day ended at 08:00)."""
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy"])
    act = ac._by_day_slot(df30["energy"]).loc[:, FIRST:]
    daily = pd.Series(spread4h(act.to_numpy()), index=act.index).where(act.notna().all(axis=1))
    daily = daily.reindex(pd.date_range(daily.index.min(), daily.index.max()))
    e = _pivot(fc, "energy")
    out = pd.DataFrame({"fc_spread4h": spread4h(e.to_numpy())}, index=e.index)
    out["yday_spread4h"] = daily.shift(1).reindex(out.index).to_numpy()
    out["spread7"] = daily.rolling(7, min_periods=5).mean().shift(1).reindex(out.index).to_numpy()
    return out


def _pivot(fc: pd.DataFrame, col: str) -> pd.DataFrame:
    """(trading day x slot FIRST..47) of one column of the days_ahead 0 forecasts."""
    x = fc[fc["days_ahead"] == 0]
    slot = ac.trading_slot(pd.DatetimeIndex(x["ts"]))
    return x.assign(slot=slot).pivot_table(index="target_date", columns="slot", values=col).loc[:, FIRST:]


class Bank:
    """Every complete day's forecast, actual and error (slots FIRST..47) and its SIGNALS, in date order."""

    def __init__(self, fc: pd.DataFrame, sig: pd.DataFrame):
        sig = sig.join(volatility(fc))
        tables ={"fc_e": _pivot(fc, "energy"), "act_e": _pivot(fc, "actual_energy"),
                  **{f"prof_{k}": _pivot(fc, k) for k in FCESS}, **{f"act_{k}": _pivot(fc, f"actual_{k}") for k in FCESS}}
        ok = None
        for t in tables.values():
            full = t.index[t.notna().all(axis=1) & (t.shape[1] == 48 - FIRST)]
            ok = full if ok is None else ok.intersection(full)
        self.dates = ok.sort_values()
        a = {k: t.reindex(self.dates).to_numpy() for k, t in tables.items()}
        self.fc_e, self.act_e = a["fc_e"], a["act_e"]
        self.err_e = self.act_e - self.fc_e
        self.prof = {k: a[f"prof_{k}"] for k in FCESS}
        self.act_f = {k: a[f"act_{k}"] for k in FCESS}
        self.err_f = {k: self.act_f[k] - self.prof[k] for k in FCESS}
        self.signals = sig.reindex(self.dates)
        self.lo = np.minimum.accumulate(self.act_e.min(axis=1))  # lowest price up to and including each day
        self.hi = np.maximum.accumulate(self.act_e.max(axis=1))

    def analogs(self, i: int, method: str) -> np.ndarray | None:
        """Indices of day i's S analogs, all before it; None if the pool is too small."""
        day = self.dates[i]
        pool = np.arange(i)
        span, cols, _ = METHODS[method]
        if span == "365":
            pool = pool[self.dates[pool] >= day - pd.Timedelta(days=365)]
        if span == "recent":
            return pool[-S:] if len(pool) >= S else None
        sig = self.signals[list(cols)].to_numpy(float)
        x, xi = sig[pool], sig[i]
        pool, x = pool[~np.isnan(x).any(axis=1)], x[~np.isnan(x).any(axis=1)]
        if len(pool) < S or np.isnan(xi).any():
            return pool[-S:] if len(pool) >= S else None  # a signal missing on day i: the most recent days
        mu, sd = x.mean(axis=0), x.std(axis=0)
        sd[sd == 0] = 1.0
        d = (((x - mu) / sd - (xi - mu) / sd) ** 2).sum(axis=1)
        return pool[np.argsort(d, kind="stable")[:S]]

    def scenarios(self, i: int, method: str) -> tuple[np.ndarray, dict, np.ndarray] | None:
        """(energy (S, slots), {FCESS product: (S, slots)}, analog indices) for day i. Forms:
          error         day i's forecast + each analog's forecast error, slot by slot
          actual        each analog's actual prices (the analog ensemble)
          actual_level  the same, shifted by day i's forecast mean less the analog's forecast mean"""
        a = self.analogs(i, method)
        if a is None:
            return None
        form = METHODS[method][2]
        if form == "error":
            energy = self.fc_e[i] + self.err_e[a]
            fcess = {k: self.prof[k][i] + self.err_f[k][a] for k in FCESS}
        elif form == "actual":
            energy, fcess = self.act_e[a], {k: self.act_f[k][a] for k in FCESS}
        else:
            energy = self.act_e[a] + (self.fc_e[i].mean() - self.fc_e[a].mean(axis=1))[:, None]
            fcess = {k: self.act_f[k][a] + (self.prof[k][i].mean() - self.prof[k][a].mean(axis=1))[:, None] for k in FCESS}
        energy = np.clip(energy, self.lo[i - 1], self.hi[i - 1])
        return energy, {k: np.maximum(v, 0.0) for k, v in fcess.items()}, a


def crps(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """CRPS of an equally weighted ensemble x (members, slots) per slot: E|X - y| - E|X - X'| / 2."""
    n = x.shape[0]
    xs = np.sort(x, axis=0)
    w = (2 * np.arange(1, n + 1) - n - 1)[:, None]
    return np.abs(x - y).mean(axis=0) - (w * xs).sum(axis=0) / n**2


def _inside(x: np.ndarray, y) -> np.ndarray:
    q10, q90 = np.quantile(x, [0.1, 0.9], axis=0)
    return (y >= q10 - 1e-9) & (y <= q90 + 1e-9)


def _daily_stats(paths: np.ndarray) -> dict:
    """Whole-day statistics of price paths (..., slots): maximum, 17:00-21:00 mean, 4-hour spread."""
    return {"max": paths.max(axis=-1), "evening": paths[..., EVENING[0]:EVENING[1]].mean(axis=-1),
            "spread4h": spread4h(paths)}


def score_day(bank: Bank, i: int, method: str, rng: np.random.Generator) -> dict | None:
    """One day's calibration record for a method."""
    sc = bank.scenarios(i, method)
    if sc is None:
        return None
    energy, fcess, a = sc
    y, f = bank.act_e[i], bank.fc_e[i]
    ties = (energy == y).sum(axis=0)
    rank = (energy < y).sum(axis=0) + rng.integers(0, ties + 1)
    p = (energy >= SPIKE).mean(axis=0)
    obs = (y >= SPIKE).astype(float)
    rec = {"day": bank.dates[i], "method": method, "crps": crps(energy, y).mean(), "mae": np.abs(f - y).mean(),
           "inside": _inside(energy, y).mean(), "below_all": (rank == 0).mean(), "above_all": (rank == S).mean(),
           "p_spike": p.mean(), "obs_spike": obs.mean(), "point_spike": (f >= SPIKE).mean(),
           "brier": ((p - obs) ** 2).mean(), "brier_point": (((f >= SPIKE) - obs) ** 2).mean(),
           "analog_age_days": float((bank.dates[i] - bank.dates[a]).days.to_numpy().mean())}
    sim, act = _daily_stats(energy), _daily_stats(y)
    for k in sim:
        rec[f"inside_{k}"] = float(_inside(sim[k][:, None], np.array([act[k]]))[0])
        r = int((sim[k] < act[k]).sum())
        rec[f"below_all_{k}"], rec[f"above_all_{k}"] = float(r == 0), float(r == S)
    for k in FCESS:
        yk, fk = bank.act_f[k][i], np.maximum(bank.prof[k][i], 0.0)
        rec[f"crps_{k}"], rec[f"mae_{k}"] = crps(fcess[k], yk).mean(), np.abs(fk - yk).mean()
        rec[f"inside_{k}"] = _inside(fcess[k], yk).mean()
    rec["_p"], rec["_obs"] = p, obs
    return rec


def evaluate(bank: Bank, method: str, period: str) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    idx = [i for i, d in enumerate(bank.dates) if d >= EVAL_START.normalize() and period_of([d])[0] == period]
    rows = [r for i in idx if (r := score_day(bank, i, method, rng)) is not None]
    return pd.DataFrame(rows)


def summary(rec: pd.DataFrame) -> dict:
    """Period-level calibration and the gate's tests."""
    lo, hi = bootstrap_ci((rec["crps"] - rec["mae"]).to_numpy())
    blo, bhi = bootstrap_ci((rec["brier"] - rec["brier_point"]).to_numpy())
    out = {"days": len(rec), "crps": rec["crps"].mean(), "point_mae": rec["mae"].mean(),
           "crps_skill": 1 - rec["crps"].mean() / rec["mae"].mean(), "crps_minus_mae_lo": lo, "crps_minus_mae_hi": hi,
           "inside_10_90": rec["inside"].mean(), "below_all": rec["below_all"].mean(), "above_all": rec["above_all"].mean(),
           "p_spike_mean": rec["p_spike"].mean(), "obs_spike": rec["obs_spike"].mean(), "point_spike": rec["point_spike"].mean(),
           "spike_ratio": rec["p_spike"].mean() / rec["obs_spike"].mean(),
           "brier": rec["brier"].mean(), "brier_point": rec["brier_point"].mean(), "brier_diff_lo": blo, "brier_diff_hi": bhi,
           **{f"{w}_{k}": rec[f"{w}_{k}"].mean() for k in DAILY for w in ("inside", "below_all", "above_all")},
           "analog_age_days": rec["analog_age_days"].mean()}

    def tails(sfx: str) -> bool:
        return all(GATE["tail"][0] <= out[f"{w}{sfx}"] <= GATE["tail"][1] for w in ("below_all", "above_all"))

    out["G1"] = GATE["G1"][0] <= out["inside_10_90"] <= GATE["G1"][1] and tails("")
    out["G2"] = hi < 0
    out["G3"] = GATE["G3"][0] <= out["spike_ratio"] <= GATE["G3"][1] and bhi < 0
    out["G4"] = all(GATE["G4"][0] <= out[f"inside_{k}"] <= GATE["G4"][1] and tails(f"_{k}") for k in DAILY)
    out["pass"] = out["G1"] and out["G2"] and out["G3"] and out["G4"]
    return out


def reliability(rec: pd.DataFrame) -> pd.DataFrame:
    """P(price >= SPIKE) by bin of the scenarios' share: mean probability, observed frequency, half-hours."""
    p, obs = np.concatenate(rec["_p"].to_list()), np.concatenate(rec["_obs"].to_list())
    bins = pd.cut(p, [-0.01, 0.0, 0.05, 0.15, 0.3, 0.5, 1.0])
    return pd.DataFrame({"p": p, "obs": obs, "bin": bins}).groupby("bin", observed=True).agg(
        mean_p=("p", "mean"), observed=("obs", "mean"), half_hours=("p", "size"))


def fcess_table(rec: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({k: {"crps": rec[f"crps_{k}"].mean(), "profile_mae": rec[f"mae_{k}"].mean(),
                             "crps_skill": 1 - rec[f"crps_{k}"].mean() / rec[f"mae_{k}"].mean(),
                             "inside_10_90": rec[f"inside_{k}"].mean()} for k in FCESS}).T


def save(bank: Bank, method: str) -> None:
    """Every scored day's S scenarios (long format) for the planner, with the analog each comes from."""
    parts = []
    for i, d in enumerate(bank.dates):
        if d < EVAL_START.normalize() or (sc := bank.scenarios(i, method)) is None:
            continue
        energy, fcess, a = sc
        n = energy.shape[1]
        parts.append(pd.DataFrame({
            "day": d, "member": np.repeat(np.arange(S), n), "analog_day": np.repeat(bank.dates[a], n),
            "slot": np.tile(np.arange(FIRST, FIRST + n), S), "energy": energy.ravel(),
            **{k: fcess[k].ravel() for k in FCESS}}))
    out = pd.concat(parts, ignore_index=True)
    if not (out["analog_day"] < out["day"]).all():  # an analog must be complete before the plan uses it
        raise RuntimeError("a scenario uses a day that is not over yet")
    out["period"] = period_of(out["day"])
    out.to_parquet(OUT)
    print(f"saved {OUT} ({out['day'].nunique()} days x {S} scenarios, method {method})")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    fc, sig = history(rebuild="--rebuild" in argv)
    bank = Bank(fc, sig)
    print(f"bank: {len(bank.dates)} complete days, {bank.dates[0]:%Y-%m-%d} .. {bank.dates[-1]:%Y-%m-%d}\n")

    # choose on validation only
    val = {m: evaluate(bank, m, "validation") for m in METHODS}
    choice = pd.DataFrame({m: summary(r) for m, r in val.items()}).T
    passing = [m for m in METHODS if choice.loc[m, "pass"]]
    best = min(passing or list(METHODS), key=lambda m: val[m]["crps"].mean())
    ref = val[best].set_index("day")["crps"]
    for m in METHODS:
        diff = (val[m].set_index("day")["crps"] - ref.reindex(val[m]["day"]).to_numpy()).to_numpy()
        choice.loc[m, ["crps_vs_best", "crps_vs_best_lo", "crps_vs_best_hi"]] = [diff.mean(), *bootstrap_ci(diff)]
    choice.index.name = "method"
    choice.to_csv(REPORTS / "scenarios_choice.csv")
    cols = ["crps", "crps_skill", "crps_vs_best", "crps_vs_best_lo", "crps_vs_best_hi", "inside_10_90", "below_all",
            "above_all", "spike_ratio", *[f"{w}_{k}" for k in DAILY for w in ("inside", "below_all", "above_all")],
            "G1", "G2", "G3", "G4", "pass"]
    print(f"gate: inside {INSIDE_REF:.3f} +/- 0.05 (half-hours) / 0.10 (days); "
          f"below / above all {GATE['tail'][0]:.3f} .. {GATE['tail'][1]:.3f}")
    print(f"=== validation, by method (chosen: {best}; passing: {', '.join(passing) or 'none'})\n"
          f"{choice[cols].astype(float).round(3).T.to_string()}\n")

    # then the test, once, for the chosen method
    test = evaluate(bank, best, "test")
    cal = pd.DataFrame({"validation": summary(val[best]), "test": summary(test)}).T
    cal.insert(0, "method", best)
    cal.index.name = "period"
    cal.to_csv(REPORTS / "scenarios_calibration.csv")
    print(f"=== {best}: calibration (gate on validation; test for information)\n{cal.drop(columns='method').T.to_string()}\n")
    rel = pd.concat({"validation": reliability(val[best]), "test": reliability(test)}, names=["period"])
    rel.to_csv(REPORTS / "scenarios_reliability.csv")
    print(f"=== P(price >= ${SPIKE:g}) reliability\n{rel.round(3).to_string()}\n")
    fct = pd.concat({"validation": fcess_table(val[best]), "test": fcess_table(test)}, names=["period", "product"])
    fct.to_csv(REPORTS / "scenarios_fcess.csv")
    print(f"=== FCESS (reported, not gated)\n{fct.round(3).to_string()}\n")
    verdict = "PASS" if cal.loc["validation", "pass"] else "FAIL"
    print(f"gate to step B (validation): {verdict} " + ", ".join(f"{g} {'ok' if cal.loc['validation', g] else 'fail'}" for g in ("G1", "G2", "G3", "G4")))
    save(bank, best)


if __name__ == "__main__":
    main()
