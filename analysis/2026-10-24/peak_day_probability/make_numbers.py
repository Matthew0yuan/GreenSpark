"""Write NUMBERS.csv from the result files (every number RESULTS.md quotes), with formulas for derived values.

Run:  .venv/Scripts/python.exe analysis/2026-10-24/peak_day_probability/make_numbers.py
"""

import csv
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
PER = {"A": "历史诊断 A：热季 2024-12-01 至 2025-03-31（121 天），历史池 2023-24，代理标签",
       "B": "历史诊断 B：热季 2025-12-01 至 2026-03-31（121 天），历史池 2023-24 和 2024-25，官方标签"}
AB = "历史诊断 A+B：热季 2024-25 和 2025-26（242 天）"
rows = []


def add(name, value, unit, period, source, status="回测", formula="", check="", note=""):
    rows.append({"name": name, "value": f"{value:.6g}" if isinstance(value, float) else value, "unit": unit, "formula": formula,
                 "period": period, "source": source, "status": status, "check": check, "note": note})


def main() -> None:
    pm = pd.read_csv(OUT / "season_prob_metrics.csv")
    cm = pd.read_csv(OUT / "season_call_metrics.csv")
    rv = pd.read_csv(OUT / "revenue.csv")
    be = pd.read_csv(OUT / "bias_model_errors.csv")
    dp = pd.read_csv(OUT / "daily_probabilities.csv", parse_dates=["trading_day"]).set_index("trading_day")
    pr = pd.read_csv(OUT / "peak_ranks.csv")
    fz = json.loads((OUT / "frozen_model.json").read_text(encoding="utf-8"))
    src_p = "results/season_prob_metrics.csv"
    # probability quality
    for fold in ("A", "B"):
        x = pm[(pm["config"] == "main") & (pm["fold"] == fold) & (pm["label"] != "proxy_er")]
        for dec, tag in (("same_day", ""), ("day_ahead", "_da")):
            for m in ("S0", "S1", "M1p", "M2", "M3"):
                r = x[(x["decision"] == dec) & (x["method"] == m)].iloc[0]
                add(f"brier_{m}{tag}_{fold}", float(r["brier"]), "1", PER[fold], src_p,
                    note=f"Brier 分数，{'当天 09:00' if dec == 'same_day' else '前一天 09:00'}决策")
            add(f"bss_M3{tag}_{fold}", float(x[(x["decision"] == dec) & (x["method"] == "M3")]["bss_vs_S0"].iloc[0]), "1", PER[fold], src_p,
                formula=f"1 - {{brier_M3{tag}_{fold}}} / {{brier_S0{tag}_{fold}}}", note="相对常数基准 S0 的 Brier 技巧分数")
        for m in ("S1", "M2", "M3"):
            r = x[(x["decision"] == "same_day") & (x["method"] == m)].iloc[0]
            add(f"sum_p_{m}_{fold}", float(r["sum_p"]), "1", PER[fold], src_p, note="全季当天概率之和（真实高峰日 4 个）")
            add(f"mean_p_peak_{m}_{fold}", float(r["mean_p_peak"]), "1", PER[fold], src_p, note="4 个高峰日的平均当天概率")
    x = pm[(pm["config"] == "main") & (pm["fold"] == "A+B") & (pm["decision"] == "same_day")]
    for m in ("S0", "S1", "M3"):
        add(f"brier_{m}_AB", float(x[x["method"] == m]["brier"].iloc[0]), "1", AB, src_p)
    add("bss_M3_AB", float(x[x["method"] == "M3"]["bss_vs_S0"].iloc[0]), "1", AB, src_p, formula="1 - {brier_M3_AB} / {brier_S0_AB}")
    for fold, y in (("A", 2024), ("B", 2025)):
        for m in ("M2", "M3"):
            r = pr[(pr["season"] == y) & (pr["method"] == m)].iloc[0]
            add(f"rank_all4_{m}_{fold}", int(r["rank_needed_for_4_peaks"]), "天", PER[fold], "results/peak_ranks.csv",
                note="事后按概率从高到低排，要排到第几天才包含全部 4 个高峰日（并列取最差位置）；只说明排序质量，不是在线策略")
    # calls
    src_c = "results/season_call_metrics.csv"
    main_c = cm[(cm["config"] == "main") & (cm["label"] != "proxy_er")]
    for fold in ("A", "B"):
        for B in (15, 30, 45):
            for m, pol in (("M1_union", "FCFS"), ("M2_flag", "FCFS"), ("M3", "DP"), ("S1", "DP")):
                r = main_c[(main_c["fold"] == fold) & (main_c["method"] == m) & (main_c["policy"] == pol) & (main_c["budget"] == B)
                           & ((main_c["cost"] == 0.03) | main_c["cost"].isna())].iloc[0]
                add(f"calls_{m}_{fold}_{B}", int(r["calls"]), "次", PER[fold], src_c, check=f"<= {B}", note=f"预算 {B} 次，在线")
                add(f"covered_{m}_{fold}_{B}", int(r["covered_days"]), "天", PER[fold], src_c, check="<= 4", note="覆盖的高峰日（共 4）")
                if B == 30:
                    add(f"intervals_{m}_{fold}_{B}", int(r["covered_intervals"]), "时段", PER[fold], src_c, check="<= 12",
                        note="17:30-19:30 窗口覆盖的高峰时段（共 12）")
                    add(f"dec_calls_{m}_{fold}_{B}", int(r["dec_calls"]), "次", PER[fold], src_c, note="12 月的调用次数（季初消耗）")
                    add(f"false_{m}_{fold}_{B}", int(r["false_calls"]), "次", PER[fold], src_c, note="调用了但不是高峰日")
        add(f"calls_saved_M3_{fold}_30", int(main_c[(main_c["fold"] == fold) & (main_c["method"] == "M1_union") & (main_c["budget"] == 30)]["calls"].iloc[0])
            - int(main_c[(main_c["fold"] == fold) & (main_c["method"] == "M3") & (main_c["policy"] == "DP") & (main_c["budget"] == 30)
                         & (main_c["cost"] == 0.03)]["calls"].iloc[0]),
            "次", PER[fold], src_c, formula=f"{{calls_M1_union_{fold}_30}} - {{calls_M3_{fold}_30}}", note="M3 比现有规则少用的次数")
    for cost in (0.0, 0.01):
        r = main_c[(main_c["fold"] == "A") & (main_c["method"] == "M3") & (main_c["policy"] == "DP") & (main_c["budget"] == 30)
                   & (main_c["cost"] == cost)].iloc[0]
        add(f"calls_M3_A_30_cost{cost:g}", int(r["calls"]), "次", PER["A"], src_c, check="<= 30", note=f"成本 c = {cost:g}（只报告，不用来选）")
        add(f"covered_M3_A_30_cost{cost:g}", int(r["covered_days"]), "天", PER["A"], src_c, check="<= 4", note=f"成本 c = {cost:g}")
    rel = pd.read_csv(OUT / "reliability.csv")
    r = rel[(rel["method"] == "M3") & (rel["bin"] == "[0.3, 1]")].iloc[0]
    add("rel_M3_top_days", int(r["days"]), "天", AB, "results/reliability.csv", note="M3 当天概率 >= 0.3 的天数")
    add("rel_M3_top_mean_p", round(float(r["mean_p"]), 4), "1", AB, "results/reliability.csv", note="这些天的平均概率")
    add("rel_M3_top_peaks", int(r["peak_days"]), "天", AB, "results/reliability.csv", note="其中真的是高峰日的天数")
    add("rel_M3_top_share", round(float(r["peak_share"]), 4), "1", AB, "results/reliability.csv", formula="{rel_M3_top_peaks} / {rel_M3_top_days}")
    sp = pd.read_csv(OUT / "sensitivity_prob_metrics.csv")
    sc = pd.read_csv(OUT / "sensitivity_call_metrics.csv")
    for cfg, tag in (("lag2 (actual one day later)", "lag2"), ("no level ratio", "nolevel"), ("block 1", "block1"), ("block 14", "block14")):
        for fold in ("A", "B"):
            r = sp[(sp["config"] == cfg) & (sp["fold"] == fold) & (sp["method"] == "M3") & (sp["decision"] == "same_day")
                   & (sp["label"] != "proxy_er")].iloc[0]
            add(f"brier_M3_{tag}_{fold}", float(r["brier"]), "1", PER[fold], "results/sensitivity_prob_metrics.csv", note=f"敏感性 {cfg}（只报告）")
            r = sc[(sc["config"] == cfg) & (sc["fold"] == fold) & (sc["method"] == "M3") & (sc["policy"] == "DP") & (sc["budget"] == 30)
                   & (sc["label"] != "proxy_er")].iloc[0]
            add(f"calls_M3_{tag}_{fold}_30", int(r["calls"]), "次", PER[fold], "results/sensitivity_call_metrics.csv", check="<= 30", note=f"敏感性 {cfg}")
            add(f"covered_M3_{tag}_{fold}_30", int(r["covered_days"]), "天", PER[fold], "results/sensitivity_call_metrics.csv", check="<= 4",
                note=f"敏感性 {cfg}")
    er = cm[(cm["config"] == "main") & (cm["label"] == "proxy_er") & (cm["fold"] == "A") & (cm["budget"] == 30)]
    for m, pol in (("M1_union", "FCFS"), ("M3", "DP")):
        r = er[(er["method"] == m) & (er["policy"] == pol) & ((er["cost"] == 0.03) | er["cost"].isna())].iloc[0]
        add(f"covered_er_label_{m}_A_30", int(r["covered_days"]), "天", PER["A"].replace("代理标签", "电能需求代理标签（敏感性）"), src_c, check="<= 4")
    # money
    src_r = "results/revenue.csv（vpp.policy.policy_simulator.account / ircr_value，独立调用近似）"
    for fold, y in (("A", 2024), ("B", 2025)):
        for led, tag in (("L1", "L1"), ("L2", "L2")):
            x = rv[(rv["season"] == y) & rv["ledger"].str.startswith(led)]
            for m, pol in (("M1_union", "FCFS"), ("M3", "DP"), ("oracle", "perfect foresight")):
                B = 4 if m == "oracle" else 30
                r = x[(x["method"] == m) & (x["policy"] == pol) & (x["budget"] == B)].iloc[0]
                add(f"zen_{tag}_{m}_{fold}", round(float(r["zenith_net"]), 4), "A$/户[报名]/季", PER[fold], src_r, status="推算",
                    note=f"{'L1 现行规则，净出口记 0' if tag == 'L1' else 'L2 净出口记负的 2027 情景，假设组合下限不起作用'}；Synergy 参考条款；"
                         f"不含平台费；{'完美预知 4 次' if m == 'oracle' else '预算 30 次'}")
                if tag == "L2":
                    add(f"ircr_{tag}_{m}_{fold}", round(float(r["ircr_value"]), 4), "A$/户[报名]/季", PER[fold], src_r, status="推算",
                        note="容量价值（整季 12 个时段中位数），已乘响应率 0.9")
                add(f"cust_{tag}_{m}_{fold}", round(float(r["customer_net"]), 4), "A$/户[报名]/季", PER[fold], src_r, status="推算",
                    note="客户相对正常自用的净收益") if tag == "L2" else None
            add(f"zen_{tag}_diff_M3_{fold}", round(float(x[(x["method"] == "M3") & (x["budget"] == 30)]["zenith_net"].iloc[0])
                                                  - float(x[(x["method"] == "M1_union") & (x["budget"] == 30)]["zenith_net"].iloc[0]), 4),
                "A$/户[报名]/季", PER[fold], src_r, status="推算", formula=f"{{zen_{tag}_M3_{fold}}} - {{zen_{tag}_M1_union_{fold}}}")
    a = float(rv[(rv["season"] == 2024) & rv["ledger"].str.startswith("L2") & (rv["method"] == "M3") & (rv["budget"] == 30)]["zenith_net"].iloc[0]) \
        - float(rv[(rv["season"] == 2024) & rv["ledger"].str.startswith("L2") & (rv["method"] == "M1_union") & (rv["budget"] == 30)]["zenith_net"].iloc[0])
    b = float(rv[(rv["season"] == 2025) & rv["ledger"].str.startswith("L2") & (rv["method"] == "M3") & (rv["budget"] == 30)]["zenith_net"].iloc[0]) \
        - float(rv[(rv["season"] == 2025) & rv["ledger"].str.startswith("L2") & (rv["method"] == "M1_union") & (rv["budget"] == 30)]["zenith_net"].iloc[0])
    add("ircr_L2_diff_M3_A", round(float(rv[(rv["season"] == 2024) & rv["ledger"].str.startswith("L2") & (rv["method"] == "M3") & (rv["budget"] == 30)]["ircr_value"].iloc[0])
                                   - float(rv[(rv["season"] == 2024) & rv["ledger"].str.startswith("L2") & (rv["method"] == "M1_union") & (rv["budget"] == 30)]["ircr_value"].iloc[0]), 4),
        "A$/户[报名]/季", PER["A"], src_r, status="推算", formula="{ircr_L2_M3_A} - {ircr_L2_M1_union_A}", note="M3 少盖 3 个时段带来的容量价值差")
    add("corr_od_er_daily_max", 0.9971, "1", "热季 2023-24、2024-25、2025-26（364 天）", "inspect_data.py 的输出（运行一次后抄录）", status="推算",
        note="运行需求日最高和实际电能需求日最高的相关系数")
    add("zen_L2_diff_M3_two_seasons", round(a + b, 4), "A$/户[报名]", AB, src_r, status="推算",
        note="两季逐季差额之和（zen_L2_diff_M3_A + zen_L2_diff_M3_B），两个热季合计，不是每年")
    # bias model
    for fold, y in (("A", 2024), ("B", 2025)):
        x = be[be["season"] == y].set_index("prediction")
        add(f"mae_f0_{fold}", round(float(x.loc["AEMO same day (f0)", "mae_mw"]), 3), "MW", PER[fold], "results/bias_model_errors.csv",
            note="AEMO 当天预测日最高 vs 实际运行需求日最高，平均绝对误差（MAE）")
        add(f"mae_M2_{fold}", round(float(x.loc["M2 same day", "mae_mw"]), 3), "MW", PER[fold], "results/bias_model_errors.csv",
            note="偏差修正后的 MAE（前向一步样本外）")
        add(f"mae_cut_M2_{fold}", round(100 * (1 - x.loc["M2 same day", "mae_mw"] / x.loc["AEMO same day (f0)", "mae_mw"]), 3), "%", PER[fold],
            "results/bias_model_errors.csv", formula=f"1 - {{mae_M2_{fold}}} / {{mae_f0_{fold}}}", note="MAE 比 AEMO 原始预测低多少（不是准确率）")
    # label and failure cases
    y24 = dp[dp["season"] == 2024]["y"]
    add("od_4th_2024", round(float(y24.loc["2025-02-06"]), 3), "MW", PER["A"], "results/daily_probabilities.csv", note="2025-02-06 运行需求日最高（代理第 4 高）")
    add("od_5th_2024", round(float(y24.loc["2025-01-29"]), 3), "MW", PER["A"], "results/daily_probabilities.csv", note="2025-01-29 运行需求日最高（第 5 高；按电能需求它排第 4）")
    add("od_gap_4th_5th_2024", round(float(y24.loc["2025-02-06"] - y24.loc["2025-01-29"]), 3), "MW", PER["A"], "results/daily_probabilities.csv",
        formula="{od_4th_2024} - {od_5th_2024}")
    for day, tag in (("2024-12-23", "20241223"), ("2025-01-21", "20250121")):
        r = dp.loc[day]
        add(f"f0_{tag}", round(float(r["f0"]), 3), "MW", PER["A"], "results/daily_probabilities.csv", note=f"{day} AEMO 当天预测日最高")
        add(f"y_{tag}", round(float(r["y"]), 3), "MW", PER["A"], "results/daily_probabilities.csv", note=f"{day} 实际运行需求日最高")
        add(f"err_{tag}", round(float(r["y"] - r["f0"]), 3), "MW", PER["A"], "results/daily_probabilities.csv", formula=f"{{y_{tag}}} - {{f0_{tag}}}",
            note="实际 - 预测（负数 = AEMO 高估）")
        add(f"p_M3_{tag}", round(float(r["M3"]), 4), "1", PER["A"], "results/daily_probabilities.csv", note=f"{day} M3 当天概率（不是高峰日）")
    r = dp.loc["2025-02-06"]
    add("p_M3_20250206", round(float(r["M3"]), 4), "1", PER["A"], "results/daily_probabilities.csv", note="M3 漏掉的代理高峰日的当天概率")
    # frozen and coverage facts
    add("m1_tol_same_day_frozen", round(fz["m1_tolerance_mw"]["same_day"], 3), "MW", "训练数据 2023-10-01 至 2026-06-30（三个热季）", "results/frozen_model.json",
        note="现有规则的当天容差（90% 分位），留给 2026-27 热季")
    add("test_period_hot_days", int(fz["formal_test_hot_season_days"]), "天", "正式测试期 2026-07-01 至 2026-10-22（114 天）", "results/frozen_model.json",
        check="= 0", note="测试期里的热季天数")
    add("season_days", 121, "天", "每个评估热季", "results/daily_probabilities.csv", status="已核实", note="2024-25、2025-26 各 121 个交易日")
    with open(HERE / "NUMBERS.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "value", "unit", "formula", "period", "source", "status", "check", "note"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> NUMBERS.csv")


if __name__ == "__main__":
    main()
