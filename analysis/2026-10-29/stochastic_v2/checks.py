"""Checks of the stochastic plans wired into v2 (PLAN.md section 5), before and beside the full run. Each says what it
can catch.

  C1 CVaR algebra (plan_sp on random scenarios, no site): beta = 0 gives sp's objective; the objective equals
     (1 - beta) x mean(values) + beta x mean(worst alpha S values) recomputed from the scenario values; the cvar plan's
     CVaR is at least sp's and its mean at most sp's. Catches a wrong sign or scale in the CVaR rows or cost.
  C2 non-anticipativity: with the first stage covering the whole horizon, sp equals plan on the scenarios' mean
     prices (objective). Catches first-stage columns that are not tied across scenarios.
  C3 degenerate scenarios: 20 scenarios all equal to the 09:00 forecast - sp runs the same days as e5 (money per
     day within $1). Catches the wiring giving the scenario plans other prices, floors, caps or end values than e5's.
  C4 future prices scrambled: actual prices of the day from interval t on, and all of the next day's, replaced by
     random ones; what the battery did before t must not move (sp, cvar). Positive controls: (a) the same scramble with
     a planted leak (scenario 0 = the day's actual prices) must move something on some day; (b) scrambling only the
     interval before t must move something on some day. Catches a plan, offer or scenario that reads prices not yet
     published.
  C5 days without scenarios fall back to roll and are counted (planner_fallback); the real run must have none.
  C6 options (plan_options, added after the full run started): on random days with FCESS prices, each option's half-hour
     does what it says and none beats the chosen plan.

  .venv/bin/python analysis/2026-10-29/stochastic_v2/checks.py
"""

import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

import scen  # noqa: E402  (this folder)

RESULTS: list[tuple[str, bool, str]] = []
C3_DAYS = ("2026-01-12", "2026-01-14")
C4_DAYS = ("2025-08-05", "2025-12-16", "2026-02-10", "2026-05-19", "2026-08-11")
C4_SLOTS = (12, 20, 30)  # 14:00, 18:00, 23:00
ACT_COLS = ["energy_mwh", "soc", *[f"{k}_mw" for k in dp.FCESS]]


def report(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"{name}: {'PASS' if ok else 'FAIL'} - {detail}", flush=True)


def c1_c2() -> None:
    rng = np.random.default_rng(7)
    asset = bt.Q2B_ASSET
    S, n = 20, 40
    base = 80 + 60 * np.sin(np.linspace(0, 3 * np.pi, n))
    worst_gap = mix_gap = 0.0
    tail_ok = mean_ok = True
    for trial in range(6):
        pe = base + rng.normal(0, 40, (S, n)) + rng.choice([0, 0, 0, 400], (S, 1)) * (np.arange(n) % 17 == 9)
        kw = dict(s_init=float(rng.uniform(5, 35)), budget=80.0, terminal_value=60.0, wear_per_mwh=105.0)
        sp = dp.plan_sp(asset, pe, None, 1, **kw)
        b0 = dp.plan_sp(asset, pe, None, 1, cvar=(0.2, 0.0), **kw)
        cv = dp.plan_sp(asset, pe, None, 1, cvar=(0.2, 0.5), **kw)
        worst_gap = max(worst_gap, abs(b0["objective"] - sp["objective"]))
        v = np.sort(cv["values"])
        mix = 0.5 * v.mean() + 0.5 * v[:4].mean()
        mix_gap = max(mix_gap, abs(mix - cv["objective"]))
        tail_ok &= cv["cvar"] >= np.sort(sp["values"])[:4].mean() - 1e-4
        mean_ok &= cv["values"].mean() <= sp["values"].mean() + 1e-4
    report("C1 CVaR algebra", worst_gap < 1e-3 and mix_gap < 1e-3 and tail_ok and mean_ok,
           f"|beta=0 - sp| {worst_gap:.2e}; |objective - recomputed mix| {mix_gap:.2e}; cvar's CVaR >= sp's {tail_ok}; "
           f"cvar's mean <= sp's {mean_ok} (6 random days)")
    gap = 0.0
    for trial in range(3):
        pe = base + rng.normal(0, 40, (S, n))
        kw = dict(s_init=20.0, budget=80.0, terminal_value=60.0, wear_per_mwh=105.0)
        whole = dp.plan_sp(asset, pe, None, n, **kw)
        mean = dp.plan(asset, pe.mean(axis=0), None, **kw)
        gap = max(gap, abs(whole["objective"] - mean["value"] + mean.get("penalty", 0.0)))
    report("C2 non-anticipativity", gap < 1e-3, f"|sp with a whole-day first stage - plan on the mean| {gap:.2e} $ (3 days)")


def c6() -> None:
    """C6 options (added 2026-10-29 with plan_options): on random days with FCESS prices, each option's first half-hour
    does what it says, none beats the chosen plan, and the option of the chosen plan's kind equals it. Catches
    restrictions on the wrong columns or not tied across scenarios."""
    rng = np.random.default_rng(5)
    asset = bt.Q2B_ASSET
    S, n = 20, 30
    ok, worst, n_opt = True, 0.0, 0
    for trial in range(5):
        pe = 90 + 60 * np.sin(np.linspace(0, 2 * np.pi, n) + trial) + rng.normal(0, 40, (S, n))
        pf = [{k: np.abs(rng.normal(8 + 6 * (k == "reg_raise"), 4, n)) for k in dp.FCESS} for _ in range(S)]
        kw = dict(s_init=float(rng.uniform(5, 35)), budget=80.0, terminal_value=70.0, wear_per_mwh=30.0)
        chosen = dp.plan_sp(asset, pe, pf, 1, **kw)
        opts = dp.plan_options(asset, pe, pf, 1, chosen, **kw)
        c = opts["chosen"]
        for name in dp.OPTIONS:
            o = opts[name]
            if not np.isfinite(o["ev"]):
                continue
            n_opt += 1
            lo = dp.OPTION_MW - 1e-6
            kind = {"sell": o["net"] >= lo and o["fcess"] <= 1e-6, "charge": -o["net"] >= lo and o["fcess"] <= 1e-6,
                    "fcess": abs(o["net"]) <= 1e-6 and o["fcess"] >= lo,
                    "hold": abs(o["net"]) <= 1e-6 and o["fcess"] <= 1e-6}[name]
            tol = max(1.0, 2e-4 * abs(c["ev"]))
            ok &= kind and o["ev"] <= c["ev"] + tol
            worst = max(worst, o["ev"] - c["ev"])
    report("C6 options consistent", ok, f"{n_opt} feasible options on 5 random days; largest option ev - chosen ev "
           f"{worst:.3f} $ (must be <= 0 up to the MIP gap); each option's half-hour of its own kind")


def run_days(days: list[dict], cfg, soc0: float = 0.0, until=None) -> dict:
    """Run cfg through days (the worker's day list); {day: (summary, log)}."""
    bt._DAYS = days
    out = {}
    for day, _, s, log, _ in hs.route_days(vb.m1(), cfg, soc0=soc0, until=until):
        out[day] = (s, log)
    return out


def c3(days: list[dict]) -> None:
    sel = [d for d in days if pd.Timestamp(C3_DAYS[0]) <= d["day"] <= pd.Timestamp(C3_DAYS[1])]
    flat = []
    for d in sel:
        d = dict(d)
        n_s = len(d["scen"]["energy"])
        e = np.r_[d["prices"]["bridge"][0][:2], d["fc0900"][2:]]
        f = d["prices"]["forecast"][1]
        d["scen"] = {**d["scen"], "energy": np.tile(e, (n_s, 1)), "fcess": {k: np.tile(f[k], (n_s, 1)) for k in dp.FCESS}}
        flat.append(d)
    e5 = run_days(flat, replace(vb.E5, record=False))
    sp = run_days(flat, replace(vb.E5, name="sp", planner="sp", record=False))
    gaps = [abs(sp[k][0]["total"] - e5[k][0]["total"]) for k in e5]
    report("C3 degenerate scenarios", max(gaps) < 1.0,
           f"sp with 20 copies of the forecast vs e5, money per day: largest |gap| ${max(gaps):.3f} over {len(gaps)} days")


def scramble(d: dict, t: int, rng, upto: int | None = None) -> dict:
    """d with actual prices from interval t on (or only intervals upto..t-1 if upto is given) replaced."""
    d = dict(d)
    lo, hi = (t, dp.N) if upto is None else (upto, t)
    rtp = np.array(d["rtp"], float)
    rtp[lo:hi] = rng.uniform(-50, 600, hi - lo)
    p5 = np.array(d["p5"], float)
    p5[lo * dp.STEPS:hi * dp.STEPS] = np.repeat(rtp[lo:hi], dp.STEPS)
    act_f = {k: np.array(v, float) for k, v in d["act_f"].items()}
    p5_f = {k: np.array(v, float) for k, v in d["p5_f"].items()}
    for k in dp.FCESS:
        act_f[k][lo:hi] = rng.uniform(0, 80, hi - lo)
        p5_f[k][lo * dp.STEPS:hi * dp.STEPS] = np.repeat(act_f[k][lo:hi], dp.STEPS)
    d.update(rtp=rtp, p5=p5, act_f=act_f, p5_f=p5_f, prices={**d["prices"], "actual": (rtp, act_f)})
    return d


def plant(d: dict) -> dict:
    """A planted leak: scenario 0 is the day's actual prices."""
    d = dict(d)
    e = np.array(d["scen"]["energy"], float)
    e[0] = d["rtp"]
    d["scen"] = {**d["scen"], "energy": e}
    return d


def moved(a: pd.DataFrame, b: pd.DataFrame, t: int) -> float:
    return float(np.abs(a[ACT_COLS].to_numpy()[:t] - b[ACT_COLS].to_numpy()[:t]).max()) if t else 0.0


def c4(days: list[dict]) -> None:
    by = {d["day"]: d for d in days}
    rng = np.random.default_rng(11)
    for planner in ("sp", "cvar"):
        cfg = replace(vb.E5, name=planner, planner=planner, record=False)
        worst, leak_moves, before_moves, n = 0.0, 0, 0, 0
        for ds in C4_DAYS:
            day = pd.Timestamp(ds)
            pair = [by[day], by[day + pd.Timedelta(days=1)]]
            soc0 = 20.0
            ref = run_days(pair, cfg, soc0, until=day)[day][1]
            ref_leak = run_days([plant(pair[0]), pair[1]], cfg, soc0, until=day)[day][1]
            for t in C4_SLOTS:
                nxt = scramble(pair[1], 0, rng)
                s = scramble(pair[0], t, rng)
                got = run_days([s, nxt], cfg, soc0, until=day)[day][1]
                worst = max(worst, moved(ref, got, t))
                leak = run_days([plant(s), nxt], cfg, soc0, until=day)[day][1]
                leak_moves += moved(ref_leak, leak, t) > 1e-6
                early = run_days([scramble(pair[0], t, rng, upto=t - 1), pair[1]], cfg, soc0, until=day)[day][1]
                before_moves += moved(ref, early, t) > 1e-6
                n += 1
        report(f"C4 future scrambled ({planner})", worst < 1e-6 and leak_moves > 0 and before_moves > 0,
               f"largest move before t {worst:.2e} over {n} (day, t); positive controls moved: planted leak "
               f"{leak_moves}/{n}, interval before t {before_moves}/{n}")


def c5(days: list[dict]) -> None:
    sel = [d for d in days if pd.Timestamp(C3_DAYS[0]) <= d["day"] <= pd.Timestamp(C3_DAYS[1])]
    sel = [dict(d, scen=None) for d in sel]
    out = run_days(sel, replace(vb.E5, name="sp", planner="sp", record=False))
    e5 = run_days(sel, replace(vb.E5, record=False))
    fb = sum(s["planner_fallback"] for s, _ in out.values())
    gap = max(abs(out[k][0]["total"] - e5[k][0]["total"]) for k in out)
    report("C5 fallback without scenarios", fb == len(out) and gap < 1e-9,
           f"{fb}/{len(out)} days counted as fallback; money vs e5 largest |gap| {gap:.2e}")


def main() -> None:
    part = sys.argv[1] if len(sys.argv) > 1 else "fast"  # "fast": C1-C3, C5 (before the full run); "c4"
    if part == "fast":
        c1_c2()
        c6()
    days = bt.load_days("0900")
    missing = scen.attach(days)
    print(f"{len(days)} days, {missing} without scenarios")
    for t in vb.STEPS["e5"]["tables"]:
        vl.build(t[0], t[1], hurdle=t[2], state=len(t) > 3 and t[3])
    bt._init_worker(days, bt.q2b_aux())
    if part == "fast":
        c3(days)
        c5(days)
    else:
        c4(days)
    pd.DataFrame(RESULTS, columns=["check", "pass", "detail"]).to_csv(HERE / f"checks_{part}.csv", index=False)
    print(f"{sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)} pass")


if __name__ == "__main__":
    main()
