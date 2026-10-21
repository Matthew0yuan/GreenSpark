"""v2 backtest (reports/v2_plan.md): v2 configurations through v1's days, ledger and bills, tested as the plan
wrote down before the runs. Choices use the days before the cutoff only; the days after it are printed once.

  --step s2    ② site reserve: v1 vs v2-site (pass: month-block 95% interval of the daily gain above 0), with
               v2-site + 20 MWh for spikes and the expected-load basis reported alongside
  --smoke      2026-01-12..23 and 2025-08-04..15 only (code checks; nothing is chosen on them)

A day's money is its ledger plus its month's bill saving per day (vpp.stochastic.daily_money). The pass test
resamples whole calendar months (the bill is monthly); the 7-day block interval of the earlier sections, the market
part's interval and the bill's better / worse months are reported alongside.

Run:  python -m vpp.v2.backtest --step s2 [--smoke]
"""

import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import site_bill as sb
from vpp import stochastic as sto
from vpp.data import PROCESSED
from vpp.v2 import harness as hs
from vpp.v2 import record as rc
from vpp.v2.config import V1, V2Config

REPORTS = bt.REPORTS
CMD = 9.0  # MW, m1's CMD (chosen before the cutoff, findings section 16)
SMOKE = (("2026-01-12", "2026-01-23"), ("2025-08-04", "2025-08-15"))
STEPS = {
    "s2": {"base": "v1", "candidates": ("site",), "ref": "site+R20",
           "configs": (V1, V2Config("site", site_reserve=True, record=True),
                       V2Config("site+R20", site_reserve=True, spike_reserve_mwh=20.0),
                       V2Config("site expected", site_reserve=True, site_load_basis="expected"))},
}


def m1() -> bt.Scenario:
    return bt._sc("m1", ct.Route("m1", cmd_mw=CMD, ircr="dynamic"), (bt.VALUE,))


def _init(days: list[dict], aux: dict, value_path=None) -> None:
    bt._init_worker(days, aux)
    if value_path is not None:
        from vpp.v2 import value as vl
        hs.VALUE_INPUTS.update(vl.load(value_path))


def _run(task: tuple) -> tuple:
    """One configuration (or, for a v1 strategy name, v1's own loop) through every day."""
    sc, cfg, keep_logs = task
    if isinstance(cfg, str):
        return (*bt._run_route(sc, cfg, keep_logs), [])
    rows, logs, meters, records = [], {}, [], []
    for day, _, summary, log, recs in hs.route_days(sc, cfg):
        rows.append({"scenario": sc.name, "strategy": cfg.name, "day": day, **summary})
        meters.append(log["meter_mw"].to_numpy().astype(np.float32))
        if keep_logs:
            logs[day] = log
        records.extend(recs)
    return sc.name, cfg.name, rows, logs, np.vstack(meters), records


def run(tasks: list[tuple], days: list[dict], aux: dict, value_path=None):
    """Run (scenario, config or v1 strategy name, keep logs) tasks in parallel. Returns the daily rows, the logs and
    float32 meters by (scenario, name), and the decision records."""
    daily, logs, meters, records = [], {}, {}, []
    workers = min(len(tasks), max(1, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(days, aux, value_path)) as pool:
        for sc, name, rows, lg, meter, recs in pool.map(_run, tasks):
            daily.extend(rows)
            if lg:
                logs[sc, name] = lg
            meters[sc, name] = meter
            records.extend(recs)
    daily = pd.DataFrame(daily)
    daily["period"] = bt.period_of(daily["day"])
    return daily, logs, meters, records


def month_ci(diff: pd.Series, n: int = 4000, seed: int = 0) -> tuple[float, float]:
    """95% interval of the mean daily difference, resampling whole calendar months (as many as there are)."""
    by = diff.groupby(diff.index.to_period("M"))
    sums, counts = by.sum().to_numpy(), by.count().to_numpy()
    pick = np.random.default_rng(seed).integers(0, len(sums), size=(n, len(sums)))
    lo, hi = np.percentile(sums[pick].sum(axis=1) / counts[pick].sum(axis=1), [2.5, 97.5])
    return float(lo), float(hi)


def compare(money: pd.DataFrame, ledger: pd.DataFrame, bills: pd.DataFrame, base: str, ref: str | None) -> pd.DataFrame:
    """Per period and configuration: $/day; the difference from base with its month-block (pass test) and 7-day
    block intervals; the market part's difference and 7-day interval; the bill part and its months better / worse
    than base; the mean against ref."""
    rows = []
    period = pd.Series(bt.period_of(money.index), index=money.index)
    led = ledger.pivot_table(index="day", columns="strategy", values="ledger_total")
    bm = bills.pivot_table(index="month", columns="strategy", values="saving")
    for p in bt.PERIODS:
        mp, lp = money[period == p], led[period.reindex(led.index) == p]
        bp = bm[bt.period_of(bm.index.to_timestamp()) == p]
        if not len(mp):
            continue
        for name in mp.columns:
            row = {"period": p, "config": name, "days": len(mp), "per_day": mp[name].mean()}
            if name != base:
                diff = mp[name] - mp[base]
                row["vs_base"] = diff.mean()
                row["vs_base_lo"], row["vs_base_hi"] = month_ci(diff)
                row["vs_base_7d_lo"], row["vs_base_7d_hi"] = sto.block_ci(diff.to_numpy())
                mdiff = (lp[name] - lp[base]).to_numpy()
                row["market_vs_base"], (row["market_lo"], row["market_hi"]) = mdiff.mean(), sto.block_ci(mdiff)
                row["bill_vs_base"] = row["vs_base"] - row["market_vs_base"]
                d = bp[name] - bp[base]
                row["bill_months_better"], row["bill_months_worse"] = int((d > 1.0).sum()), int((d < -1.0).sum())
            if ref is not None and ref in mp and name != ref:
                row["vs_ref"] = (mp[name] - mp[ref]).mean()
            rows.append(row)
    return pd.DataFrame(rows).set_index(["period", "config"])


def diagnostics(logs: dict) -> pd.DataFrame:
    """Per configuration and period: mean stored energy, stored at 08:00 (start) and 16:00, energy charged
    20:00-08:00, energy the standing offer sold."""
    rows = []
    for (_, name), by_day in logs.items():
        for day, lg in by_day.items():
            rows.append({"config": name, "period": bt.period_of([day])[0], "soc_mean": lg["soc"].mean(),
                         "soc_0800": lg["soc_start"].iloc[0], "soc_1600": lg["soc"].iloc[15],
                         "night_charge_mwh": -lg["energy_mwh"].iloc[24:].clip(upper=0).sum(),
                         "offer_mwh": lg["offer_mwh"].sum()})
    return pd.DataFrame(rows).groupby(["period", "config"]).mean()


def smoke_days(days: list[dict]) -> list[dict]:
    return [d for d in days if any(pd.Timestamp(a) <= d["day"] <= pd.Timestamp(b) for a, b in SMOKE)]


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    step = argv[argv.index("--step") + 1]
    spec = STEPS[step]
    smoke = "--smoke" in argv
    days = bt.load_days("0900")
    if smoke:
        days = smoke_days(days)
    aux = bt.q2b_aux()
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    sc = m1()
    daily, logs, meters, records = run([(sc, cfg, True) for cfg in spec["configs"]], days, aux, spec.get("value_path"))
    ledger = bt.q2b_ledger(daily, [sc])
    bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
    money = sto.daily_money(ledger, bills)
    table = compare(money, ledger, bills, spec["base"], spec.get("ref"))
    diag = diagnostics(logs)
    tag = f"v2_{step}" + ("_smoke" if smoke else "")
    ledger.to_csv(REPORTS / f"{tag}_daily.csv", index=False)
    bills.to_csv(REPORTS / f"{tag}_bills.csv", index=False)
    ircr.to_csv(REPORTS / f"{tag}_ircr.csv", index=False)
    table.to_csv(REPORTS / f"{tag}_result.csv")
    diag.to_csv(REPORTS / f"{tag}_diag.csv")
    if records:
        rc.frame(records).to_parquet(PROCESSED / f"{tag}_records.parquet")
    pd.set_option("display.width", 250)
    print(f"\n{len(days)} days on m1, $/day; pass test: month-block 95% interval of the gain over {spec['base']}\n"
          f"{table.round(1).T.to_string()}\n")
    print(f"=== stored energy (MWh) and offers\n{diag.round(2).to_string()}\n")
    print(f"=== IRCR, {bt.IRCR_SEASON}-{bt.IRCR_SEASON - 1999} Hot Season ($/year)\n"
          f"{ircr[['strategy', 'ircr_mw', 'saving_per_year', 'peak_days_covered']].round(2).to_string(index=False)}\n")
    if not smoke and "validation" in table.index.get_level_values(0):
        val = table.loc["validation"]
        passing = [c for c in spec["candidates"] if c in val.index and val.loc[c, "vs_base_lo"] > 0
                   and (spec.get("ref") is None or step == "s2" or val.loc[c, "vs_ref"] >= 0)]
        print(f"pass (before the cutoff): {', '.join(passing) or 'none'}")


if __name__ == "__main__":
    main()
