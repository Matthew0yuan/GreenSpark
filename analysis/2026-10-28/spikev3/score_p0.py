"""spikev3 step 0 scoring (PLAN.md section 2 and change log 1). For every run, the difference from its baseline in
$/day (money = market + the month's bill saving per day), its market and bill parts, the month-block 95% interval
(vpp.v2.backtest.month_ci), the 7-day block interval (vpp.stochastic.block_ci) and the in-sample months above 0.
Baselines: R0 (e5); for O-A and its controls R0' when the null table differs from the official one.

Decision per road (O-A, O-B), in-sample only. H = oracle - baseline, I = oracle - control (seed 0):
  GO           H >= 50 with month-block lower bound > 0, I >= H/2 with lower bound > 0, H > 0 in >= 10 of 15 months
  CALIBRATION  not GO, but the control alone beats the baseline by >= 25 with lower bound > 0
  NO-GO        otherwise
Consistency: H(O-B) > H(O-B2) + 10 or H(O-B2) > H(O-C-e) + 10 makes the result inconclusive; H(O-C-e) < 50 makes the
O-B road NO-GO by construction. A NO-GO road whose test-A headroom is >= 50 is inconclusive (re-test on new days).
Every oracle number is a perfect-foresight bound, not something that can be earned.

Output: out/score_p0.csv, out/levels_p0.csv, out/months_p0.csv, out/decision_p0.json, NUMBERS.csv
Run:    .venv/bin/python analysis/2026-10-28/spikev3/score_p0.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import stochastic as sto  # noqa: E402
from vpp.spikev3 import protocol as pr  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402

OUT = HERE / "out"
R0 = vb.E5.name
R0N = "R0' null table"
IN = "in-sample 2025-04-01..2026-06-30"
LATE = "in-sample 2026-04-01..06-30 (report only)"
TEST_A = "test 2026-07-01..10-15"
SEGMENTS = {**pr.SEGMENTS, LATE: (pd.Timestamp("2026-04-01"), pd.Timestamp("2026-06-30"))}
CAL = 25.0   # $/day: the control's own gain that calls for a calibration
MONTHS = 10  # of the 15 in-sample months with H > 0
CONSIST = 10.0


def segment_of(index: pd.DatetimeIndex, name: str) -> np.ndarray:
    lo, hi = SEGMENTS[name]
    return (index >= lo) & (index <= hi)


def diff_row(money, led, run, base, name) -> dict:
    sel = segment_of(money.index, name)
    diff = (money.loc[sel, run] - money.loc[sel, base]).dropna()
    mdiff = (led.loc[sel, run] - led.loc[sel, base]).dropna()
    rec = {"period": name, "run": run, "base": base, "days": len(diff), "diff": diff.mean(), "market": mdiff.mean(),
           "bill": diff.mean() - mdiff.mean(), "days_better": int((diff > 1).sum()), "days_worse": int((diff < -1).sum())}
    by_m = diff.groupby(diff.index.to_period("M")).mean()
    rec["months"], rec["months_above_0"] = len(by_m), int((by_m > 0).sum())
    if len(by_m) >= 2:
        rec["month_lo"], rec["month_hi"] = vb.month_ci(diff)
    if len(diff) >= 2 * sto.BLOCK:
        rec["block7_lo"], rec["block7_hi"] = sto.block_ci(diff.to_numpy())
    return rec


def main() -> None:
    money = pd.read_csv(OUT / "money.csv", index_col=0, parse_dates=True)
    led = pd.read_csv(OUT / "daily.csv", parse_dates=["day"]).pivot_table(index="day", columns="strategy",
                                                                           values="ledger_total").reindex(money.index)
    runs = list(money.columns)
    a_base = R0N if R0N in runs else R0
    pairs = [("R5 no-state", R0), ("O-B", R0), ("O-B2", R0), ("O-C-e", R0), ("O-C-ef", R0),
             *((f"O-B ctrl s{s}", R0) for s in (0, 1, 2)), ("O-B", "O-B ctrl s0"),
             ("O-A", a_base), *((f"O-A ctrl s{s}", a_base) for s in (0, 1, 2)), ("O-A", "O-A ctrl s0")]
    if a_base != R0:
        pairs.append((R0N, R0))
    rows = [diff_row(money, led, a, b, name) for a, b in pairs for name in SEGMENTS]
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "score_p0.csv", index=False)
    levels = pd.DataFrame([{"period": name, "run": r, "money_per_day": money.loc[segment_of(money.index, name), r].mean(),
                            "market_per_day": led.loc[segment_of(money.index, name), r].mean()}
                           for name in SEGMENTS for r in runs])
    levels.to_csv(OUT / "levels_p0.csv", index=False)
    months = pd.concat({f"{a} - {b}": (money[a] - money[b])[segment_of(money.index, IN)].groupby(
        lambda d: d.to_period("M")).mean() for a, b in pairs}, axis=1)
    months.to_csv(OUT / "months_p0.csv")

    def get(run, base, name=IN):
        return res[(res["run"] == run) & (res["base"] == base) & (res["period"] == name)].iloc[0]

    decision = {"baseline_for_O-A": a_base}
    for road, base in (("O-A", a_base), ("O-B", R0)):
        h, i, cs = get(road, base), get(road, f"{road} ctrl s0"), get(f"{road} ctrl s0", base)
        go = (h["diff"] >= pr.GATE and h["month_lo"] > 0 and i["diff"] >= h["diff"] / 2 and i["month_lo"] > 0
              and h["months_above_0"] >= MONTHS)
        cal = (not go) and cs["diff"] >= CAL and cs["month_lo"] > 0
        out = "GO" if go else "CALIBRATION" if cal else "NO-GO"
        ta = get(road, base, TEST_A)
        note = []
        if out == "NO-GO" and ta["diff"] >= pr.GATE:
            note.append("inconclusive: test-A headroom >= 50, re-test on new unseen days")
        decision[road] = {"H": h["diff"], "H_month_lo": h["month_lo"], "H_month_hi": h["month_hi"],
                          "H_market": h["market"], "H_bill": h["bill"], "months_above_0": int(h["months_above_0"]),
                          "I": i["diff"], "I_month_lo": i["month_lo"], "control_vs_base": cs["diff"],
                          "control_month_lo": cs["month_lo"],
                          "controls_s1_s2": [get(f"{road} ctrl s{s}", base)["diff"] for s in (1, 2)],
                          "test_A_H": ta["diff"], "outcome": out, "notes": note}
    hb, hb2, hce = (get(r, R0)["diff"] for r in ("O-B", "O-B2", "O-C-e"))
    consistent = not (hb > hb2 + CONSIST or hb2 > hce + CONSIST)
    decision["consistency"] = {"H_O-B": hb, "H_O-B2": hb2, "H_O-C-e": hce, "consistent": bool(consistent),
                               "O-B road possible (H O-C-e >= 50)": bool(hce >= pr.GATE)}
    if not consistent:
        decision["O-B"]["notes"].append("inconclusive: the dispatcher does not use more information monotonically")
    if hce < pr.GATE and decision["O-B"]["outcome"] == "GO":
        decision["O-B"]["outcome"] = "NO-GO"
        decision["O-B"]["notes"].append("H(O-C-e) < 50: NO-GO by construction")
    oc = get("O-C-ef", R0)
    decision["O-C-ef report"] = {"H": oc["diff"], "month_lo": oc["month_lo"], "market": oc["market"], "bill": oc["bill"]}
    b4 = get("R5 no-state", R0)
    oa_r5 = get("O-A", a_base)["diff"] - b4["diff"]  # (O-A - base) - (R5 - R0) = O-A - R5 when base is R0
    decision["B4 now"] = {"e5_minus_R5": -b4["diff"], "e5_minus_R5_month_lo": -b4["month_hi"],
                          "e5_minus_R5_month_hi": -b4["month_lo"], "OA_minus_R5": oa_r5,
                          "capture_share": -b4["diff"] / oa_r5 if oa_r5 > 0 else None}
    a, b = decision["O-A"]["outcome"] == "GO", decision["O-B"]["outcome"] == "GO"
    decision["step1"] = ("both: half-hour and day models" if a and b else "half-hour spike model" if b
                         else "spike-day model" if a else
                         "calibration follow-up" if "CALIBRATION" in (decision["O-A"]["outcome"], decision["O-B"]["outcome"])
                         else "no step 1 now: better spike information does not pass in-sample")
    decision["inconclusive_roads"] = [r for r in ("O-A", "O-B") if decision[r]["notes"]]
    ce = get("O-C-e", R0)
    # PLAN 2.4: perfect prices worth >= 50 with a lower bound > 0 means the money is in ordinary price forecasting
    decision["price_forecast_project"] = bool(ce["diff"] >= pr.GATE and ce["month_lo"] > 0)
    (OUT / "decision_p0.json").write_text(json.dumps(decision, indent=1, default=float) + "\n")

    with pd.option_context("display.width", 230, "display.max_rows", 200):
        print("== $/day by period\n" + levels.pivot_table(index="run", columns="period", values="money_per_day")
              .round(1).to_string())
        cols = ["period", "run", "base", "days", "diff", "market", "bill", "month_lo", "month_hi", "block7_lo",
                "block7_hi", "months_above_0"]
        print("\n== differences ($/day; oracles are perfect-foresight bounds)\n" + res[cols].round(1).to_string(index=False))
    print("\n== decision\n" + json.dumps(decision, indent=1, default=lambda x: round(float(x), 2)))
    numbers(res, levels)


def numbers(res: pd.DataFrame, levels: pd.DataFrame) -> None:
    periods = {IN: "样本内 2025-04-01 至 2026-06-30（456 天）", LATE: "样本内后段 2026-04-01 至 2026-06-30（91 天，只报告）",
               TEST_A: "测试期 2026-07-01 至 2026-10-15（107 天，已看过）",
               "test 2026-10-16..10-22": "测试期 2026-10-16 至 2026-10-22（7 天，没看过）"}
    tag = {IN: "样本内", LATE: "样本内后段", TEST_A: "测试A", "test 2026-10-16..10-22": "测试B"}
    out = []

    def add(name, value, unit, period, formula="", status="回测", note=""):
        out.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period,
                    "source": "out/score_p0.csv, out/levels_p0.csv", "status": status, "check": "", "note": note})
    for _, r in levels.iterrows():
        add(f"{tag[r['period']]}_{r['run']}_每天的钱", r["money_per_day"], "A$/天", periods[r["period"]])
        add(f"{tag[r['period']]}_{r['run']}_每天市场", r["market_per_day"], "A$/天", periods[r["period"]])
    for _, r in res.iterrows():
        p, t = periods[r["period"]], tag[r["period"]]
        k = f"{t}_{r['run']}_减_{r['base']}"
        oracle = r["run"].startswith("O-") and "ctrl" not in r["base"]
        add(k, r["diff"], "A$/天", p, f"{{{t}_{r['run']}_每天的钱}} - {{{t}_{r['base']}_每天的钱}}",
            note="完美预知上限，不可实现" if oracle else "")
        add(f"{k}_市场", r["market"], "A$/天", p, f"{{{t}_{r['run']}_每天市场}} - {{{t}_{r['base']}_每天市场}}")
        add(f"{k}_电费", r["bill"], "A$/天", p, f"{{{k}}} - {{{k}_市场}}")
        for col, label in (("month_lo", "月块下限"), ("month_hi", "月块上限"), ("block7_lo", "7天块下限"), ("block7_hi", "7天块上限")):
            if col in r and pd.notna(r[col]):
                add(f"{k}_{label}", r[col], "A$/天", p)
        if r["period"] == IN:
            add(f"{k}_为正的月数", r["months_above_0"], "月", p)
    add("门槛", pr.GATE, "A$/天", periods[IN], status="假设", note="PLAN 变更记录 1 事先定")
    add("校准门槛", CAL, "A$/天", periods[IN], status="假设", note="PLAN 变更记录 1 事先定")
    c = json.loads((OUT / "constants.json").read_text())
    add("注入尖峰价c", c["c_spike_price"], "A$/MWh", periods[IN], note="样本内 RTP ≥ $200 的半小时的中位数")
    add("真实标签占比", 100 * c["label_share_in_sample"], "%", periods[IN], note="09:00 起有 ≥ $200 半小时的交易日")
    pd.DataFrame(out).to_csv(HERE / "NUMBERS.csv", index=False)
    print(f"{len(out)} numbers -> NUMBERS.csv")


if __name__ == "__main__":
    main()
