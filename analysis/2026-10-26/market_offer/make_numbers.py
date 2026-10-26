"""NUMBERS.csv from out/checks.json (check_offers.py): every number RESULTS.md cites.

Run (repo root):  .venv/bin/python analysis/2026-10-26/market_offer/make_numbers.py
"""

import csv
import json
from pathlib import Path

HERE = Path(__file__).parent
ALL = "全部 2025-04-01 至 2026-10-22（570 天）"
PER = {"样本内": "样本内 2025-04-01 至 2026-06-30（456 天）", "测试期A": "测试期 2026-07-01 至 2026-10-15（107 天）",
       "测试期B": "测试期 2026-10-16 至 2026-10-22（7 天）"}


def main() -> None:
    r = json.loads((HERE / "out/checks.json").read_text())
    rows = []

    def add(name, value, unit, period, status="回测", formula="", source="out/checks.json", check="", note=""):
        rows.append([name, value, unit, formula, period, source, status, check, note])

    add("V0b_比较天数", r["V0b"]["days"], "天", "2025-04-01 至 2026-10-15（563 天）", note="和 reports/v2_g5_daily.csv 的 +lock 行")
    add("V0b_total最大差", f"{r['V0b']['max_abs_diff']['total']:.3g}", "A$", "2025-04-01 至 2026-10-15（563 天）",
        check="<= 1e-6")
    add("V1_报价请求数", r["V1"]["gate"]["days"] + r["V1"]["0900"]["days"], "1", ALL, note="截止时报价和 09:00 报价各 570 份")
    add("V1_截止时报价时段数", r["V1"]["gate"]["dis"], "次", ALL, note="每天 288 个调度时段")
    add("V1_0900报价时段数", r["V1"]["0900"]["dis"], "次", ALL, note="每天 09:15 以后的 273 个调度时段")
    add("V1_总时段数", r["V1"]["gate"]["dis"] + r["V1"]["0900"]["dis"], "次", ALL,
        formula="{V1_截止时报价时段数} + {V1_0900报价时段数}")
    add("V1_错误数", r["V1"]["gate"]["errors"] + r["V1"]["0900"]["errors"], "1", ALL, check="<= 0")
    add("V1c_故意做坏种类", len(r["V1c"]["plants"]), "1", "2026-10-20（1 天）")
    add("V1c_被抓到种类", sum(p["caught"] for p in r["V1c"]["plants"].values()), "1", "2026-10-20（1 天）")
    v2 = r["V2"]
    add("V2_比较步数", v2["strict"], "次", ALL, note="价格低于当月上限、计划充电不超过充电能力的 5 分钟步")
    add("V2_不一致步数", v2["mismatch"], "次", ALL, check="<= 0")
    add("V2_最大差", round(v2["max_diff_strict"], 4), "MW", ALL)
    add("V2_价格到上限步数", v2["outside"]["价格到了上限"], "次", ALL)
    add("V2_价格到上限不一致步数", v2["outside_mismatch"], "次", ALL)
    add("V2_阳性对照_价格加1元不一致步数", v2["planted_price_mismatch"], "次", ALL, check=">= 1")
    add("V2_阳性对照_数量加0.01MW不一致步数", v2["planted_mw_mismatch"], "次", ALL, check=">= 1")
    for p, n in v2["ladder_sold_steps"].items():
        add(f"V2_多卖成交步数_{p}", n, "次", PER[p])
    v3 = r["V3"]
    add("V3_抽样数", len(v3["samples"]), "1", ALL)
    add("V3_截止前报价不变", v3["same"], "1", ALL)
    add("V3_后面报价变了", v3["later_changed"], "1", ALL, check=">= 15")
    add("V3_偷看版本被抓到", v3["leak_caught"], "1", ALL, check=">= 1")
    for p in PER:
        v = r["V4"][p]
        add(f"V4_天数_{p}", v["days"], "天", PER[p])
        add(f"V4_步数_{p}", v["steps"], "次", PER[p])
        add(f"V4_有差异步数_{p}", v["steps_off"], "次", PER[p])
        add(f"V4_有差异比例_{p}", round(100 * v["steps_off"] / v["steps"], 1), "%", PER[p],
            formula=f"{{V4_有差异步数_{p}}} / {{V4_步数_{p}}}", status="推算")
        add(f"V4_差异电量_{p}", round(v["abs_mwh"], 1), "MWh", PER[p], status="推算", note="|截止时报价出清量 − 回测执行量| × 5 分钟")
        add(f"V4_推算金额_{p}", round(v["money_aud"]), "A$", PER[p], status="推算",
            note="只算 energy：电量差 × 5 分钟价格；不含 FCESS、电费、存电以后的价值")
        add(f"V4_推算金额每天_{p}", round(v["money_aud_per_day"], 1), "A$/天", PER[p], status="推算",
            formula=f"{{V4_推算金额_{p}}} / {{V4_天数_{p}}}")
        for c, x in v["by_cause"].items():
            key = c.split("（")[0].replace("、", "_").replace(" ", "")
            add(f"V4_{key}_步数_{p}", x["steps"], "次", PER[p], status="推算")
            add(f"V4_{key}_电量_{p}", round(x["abs_mwh"], 1), "MWh", PER[p], status="推算")
            add(f"V4_{key}_金额_{p}", round(x["money_aud"]), "A$", PER[p], status="推算")
    o = r["others"]
    add("每个时段最多段数", o["max_tranches"], "1", ALL, check="<= 10")
    add("合并段次数", o["merged"], "1", ALL)
    for p in PER:
        x = o[p]
        add(f"负价时计划放电步数_{p}", x["neg_price_plan_discharge_steps"], "次", PER[p])
        add(f"负价时计划放电电量_{p}", round(x["neg_price_plan_discharge_mwh"], 2), "MWh", PER[p])
        add(f"raise梯形冲突步数_{p}", x["raise_trapezium_out"], "次", PER[p])
        add(f"有raise的步数_{p}", x["steps_with_raise"], "次", PER[p])
        add(f"raise梯形冲突比例_{p}", round(100 * x["raise_trapezium_out"] / x["steps_with_raise"], 1), "%", PER[p],
            formula=f"{{raise梯形冲突步数_{p}}} / {{有raise的步数_{p}}}", status="推算")
        add(f"lower梯形冲突步数_{p}", x["lower_trapezium_out"], "次", PER[p])
        add(f"有lower的步数_{p}", x["steps_with_lower"], "次", PER[p])
    with open(HERE / "NUMBERS.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "value", "unit", "formula", "period", "source", "status", "check", "note"])
        w.writerows(rows)
    print(len(rows), "numbers")


if __name__ == "__main__":
    main()
