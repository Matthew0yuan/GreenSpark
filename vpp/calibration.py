"""Step D of reports/q2b_plan.md, section 9: a Bayesian calibration of the scenarios' spike probability, and
the range of scenario weights (the ambiguity set) it leaves for the stochastic plan. Built and checked before
any optimiser uses it; the design, the grid and the gate were written into the plan before the first run.

Why. Step A's scenarios (vpp.scenarios) give P(price >= SPIKE) that is right on average before the cutoff
(0.93 of what happened) but drifts: 0.59 .. 1.99 by month, 1.39 after the cutoff. A fixed correction cannot
fix a drift, so the plan works with a range: how far the probability may be off, from the data, updated
every day. The posterior mean alone would not make decisions robust (expected value is linear in the
probability); the stochastic plan takes the worst case over the range.

Model. A half-hour whose scenarios have c of the S members at or above SPIKE falls in bin BIN_OF[c]
(0 | 1 | 2-3 | 4-6 | 7-10 | 11-20 members). Each bin's actual spike rate q ~ Beta, prior mean = the bin's
smoothed share (c_mid + 0.5) / (S + 1), prior strength PRIOR_N half-hours [fixed]. After each trading day
the bin's half-hours update it, as effective counts: n / (1 + (n - 1) rho) for the same-day clustering of
spikes (rho = within-day correlation in a bin); evidence is discounted with half-life h days before each
new day (drift). The 09:00 plan of day D uses the trading days before D only (all over by 08:00).
Scenarios exist from late 2024-07; the days before EVAL_START only build up evidence.

Grid [fixed]: h in HALF_LIVES, rho in RHOS. Gate, on validation (before vpp.evaluate.CUTOFF), day by day:
  D1  sum of predicted probabilities / spike half-hours in 0.8 .. 1.25
  D2  the day's number of spike half-hours against its predictive distribution (posterior q, then the
      same-day clustering): randomised PIT inside 0.1 .. 0.9 on 75-85% of days, each tail 5-15%
  D3  Brier score per half-hour not worse than the raw scenarios: 95% interval of the daily difference
      has its lower end <= 0
Choice: among the settings that pass, the lowest CRPS of the day's count. None passing: stop (no step B).
The test (from CUTOFF) is scored once, for the chosen setting.

Ambiguity set, per day: [L, U] = the 80% interval of the expected number of spike half-hours (parameter
uncertainty only); scenario weights w >= 0, sum 1, with sum_s w_s K_s in [L, U] (K_s: member s's spike
half-hours). Nominal weights: the equal weights tilted exponentially to the posterior mean count.

Run:  python -m vpp.calibration
"""

import numpy as np
import pandas as pd

from vpp import scenarios as sc
from vpp.backtest import bootstrap_ci
from vpp.data import PROCESSED, ROOT
from vpp.evaluate import CUTOFF, period_of

REPORTS = ROOT / "reports"
OUT = PROCESSED / "calibration_0900.parquet"
METHOD = "anen365_vol_level"                     # step A's choice
S, SPIKE = sc.S, sc.SPIKE
BIN_OF = np.array([0, 1, 2, 2, 3, 3, 3, 4, 4, 4, 4] + [5] * 10)   # members at or above SPIKE (0..20) -> bin
BIN_MID = np.array([0.0, 1.0, 2.5, 5.0, 8.5, 15.5])                # each bin's middle member count
NB = len(BIN_MID)
PRIOR_MEAN = (BIN_MID + 0.5) / (S + 1)
PRIOR_N = 20.0                                   # prior strength, half-hours [fixed]
HALF_LIVES = (30, 90, 365, None)                 # days; None: no discount [fixed grid]
RHOS = (0.0, 0.05, 0.15, 0.30)                   # within-day correlation of spikes in a bin [fixed grid]
DRAWS = 2000
LEVEL = 0.8                                      # the ambiguity set's interval
GATE = {"D1": (0.8, 1.25), "D2": (0.75, 0.85), "tail": (0.05, 0.15)}


def days() -> list[dict]:
    """Every day with scenarios: its half-hours' bins and outcomes, the raw probability, each member's count."""
    bank = sc.Bank(*sc.history())
    out = []
    for i, d in enumerate(bank.dates):
        r = bank.scenarios(i, METHOD)
        if r is None:
            continue
        hit = r[0] >= SPIKE                      # (S, slots)
        c = hit.sum(axis=0)
        out.append({"day": d, "bin": BIN_OF[c], "p_raw": c / S, "y": bank.act_e[i] >= SPIKE, "K": hit.sum(axis=1)})
    return out


def _check_saved(rows: list[dict]) -> None:
    """The members' counts must match step A's saved scenarios (data/processed/scenarios_0900.parquet)."""
    saved = pd.read_parquet(sc.OUT, columns=["day", "member", "energy"])
    k = (saved["energy"] >= SPIKE).groupby([saved["day"], saved["member"]]).sum().unstack()
    mine = pd.DataFrame({r["day"]: r["K"] for r in rows if r["day"] in k.index}).T
    if not np.array_equal(mine.to_numpy(), k.reindex(mine.index).to_numpy()):
        raise RuntimeError("scenarios differ from step A's saved ones")


def _predictive(rng, a, b, n, rho):
    """Draws of (the expected count sum_b n_b q_b, the realised count) for one day's bins n."""
    q = np.clip(rng.beta(a, b, size=(DRAWS, NB)), 1e-9, 1 - 1e-9)
    expected = q @ n
    p = q if rho == 0 else rng.beta(q * (1 - rho) / rho, (1 - q) * (1 - rho) / rho)
    k = rng.binomial(np.broadcast_to(n, (DRAWS, NB)).astype(int), p).sum(axis=1)
    return expected, k


def _crps(x: np.ndarray, y: float) -> float:
    xs = np.sort(x)
    n = len(xs)
    return float(np.abs(xs - y).mean() - ((2 * np.arange(1, n + 1) - n - 1) * xs).sum() / n**2)


def run(rows: list[dict], half_life, rho, seed: int = 0) -> pd.DataFrame:
    """Day-by-day predictions of one setting: each day from the evidence of the days before it only."""
    rng = np.random.default_rng(seed)
    a0, b0 = PRIOR_N * PRIOR_MEAN, PRIOR_N * (1 - PRIOR_MEAN)
    A, B, last = np.zeros(NB), np.zeros(NB), None
    out = []
    for r in rows:
        d = r["day"]
        if last is not None and half_life is not None:
            f = 0.5 ** ((d - last).days / half_life)
            A, B = A * f, B * f
        n = np.bincount(r["bin"], minlength=NB).astype(float)
        k = np.bincount(r["bin"], weights=r["y"].astype(float), minlength=NB)
        if d >= sc.EVAL_START.normalize():
            a, b = a0 + A, b0 + B
            q_hat = a / (a + b)
            expected, draws = _predictive(rng, a, b, n, rho)
            obs = int(r["y"].sum())
            pit = (draws < obs).mean() + rng.uniform() * (draws == obs).mean()
            lo, hi = np.quantile(expected, [(1 - LEVEL) / 2, (1 + LEVEL) / 2])
            p = q_hat[r["bin"]]
            out.append({"day": d, "pred": p.sum(), "raw": r["p_raw"].sum(), "obs": obs, "pit": pit,
                        "crps": _crps(draws, obs), "brier": ((p - r["y"]) ** 2).mean(),
                        "brier_raw": ((r["p_raw"] - r["y"]) ** 2).mean(), "L": lo, "U": hi, "mean": float(q_hat @ n)})
        n_eff = n / (1 + (n - 1).clip(min=0) * rho)
        k_eff = np.divide(k * n_eff, n, out=np.zeros(NB), where=n > 0)
        A, B, last = A + k_eff, B + (n_eff - k_eff), d
    t = pd.DataFrame(out)
    t["period"] = period_of(t["day"])
    return t


def summary(t: pd.DataFrame) -> dict:
    lo, _ = bootstrap_ci((t["brier"] - t["brier_raw"]).to_numpy())
    inside = ((t["pit"] >= 0.1) & (t["pit"] <= 0.9)).mean()
    below, above = (t["pit"] < 0.1).mean(), (t["pit"] > 0.9).mean()
    out = {"days": len(t), "ratio": t["pred"].sum() / t["obs"].sum(), "raw_ratio": t["raw"].sum() / t["obs"].sum(),
           "pit_inside": inside, "pit_below": below, "pit_above": above, "crps": t["crps"].mean(),
           "brier": t["brier"].mean(), "brier_raw": t["brier_raw"].mean(), "brier_diff_lo": lo,
           "interval_width": ((t["U"] - t["L"]) / t["mean"].clip(lower=1e-9)).median()}
    out["D1"] = GATE["D1"][0] <= out["ratio"] <= GATE["D1"][1]
    out["D2"] = GATE["D2"][0] <= inside <= GATE["D2"][1] and all(GATE["tail"][0] <= x <= GATE["tail"][1] for x in (below, above))
    out["D3"] = lo <= 0
    out["pass"] = out["D1"] and out["D2"] and out["D3"]
    return out


def tilt(K: np.ndarray, target: float) -> np.ndarray:
    """Equal weights tilted exponentially so that sum w K = target (the nearest end when out of reach)."""
    if K.max() == K.min():
        return np.full(len(K), 1 / len(K))
    target = min(max(target, K.min()), K.max())
    lo, hi = -50.0, 50.0
    for _ in range(100):
        eta = (lo + hi) / 2
        w = np.exp(eta * (K - K.mean()))
        w /= w.sum()
        lo, hi = (eta, hi) if w @ K < target else (lo, eta)
    return w


def ambiguity(rows: list[dict], t: pd.DataFrame) -> pd.DataFrame:
    """Per day and member: the member's spike count, its nominal weight, and the day's [L, U]."""
    by_day = t.set_index("day")
    parts = []
    for r in rows:
        if r["day"] not in by_day.index:
            continue
        x = by_day.loc[r["day"]]
        K = r["K"].astype(float)
        parts.append(pd.DataFrame({"day": r["day"], "member": np.arange(S), "K": r["K"], "w_nominal": tilt(K, x["mean"]),
                                   "L": x["L"], "U": x["U"], "expected": x["mean"], "actual": x["obs"],
                                   "reachable": bool(K.max() >= x["L"] and K.min() <= x["U"])}))
    out = pd.concat(parts, ignore_index=True)
    out["period"] = period_of(out["day"])
    return out


def main() -> None:
    rows = days()
    _check_saved(rows)
    print(f"{len(rows)} days with scenarios, {rows[0]['day']:%Y-%m-%d} .. {rows[-1]['day']:%Y-%m-%d}; "
          f"scored from {sc.EVAL_START:%Y-%m-%d}, test from {CUTOFF:%Y-%m-%d}\n")
    grid = {}
    for h in HALF_LIVES:
        for rho in RHOS:
            t = run(rows, h, rho)
            grid[(h, rho)] = (t, summary(t[t["period"] == "validation"]))
    choice = pd.DataFrame({f"h={h or 'none'}, rho={rho}": s for (h, rho), (_, s) in grid.items()}).T
    choice.index.name = "setting"
    choice.to_csv(REPORTS / "calibration_choice.csv")
    passing = [k for k, (_, s) in grid.items() if s["pass"]]
    pd.set_option("display.width", 250)
    print("=== validation, every setting\n" + choice.astype(float).round(3).to_string() + "\n")
    if not passing:
        print("no setting passes D1-D3 on validation: stop, no step B")
        return
    best = min(passing, key=lambda k: grid[k][1]["crps"])
    t = grid[best][0]
    res = pd.DataFrame({p: summary(t[t["period"] == p]) for p in ("validation", "test")}).T
    res.insert(0, "setting", f"h={best[0] or 'none'}, rho={best[1]}")
    res.to_csv(REPORTS / "calibration_result.csv")
    print(f"=== chosen h={best[0]}, rho={best[1]} (lowest validation CRPS of {len(passing)} passing); test scored once\n"
          + res.drop(columns="setting").astype(float).round(3).T.to_string() + "\n")
    m = t.assign(month=t["day"].dt.to_period("M")).groupby("month").agg(
        obs=("obs", "sum"), raw=("raw", "sum"), bayes=("pred", "sum"), L=("L", "sum"), U=("U", "sum"))
    m["raw_ratio"], m["bayes_ratio"] = m["raw"] / m["obs"], m["bayes"] / m["obs"]
    m["inside_80"] = (m["obs"] >= m["L"]) & (m["obs"] <= m["U"])
    m.to_csv(REPORTS / "calibration_monthly.csv")
    print("=== by month (for information, not gated): spike half-hours, raw and Bayesian predictions, the summed 80% range\n" + m.round(2).to_string() + "\n")
    amb = ambiguity(rows, t)
    amb.to_parquet(OUT)
    day = amb.drop_duplicates("day")
    print(f"saved {OUT}: {day['day'].nunique()} days; 20 scenarios cannot reach [L, U] on "
          f"{int((~day['reachable']).sum())} days")


if __name__ == "__main__":
    main()
