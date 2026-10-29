"""The expected value of each kind of action, side by side (user's request 2026-10-29, after the full run): sp0 replayed
on a few days from the full run's state, with options at 09:00, 16:00 and 18:00 (vpp.dispatch.plan_options), and
the plain-Chinese line for each (vpp.v2.explain.options_line).

Days, by a rule fixed before looking (no choosing on what the lines say): the in-sample day with the most half-hours
at or above $200; the in-sample day with the smallest spread of half-hour prices (calmest); the last test day.

Checks, each saying what it catches:
  R1 the replay without options equals the full run's day (money and stored energy at the end): the start state
     (stored energy, the month's highest import) is right.
  R2 the replay with options equals R1: computing the options does not change what the battery does.
  R3 each option's half-hour does what it says (vpp.v2.explain.option_kind: sell >= 1 MW out, no FCESS; charge >= 1
     MW in, no FCESS; FCESS no energy, >= 1 MW FCESS; hold nothing); the chosen plan's expected value is at least every
     option's; and when the chosen action is of one kind only, that option's expected value equals it. Catches restrictions put on the wrong
     columns or not tied to the scenarios' common first half-hour.
  R4 every number in the lines and the note comes from the computation (vpp.v2.explain.check_numbers).

  .venv/bin/python analysis/2026-10-29/stochastic_v2/options_demo.py
"""

import sys
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
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import explain as ex  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

import run  # noqa: E402  (this folder)
import scen  # noqa: E402

NAME = "sp0"
AT = (2, 16, 20)  # 09:00, 16:00, 18:00
TOL = 1e-6


PARTS = {"in-sample": "样本内", "test seen": "测试期已看过的一段", "test unseen": "测试期没看过的一段"}


def pick_days(days: list[dict]) -> list[tuple[pd.Timestamp, str]]:
    ins = [d for d in days if d["day"] < bt.CUTOFF]
    most = max(ins, key=lambda d: (np.asarray(d["rtp"]) >= 200).sum())["day"]
    calm = min(ins, key=lambda d: np.ptp(d["rtp"]))["day"]
    return [(most, "$200 以上的半小时最多的一天"), (calm, "半小时价格最平的一天"), (days[-1]["day"], "最后一天")]


def money(x: float) -> str:
    return f"−${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def start_state(daily: pd.DataFrame, log: pd.DataFrame, days: list[dict], day: pd.Timestamp) -> tuple[float, dict]:
    """Stored energy at the day's start and each month's highest import before it, from the full run (as
    vpp.v2.report._start_state)."""
    months = {d["day"]: d["slot_month"] for d in days}
    mtd = {}
    for d, g in log[log["day"] < day].groupby("day"):
        for m, x in zip(months[d], g.sort_values("slot")["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), x)
    before = daily[daily["day"] < day]
    return (float(before["soc_end"].iloc[-1]) if len(before) else 0.0), mtd


def replay(sub: list[dict], cfg, soc0: float, mtd0: dict, day: pd.Timestamp) -> dict:
    bt._DAYS = sub
    return next(s for d, _, s, _, _ in hs.route_days(vb.m1(), cfg, soc0, mtd0, until=day) if d == day)


def main() -> None:
    days = bt.load_days("0900")
    scen.attach(days)
    for t in vb.STEPS["e5"]["tables"]:
        vl.build(t[0], t[1], hurdle=t[2], state=len(t) > 3 and t[3])
    bt._init_worker(days, bt.q2b_aux())
    cfg = next(c for c in run.CONFIGS if c.name == NAME)
    daily = pd.read_csv(HERE / "daily.csv", parse_dates=["day"])
    daily = daily[daily["strategy"] == NAME].sort_values("day")
    log = pd.read_parquet(HERE / "log.parquet", columns=["day", "slot", "strategy", "meter_mw"])
    log = log[log["strategy"] == NAME]
    hurdle = cfg.plan_hurdle if cfg.plan_hurdle is not None else bt.HURDLE
    note, note_facts = ex.options_note(hurdle, 20, power_mw=vb.m1().asset.battery.power_mw)
    rows, lines, checks = [], [], []
    for day, why in pick_days(days):
        i = next(k for k, d in enumerate(days) if d["day"] == day)
        sub = days[i:i + 3]
        soc0, mtd0 = start_state(daily, log, days, day)
        saved = daily[daily["day"] == day].iloc[0]
        plain = replay(sub, cfg, soc0, mtd0, day)
        r1 = abs(plain["total"] - saved["total"]) < TOL and abs(plain["soc_end"] - saved["soc_end"]) < TOL
        withopt = replay(sub, replace(cfg, options_at=AT), soc0, mtd0, day)
        r2 = withopt["total"] == plain["total"] and withopt["soc_end"] == plain["soc_end"]
        lines.append(f"\n**{day:%Y-%m-%d}**（{PARTS[run.part_of([day])[0]]}，{why}；{NAME} 这一天实际赚 {money(withopt['total'])}）")
        r3 = r4 = True
        for t0 in AT:
            if f"opt{t0}_chosen_ev" not in withopt:
                lines.append(f"{ex.op.clock(t0)}：这半小时没有按情景排的新计划（例如被挂单成交后的重排抢先），没有选项可比")
                continue
            o = {k: withopt[k] for k in withopt if k.startswith(f"opt{t0}_")}
            chosen = (o[f"opt{t0}_chosen_ev"], o[f"opt{t0}_chosen_net"], o[f"opt{t0}_chosen_fcess"])
            tol = max(1.0, 2e-4 * abs(chosen[0]))
            for name in dp.OPTIONS:
                ev, net, fc = (o[f"opt{t0}_{name}_{k}"] for k in ("ev", "net", "fcess"))
                rows.append({"day": day.date(), "slot": t0, "time": ex.op.clock(t0), "option": name, "ev": ev,
                             "net_mw": net, "fcess_mw": fc, "chosen_ev": chosen[0], "chosen_net_mw": chosen[1],
                             "chosen_fcess_mw": chosen[2]})
                if np.isfinite(ev):
                    r3 &= ex.option_kind(net, fc) == name and ev <= chosen[0] + tol
                    if ex.option_kind(chosen[1], chosen[2]) == name:
                        r3 &= abs(ev - chosen[0]) <= tol
            text, facts = ex.options_line(withopt, t0, 20)
            r4 &= not ex.check_numbers([text], facts)
            lines.append(text)
        checks.append({"day": day.date(), "R1 replay = run": r1, "R2 options change nothing": r2,
                       "R3 options consistent": r3, "R4 numbers traced": r4 and not ex.check_numbers([note], note_facts)})
    pd.DataFrame(rows).to_csv(HERE / "options_demo.csv", index=False)
    ck = pd.DataFrame(checks)
    ck.to_csv(HERE / "options_checks.csv", index=False)
    head = f"# 各选项期望值并排（{NAME}，按事先定的规则选的 3 天）\n\n{note}\n"
    (HERE / "options_demo.md").write_text(head + "\n\n".join(lines) + "\n", encoding="utf-8")
    print(note)
    print("\n".join(lines))
    print(f"\n{ck.to_string(index=False)}")


if __name__ == "__main__":
    main()
