"""NUMBERS.csv from the out/ tables (every number RESULTS.md quotes, plus the full comparison tables).

Run: .venv/bin/python analysis/2026-10-28/scarcity_signals/write_numbers.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
IN = "样本内 2025-04-01 至 2026-06-30（456 天）"
PERIOD = {
    "in-sample 2025-04-01..2026-06-30": IN,
    "test A 2026-07-01..10-15": "测试期 A 2026-07-01 至 2026-10-15（107 天，已看过）",
    "test B 2026-10-16..10-22": "测试期 B 2026-10-16 至 2026-10-22（7 天，没看过）",
}
TASK = {0: "D0", 1: "D1"}
rows = []


def add(name, value, unit, period, source, status="回测", formula="", check="", note=""):
    rows.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period, "source": source,
                 "status": status, "check": check, "note": note})


def comparison(prefix: str, r: pd.Series, period: str, source: str, base_col: str, week: bool) -> None:
    add(f"{prefix}_尖峰半小时数", r["spikes"], "半小时", period, source, check=">= 0")
    add(f"{prefix}_AP基准", r[base_col], "1", period, source)
    add(f"{prefix}_AP候选", r["ap_cand"], "1", period, source)
    if pd.notna(r.get("gain")):
        add(f"{prefix}_AP增益", r["gain"], "1", period, source, formula=f"{{{prefix}_AP候选}} - {{{prefix}_AP基准}}")
        add(f"{prefix}_AP增益_日块下限", r["day_lo"], "1", period, source)
        add(f"{prefix}_AP增益_日块上限", r["day_hi"], "1", period, source)
        if week:
            add(f"{prefix}_AP增益_周块下限", r["week_lo"], "1", period, source)
            add(f"{prefix}_AP增益_周块上限", r["week_hi"], "1", period, source)


def main() -> None:
    rep = pd.read_csv(OUT / "reproduction.csv")
    for _, r in rep.iterrows():
        add(f"复现_{TASK[r['days_ahead']]}_最大绝对差", r["max_abs_diff"], "1", "旧划分，2024-10 起全部样本外月份", "out/reproduction.csv",
            check="< 1e-9", note="重跑 C_strict 对已发表预测")
    leak = pd.read_csv(OUT / "leak_audit.csv")
    add("泄漏审计_A1A2_改变的检查数", int((leak[leak["check"].isin(["A1", "A2"])]["changed"] != 0).sum()), "次",
        "随机 30 个发出日（2023-12 至 2026-10）", "out/leak_audit.csv", check="<= 0")
    p1 = leak[leak["check"] == "P1"]
    add("泄漏审计_P1_抓到的天数", int(p1.groupby("day")["changed"].min().gt(0).sum()), "天",
        "随机 30 个发出日（2023-12 至 2026-10）", "out/leak_audit.csv", note="两种埋下的泄漏每天都被抓到才计 1 天")

    main_ = pd.read_csv(OUT / "score_main.csv")
    for _, r in main_.iterrows():
        p = f"样本内_{TASK[r['days_ahead']]}_{r['model']}"
        comparison(p, r, IN, "out/score_main.csv", "ap_C_strict", week=True)
        add(f"{p}_增益为正的季度数", r["quarters_positive"], "季", IN, "out/score_main.csv", check="<= 5")
    test = pd.read_csv(OUT / "score_test.csv")
    for _, r in test.iterrows():
        seg = "测试A" if r["period"].startswith("test A") else "测试B"
        comparison(f"{seg}_{TASK[r['days_ahead']]}_{r['model']}", r, PERIOD[r["period"]], "out/score_test.csv", "ap_C_strict", week=False)
    sec = pd.read_csv(OUT / "score_secondary.csv")
    for _, r in sec.iterrows():
        seg = {"in-sample": "样本内", "test A": "测试A", "test B": "测试B"}[r["period"].split(" 20")[0]]
        comparison(f"{seg}_{TASK[r['days_ahead']]}_{r['model']}_对_{r['versus']}", r, PERIOD[r["period"]], "out/score_secondary.csv",
                   "ap_versus", week=seg == "样本内")
    seeds = OUT / "score_seeds.csv"
    if seeds.exists():
        for _, r in pd.read_csv(seeds).iterrows():
            add(f"种子{r['seed']}_样本内_{TASK[r['days_ahead']]}_{r['model']}_AP增益", r["gain"], "1", IN, "out/score_seeds.csv")
    seeds_test = OUT / "score_seeds_test.csv"
    if seeds_test.exists():
        for _, r in pd.read_csv(seeds_test).iterrows():
            add(f"种子{r['seed']}_测试A_{TASK[r['days_ahead']]}_C+stem_AP增益", r["gain"], "1", PERIOD["test A 2026-07-01..10-15"],
                "out/score_seeds_test.csv", note="只报告")

    bud = OUT / "score_budgets.csv"
    if bud.exists():
        for _, r in pd.read_csv(bud).iterrows():
            seg = "样本内" if r["period"].startswith("in-sample") else "测试A"
            p = f"{seg}_{TASK[r['days_ahead']]}_{r['candidate']}_报警{r['budget']:.0%}"
            per = PERIOD[r["period"]]
            add(f"{p}_报警半小时数", r["flagged"], "半小时", per, "out/score_budgets.csv", note="只报告")
            add(f"{p}_尖峰半小时数", r["spikes"], "半小时", per, "out/score_budgets.csv", note="只报告")
            for tag, label in (("base", "基准"), ("cand", "候选")):
                add(f"{p}_{label}_抓到", r[f"hits_{tag}"], "半小时", per, "out/score_budgets.csv", check=f"<= {r['spikes']}", note="只报告")
                add(f"{p}_{label}_召回率", 100 * r[f"recall_{tag}"], "%", per, "out/score_budgets.csv",
                    formula=f"{{{p}_{label}_抓到}} / {{{p}_尖峰半小时数}}", note="只报告")
                add(f"{p}_{label}_精确率", 100 * r[f"precision_{tag}"], "%", per, "out/score_budgets.csv",
                    formula=f"{{{p}_{label}_抓到}} / {{{p}_报警半小时数}}", note="只报告")
    on = pd.read_csv(OUT / "describe_onsets.csv")
    names = {("jump in the 60 min before", "spike onsets"): "机制_尖峰开始前60分钟有上调价跳变_比例",
             ("jump in the 60 min before", "all 5-min times"): "机制_任意时刻前60分钟有上调价跳变_比例",
             ("spike onset in the next 60 min", "jump intervals"): "机制_跳变后60分钟内尖峰开始_比例",
             ("spike onset in the next 60 min", "all 5-min times"): "机制_任意时刻后60分钟内尖峰开始_比例"}
    for _, r in on.iterrows():
        n = names[(r["measure"], r["rows"])]
        add(n, 100 * r["share"], "%", IN, "out/describe_onsets.csv")
        add(f"{n}_下限", 100 * r["lo"], "%", IN, "out/describe_onsets.csv")
        add(f"{n}_上限", 100 * r["hi"], "%", IN, "out/describe_onsets.csv")
        add(f"{n}_样本数", r["n"], "1", IN, "out/describe_onsets.csv")
    add("机制_尖峰开始前跳变_倍数", on["share"].iloc[0] / on["share"].iloc[1], "1", IN, "out/describe_onsets.csv",
        formula="{机制_尖峰开始前60分钟有上调价跳变_比例} / {机制_任意时刻前60分钟有上调价跳变_比例}")
    add("机制_跳变后尖峰_倍数", on["share"].iloc[2] / on["share"].iloc[3], "1", IN, "out/describe_onsets.csv",
        formula="{机制_跳变后60分钟内尖峰开始_比例} / {机制_任意时刻后60分钟内尖峰开始_比例}")
    req = pd.read_csv(OUT / "describe_requirement.csv")
    for _, r in req.iterrows():
        add(f"机制_{r['service']}_跳变时需求量上升_比例", 100 * r["req_up_at_jumps"], "%", IN, "out/describe_requirement.csv")
        add(f"机制_{r['service']}_所有时刻需求量上升_比例", 100 * r["req_up_all"], "%", IN, "out/describe_requirement.csv")
    st = pd.read_csv(OUT / "describe_stem_deciles.csv")
    for (auction, tag) in (("same day's auction (D+0)", "当天场次"), ("previous day's auction (D+1)", "前一天场次")):
        g = st[st["auction"] == auction]
        for dec in (1, 10):
            r = g[g["decile"] == dec].iloc[0]
            add(f"机制_STEM{tag}_第{dec}组_尖峰日比例", 100 * r["spike_day_rate"], "%", IN, "out/describe_stem_deciles.csv")
            add(f"机制_STEM{tag}_第{dec}组_最高价下限", r["stem_max_lo"], "A$/MWh", IN, "out/describe_stem_deciles.csv")
        add(f"机制_STEM{tag}_斯皮尔曼相关", g["spearman"].iloc[0], "1", IN, "out/describe_stem_deciles.csv")
        add(f"机制_STEM{tag}_全部天_尖峰日比例", 100 * (g["spike_day_rate"] * g["days"]).sum() / g["days"].sum(), "%", IN,
            "out/describe_stem_deciles.csv")
    pd.DataFrame(rows).to_csv(HERE / "NUMBERS.csv", index=False)
    print(f"{len(rows)} numbers -> NUMBERS.csv")


if __name__ == "__main__":
    main()
