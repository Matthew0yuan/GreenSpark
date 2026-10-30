"""Write NUMBERS.csv from out/ (every number in RESULTS.md).

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/peak_day_money/summarize.py
"""

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
PER = {"A": "热季 2024-12-01 至 2025-03-31（121 天，历史诊断）", "B": "热季 2025-12-01 至 2026-03-31（121 天，历史诊断）"}
LAB = {"proxy_od": "运行需求代理", "proxy_er": "电能需求代理", "official": "官方"}
SHORT = {"R0 existing rule": "R0", "R1 M3 + DP c=0.03": "R1", "R2 M3 + DP money cost q=0.75": "R2",
         "R3 M3 + money marginal q=0.75": "R3", "oracle": "完美预知", "ref M3 + DP c=0 (spend all)": "用满预算"}
rows = []


def add(name, value, unit, period, source, status="回测", formula="", check="", note=""):
    rows.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period, "source": source,
                 "status": status, "check": check, "note": note})


def main() -> None:
    m = pd.read_csv(OUT / "money.csv")
    c = pd.read_csv(OUT / "calls.csv")
    vc = pd.read_csv(OUT / "value_curve.csv")
    lg = pd.read_csv(OUT / "r3_log.csv", parse_dates=["trading_day"])
    fz = json.loads((OUT / "frozen_money_rule.json").read_text(encoding="utf-8"))
    ledger = {"L1": [x for x in m["ledger"].unique() if x.startswith("L1")][0], "L2": [x for x in m["ledger"].unique() if x.startswith("L2")][0]}
    note_l2 = "L2 情景（2027 年起净出口记负，假设），每个已加入户每个热季"
    for (fold, label), g in m.groupby(["fold", "label"]):
        per = f"{PER[fold]}，{LAB[label]}标签"
        for meth, short in SHORT.items():
            for B in ((4,) if meth == "oracle" else (15, 30, 45)):
                r = g[(g["method"] == meth) & (g["budget"] == B)]
                for lk in ("L2", "L1"):
                    rr = r[r["ledger"] == ledger[lk]].iloc[0]
                    tag = f"{fold}_{label}_{short}_{B}次_{lk}"
                    add(f"Zenith净额_{tag}", round(float(rr["zenith_net"]), 2), "A$", per, "out/money.csv",
                        note=note_l2 if lk == "L2" else "L1 现行规则，每个已加入户每个热季")
                    if lk == "L2":
                        add(f"客户净收益_{tag}", round(float(rr["customer_net"]), 2), "A$", per, "out/money.csv", note=note_l2)
                        add(f"调用次数_{tag}", int(rr["calls"]), "次", per, "out/money.csv", check=f"<= {B}")
                cm = c[(c["fold"] == fold) & (c["label"] == label) & (c["method"] == meth) & (c["budget"] == B)]
                if len(cm):
                    add(f"盖住高峰日_{fold}_{label}_{short}_{B}次", int(cm["covered_days"].iloc[0]), "天", per, "out/calls.csv", check="<= 4")
    for _, r in vc[vc["q"] == 0.75].iterrows():
        per = f"训练季 {r['train']}，按评估季 {int(r['season'])}-{int(r['season']) + 1} 的容量价格"
        for k in (2, 3, 4):
            add(f"V{k}_{r['fold']}", round(float(r[f"V_{k}"]), 1), "A$", per, "out/value_curve.csv", status="推算", note=note_l2)
        add(f"每次调用成本_{r['fold']}", round(float(r["cost_per_call"]), 2), "A$/次", per, "out/value_curve.csv", status="推算",
            note="每个已加入户，L2 和 L1 相同")
        add(f"高峰日期望边际价值_{r['fold']}", round(float(r["dVbar"]), 1), "A$", per, "out/value_curve.csv", status="推算", note="q = 0.75")
    x = lg[(lg["q"] == 0.75) & (lg["budget"] == 30)]
    miss = x[x["trading_day"] == "2025-02-06"].iloc[0]
    add("M3概率_2025-02-06", round(float(miss["p_m3"]), 4), "-", PER["A"], "out/r3_log.csv")
    add("R3边际价值_2025-02-06", round(float(miss["dV"]), 2), "A$", PER["A"], "out/r3_log.csv", status="推算", note=note_l2)
    for B in (15, 30):
        y = lg[(lg["q"] == 0.75) & (lg["budget"] == B)]
        add(f"R3名额代价最大值_{B}次", round(float(y["lam"].replace(float("inf"), float("nan")).max()), 4), "A$",
            "两个历史热季", "out/r3_log.csv", status="推算", note="名额用完以后的日子不计")
    add("冻结版_容量价格_2027-28", fz["rcp_2027_28"], "A$/(MW*年)", "容量年 2027-28", "out/frozen_money_rule.json", status="调研",
        note="vpp/contracts.py RCP[2027]")
    for k in (2, 3, 4):
        add(f"冻结版_V{k}", round(fz["value_aud_by_peak_days_covered"][k], 1), "A$", "三个热季 2023-24 至 2025-26，按 2027-28 容量价格",
            "out/frozen_money_rule.json", status="推算", note=note_l2)
    add("冻结版_每次调用成本", round(fz["cost_per_call_aud"], 2), "A$/次", "三个热季 2023-24 至 2025-26", "out/frozen_money_rule.json", status="推算")
    combo = OUT / "posthoc_combo.csv"
    if combo.exists():
        pc = pd.read_csv(combo)
        pc = pc[pc["ledger"] == "L2"]
        short = {"existing rule (q=0.9)": "现有规则q0.9", "strict rule (q=0.75)": "严一点q0.75", "R3": "R3",
                 "strict or R3": "任一个说调", "strict and R3": "两个都说调"}
        for _, r in pc.iterrows():
            fold = "A" if r["season"] == 2024 else "B"
            per = f"{PER[fold]}，{LAB[r['label']]}标签"
            tag = f"{fold}_{r['label']}_{short[r['rule']]}_30次"
            add(f"事后_Zenith净额_{tag}", round(float(r["zenith_net"]), 2), "A$", per, "out/posthoc_combo.csv",
                note=note_l2 + "；看完结果后加的，不参与判定")
            add(f"事后_调用次数_{tag}", int(r["calls"]), "次", per, "out/posthoc_combo.csv", check="<= 30")
            add(f"事后_盖住高峰日_{tag}", int(r["covered_days"]), "天", per, "out/posthoc_combo.csv", check="<= 4")
    b23 = OUT / "posthoc_2023_budget.csv"
    if b23.exists():
        for _, r in pd.read_csv(b23).iterrows():
            tag = f"{r['rule'].split(' ')[0].replace('=', '')}_{int(r['budget'])}次"
            per = "热季 2023-12-01 至 2024-03-31（容差用后面两个热季，不是前向）"
            add(f"事后_2023_调用次数_{tag}", int(r["calls"]), "次", per, "out/posthoc_2023_budget.csv", check=f"<= {int(r['budget'])}")
            add(f"事后_2023_盖住高峰日_{tag}", int(r["covered_days"]), "天", per, "out/posthoc_2023_budget.csv", check="<= 4",
                note=f"最后一次调用 {r['last_call']}")
    pd.DataFrame(rows).to_csv(HERE / "NUMBERS.csv", index=False, encoding="utf-8")
    print(f"{len(rows)} numbers")


if __name__ == "__main__":
    main()
