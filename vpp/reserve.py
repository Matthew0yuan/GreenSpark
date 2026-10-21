"""Step R of reports/q2b_plan.md, section 9: each day at 09:00, how much stored energy to keep for price spikes
in the evening window (vpp.backtest.RESERVE_WINDOW; vpp.dispatch.Reserve). Settings and the pass test were
written into the plan before the full run.

Every option is played out in every price scenario (vpp.scenarios) with the rules that will actually run - the
plan on the point forecast, its re-plans and the standing offer, interval by interval - so no option is credited
with knowing the scenario's future. A day's play: 08:00-09:00 at the prices that happened (public at 09:00);
from 09:00 the scenario's analog day's own 30-min and 5-min prices, shifted by the scenario's level shift; the
factory's load as forecast. Its money: the day's cash, the energy stored at the end at its buy-back value, less
the CMD and IRCR steering penalties on what the meter went over.

The choice does not trust one set of probabilities: over the weights the risk preferences' ambiguity allows (as
for the "dro" planner, vpp.dispatch.plan_sp), option R's regret is the most another option could earn more; the
option with the least regret is chosen, the smaller reserve when within TIE. If its regret is 0, it was best
for every allowed weighting: the choice did not depend on the probabilities.

Compared with the dispatcher as it is (R = 0 every day) on the 10 MW main route m1. For information only: a
fixed R every day; and, from the same state each day, the options' results at the actual prices, which give the
choice by the equally weighted mean and the best choice in hindsight.

Pass (validation, before vpp.evaluate.CUTOFF): the choice earns more than value per day, the 95% interval of the
daily difference above 0 (vpp.stochastic.block_ci; a day's money is its ledger plus its month's bill saving per
day). The test is printed once, for every strategy.

--replay re-runs every strategy from the recorded choices (reports/reserve_choices.csv, no plays): the bills by
month (reports/reserve_bills.csv) and the market's own interval, as the bill rests on a few monthly peaks.

Run:  python -m vpp.reserve [--smoke | --replay]
"""

import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import scenarios as sn
from vpp import site_bill as sb
from vpp import stochastic as sto
from vpp.data import PROCESSED

REPORTS = bt.REPORTS
OPTIONS = (0.0, 10.0, 20.0, 30.0)   # MWh kept for spikes [fixed in the plan]
TIE = 1.0                            # $: options closer than this count as equal; the smaller reserve is chosen
CHOICE = "choice"                    # the strategy that chooses each day
REF_PRICE = 90.0                     # $/MWh: stored energy in the adjusted worst days (findings, section 19)
FIXED = {f"R={r:g}": bt.VALUE if r == 0 else f"{bt.VALUE}|res={r:g}" for r in OPTIONS}


class Analogs:
    """Each scenario's prices to play a day out in: its analog day's actual 30-min and 5-min prices from 09:00,
    shifted by the scenario's level shift (energy: the median over the day of the scenario less the analog, which
    is the shift wherever the scenario was not clipped to past prices; FCESS: the least, as FCESS is floored at 0).
    5-min energy is kept within the range of 5-min prices seen before the day."""

    def __init__(self):
        self.sc = {d: g.sort_values(["member", "slot"]) for d, g in pd.read_parquet(sn.OUT).groupby("day")}
        cols = ["energy"] + dp.FCESS
        self.a30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=cols)
        self.a5 = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=cols)
        e5 = self.a5["energy"]
        by = e5.groupby((e5.index - bt.H).normalize())
        self.lo5 = by.min().cummin().shift(1)  # lowest 5-min price before each trading day
        self.hi5 = by.max().cummax().shift(1)

    def plays(self, d: dict) -> list[dict] | None:
        """run_day price arguments (rtp, act_f, p5, p5_f) for each scenario of day d; None without scenarios."""
        g = self.sc.get(d["day"])
        if g is None:
            return None
        first, out = sn.FIRST, []
        lo, hi = self.lo5.get(d["day"], -np.inf), self.hi5.get(d["day"], np.inf)
        for _, m in g.groupby("member"):
            a = m["analog_day"].iloc[0]
            ts = pd.date_range(a + bt.H + first * bt.HALF, periods=dp.N - first, freq="30min")
            ts5 = pd.date_range(a + bt.H + first * bt.HALF, periods=(dp.N - first) * dp.STEPS, freq="5min")
            act, act5 = self.a30.reindex(ts), self.a5.reindex(ts5)
            shift = float(np.median(m["energy"].to_numpy() - act["energy"].to_numpy()))
            p = {"rtp": np.r_[d["rtp"][:first], m["energy"].to_numpy()],
                 "p5": np.r_[d["p5"][:first * dp.STEPS], np.clip(act5["energy"].to_numpy() + shift, lo, hi)],
                 "act_f": {}, "p5_f": {}}
            for k in dp.FCESS:
                fk = float((m[k].to_numpy() - act[k].to_numpy()).min())
                p["act_f"][k] = np.r_[d["act_f"][k][:first], m[k].to_numpy()]
                p["p5_f"][k] = np.r_[d["p5_f"][k][:first * dp.STEPS], np.maximum(act5[k].to_numpy() + fk, 0.0)]
            out.append(p)
        return out


def money(summary: dict, log: pd.DataFrame, terms: dp.DayTerms) -> float:
    """A day's money for comparing options: cash, the energy stored at the end at its buy-back value, less the
    economic caps' steering penalties on what the meter went over ($/MW of the largest excess for "peak" caps,
    $/MWh over for "slot" caps)."""
    caps = {x.name: x for x in (*terms.layers, *(y for r in terms.reveals for y in r.layers)) if not x.compliance}
    pen = sum(x.penalty * summary.get(f"excess_{n}_{'mw' if x.kind == 'peak' else 'mwh'}", 0.0) for n, x in caps.items())
    return summary["total"] + float(log["stored_value"].iloc[-1]) * summary["soc_end"] - pen


def _play(task) -> float:
    kw, mwh, prices = task
    kw = dict(kw, reserve=dp.Reserve(mwh, *bt.RESERVE_WINDOW) if mwh > 0 else None)
    if prices is not None:
        kw.update(prices)
    summary, log = dp.run_day(**kw)
    return money(summary, log, kw["terms"])


def regrets(V: np.ndarray, K: np.ndarray, ambiguity: tuple) -> np.ndarray:
    """Each option's (rows of V: money per scenario) largest regret over the allowed weights: the most any other
    option earns more in expectation, at the weights that favour it most."""
    n, S = V.shape
    lo, hi = ambiguity
    amb = (K, lo * K.mean(), hi * K.mean(), lo / S, hi / S)
    out = np.zeros(n)
    for i in range(n):
        for j in range(n):
            if i != j:
                gain = V[j] - V[i]
                out[i] = max(out[i], float(dp.worst_weights(-gain, *amb) @ gain))
    return out


def least(x: np.ndarray, tie: float = TIE) -> int:
    """Index of the least value, the first (smallest reserve) among those within tie of it."""
    return int(np.flatnonzero(x <= x.min() + tie)[0])


class Chooser:
    """decide(d, kw) for vpp.backtest._route_days: plays every option out in every scenario and chooses; then, for
    information only, plays every option at the day's actual prices from the same state. Records each day."""

    def __init__(self, pool: ProcessPoolExecutor, analogs: Analogs):
        self.pool, self.analogs, self.rows = pool, analogs, []

    def __call__(self, d: dict, kw: dict) -> dp.Reserve | None:
        plays = self.analogs.plays(d)
        rec = {"day": d["day"], "soc_start": kw["s_start"]}
        if plays is None:  # no scenarios: keep nothing, as the dispatcher does now
            rec["chosen"] = 0.0
            self.rows.append(rec)
            return None
        base = dict(kw, load_act=kw["load_fc"])  # the actual load is not known at 09:00
        tasks = [(base, r, p) for r in OPTIONS for p in plays]
        V = np.array(list(self.pool.map(_play, tasks, chunksize=4))).reshape(len(OPTIONS), len(plays))
        K = np.array([(p["rtp"][sn.FIRST:] >= sn.SPIKE).sum() for p in plays], float)
        reg = regrets(V, K, kw["prefs"].ambiguity)
        i = least(reg)
        actual = np.array(list(self.pool.map(_play, [(kw, r, None) for r in OPTIONS])))  # information only
        rec.update({"chosen": OPTIONS[i], "regret": reg[i], "robust": reg[i] <= TIE, "spikes_mean": K.mean(),
                    "nominal": OPTIONS[least(-V.mean(axis=1))], "hindsight": OPTIONS[least(-actual)],
                    **{f"expected_{r:g}": v for r, v in zip(OPTIONS, V.mean(axis=1))},
                    **{f"regret_{r:g}": v for r, v in zip(OPTIONS, reg)},
                    **{f"actual_{r:g}": v for r, v in zip(OPTIONS, actual)}})
        self.rows.append(rec)
        return dp.Reserve(OPTIONS[i], *bt.RESERVE_WINDOW) if OPTIONS[i] > 0 else None


def compare(money_: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Per period and strategy: $/day, against value (block-bootstrap 95% interval), the worst 5% of days with the
    day's change in stored energy at REF_PRICE, and the worst 10% of weeks."""
    rows = []
    period = pd.Series(bt.period_of(money_.index), index=money_.index)
    for p in bt.PERIODS:
        mp = money_[period == p]
        if not len(mp):
            continue
        for name in mp.columns:
            x, diff = mp[name], mp[name] - mp["R=0"]
            lo, hi = sto.block_ci(diff.to_numpy()) if name != "R=0" else (0.0, 0.0)
            led = ledger[(ledger["strategy"] == name) & (ledger["period"] == p)].sort_values("day")
            adj = led["ledger_total"] + (led["soc_end"] - led["soc_start"]) * REF_PRICE
            weeks = led.set_index("day")["ledger_total"].resample("W").sum()
            rows.append({"period": p, "strategy": name, "days": len(x), "per_day": x.mean(), "vs_value": diff.mean(),
                         "vs_value_lo": lo, "vs_value_hi": hi,
                         "worst_5pct_days_adj": adj.nsmallest(max(1, int(np.ceil(0.05 * len(adj))))).mean(),
                         "worst_10pct_weeks": weeks.nsmallest(max(1, int(np.ceil(0.1 * len(weeks))))).mean(),
                         "reserve_held_mwh": led["reserve_held_mwh"].fillna(0.0).mean() if "reserve_held_mwh" in led else 0.0,
                         "reserve_sold_mwh": led["reserve_sold_mwh"].fillna(0.0).mean() if "reserve_sold_mwh" in led else 0.0})
    return pd.DataFrame(rows).set_index(["period", "strategy"])



def replay() -> None:
    """Every strategy again, the choice from its recorded reserves: checks the ledger reproduces, then the bill
    saving by month against R=0 and the market's daily difference with its interval."""
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    m1 = bt._sc("m1", ct.Route("m1", cmd_mw=sto.CMD, ircr="dynamic"), tuple(FIXED.values()))
    label = {v: k for k, v in FIXED.items()}
    daily, _, meters = bt.run_tasks(bt.q2b_tasks([m1]), days, aux)
    daily["strategy"] = daily["strategy"].map(label)
    meters = {(sc, label[s]): m for (sc, s), m in meters.items()}
    chosen = pd.read_csv(REPORTS / "reserve_choices.csv", parse_dates=["day"]).set_index("day")["chosen"]
    bt._init_worker(days, aux)
    rows, ms = [], []
    pick = lambda d, kw: dp.Reserve(chosen[d["day"]], *bt.RESERVE_WINDOW) if chosen.get(d["day"], 0.0) > 0 else None  # noqa: E731
    for day, _, summary, log in bt._route_days(m1, bt.VALUE, decide=pick):
        rows.append({"scenario": "m1", "strategy": CHOICE, "day": day, **summary})
        ms.append(log["meter_mw"].to_numpy().astype(np.float32))
    choice = pd.DataFrame(rows)
    choice["period"] = bt.period_of(choice["day"])
    meters["m1", CHOICE] = np.vstack(ms)
    ledger = bt.q2b_ledger(pd.concat([daily, choice], ignore_index=True), [m1])
    run = pd.read_csv(REPORTS / "reserve_daily.csv", parse_dates=["day"]).set_index(["strategy", "day"])["ledger_total"]
    gap = float((ledger.set_index(["strategy", "day"])["ledger_total"].reindex(run.index) - run).abs().max())
    print(f"the replay reproduces the recorded run's ledger: largest difference ${gap:.2g}")
    bills, _ = bt.q2b_bills(meters, days, [m1], cmd_nb)
    bills.to_csv(REPORTS / "reserve_bills.csv", index=False)
    b = bills.assign(per_day=bills["saving"] / bills["days"]).pivot_table(index="month", columns="strategy", values="per_day")
    print(f"=== bill saving $/day by month, against R=0\n{b.sub(b['R=0'], axis=0).drop(columns='R=0').round(0).to_string()}\n")
    led = ledger.pivot_table(index="day", columns="strategy", values="ledger_total")
    period = pd.Series(bt.period_of(led.index), index=led.index)
    print("=== the market alone (daily ledger), against R=0")
    for p in bt.PERIODS:
        for s_ in [*list(FIXED)[1:], CHOICE]:
            diff = (led[s_] - led["R=0"])[period == p].to_numpy()
            lo, hi = sto.block_ci(diff)
            print(f"  {p} {s_}: {diff.mean():+.1f} $/day, 95% interval {lo:+.1f} .. {hi:+.1f}")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--replay" in argv:
        return replay()
    smoke = "--smoke" in argv
    days = bt.load_days("0900")
    if smoke:
        days = [d for d in days if d["day"].month == 1][:10]
    aux = bt.q2b_aux()
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    route = ct.Route("m1", cmd_mw=sto.CMD, ircr="dynamic")
    m1 = bt._sc("m1", route, tuple(FIXED.values()))
    label = {v: k for k, v in FIXED.items()}

    # a fixed reserve every day (R = 0: the dispatcher as it is)
    daily, _, meters = bt.run_tasks(bt.q2b_tasks([m1]), days, aux)
    daily["strategy"] = daily["strategy"].map(label)
    meters = {(sc, label[s]): m for (sc, s), m in meters.items()}

    # the daily choice: one day after another, the plays of each day in parallel
    bt._init_worker(days, aux)
    rows, ms = [], []
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as pool:
        chooser = Chooser(pool, Analogs())
        for day, _, summary, log in bt._route_days(m1, bt.VALUE, decide=chooser):
            rows.append({"scenario": "m1", "strategy": CHOICE, "day": day, **summary})
            ms.append(log["meter_mw"].to_numpy().astype(np.float32))
    choice = pd.DataFrame(rows)
    choice["period"] = bt.period_of(choice["day"])
    meters["m1", CHOICE] = np.vstack(ms)
    daily = pd.concat([daily, choice], ignore_index=True)
    daily["soc_start"] = daily.sort_values("day").groupby("strategy")["soc_end"].shift(1).fillna(0.0)

    ledger = bt.q2b_ledger(daily, [m1])
    bills, ircr = bt.q2b_bills(meters, days, [m1], cmd_nb)
    table = compare(sto.daily_money(ledger, bills), ledger)
    days_rec = pd.DataFrame(chooser.rows)
    days_rec["period"] = bt.period_of(days_rec["day"])
    tag = "_smoke" if smoke else ""
    ledger.to_csv(REPORTS / f"reserve_daily{tag}.csv", index=False)
    days_rec.to_csv(REPORTS / f"reserve_choices{tag}.csv", index=False)
    table.to_csv(REPORTS / f"reserve_result{tag}.csv")

    print(f"=== $/day, m1, value offer rule, by evening reserve; the 95% interval against R=0 is a "
          f"{sto.BLOCK}-day block bootstrap\n{table.round(1).to_string()}\n")
    r = days_rec.dropna(subset=["regret"])
    for p, g in r.groupby("period"):
        paired = {"choice": g.apply(lambda x: x[f"actual_{x['chosen']:g}"], axis=1),
                  "nominal": g.apply(lambda x: x[f"actual_{x['nominal']:g}"], axis=1),
                  "hindsight": g.apply(lambda x: x[f"actual_{x['hindsight']:g}"], axis=1),
                  **{f"R={o:g}": g[f"actual_{o:g}"] for o in OPTIONS}}
        base = paired["R=0"]
        print(f"--- {p}: {len(g)} days; chosen {g['chosen'].value_counts().sort_index().to_dict()}; the choice did not "
              f"depend on the probabilities on {g['robust'].mean():.0%} of days")
        print("    from the same state each day, $/day at the actual prices against R=0: "
              + ", ".join(f"{k} {(v - base).mean():+.1f}" for k, v in paired.items() if k != "R=0"))
        exp = g.apply(lambda x: x[f"expected_{x['chosen']:g}"], axis=1)
        print(f"    the plays' expected money of the chosen option less its actual result: {(exp - paired['choice']).mean():+.1f} $/day")
    print(f"\n=== IRCR, {bt.IRCR_SEASON}-{bt.IRCR_SEASON - 1999} Hot Season ($/year)\n"
          f"{ircr[['strategy', 'ircr_mw', 'saving_per_year', 'peak_days_covered']].round(2).to_string(index=False)}\n")
    if "validation" in table.index.get_level_values(0):
        v = table.loc[("validation", CHOICE)]
        print(f"pass (validation): {'yes' if v['vs_value_lo'] > 0 else 'no'} "
              f"({v['vs_value']:+.1f} $/day, 95% interval {v['vs_value_lo']:+.1f} .. {v['vs_value_hi']:+.1f})")


if __name__ == "__main__":
    main()
