"""v2 backtest (reports/v2_plan.md): v2 configurations through v1's days, ledger and bills, tested as the plan
wrote down before the runs. Choices use the days before the cutoff only; the days after it are printed once.

  --step s2    ② site reserve: v1 vs v2-site (pass: month-block 95% interval of the daily gain above 0), with
               v2-site + 20 MWh for spikes and the expected-load basis reported alongside
  --step s3    the relative value curve (rel) and its layered offers (rel+ladder) on v2-site (pass: gain over
               v2-site above 0 by the month-block interval, and a mean at least v2-site + 20 MWh's; both passing,
               the higher mean)
  --step sens  the sensitivities of section 5.4, each against its own base (reported, never chosen on)
  --step b2    round 4 (section 11.4): the value table and the real-time offers at the wear plus h = 0 / 25 / 50, the
               plans keeping the hurdle of 75 (pass: month-block 95% interval of the gain over rel+ladder above 0;
               several passing, the highest mean)
  --step b3    round 4 (section 11.5): the offers' buy side on B2's winner rw50 (pass: as b2, over rw50)
  --step b4    round 4 (section 11.6): values by the market state of the day and of the next, on rw50 (as b3)
  --step t5    round 5 (section 12.3): the 08:00 plan's end value without tomorrow's site reserve, public at 09:00,
               on B4's winner (a fix, reported only; the base of the later round-5 steps)
  --step c5    round 5 (section 12.4): today's offer values also by whether the day has already spiked, on t5
               (pass: month-block 95% interval of the gain over t5 above 0)
  --step e5    round 5 (section 12.5): the plans also redone at the start of every trading interval, on t5 (as c5),
               with the energy market by time of day and the night re-plans after a dip (reports/v2_e5_roll.csv)
  --step f5    round 5 (section 12.6): B3's buy side at the full value, capped, with or without a floor under the
               values, and the night fill (a reference), on e5 (pass: as c5; both passing, the higher mean)
  --step g5    round 5 (section 12.7): the gate closure (offer prices 20 minutes old; prices and quantities locked 15
               minutes before) and the $1,000 offer ceiling, layer by layer on e5 (reported only)
  --smoke  2026-01-12..23 and 2025-08-04..15 only (code checks; nothing is chosen on them)

A day's money is its ledger plus its month's bill saving per day (vpp.stochastic.daily_money). The pass test
resamples whole calendar months (the bill is monthly); the 7-day block interval of the earlier sections, the market
part's interval and the bill's better / worse months are reported alongside.

Run:  python -m vpp.v2.backtest --step s2|s3|sens|b2|b3|b4|t5|c5|e5|f5|g5 [--smoke]
"""

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import site_bill as sb
from vpp import stochastic as sto
from vpp.data import PROCESSED
from vpp.v2 import harness as hs
from vpp.v2 import record as rc
from vpp.v2 import value as vl
from vpp.v2.config import V1, V2Config

REPORTS = bt.REPORTS
CMD = 9.0  # MW, m1's CMD (chosen before the cutoff, findings section 16)
SMOKE = (("2026-01-12", "2026-01-23"), ("2025-08-04", "2025-08-15"))
FIX = dict(offer_end=True)  # every v2 configuration prices the value offer's last half-hour (v2_plan.md, 9.1)
SITE = V2Config("site", site_reserve=True, **FIX)  # step s2's winner: the base of step s3
WIN = V2Config("rel+ladder", site_reserve=True, value="relative", ladder=True, **FIX)  # step s3's winner
STEPS = {
    "s2": {"base": "v1", "candidates": ("site",), "ref": "site+R20", "tables": (),
           "configs": (V1, V2Config("site", site_reserve=True, record=True, **FIX),
                       V2Config("site+R20", site_reserve=True, spike_reserve_mwh=20.0, **FIX),
                       V2Config("site expected", site_reserve=True, site_load_basis="expected", **FIX),
                       V2Config("v1 fixed", **FIX))},
    "s3": {"base": "site", "candidates": ("rel", "rel+ladder"), "ref": "site+R20", "tables": (("median", 40.0),),
           "configs": (SITE, V2Config("site+R20", site_reserve=True, spike_reserve_mwh=20.0, **FIX),
                       V2Config("rel", site_reserve=True, value="relative", **FIX),
                       V2Config("rel+ladder", site_reserve=True, value="relative", ladder=True, record=True, **FIX))},
}
STEPS["r3"] = {"base": WIN.name, "candidates": ("rel+ladder 48h",), "ref": None, "tables": (("median", 40.0),),
               "configs": (WIN, V2Config("rel+ladder 48h", site_reserve=True, value="relative", ladder=True, plan_days=2,
                                         record=True, **FIX))}
# round 4, B2 (v2_plan.md section 11.4): the value table and the real-time offers at the wear plus h, the plans at 75
B2 = tuple(V2Config(f"rw{h:g}", site_reserve=True, value="relative", ladder=True, hurdle=h, record=True, **FIX)
           for h in (0.0, 25.0, 50.0))
STEPS["b2"] = {"base": WIN.name, "candidates": tuple(c.name for c in B2), "ref": None,
               "tables": (("median", 40.0), *(("median", 40.0, c.hurdle) for c in B2)), "configs": (WIN, *B2)}
# round 4, B3 (section 11.5): the buy side of the offers, on B2's winner rw50
B2_WIN = B2[2]
B3 = V2Config(f"{B2_WIN.name}+buy", site_reserve=True, value="relative", ladder=True, hurdle=B2_WIN.hurdle, buy=True,
              record=True, **FIX)
STEPS["b3"] = {"base": B2_WIN.name, "candidates": (B3.name,), "ref": None, "tables": (("median", 40.0, B2_WIN.hurdle),),
               "configs": (B2_WIN, B3)}
# round 4, B4 (section 11.6): values by market state, on rw50 (B3 did not pass)
B4 = V2Config(f"{B2_WIN.name}+state", site_reserve=True, value="relative", ladder=True, hurdle=B2_WIN.hurdle, state=True,
              record=True, **FIX)
STEPS["b4"] = {"base": B2_WIN.name, "candidates": (B4.name,), "ref": None,
               "tables": (("median", 40.0, B2_WIN.hurdle), ("median", 40.0, B2_WIN.hurdle, True)), "configs": (B2_WIN, B4)}
# round 5 (section 12), on round 4's winner B4. t5 (12.3): the 08:00 plan's end value without tomorrow's site reserve,
# which is public at 09:00 (a fix: reported, and the base of the round's later steps)
R4_WIN = B4
T5 = replace(R4_WIN, name=f"{R4_WIN.name} t5", terminal_0900=True)
STEPS["t5"] = {"base": R4_WIN.name, "candidates": (T5.name,), "ref": None, "report_only": True,
               "tables": STEPS["b4"]["tables"], "configs": (R4_WIN, T5)}
# c5 (12.4): today's offer values also by whether the day has already spiked, on t5
C5 = replace(T5, name=f"{T5.name}+regime", regime=True)
STEPS["c5"] = {"base": T5.name, "candidates": (C5.name,), "ref": None,
               "tables": (*STEPS["b4"]["tables"], ("median", 40.0, B2_WIN.hurdle, True, True)), "configs": (T5, C5)}
# e5 (12.5): the plans also redone at the start of every trading interval, on t5 (c5 did not pass)
E5 = replace(T5, name=f"{T5.name}+roll", replan_every=1)
STEPS["e5"] = {"base": T5.name, "candidates": (E5.name,), "ref": None, "tables": STEPS["b4"]["tables"], "configs": (T5, E5)}
# f5 (12.6): B3's buy side at the full value (capped), or with a floor under the values; "fill" a reference; on e5
F5A = replace(E5, name=f"{E5.name}+bid", bid="full")
F5B = replace(E5, name=f"{E5.name}+bid floor", bid="floor")
F5R = replace(E5, name=f"{E5.name}+fill", bid="fill", record=False)
STEPS["f5"] = {"base": E5.name, "candidates": (F5A.name, F5B.name), "ref": None, "tables": STEPS["b4"]["tables"],
               "configs": (replace(E5, record=False), F5A, F5B, F5R)}
# g5 (12.7, reported only): the gate closure and the offer price ceiling, layer by layer, on e5 (f5 did not pass)
G5A = replace(E5, name=f"{E5.name}+lag20", offer_lag=4, record=False)
G5B = replace(G5A, name=f"{E5.name}+lock", offer_lock=3)
G5C = replace(G5B, name=f"{E5.name}+lock+cap", offer_cap=1000.0)
STEPS["g5"] = {"base": E5.name, "candidates": (G5A.name, G5B.name, G5C.name), "ref": None, "report_only": True,
               "layers": ((G5A.name, E5.name), (G5B.name, G5A.name), (G5C.name, G5B.name)),
               "tables": STEPS["b4"]["tables"], "configs": (replace(E5, record=False), G5A, G5B, G5C)}
ROUND5 = ("t5", "c5", "e5", "f5", "g5")
BLOCKS = (("08-16", 0, 16), ("16-20", 16, 24), ("20-24", 24, 32), ("00-08", 32, 48))  # e5's market by time of day


def roll_diagnostics(logs: dict, days: list[dict], base: str, cand: str) -> tuple[pd.DataFrame, str]:
    """e5 (v2_plan.md section 12.5), per period: the energy market's money (RTP x MWh) of base and candidate in four
    blocks of the day; night re-plans after a dip (the latest 5-min price at least $20 below the forecast of the
    interval it ends, the candidate then charging at least 1 MW more than the base), and the share of them where the
    next interval's settled price did not stay $20 below its forecast; with the interpretation rule written down
    beforehand (at least half the in-sample market gain from 00-08 and stored energy at 08:00 at least 2 MWh higher:
    possibly the nowcast moving prices but not the end value)."""
    by = {d["day"]: d for d in days}
    rows = []
    for name in (base, cand):
        for day, lg in logs[next(k for k in logs if k[1] == name)].items():
            rev = (lg["rtp"] * lg["energy_mwh"]).to_numpy()
            rows.append({"config": name, "period": bt.period_of([day])[0], "day": day,
                         **{b: rev[lo:hi].sum() for b, lo, hi in BLOCKS}, "soc_0800": float(lg["soc_start"].iloc[0])})
    x = pd.DataFrame(rows)
    blocks = x.groupby(["period", "config"])[[b for b, _, _ in BLOCKS] + ["soc_0800"]].mean()
    lb, lc = logs[next(k for k in logs if k[1] == base)], logs[next(k for k in logs if k[1] == cand)]
    dips = []
    for day, c in lc.items():
        b, p5, fc = lb[day], by[day]["p5"], c["forecast"].to_numpy()
        charge_c, charge_b = (-g["energy_mwh"].to_numpy() / dp.DT for g in (c, b))
        for t in range(33, 48):  # 00:30-07:30: the re-plan at t after interval t - 1 (night)
            if c["replanned"].iloc[t] and p5[t * 6 - 1] - fc[t - 1] <= -20.0 and charge_c[t] - charge_b[t] >= 1.0:
                dips.append({"period": bt.period_of([day])[0], "kept_low": c["rtp"].iloc[t] <= fc[t] - 20.0})
    d = pd.DataFrame(dips, columns=["period", "kept_low"])
    for period in blocks.index.get_level_values(0).unique():
        sel = d[d["period"] == period]
        blocks.loc[(period, cand), "dip_replans"] = len(sel)
        blocks.loc[(period, cand), "dip_not_kept_low_share"] = float((~sel["kept_low"]).mean()) if len(sel) else np.nan
    note = ""
    if "validation" in blocks.index.get_level_values(0):
        v = blocks.loc["validation"]
        gain = v.loc[cand, [b for b, _, _ in BLOCKS]] - v.loc[base, [b for b, _, _ in BLOCKS]]
        night_share = gain["00-08"] / gain.sum() if gain.sum() > 0 else np.nan
        soc_up = v.loc[cand, "soc_0800"] - v.loc[base, "soc_0800"]
        flag = gain.sum() > 0 and night_share >= 0.5 and soc_up >= 2.0
        note = (f"in-sample energy-market gain ${gain.sum():.1f}/day, {night_share:.0%} of it 00-08; stored at 08:00 "
                f"{soc_up:+.2f} MWh: " + ("possibly the nowcast moving prices but not the end value" if flag
                                           else "the rule for a possible nowcast artefact is not met"))
    return blocks, note
SENS = (  # (configuration, its base): one setting changed each
    (V2Config("v1 lag 15 min", offer_lag=3), "v1"), (V2Config("v1 lag 30 min", offer_lag=6), "v1"),
    (V2Config("v1 36 MWh", usable_mwh=36.0), "v1"),
    (V2Config("rel+ladder lag 15 min", site_reserve=True, value="relative", ladder=True, offer_lag=3, **FIX), WIN.name),
    (V2Config("rel+ladder lag 30 min", site_reserve=True, value="relative", ladder=True, offer_lag=6, **FIX), WIN.name),
    (V2Config("rel+ladder 36 MWh", site_reserve=True, value="relative", ladder=True, usable_mwh=36.0, **FIX), WIN.name),
    (V2Config("rel+ladder unshifted", site_reserve=True, value="relative", ladder=True, curve_shift=False, **FIX),
     WIN.name),
    (V2Config("rel+ladder mean level", site_reserve=True, value="relative", ladder=True, level_stat="mean", **FIX),
     WIN.name),
    (V2Config("site expected", site_reserve=True, site_load_basis="expected", **FIX), SITE.name),
)
SENS_TABLES = (("median", 40.0), ("median", 36.0), ("mean", 40.0))


def m1() -> bt.Scenario:
    return bt._sc("m1", ct.Route("m1", cmd_mw=CMD, ircr="dynamic"), (bt.VALUE,))


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


def run(tasks: list[tuple], days: list[dict], aux: dict):
    """Run (scenario, config or v1 strategy name, keep logs) tasks in parallel. Returns the daily rows, the logs and
    float32 meters by (scenario, name), and the decision records. Value curves load in the workers on first use."""
    daily, logs, meters, records = [], {}, {}, []
    workers = min(len(tasks), max(1, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers, initializer=bt._init_worker, initargs=(days, aux)) as pool:
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


def _against(row: dict, mp: pd.DataFrame, lp: pd.DataFrame, bp: pd.DataFrame, name: str, base: str) -> dict:
    """The difference from base: mean, month-block and 7-day block intervals, market part, bill part and months."""
    diff = mp[name] - mp[base]
    row["vs_base"] = diff.mean()
    row["vs_base_lo"], row["vs_base_hi"] = month_ci(diff)
    row["vs_base_7d_lo"], row["vs_base_7d_hi"] = sto.block_ci(diff.to_numpy())
    mdiff = (lp[name] - lp[base]).to_numpy()
    row["market_vs_base"], (row["market_lo"], row["market_hi"]) = mdiff.mean(), sto.block_ci(mdiff)
    row["bill_vs_base"] = row["vs_base"] - row["market_vs_base"]
    d = bp[name] - bp[base]
    row["bill_months_better"], row["bill_months_worse"] = int((d > 1.0).sum()), int((d < -1.0).sum())
    return row


def _by_period(money: pd.DataFrame, ledger: pd.DataFrame, bills: pd.DataFrame):
    period = pd.Series(bt.period_of(money.index), index=money.index)
    led = ledger.pivot_table(index="day", columns="strategy", values="ledger_total")
    bm = bills.pivot_table(index="month", columns="strategy", values="saving")
    for p in bt.PERIODS:
        mp = money[period == p]
        if len(mp):
            yield p, mp, led[period.reindex(led.index) == p], bm[bt.period_of(bm.index.to_timestamp()) == p]


def compare(money: pd.DataFrame, ledger: pd.DataFrame, bills: pd.DataFrame, base: str, ref: str | None) -> pd.DataFrame:
    """Per period and configuration: $/day, the difference from base (see _against) and the mean against ref."""
    rows = []
    for p, mp, lp, bp in _by_period(money, ledger, bills):
        for name in mp.columns:
            row = {"period": p, "config": name, "days": len(mp), "per_day": mp[name].mean()}
            if name != base:
                row = _against(row, mp, lp, bp, name, base)
            if ref is not None and ref in mp and name != ref:
                row["vs_ref"] = (mp[name] - mp[ref]).mean()
            rows.append(row)
    return pd.DataFrame(rows).set_index(["period", "config"])


def compare_pairs(money: pd.DataFrame, ledger: pd.DataFrame, bills: pd.DataFrame, pairs) -> pd.DataFrame:
    """Per period: each sensitivity against its own base (see _against)."""
    rows = []
    for p, mp, lp, bp in _by_period(money, ledger, bills):
        for name, base in pairs:
            rows.append(_against({"period": p, "config": name, "base": base, "days": len(mp), "per_day": mp[name].mean()},
                                 mp, lp, bp, name, base))
    return pd.DataFrame(rows).set_index(["period", "config"])


def diagnostics(logs: dict) -> pd.DataFrame:
    """Per configuration and period: mean stored energy, stored at 08:00 (start) and 16:00, energy charged
    20:00-08:00, energy the standing offer sold; MWh bought and sold a day at their mean RTP (round 4)."""
    rows = []
    for (_, name), by_day in logs.items():
        for day, lg in by_day.items():
            e, rtp = lg["energy_mwh"], lg["rtp"]
            rows.append({"config": name, "period": bt.period_of([day])[0], "soc_mean": lg["soc"].mean(),
                         "soc_0800": lg["soc_start"].iloc[0], "soc_1600": lg["soc"].iloc[15],
                         "night_charge_mwh": -lg["energy_mwh"].iloc[24:].clip(upper=0).sum(),
                         "offer_mwh": lg["offer_mwh"].sum(), "bought_mwh": -e.clip(upper=0).sum(),
                         "sold_mwh": e.clip(lower=0).sum(), "buy_cost": -(rtp * e.clip(upper=0)).sum(),
                         "sell_revenue": (rtp * e.clip(lower=0)).sum()})
    out = pd.DataFrame(rows).groupby(["period", "config"]).mean()
    out["buy_price"] = out.pop("buy_cost") / out["bought_mwh"]
    out["sell_price"] = out.pop("sell_revenue") / out["sold_mwh"]
    return out


FULL_MWH = 38.0  # "full": stored energy at least this (round 5, section 12.2)


def distribution(logs: dict) -> pd.DataFrame:
    """Round 5 (v2_plan.md section 12.2), per configuration and period: stored energy at 08:00 (10th, 50th and 90th
    percentiles), the days that reach FULL_MWH, the share of the energy stored by charging 20:00-08:00 that is drawn
    08:00-24:00 the next day, and the intervals where buying beyond the plan came with the meter above both its cap
    and the month's earlier highest (0 expected; NaN without a buy side)."""
    eta = bt.Q2B_ASSET.battery.eta
    rows = []
    for (_, name), by_day in logs.items():
        days = sorted(by_day)
        month_max: dict = {}
        for k, day in enumerate(days):
            lg = by_day[day]
            e = lg["energy_mwh"].to_numpy()
            stored_in = -e[24:].clip(max=0).sum() * eta  # charged 20:00-08:00, as stored MWh
            nxt = by_day.get(days[k + 1]) if k + 1 < len(days) and days[k + 1] == day + pd.Timedelta(days=1) else None
            drawn = nxt["energy_mwh"].to_numpy()[:32].clip(min=0).sum() / eta if nxt is not None else np.nan
            over = np.nan
            if "bid_mwh" in lg:
                m = day.strftime("%Y-%m")
                meter, cap, bought = (lg[c].to_numpy() for c in ("meter_mw", "cap_mw", "bid_mwh"))
                before = np.maximum.accumulate(np.r_[month_max.get(m, 0.0), meter[:-1]])
                over = int(((bought > 1e-9) & (meter > np.maximum(cap, before) + 1e-6)).sum())
                month_max[m] = max(month_max.get(m, 0.0), float(meter.max()))
            rows.append({"config": name, "period": bt.period_of([day])[0], "soc_0800": float(lg["soc_start"].iloc[0]),
                         "full": max(float(lg["soc_start"].iloc[0]), float(lg["soc"].max())) >= FULL_MWH - 1e-9,
                         "stored_in": stored_in, "used": min(stored_in, drawn) if np.isfinite(drawn) else np.nan,
                         "stored_in_known": stored_in if np.isfinite(drawn) else np.nan, "buy_over_cap": over})
    x = pd.DataFrame(rows)
    g = x.groupby(["period", "config"])
    out = pd.DataFrame({"soc_0800_p10": g["soc_0800"].quantile(0.1), "soc_0800_p50": g["soc_0800"].quantile(0.5),
                        "soc_0800_p90": g["soc_0800"].quantile(0.9), "days": g.size(), "days_full": g["full"].sum(),
                        "night_used_share": g["used"].sum() / g["stored_in_known"].sum(),
                        "buy_over_cap": g["buy_over_cap"].sum(min_count=1)})
    return out


def smoke_days(days: list[dict]) -> list[dict]:
    return [d for d in days if any(pd.Timestamp(a) <= d["day"] <= pd.Timestamp(b) for a, b in SMOKE)]


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    step = argv[argv.index("--step") + 1]
    smoke = "--smoke" in argv
    days = bt.load_days("0900")
    if smoke:
        days = smoke_days(days)
    aux = bt.q2b_aux()
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    sc = m1()
    if step == "sens":
        spec = {"tables": SENS_TABLES, "configs": (V1, WIN, SITE, *(c for c, _ in SENS))}
    else:
        spec = STEPS[step]
    for t in spec["tables"]:  # (level statistic, MWh[, hurdle[, by market state[, by regime]]])
        vl.build(t[0], t[1], hurdle=t[2] if len(t) > 2 else bt.HURDLE, state=len(t) > 3 and t[3],
                 regime=len(t) > 4 and t[4])
    daily, logs, meters, records = run([(sc, cfg, step != "sens" or cfg.name in ("v1", WIN.name)) for cfg in spec["configs"]],
                                       days, aux)
    ledger = bt.q2b_ledger(daily, [sc])
    bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
    money = sto.daily_money(ledger, bills)
    tag = f"v2_{step}" + ("_smoke" if smoke else "")
    pd.set_option("display.width", 250)
    if step == "sens":
        table = compare_pairs(money, ledger, bills, [(c.name, b) for c, b in SENS])
        table.to_csv(REPORTS / f"{tag}_result.csv")
        print(f"\n{len(days)} days on m1, $/day: each sensitivity against its base (reported only)\n"
              f"{table[['base', 'per_day', 'vs_base', 'vs_base_lo', 'vs_base_hi', 'market_vs_base', 'bill_vs_base']].round(1).to_string()}\n")
        return
    table = compare(money, ledger, bills, spec["base"], spec.get("ref"))
    diag = diagnostics(logs)
    ledger.to_csv(REPORTS / f"{tag}_daily.csv", index=False)
    bills.to_csv(REPORTS / f"{tag}_bills.csv", index=False)
    ircr.to_csv(REPORTS / f"{tag}_ircr.csv", index=False)
    table.to_csv(REPORTS / f"{tag}_result.csv")
    diag.to_csv(REPORTS / f"{tag}_diag.csv")
    if step in ROUND5:
        dist = distribution(logs)
        dist.to_csv(REPORTS / f"{tag}_soc.csv")
        print(f"=== stored energy at 08:00, full days, night charge drawn the next day, buys over a cap\n"
              f"{dist.round(2).to_string()}\n")
    if step == "e5":
        blocks, note = roll_diagnostics(logs, days, spec["base"], spec["candidates"][0])
        blocks.to_csv(REPORTS / f"{tag}_roll.csv")
        print(f"=== energy market $/day by time of day, night re-plans after a dip\n{blocks.round(2).to_string()}\n{note}\n")
    if records:
        rc.frame(records).to_parquet(PROCESSED / f"{tag}_records.parquet")
    pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
              ignore_index=True).to_parquet(PROCESSED / f"{tag}_log.parquet")
    print(f"\n{len(days)} days on m1, $/day; pass test: month-block 95% interval of the gain over {spec['base']}\n"
          f"{table.round(1).T.to_string()}\n")
    print(f"=== stored energy (MWh) and offers\n{diag.round(2).to_string()}\n")
    print(f"=== IRCR, {bt.IRCR_SEASON}-{bt.IRCR_SEASON - 1999} Hot Season ($/year)\n"
          f"{ircr[['strategy', 'ircr_mw', 'saving_per_year', 'peak_days_covered']].round(2).to_string(index=False)}\n")
    if spec.get("layers"):  # g5: each layer against the one before it
        layers = compare_pairs(money, ledger, bills, spec["layers"])
        layers.to_csv(REPORTS / f"{tag}_layers.csv")
        print(f"=== each layer against the one before\n"
              f"{layers[['base', 'per_day', 'vs_base', 'vs_base_lo', 'vs_base_hi', 'market_vs_base', 'bill_vs_base']].round(1).to_string()}\n")
        print("reported only: what the backtest overstates without the gate closure and the price ceiling")
    elif spec.get("report_only"):
        print(f"reported only: {', '.join(spec['candidates'])} is the base of the round's later steps")
    elif not smoke and "validation" in table.index.get_level_values(0):
        val = table.loc["validation"]
        passing = [c for c in spec["candidates"] if c in val.index and val.loc[c, "vs_base_lo"] > 0
                   and (step == "s2" or spec.get("ref") is None or val.loc[c, "vs_ref"] >= 0)]
        best = max(passing, key=lambda c: val.loc[c, "per_day"]) if passing else None
        print(f"pass (before the cutoff): {', '.join(passing) or 'none'}; chosen: {best or 'none'}")


if __name__ == "__main__":
    main()
