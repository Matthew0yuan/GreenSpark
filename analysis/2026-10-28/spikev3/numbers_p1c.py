"""NUMBERS.csv rows for spikev3 step 1c (PLAN.md section 6): feature attribution (explain_p1c.py) and, once
overfit_p1c.py has run, the placebo and split checks. Replaces the rows starting P1C_.

Run:    .venv/bin/python analysis/2026-10-28/spikev3/numbers_p1c.py
"""

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
P_IN = "样本内 2025-04-01 至 2026-06-30（456 天）"
P_TA = "测试期 2026-07-01 至 2026-10-15（107 天，已看过）"
PERIOD = {"in-sample 2025-04-01..2026-06-30": ("样本内", P_IN), "test 2026-07-01..10-15": ("测试A", P_TA)}
PUSHERS = ["G1STEM", "G0近期尖峰频率", "预调度数量和余量", "AEMO预调度价格", "天气", "原有历史价格", "时间"]
KEY = ["pd_headroom", "aemo_energy", "stem_price", "stem_cover"]


def safe(s: str) -> str:
    return s.replace(" ", "")


def main() -> None:
    path = HERE / "NUMBERS.csv"
    old = pd.read_csv(path)
    old = old[~old["name"].str.startswith("P1C_")]
    out = []
    val = dict(zip(old["name"], old["value"]))

    def add(name, value, unit, period, formula="", note="", source="out/p1c_shap_groups.csv"):
        if value is None:
            value = eval(formula.replace("{", "val['").replace("}", "']"))
        val[name] = value
        out.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period,
                    "source": source, "status": "回测", "check": "", "note": note})

    # attribution by group: mean contribution (log-odds) and its change from in-sample to test
    gr = pd.read_csv(OUT / "p1c_shap_groups.csv")
    for _, r in gr.iterrows():
        tag, per = PERIOD[r["period"]]
        add(f"P1C_归因_D{r['days_ahead']}_{tag}_{safe(r['group'])}_平均贡献", r["mean"], "1", per,
            note="spikev3 原始概率的对数几率贡献（校准前）")
    for d in (0, 1):
        groups = [safe(g) for g in gr[gr["days_ahead"] == d]["group"].unique()]
        for g in groups:
            add(f"P1C_归因_D{d}_{g}_测试期变化", None, "1", f"{P_TA} 减 {P_IN}",
                f"{{P1C_归因_D{d}_测试A_{g}_平均贡献}} - {{P1C_归因_D{d}_样本内_{g}_平均贡献}}")
        pushers = [g for g in PUSHERS if g in groups]
        total = " + ".join(f"{{P1C_归因_D{d}_{g}_测试期变化}}" for g in pushers)
        add(f"P1C_归因_D{d}_特征推高合计", None, "1", f"{P_TA} 减 {P_IN}", total, "不含常数项")
        for g in pushers:
            add(f"P1C_归因_D{d}_{g}_推高占比", None, "1", f"{P_TA} 减 {P_IN}",
                f"{{P1C_归因_D{d}_{g}_测试期变化}} / {{P1C_归因_D{d}_特征推高合计}}")

    # attribution by feature: the most used features and how the key ones differ on spike rows
    fr = pd.read_csv(OUT / "p1c_shap_features.csv")
    ins = fr[(fr["period"] == "in-sample 2025-04-01..2026-06-30") & (fr["feature"] != "bias")]
    for d in (0, 1):
        f = ins[ins["days_ahead"] == d].nlargest(5, "mean_abs")
        for rank, (_, r) in enumerate(f.iterrows(), 1):
            add(f"P1C_归因_D{d}_第{rank}名_{r['feature']}_平均绝对贡献", r["mean_abs"], "1", P_IN,
                source="out/p1c_shap_features.csv")
        for _, r in ins[(ins["days_ahead"] == d) & ins["feature"].isin(KEY)].iterrows():
            for col, lab in (("mean_spike", "尖峰行平均贡献"), ("mean_nonspike", "非尖峰行平均贡献")):
                add(f"P1C_归因_D{d}_{r['feature']}_{lab}", r[col], "1", P_IN, source="out/p1c_shap_features.csv")

    # reliability: the two highest deciles
    rel = pd.read_csv(OUT / "p1c_reliability.csv")
    for _, r in rel[rel["bin"] >= 8].iterrows():
        tag, per = PERIOD[r["period"]]
        k = f"P1C_可靠性_D{r['days_ahead']}_{tag}_第{int(r['bin']) + 1}档"
        for col, lab in (("mean_p", "预测概率"), ("observed", "实际尖峰率"), ("obs_lo", "实际下限"), ("obs_hi", "实际上限")):
            add(f"{k}_{lab}", 100 * r[col], "%", per, source="out/p1c_reliability.csv")
    for d in (0, 1):
        for what in ("平均预测概率", "实际尖峰率"):
            add(f"P1C_D{d}_spikev3_{what}_测试期对样本内倍数", None, "1", f"{P_TA} 对 {P_IN}",
                f"{{P1_测试A_D{d}_spikev3_{what}}} / {{P1_样本内_D{d}_spikev3_{what}}}")

    # placebo and split (after overfit_p1c.py)
    if (OUT / "decision_p1c.json").exists():
        plc = pd.read_csv(OUT / "p1c_placebo.csv")
        for _, r in plc.iterrows():
            k = f"P1C_假特征_D{r['days_ahead']}_{r['group']}_{'真' if r['kind'] == 'real' else '种子' + str(r['seed'])}"
            add(f"{k}_对数损失改善", r["delta"], "1", P_IN, source="out/p1c_placebo.csv")
            add(f"{k}_日块下限", r["day_lo"], "1", P_IN, source="out/p1c_placebo.csv")
        dec = json.loads((OUT / "decision_p1c.json").read_text())
        add("P1C_假特征_通过个数", dec["placebo"]["passed"], "次", P_IN, source="out/decision_p1c.json")
        add("P1C_假特征_比较个数", dec["placebo"]["comparisons"], "次", P_IN, source="out/decision_p1c.json")
        sp = pd.read_csv(OUT / "p1c_split.csv")
        for _, r in sp.iterrows():
            per = ("选择段 2025-04-01 至 2025-12-31（275 天）" if r["part"] == "selection"
                   else "检验段 2026-01-01 至 2026-06-30（181 天）")
            k = f"P1C_切开_D{r['days_ahead']}_{r['part']}_{r['model']}_对{r['versus']}"
            for col, lab in (("delta", "对数损失改善"), ("day_lo", "日块下限"), ("day_hi", "日块上限")):
                add(f"{k}_{lab}", r[col], "1", per, source="out/p1c_split.csv")

    pd.concat([old, pd.DataFrame(out)], ignore_index=True).to_csv(path, index=False)
    print(f"{len(out)} step-1c numbers -> NUMBERS.csv")


if __name__ == "__main__":
    main()
