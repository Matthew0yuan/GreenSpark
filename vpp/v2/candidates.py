"""R2: choose among candidate strategies each day at 09:00 (reports/v2_plan.md, section 7.3; FR-09).

Candidates (in force from 09:00): a as now (offer margin alpha 1.2), b sells more readily (1.0), c holds more
(1.5), d keeps 10 MWh for 16:00-20:00. Each is played out in the day's 20 price scenarios (the project's analog
ensemble, vpp.scenarios; 08:00-09:00 at the prices already seen, the load as forecast) through the real execution
rules to 08:00 tomorrow. A play's money is its cash, the energy left at the plan's end-of-day curve, less the CMD
and IRCR steering penalties and the site reserve's shortfall at its value. Score = (1 - lam) x weighted mean +
lam x the mean of the worst 5%; the weights may be 0.5-1.7 x equal (as section 20), and the candidate with the
least worst-case regret is chosen (within $1: a). lam = 0 is tested; lam = 0.5 is reported.

Pass (before the cutoff): lam = 0 earns more per day than the base (rel+ladder), month-block 95% interval above 0.

Run:  python -m vpp.v2.candidates [--smoke]          (in the background: about 15 minutes per lambda)
"""

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import dispatch as dp
from vpp import reserve as rs
from vpp import scenarios as sn
from vpp import site_bill as sb
from vpp import stochastic as sto
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import record as rc
from vpp.v2 import value as vl
from vpp.v2.config import V2Config

REPORTS = bt.REPORTS
OPTIONS = (("a", None, 0.0), ("b", 1.0, 0.0), ("c", 1.5, 0.0), ("d", None, 10.0))  # name, alpha from 09:00, MWh kept
TIE = 1.0
WORST = 0.05
BASE = vb.WIN  # rel+ladder


def apply(kw: dict, option: tuple) -> dict:
    """run_day arguments changed by a candidate."""
    _, alpha, mwh = option
    out = {"bids": replace(kw["bids"], alpha2=alpha)}
    out["reserve"] = dp.Reserve(mwh, *bt.RESERVE_WINDOW) if mwh > 0 else None
    return out


def money(kw: dict, summary: dict, log: pd.DataFrame) -> float:
    """A play's money: cash, the energy left at the plan's end-of-day curve, less the economic caps' steering
    penalties and the shortfall below the economic floors (the site reserve) at their values."""
    terms, bat = kw["terms"], kw["asset"].battery
    caps = {x.name: x for x in (*terms.layers, *(y for r in terms.reveals for y in r.layers)) if not x.compliance}
    pen = sum(x.penalty * summary.get(f"excess_{n}_{'mw' if x.kind == 'peak' else 'mwh'}", 0.0) for n, x in caps.items())
    soc = log["soc"].to_numpy()
    lf = np.asarray(kw["load_fc"], float) * (1 + kw["prefs"].load_margin)
    for o in (*terms.obligations, *(o for r in terms.reveals for o in r.obligations)):
        if not o.compliance:
            need = np.array([o.need(bat, (k + 1) * dp.DT, lf) for k in range(dp.N)])
            pen += o.value_per_mwh * max(0.0, float((need - soc).max()))
    curve = kw.get("terminal")
    s = summary["soc_end"]
    if curve is None:
        stored = float(log["stored_value"].iloc[-1]) * s
    else:
        c = np.asarray(curve, float)
        stored = float((np.clip(s - np.arange(len(c)), 0.0, 1.0) * c).sum())
    return summary["total"] + stored - pen


def _play(task) -> float:
    kw, option, prices = task
    kw = dict(kw, **apply(kw, option))
    if prices is not None:
        kw.update(prices)
    summary, log = dp.run_day(**kw)
    return money(kw, summary, log)


def regrets(V: np.ndarray, K: np.ndarray, ambiguity: tuple, lam: float) -> np.ndarray:
    """Each candidate's largest regret over the allowed weights: the most another earns more on the score
    (1 - lam) x weighted mean + lam x mean of the worst 5%, at the weights that favour it most."""
    n, S = V.shape
    lo, hi = ambiguity
    amb = (K, lo * K.mean(), hi * K.mean(), lo / S, hi / S)
    k = max(1, int(np.ceil(WORST * S)))
    worst = np.sort(V, axis=1)[:, :k].mean(axis=1)
    out = np.zeros(n)
    for i in range(n):
        for j in range(n):
            if i != j:
                gain = V[j] - V[i]
                g = (1 - lam) * float(dp.worst_weights(-gain, *amb) @ gain) + lam * (worst[j] - worst[i])
                out[i] = max(out[i], g)
    return out


class Chooser:
    """decide(d, kw) for vpp.v2.harness.route_days: plays every candidate in every scenario, chooses, and records
    the day (for information it also plays each candidate at the day's actual prices from the same state)."""

    def __init__(self, pool: ProcessPoolExecutor, analogs: rs.Analogs, lam: float):
        self.pool, self.analogs, self.lam, self.rows = pool, analogs, lam, []

    def __call__(self, d: dict, kw: dict) -> dict:
        plays = self.analogs.plays(d)
        base = {k: v for k, v in kw.items() if k != "on_plan"}
        rec = {"day": d["day"], "soc_start": kw["s_start"]}
        if plays is None or kw.get("bids") is None:
            rec["chosen"] = "a"
            self.rows.append(rec)
            return apply(kw, OPTIONS[0])
        tasks = [(dict(base, load_act=base["load_fc"]), o, p) for o in OPTIONS for p in plays]
        V = np.array(list(self.pool.map(_play, tasks, chunksize=4))).reshape(len(OPTIONS), len(plays))
        K = np.array([(p["rtp"][sn.FIRST:] >= sn.SPIKE).sum() for p in plays], float)
        reg = regrets(V, K, kw["prefs"].ambiguity, self.lam)
        i = rs.least(reg, TIE)
        actual = np.array(list(self.pool.map(_play, [(base, o, None) for o in OPTIONS])))  # information only
        rec.update({"chosen": OPTIONS[i][0], "regret": reg[i], "robust": reg[i] <= TIE, "spikes_mean": K.mean(),
                    "nominal": OPTIONS[rs.least(-V.mean(axis=1), TIE)][0],
                    "hindsight": OPTIONS[rs.least(-actual, TIE)][0],
                    **{f"expected_{o[0]}": v for o, v in zip(OPTIONS, V.mean(axis=1))},
                    **{f"worst_{o[0]}": v for o, v in zip(OPTIONS, np.sort(V, axis=1)[:, 0])},
                    **{f"regret_{o[0]}": v for o, v in zip(OPTIONS, reg)},
                    **{f"actual_{o[0]}": v for o, v in zip(OPTIONS, actual)}})
        self.rows.append(rec)
        return apply(kw, OPTIONS[i])


def run_chooser(days: list[dict], aux: dict, lam: float, pool, analogs) -> tuple:
    """The chooser through every day in this process (its plays use the pool): daily rows, logs, meters, records,
    choices."""
    bt._init_worker(days, aux)
    sc = vb.m1()
    cfg = replace(BASE, name=f"choose lam={lam:g}", record=lam == 0.0)
    chooser = Chooser(pool, analogs, lam)
    rows, logs, meters, records = [], {}, [], []
    for day, _, summary, log, recs in hs.route_days(sc, cfg, decide=chooser):
        rows.append({"scenario": sc.name, "strategy": cfg.name, "day": day, **summary})
        meters.append(log["meter_mw"].to_numpy().astype(np.float32))
        logs[day] = log
        records.extend(recs)
    return rows, logs, np.vstack(meters), records, pd.DataFrame(chooser.rows).assign(config=cfg.name)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    smoke = "--smoke" in argv
    days = bt.load_days("0900")
    if smoke:
        days = vb.smoke_days(days)
    aux = bt.q2b_aux()
    vl.build("median", 40.0)
    sc = vb.m1()
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    daily, logs, meters, records = vb.run([(sc, BASE, True)], days, aux)  # the base, as in step s3
    rows, choices = [daily], []
    workers = max(1, (os.cpu_count() or 2) - 1)
    analogs = rs.Analogs()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for lam in (0.0, 0.5):
            r, lg, m, rec, ch = run_chooser(days, aux, lam, pool, analogs)
            name = f"choose lam={lam:g}"
            rows.append(pd.DataFrame(r).assign(period=lambda f: bt.period_of(f["day"])))
            logs[sc.name, name], meters[sc.name, name] = lg, m
            records += rec
            choices.append(ch)
            print(f"lambda {lam:g} done: chosen {ch['chosen'].value_counts().to_dict()}", flush=True)
    daily = pd.concat(rows, ignore_index=True)
    ledger = bt.q2b_ledger(daily, [sc])
    bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
    money_ = sto.daily_money(ledger, bills)
    table = vb.compare(money_, ledger, bills, BASE.name, None)
    choices = pd.concat(choices, ignore_index=True)
    choices["period"] = bt.period_of(choices["day"])
    tag = "v2_r2" + ("_smoke" if smoke else "")
    ledger.to_csv(REPORTS / f"{tag}_daily.csv", index=False)
    bills.to_csv(REPORTS / f"{tag}_bills.csv", index=False)
    table.to_csv(REPORTS / f"{tag}_result.csv")
    choices.to_csv(REPORTS / f"{tag}_choices.csv", index=False)
    if records:
        rc.frame(records).to_parquet(PROCESSED / f"{tag}_records.parquet")
    pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
              ignore_index=True).to_parquet(PROCESSED / f"{tag}_log.parquet")
    pd.set_option("display.width", 250)
    print(f"\n{len(days)} days on m1, $/day; pass test: month-block 95% interval of the gain over {BASE.name}\n"
          f"{table.round(1).T.to_string()}\n")
    summ = choices.groupby(["config", "period"]).agg(
        days=("day", "size"), robust=("robust", "mean"),
        **{f"chose_{o[0]}": ("chosen", lambda x, o=o: int((x == o[0]).sum())) for o in OPTIONS})
    print(f"=== choices\n{summ.to_string()}\n")
    if not smoke and "validation" in table.index.get_level_values(0):
        v = table.loc[("validation", "choose lam=0")]
        print(f"pass (before the cutoff): {'yes' if v['vs_base_lo'] > 0 else 'no'} "
              f"(gain {v['vs_base']:+.1f}, interval [{v['vs_base_lo']:+.1f}, {v['vs_base_hi']:+.1f}])")


if __name__ == "__main__":
    main()
