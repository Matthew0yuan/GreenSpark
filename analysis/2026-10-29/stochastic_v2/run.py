"""PLAN.md: e5 and its stochastic versions on m1, every backtest day; the pass rule of PLAN.md section 7 on the
in-sample days, the test's two parts reported.

  .venv/bin/python analysis/2026-10-29/stochastic_v2/run.py [--smoke]
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
for p in (ROOT, str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp import stochastic as sto  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

import scen  # noqa: E402  (this folder)

E5 = replace(vb.E5, name="e5", record=False)
CONFIGS = (E5, *(replace(E5, name=f"roll{h}", plan_hurdle=float(h)) for h in (50, 25, 0)),
           replace(E5, name="mean75", planner="mean"),
           *(replace(E5, name=f"sp{h}", planner="sp", plan_hurdle=float(h)) for h in (75, 50, 25, 0)),
           replace(E5, name="cvar0", planner="cvar", plan_hurdle=0.0))
CANDIDATES = {"sp75": "e5", "sp50": "roll50", "sp25": "roll25", "sp0": "roll0", "cvar0": "roll0"}  # -> same-hurdle roll
UNSEEN = pd.Timestamp("2026-10-16")
PARTS = ("in-sample", "test seen", "test unseen")
STORED = 90.0  # $/MWh: the day's change in stored energy in the tail metric (findings 19.3) [fixed in PLAN.md]
COMPLIANCE = ("plans_short", "plan_shortfall_mwh", "capacity_short_mw_ti", "budget_overrun_mwh", "timing_errors")
TOL = 1e-6


def part_of(day) -> np.ndarray:
    d = pd.to_datetime(pd.Series(day))
    return np.where(d < bt.CUTOFF, PARTS[0], np.where(d < UNSEEN, PARTS[1], PARTS[2]))


def _timed(task: tuple) -> tuple:
    t0 = time.time()
    return (*vb._run(task), time.time() - t0)


def run(days: list[dict], aux: dict, configs) -> tuple:
    sc = vb.m1()
    daily, logs, meters, seconds = [], {}, {}, {}
    tasks = [(sc, cfg, True) for cfg in configs]
    tasks.sort(key=lambda t: t[1].planner in ("", "mean"))  # the slow ones first
    workers = min(len(tasks), max(1, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers, initializer=bt._init_worker, initargs=(days, aux)) as pool:
        for s, name, rows, lg, meter, _, dt in pool.map(_timed, tasks):
            daily.extend(rows)
            logs[s, name], meters[s, name], seconds[name] = lg, meter, dt
            print(f"  {name}: {len(rows)} days in {dt / 60:.1f} min", flush=True)
    daily = pd.DataFrame(daily)
    daily["period"] = bt.period_of(daily["day"])
    return sc, daily, logs, meters, seconds


def balance(by_day: dict) -> tuple[float, float, float]:
    """Energy from nothing, from the log alone: in each half-hour, with e the battery's net MWh out, the stored energy
    can rise at most min(-e / eta, -e x eta) (all one way, losses charged; charging and discharging in the same
    half-hour only loses more). Returns the largest rise above that bound, the largest gap between a day's starting
    stored energy and the day before's last, MWh, and the mean MWh a day lost beyond the bound (round trips within a
    half-hour)."""
    eta = bt.Q2B_ASSET.battery.eta
    worst = chain = lost = 0.0
    prev_end = None
    for day in sorted(by_day):
        lg = by_day[day]
        soc, e = lg["soc"].to_numpy(), lg["energy_mwh"].to_numpy()
        start = float(lg["soc_start"].iloc[0])
        if prev_end is not None:
            chain = max(chain, abs(start - prev_end))
        rise = soc - np.r_[start, soc[:-1]]
        bound = np.minimum(-e / eta, -e * eta)
        worst = max(worst, float((rise - bound).max()))
        lost += float((bound - rise).sum())
        prev_end = float(soc[-1])
    return worst, chain, lost / len(by_day)


def physics(daily: pd.DataFrame, logs: dict) -> pd.DataFrame:
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    rows = []
    for (_, name), by_day in logs.items():
        lg = pd.concat(by_day.values())
        g = daily[daily["strategy"] == name]
        free, chain, lost = balance(by_day)
        rows.append({"config": name, "power_over": int((np.abs(lg["energy_mwh"]) / dp.DT > P + TOL).sum()),
                     "soc_outside": int(((lg["soc"] < -TOL) | (lg["soc"] > E + TOL)).sum()),
                     "free_energy_max_mwh": free, "day_chain_gap_mwh": chain, "inner_loss_mwh_per_day": lost,
                     "fallback_days": int(g["planner_fallback"].sum()),
                     **{c: float(g[c].sum()) for c in COMPLIANCE if c in g}})
    return pd.DataFrame(rows).set_index("config")


def against(mp: pd.DataFrame, lp: pd.DataFrame, bp: pd.DataFrame, name: str, base: str, months: bool) -> dict:
    """Gain over base: mean, month-block interval (if months), 7-day block interval, market and bill parts."""
    diff = mp[name] - mp[base]
    row = {"vs": base, "gain": diff.mean()}
    if months and len(diff) > 7:
        row.update(zip(("gain_lo", "gain_hi"), vb.month_ci(diff)))
        row.update(zip(("gain_7d_lo", "gain_7d_hi"), sto.block_ci(diff.to_numpy())))
    row["market"] = (lp[name] - lp[base]).mean()
    row["bill"] = row["gain"] - row["market"]
    if bp is not None and len(bp):
        d = bp[name] - bp[base]
        row["bill_months_better"], row["bill_months_worse"] = int((d > 1.0).sum()), int((d < -1.0).sum())
    return row


def tails(money: pd.DataFrame, daily: pd.DataFrame, part: pd.Series) -> pd.DataFrame:
    """Worst 5% of days with the stored energy change at STORED $/MWh, and the worst 10% of calendar weeks."""
    soc = daily.pivot_table(index="day", columns="strategy", values="soc_end")
    soc.index = pd.to_datetime(soc.index)
    start = soc.shift(1).fillna(0.0)
    adj = money + (soc - start).reindex(money.index) * STORED
    rows = []
    for p in PARTS[:2]:
        sel = part == p
        for name in money.columns:
            x, a = money.loc[sel, name], adj.loc[sel, name]
            k = max(1, int(np.ceil(0.05 * len(x))))
            weeks = x.groupby(x.index.to_period("W")).sum()
            weeks = weeks[x.groupby(x.index.to_period("W")).size() == 7]
            kw = max(1, int(np.ceil(0.10 * len(weeks))))
            rows.append({"period": p, "config": name, "worst5_days_stored": a.nsmallest(k).mean(),
                         "worst10_weeks": weeks.nsmallest(kw).mean(), "full_weeks": len(weeks)})
    return pd.DataFrame(rows).set_index(["period", "config"])


def main() -> None:
    smoke = "--smoke" in sys.argv
    t0 = time.time()
    days = bt.load_days("0900")
    if smoke:
        days = vb.smoke_days(days)
    missing = scen.attach(days)
    print(f"{len(days)} days ({days[0]['day']:%Y-%m-%d} .. {days[-1]['day']:%Y-%m-%d}), {missing} without scenarios")
    for t in vb.STEPS["e5"]["tables"]:
        vl.build(t[0], t[1], hurdle=t[2], state=len(t) > 3 and t[3])
    aux = bt.q2b_aux()
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    configs = CONFIGS if not smoke else tuple(c for c in CONFIGS if c.name in ("e5", "roll0", "sp0", "cvar0"))
    sc, daily, logs, meters, seconds = run(days, aux, configs)
    tag = "_smoke" if smoke else ""
    daily.to_csv(HERE / f"daily{tag}.csv", index=False)
    pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
              ignore_index=True).to_parquet(HERE / f"log{tag}.parquet")
    ledger = bt.q2b_ledger(daily, [sc])
    bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
    money = sto.daily_money(ledger, bills)
    money.index = pd.to_datetime(money.index)
    ledger.to_csv(HERE / f"ledger{tag}.csv", index=False)
    bills.to_csv(HERE / f"bills{tag}.csv", index=False)
    ircr.to_csv(HERE / f"ircr{tag}.csv", index=False)
    money.to_csv(HERE / f"money{tag}.csv")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)

    phys = physics(daily, logs)
    phys["minutes"] = pd.Series(seconds) / 60
    phys["s_per_day"] = pd.Series(seconds) / len(days)
    phys.to_csv(HERE / f"physics{tag}.csv")
    print(f"\n=== physics, compliance, run time\n{phys.round(4).to_string()}\n")

    part = pd.Series(part_of(money.index), index=money.index)
    led = ledger.pivot_table(index="day", columns="strategy", values="ledger_total")
    led.index = pd.to_datetime(led.index)
    bm = bills.pivot_table(index="month", columns="strategy", values="saving")
    bm_part = pd.Series(part_of(bm.index.to_timestamp()), index=bm.index)
    rows = []
    names = [c.name for c in configs]
    for p in PARTS:
        sel = part == p
        if not sel.any():
            continue
        mp, lp = money[sel], led.reindex(money.index)[sel]
        bp = bm[bm_part == p] if p == PARTS[0] else None
        for name in names:
            row = {"period": p, "config": name, "days": int(sel.sum()), "per_day": mp[name].mean()}
            if name != "e5":
                row.update({f"e5_{k}": v for k, v in against(mp, lp, bp, name, "e5", p != PARTS[2]).items() if k != "vs"})
            if name in CANDIDATES and CANDIDATES[name] != "e5":
                row.update({f"roll_{k}": v for k, v in against(mp, lp, bp, name, CANDIDATES[name], p != PARTS[2]).items()})
            rows.append(row)
    table = pd.DataFrame(rows).set_index(["period", "config"])
    table.to_csv(HERE / f"result{tag}.csv")
    cols = [c for c in ("days", "per_day", "e5_gain", "e5_gain_lo", "e5_gain_hi", "e5_market", "e5_bill", "roll_vs",
                        "roll_gain", "roll_gain_lo", "roll_gain_hi") if c in table]
    print(f"=== $/day; gains with month-block 95% intervals (none on the unseen 7 days)\n{table[cols].round(1).to_string()}\n")

    tl = tails(money, daily, part)
    tl.to_csv(HERE / f"tails{tag}.csv")
    print(f"=== tails ($): worst 5% days with stored energy at ${STORED:g}/MWh, worst 10% full weeks\n{tl.round(1).to_string()}\n")
    opt = (ledger.assign(part=part_of(ledger["day"]), optimism=ledger["plan_money"] - ledger["plan_got"])
           .groupby(["part", "strategy"])[["plan_money", "plan_got", "optimism"]].mean())
    opt.to_csv(HERE / f"optimism{tag}.csv")
    print(f"=== the 09:00 plan's expected money less what followed ($/day)\n{opt.round(1).to_string()}\n")
    diag = vb.diagnostics(logs)
    diag.to_csv(HERE / f"diag{tag}.csv")
    print(f"=== stored energy (MWh), buys and sells\n{diag.round(2).to_string()}\n")
    fc = (ledger.assign(part=part_of(ledger["day"])).groupby(["part", "strategy"])[["energy", "fcess", "wear"]].mean())
    fc.to_csv(HERE / f"parts{tag}.csv")
    print(f"=== market parts ($/day)\n{fc.round(1).to_string()}\n")
    monthly = (money.sub(money["e5"], axis=0).drop(columns="e5").groupby(money.index.to_period("M")).mean())
    monthly.to_csv(HERE / f"monthly_vs_e5{tag}.csv")
    print(f"=== gain over e5 by month ($/day)\n{monthly.round(0).to_string()}\n")
    print(f"=== IRCR 2025-26 Hot Season ($/year)\n"
          f"{ircr[['strategy', 'ircr_mw', 'saving_per_year', 'peak_days_covered']].round(2).to_string(index=False)}\n")

    ok_phys = (phys[["power_over", "soc_outside", "fallback_days"]].to_numpy().sum() == 0
               and (phys["free_energy_max_mwh"] < TOL).all() and (phys["day_chain_gap_mwh"] < TOL).all())
    comp_base = phys.loc["e5", [c for c in COMPLIANCE if c in phys]]
    verdict = {}
    if not smoke:
        ins = table.loc[PARTS[0]]
        for name, ref in CANDIDATES.items():
            a = ins.loc[name, "e5_gain_lo"] > 0
            b = a if ref == "e5" else ins.loc[name, "roll_gain_lo"] > 0
            c = bool((phys.loc[name, comp_base.index] <= comp_base + TOL).all())
            verdict[name] = (a, b, c)
            print(f"{name}: (a) over e5 lo {ins.loc[name, 'e5_gain_lo']:.1f} {'ok' if a else 'no'}; (b) over {ref} lo "
                  f"{ins.loc[name, 'e5_gain_lo' if ref == 'e5' else 'roll_gain_lo']:.1f} {'ok' if b else 'no'}; "
                  f"(c) compliance {'ok' if c else 'no'}")
        passing = [n for n, v in verdict.items() if all(v)]
        best = max(passing, key=lambda n: ins.loc[n, "per_day"]) if passing else None
        leads = [f"roll{h}" for h in (50, 25, 0) if ins.loc[f"roll{h}", "e5_gain_lo"] > 0]
        print(f"physics and fallback {'ok' if ok_phys else 'NOT ok'}; pass (in-sample): {', '.join(passing) or 'none'}; "
              f"chosen: {best or 'none'}; hurdle leads (roll-h passing (a), not chosen): {', '.join(leads) or 'none'}")
        pd.DataFrame([{"candidate": n, "a": v[0], "b": v[1], "c": v[2], "pass": all(v)} for n, v in verdict.items()]) \
            .to_csv(HERE / "verdict.csv", index=False)
    print(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
