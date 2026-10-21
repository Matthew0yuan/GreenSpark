"""One decision's five outputs and its plain-Chinese account (reports/v2_plan.md, step 7).

Replays the chosen day of a v2 run exactly: the stored energy it started with and each month's highest import so far
come from the run's saved daily results and logs, and the replayed day must match the saved one. It takes the latest
plan the day had adopted by the given time and prints, in Chinese: the account (every number checked against the
record), the charging plan, the reserve plan, the bidding plan, the allocation and the money and risk. The report is
also written to reports/v2_report_<day>_<HHMM>.md.

Run:  python -m vpp.v2.report --day 2026-02-03 --at 21:00 [--config rel+ladder] [--step s3]
"""

import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import dispatch as dp
from vpp import stochastic as sto
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb
from vpp.v2 import explain as ex
from vpp.v2 import harness as hs
from vpp.v2 import outputs as op
from vpp.v2 import value as vl

REPORTS = bt.REPORTS


def _config(step: str, name: str):
    for cfg in vb.STEPS[step]["configs"]:
        if cfg.name == name:
            return replace(cfg, record=True)
    raise KeyError(f"no configuration {name!r} in step {step}")


def _start_state(step: str, name: str, days: list[dict], day: pd.Timestamp) -> tuple[float, dict, pd.Series]:
    """The stored energy at the day's start, each month's highest import before it, and the day's saved row."""
    daily = pd.read_csv(REPORTS / f"v2_{step}_daily.csv", parse_dates=["day"])
    daily = daily[daily["strategy"] == name].set_index("day").sort_index()
    log = pd.read_parquet(PROCESSED / f"v2_{step}_log.parquet", columns=["day", "slot", "strategy", "meter_mw"])
    log = log[(log["strategy"] == name) & (log["day"] < day)]
    months = {d["day"]: d["slot_month"] for d in days}
    mtd = {}
    for d, g in log.groupby("day"):
        for m, x in zip(months[d], g.sort_values("slot")["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), x)
    before = daily[daily.index < day]
    return (float(before["soc_end"].iloc[-1]) if len(before) else 0.0), mtd, daily.loc[day]


def _history(day: pd.Timestamp) -> tuple[np.ndarray, pd.Series]:
    """The highest 5-min price of each half-hour, less its day's level, over the 365 days before day."""
    p5 = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["energy"])["energy"]
    p5 = p5[(p5.index >= day - pd.Timedelta(days=365) + pd.Timedelta(hours=8)) & (p5.index < day + pd.Timedelta(hours=8))]
    trade = (p5.index - pd.Timedelta(hours=8)).normalize()
    slot = ((p5.index - pd.Timedelta(hours=8)) - trade) // pd.Timedelta(minutes=30)
    mx = p5.groupby([trade, slot]).max().unstack()
    lv = vl.levels(vl.prices()).reindex(mx.index)
    return (mx.to_numpy() - lv.to_numpy()[:, None]), lv


def _table(frame: pd.DataFrame, cols: dict, fmt: dict) -> str:
    """A markdown table of the given columns (key: column, value: header), numbers formatted per column."""
    if not len(frame):
        return "（无）"
    head = "| " + " | ".join(cols.values()) + " |\n|" + "---|" * len(cols) + "\n"
    rows = []
    for _, r in frame.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, (bool, np.bool_)):
                cells.append("是" if v else "否")
            else:
                cells.append("—" if isinstance(v, float) and not np.isfinite(v) else fmt.get(c, "{}").format(v))
        rows.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(rows)


def report(day: pd.Timestamp, at: str, name: str = "rel+ladder", step: str = "s3") -> tuple[str, list[str]]:
    cfg = _config(step, name)
    if cfg.value == "relative" or cfg.ladder:
        vl.build(cfg.level_stat, cfg.usable_mwh or bt.Q2B_ASSET.battery.energy_mwh)
    days = bt.load_days("0900")
    i = next(k for k, d in enumerate(days) if d["day"] == day)
    sub = days[i:i + 2]
    sto.attach(sub)
    soc0, mtd0, saved = _start_state(step, name, days, day)
    bt._init_worker(sub, bt.q2b_aux())
    sc = hs.scenario(vb.m1(), cfg)
    seen = {}
    rows = [r for r in hs.route_days(vb.m1(), cfg, soc0, mtd0, until=day,
                                     on_day=lambda d, kw, s, lg: seen.update(kw=kw, summary=s, log=lg))]
    _, _, summary, log, records = rows[0]
    same = summary["total"] == saved["total"] and summary["soc_end"] == saved["soc_end"]
    slot = int((pd.Timestamp(f"2000-01-01 {at}") - pd.Timestamp("2000-01-01 08:00")) / pd.Timedelta(minutes=30)) % dp.N
    adopted = [r for r in records if r["t0"] <= slot]
    rec = adopted[-1]
    _, strat, prefs = bt.strategy(bt.VALUE, sc.prefs)
    kw = seen["kw"]
    common = dict(bat=sc.asset.battery, load_margin=prefs.load_margin, sustain_h=sc.asset.limits.fcess_sustain_h,
                  wear=sc.asset.wear_per_mwh, alpha=strat.alpha, dw=dp.decision_wear(sc.asset, prefs), k_min=strat.k_min,
                  bids=kw.get("bids"), ladder=cfg.ladder, offer_lag=cfg.offer_lag, scen=sub[0].get("scen"),
                  p5_history=_history(day) if kw.get("bids") is not None else None)
    snap = op.Snapshot(rec, **common)
    prev = op.Snapshot(adopted[-2], **common) if len(adopted) > 1 else None
    lines, facts = ex.explain(snap, prev)
    bad = ex.check_numbers(lines, facts)
    now_soc = float(log["soc"].iloc[slot - 1]) if slot > 0 else float(log["soc_start"].iloc[0])
    t = f"{op.clock(slot)}"
    md = [f"# v2 决策报告：{day.date()} {t}（{name}）",
          f"\n回放核对：{'和全量回测完全一致' if same else '和全量回测不一致！'}；{t} 实际存电 {now_soc:.1f} MWh；"
          f"下面是到 {t} 为止最近一次采用的计划（{op.clock(int(rec['t0']))}，{ex.KIND.get(rec['kind'], rec['kind'])}）。"
          f"解释里的每个数字都能在计算记录里找到：{'是' if not bad else '否，' + '; '.join(bad)}。",
          "\n## 决策说明\n", *[f"- {x}" for x in lines],
          "\n## ① 充电计划\n", _table(op.charging_plan(snap), {"time": "时段", "charge_mw": "充电 MW", "grid_mwh": "买电 MWh",
                                                               "price": "预计电价 $/MWh", "cost": "预计成本 $",
                                                               "layer": "填哪一层", "worth_at_0800": "这度电存到明早值 $/MWh",
                                                               "max_price": "电价高于这个就不值 $/MWh"},
                                 {"charge_mw": "{:.2f}", "grid_mwh": "{:.2f}", "price": "{:.0f}", "cost": "{:,.0f}",
                                  "worth_at_0800": "{:.0f}", "max_price": "{:.0f}"}),
          "\n## ② 留电计划\n", _table(op.reserve_plan(snap), {"time": "时点", "stored": "计划存电 MWh", "mandatory": "强制预留",
                                                              "site": "工厂备电", "site_ircr": "其中 IRCR",
                                                              "opportunity": "机会电", "next_mwh_worth": "再多一度值 $/MWh"},
                                  {k: "{:.1f}" for k in ("stored", "mandatory", "site", "site_ircr", "opportunity")}
                                  | {"next_mwh_worth": "{:.0f}"}),
          "\n## ③ 报价计划（今后 4 小时，按预测价所在的价格档估算）\n",
          _table(op.bidding_plan(snap), {"time": "时段", "stored": "计划存电 MWh", "first_mwh": "第一度电 $/MWh",
                                          "for_5_mwh": "卖到 5 MWh $/MWh", "for_10_mwh": "卖到 10 MWh $/MWh",
                                          "protected_mwh": "受保护 MWh", "protected_from": "受保护的电门槛 $/MWh",
                                          "locked": "已过截止", "first_fill_freq": "第一档历史成交频率（未校准）"},
                 {"stored": "{:.1f}", "first_mwh": "{:.0f}", "for_5_mwh": "{:.0f}", "for_10_mwh": "{:.0f}",
                  "protected_mwh": "{:.1f}", "protected_from": "{:,.0f}", "first_fill_freq": "{:.0%}"})
          if kw.get("bids") is not None else "挂单用 value 规则：价格第一次超过最低挂单价时才现算卖价，不能提前列出。",
          "\n## ④ 容量分配（计划，每个半小时）\n",
          _table(op.allocation(snap), {"time": "时段", "to_factory_mw": "放给工厂 MW", "to_grid_mw": "送电网 MW",
                                       "charge_mw": "充电 MW", "reg_raise": "RR", "cont_raise": "CR", "reg_lower": "RL",
                                       "cont_lower": "CL", "stored": "存电 MWh", "mandatory": "①", "fcess_backing": "FCESS 要撑",
                                       "site": "②", "opportunity": "③", "empty": "空位"},
                 {k: "{:.2f}" for k in ("to_factory_mw", "to_grid_mw", "charge_mw", "reg_raise", "cont_raise", "reg_lower",
                                         "cont_lower")} | {k: "{:.1f}" for k in ("stored", "mandatory", "fcess_backing", "site",
                                                                               "opportunity", "empty")}),
          "\n## ⑤ 钱和风险\n"]
    def usd(v: float) -> str:
        return f"{'−' if v < 0 else ''}${abs(v):,.0f}"
    m = op.money(snap)
    md.append(f"- 计划自己估的钱（到明早）：电能买卖净额 {usd(m['energy'])}，FCESS {usd(m['fcess'])}，损耗 {usd(m['wear'])}，"
              f"剩下的电按存电价值 {usd(m['stored'])}；合计 {usd(m['money'])}。")
    rk = op.risk(snap)
    md.append(f"- 风险（只看计划本身，不含实时挂单）：20 种价格走势平均 {usd(rk['mean'])}，最差的 5% 是 {usd(rk['worst_5pct'])}，"
              f"最好 {usd(rk['best'])}。" if rk else "- 风险：只对 09:00 的计划计算（那时才有 20 种价格走势）。")
    md.append(f"- 当天实际结算（电费节省另算）：市场收入 {usd(summary['total'])} = 电能买卖净额 {usd(summary['energy'])} + "
              f"FCESS {usd(summary['fcess'])} − 损耗 {usd(summary['wear'])}。")
    return "\n".join(md) + "\n", bad


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    day = pd.Timestamp(argv[argv.index("--day") + 1])
    at = argv[argv.index("--at") + 1] if "--at" in argv else "21:00"
    name = argv[argv.index("--config") + 1] if "--config" in argv else "rel+ladder"
    step = argv[argv.index("--step") + 1] if "--step" in argv else "s3"
    text, bad = report(day, at, name, step)
    out = REPORTS / f"v2_report_{day.date()}_{at.replace(':', '')}.md"
    out.write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to a narrower code page
    print(text)
    print(f"(written to {out})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
