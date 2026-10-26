"""Write NUMBERS.csv from out/ (every number RESULTS.md quotes), so nothing is copied by hand.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_forecast/write_numbers.py
"""

import sys

sys.dont_write_bytecode = True

import csv  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

P_IN = "样本内 2025-04-01 至 2026-06-30（456 天）"
P_HIST = "历史诊断 2024-05-01 至 2025-03-31（三个方法都有预测、有标签的 330 天）"
P_TEST = "测试期已看过 2026-07-01 至 2026-10-15（107 天，106 天有标签）"
P_UNSEEN = "测试期没看过 2026-10-16 至 2026-10-22（7 天）"
BOOL = {True: True, False: False, "True": True, "False": False}


def main() -> None:
    s = json.load(open(OUT / "summary.json", encoding="utf-8"))
    dg = json.load(open(OUT / "diag.json", encoding="utf-8"))
    x = pd.read_csv(OUT / "daily_predictions.csv", parse_dates=["day"], index_col="day")
    for c in [c for c in x.columns if c.startswith(("top_", "mtop_"))] + ["spike", "lrc", "ircr_peak"]:
        x[c] = x[c].map(BOOL)
    main_r = pd.read_csv(OUT / "main_result.csv").set_index("method")
    sec = pd.read_csv(OUT / "secondary.csv")
    hr = pd.read_csv(OUT / "vs_high_risk.csv").set_index("method")
    es = pd.read_csv(OUT / "error_split.csv").set_index("group")
    hl = pd.read_csv(OUT / "history_lrc_outage_lowwind.csv")
    fits = pd.read_csv(OUT / "fits.csv").set_index("month")
    sc = pd.read_csv(OUT / "selfcheck.csv")

    rows = []

    def add(name, value, unit, period, source, status="回测", formula="", check="", note=""):
        rows.append({"name": name, "value": round(float(value), 4), "unit": unit, "formula": formula,
                     "period": period, "source": source, "status": status, "check": check, "note": note})

    xi = x[x["period"] == "in_sample"].dropna(subset=["rank_actual"])
    # ---- main criterion
    add("样本内_评估天数", len(xi), "天", P_IN, "out/daily_predictions.csv")
    add("样本内_月数", xi["month"].nunique(), "月", P_IN, "out/main_result.csv")
    add("样本内_k", s["in_sample"]["k"], "天", P_IN, "out/summary.json", note="round(0.10 × 评估天数)")
    add("样本内_B0_命中天数", main_r.loc["M1", "hits_B0"], "天", P_IN, "out/main_result.csv", check="<= 46")
    for m in ("M1", "M2"):
        add(f"样本内_{m}_命中天数", main_r.loc[m, "hits_method"], "天", P_IN, "out/main_result.csv", check="<= 46")
    add("样本内_B0_召回率", 100 * main_r.loc["M1", "recall_B0"], "%", P_IN, "out/main_result.csv",
        formula="{样本内_B0_命中天数} / {样本内_k}")
    for m in ("M1", "M2"):
        r = main_r.loc[m]
        add(f"样本内_{m}_召回率", 100 * r["recall_method"], "%", P_IN, "out/main_result.csv",
            formula=f"{{样本内_{m}_命中天数}} / {{样本内_k}}")
        add(f"样本内_{m}_减B0", 100 * r["diff"], "%", P_IN, "out/main_result.csv",
            formula=f"{{样本内_{m}_召回率}} - {{样本内_B0_召回率}}", note="百分点")
        add(f"样本内_{m}_减B0_区间下限", 100 * r["lo"], "%", P_IN, "out/main_result.csv",
            note="按月整块重采样 2000 次，2.5% 分位；通过要求 > 0", check="<= 0")
        add(f"样本内_{m}_减B0_区间上限", 100 * r["hi"], "%", P_IN, "out/main_result.csv", note="97.5% 分位")
    add("样本内_M1减B0_重采样不大于0的比例", 100 * dg["boot_M1_share_le0"], "%", P_IN, "out/diag.json")

    # ---- monthly version
    nm = int(xi["mtop_actual"].sum())
    add("样本内_每月版_实际最紧天数", nm, "天", P_IN, "out/daily_predictions.csv", note="每月最低 10% 合计")
    for m in ("B0", "M1", "M2"):
        hits = int((xi[f"mtop_{m}"] & xi["mtop_actual"]).sum())
        add(f"样本内_每月版_{m}_命中天数", hits, "天", P_IN, "out/daily_predictions.csv")
        add(f"样本内_每月版_{m}_召回率", 100 * hits / nm, "%", P_IN, "out/secondary.csv",
            formula=f"{{样本内_每月版_{m}_命中天数}} / {{样本内_每月版_实际最紧天数}}")
    si = sec[sec["period"] == "in_sample"].set_index("method")
    for m in ("M1", "M2"):
        add(f"样本内_每月版_{m}_减B0", 100 * si.loc[m, "month_diff_vs_B0"], "%", P_IN, "out/secondary.csv",
            formula=f"{{样本内_每月版_{m}_召回率}} - {{样本内_每月版_B0_召回率}}", note="百分点")
        add(f"样本内_每月版_{m}_区间下限", 100 * si.loc[m, "month_lo"], "%", P_IN, "out/secondary.csv")
        add(f"样本内_每月版_{m}_区间上限", 100 * si.loc[m, "month_hi"], "%", P_IN, "out/secondary.csv")

    # ---- MAE, bias, Spearman
    for m in ("M1", "M2"):
        add(f"样本内_{m}_MAE", si.loc[m, "mae"], "MW", P_IN, "out/secondary.csv", check=">= 0")
        add(f"样本内_{m}_平均偏差", si.loc[m, "bias"], "MW", P_IN, "out/secondary.csv", note="预测 − 实际")
    for m in ("B0", "M1", "M2"):
        add(f"样本内_{m}_秩相关", si.loc[m, "spearman"] * (-1 if m == "B0" else 1), "1", P_IN, "out/secondary.csv",
            note="Spearman；B0 取需求预测和余量的秩相关的相反数（需求高 = 紧）")

    # ---- spike shares
    for m in ("actual", "B0", "M1", "M2"):
        r = si.loc[m]
        tag = "实际最紧" if m == "actual" else m
        add(f"样本内_尖峰比例_{tag}_最紧46天", 100 * r["spike_top"], "%", P_IN, "out/secondary.csv")
        add(f"样本内_尖峰比例_{tag}_其余", 100 * r["spike_rest"], "%", P_IN, "out/secondary.csv")
        add(f"样本内_尖峰比例_{tag}_差", 100 * r["spike_diff"], "%", P_IN, "out/secondary.csv",
            formula=f"{{样本内_尖峰比例_{tag}_最紧46天}} - {{样本内_尖峰比例_{tag}_其余}}", note="百分点")
        add(f"样本内_尖峰比例_{tag}_差_区间下限", 100 * r["spike_lo"], "%", P_IN, "out/secondary.csv")
        add(f"样本内_尖峰比例_{tag}_差_区间上限", 100 * r["spike_hi"], "%", P_IN, "out/secondary.csv")

    # ---- vs high-risk judgment
    add("样本内_高风险判断_标出天数", hr.loc["high_risk_d1", "days_flagged"], "天", P_IN, "out/vs_high_risk.csv")
    add("样本内_高风险判断_标出比例", 100 * hr.loc["high_risk_d1", "days_flagged"] / len(xi), "%", P_IN,
        "out/vs_high_risk.csv", formula="{样本内_高风险判断_标出天数} / {样本内_评估天数}")
    for m in ("high_risk_d1", "B0", "M1", "M2"):
        tag = "高风险判断" if m == "high_risk_d1" else m
        add(f"样本内_同样天数_{tag}_命中天数", hr.loc[m, "hits"], "天", P_IN, "out/vs_high_risk.csv")
        add(f"样本内_同样天数_{tag}_召回率", 100 * hr.loc[m, "recall"], "%", P_IN, "out/vs_high_risk.csv",
            formula=f"{{样本内_同样天数_{tag}_命中天数}} / {{样本内_k}}")
    for m in ("M1", "M2"):
        add(f"样本内_同样天数_{m}_减高风险判断", 100 * hr.loc[m, "diff_vs_high_risk"], "%", P_IN,
            "out/vs_high_risk.csv", formula=f"{{样本内_同样天数_{m}_召回率}} - {{样本内_同样天数_高风险判断_召回率}}",
            note="百分点")
        add(f"样本内_同样天数_{m}_减高风险判断_区间下限", 100 * hr.loc[m, "lo"], "%", P_IN, "out/vs_high_risk.csv")
        add(f"样本内_同样天数_{m}_减高风险判断_区间上限", 100 * hr.loc[m, "hi"], "%", P_IN, "out/vs_high_risk.csv")

    # ---- not hot
    add("样本内_天不热的最紧日", s["in_sample"]["not_hot_tight_days"], "天", P_IN, "out/summary.json",
        note="实际最紧 46 天里 B0 不在前 10% 的")
    for m in ("B0", "M1", "M2"):
        add(f"样本内_天不热的最紧日_{m}_抓到", s["in_sample"]["not_hot_hits"][m], "天", P_IN, "out/summary.json")

    # ---- error split
    for g, tag in (("样本内实际最紧、M1 漏掉的", "M1漏掉"), ("样本内 IRCR 高峰日", "IRCR高峰日"),
                   ("样本内全部", "全部"), ("样本内实际最紧、M1 抓到的", "M1抓到")):
        r = es.loc[g]
        add(f"误差分解_{tag}_天数", r["days"], "天", P_IN, "out/error_split.csv")
        add(f"误差分解_{tag}_总误差", r["e_total"], "MW", P_IN, "out/error_split.csv",
            formula=f"{{误差分解_{tag}_未登记停机}} + {{误差分解_{tag}_剩余需求误差}} + {{误差分解_{tag}_电池}} + "
                    f"{{误差分解_{tag}_时段}}", note="M1 预测 − 实际，平均")
        add(f"误差分解_{tag}_未登记停机", r["e_outage"], "MW", P_IN, "out/error_split.csv")
        add(f"误差分解_{tag}_剩余需求误差", r["e_residual"], "MW", P_IN, "out/error_split.csv")
        add(f"误差分解_{tag}_电池", r["e_battery"], "MW", P_IN, "out/error_split.csv")
        add(f"误差分解_{tag}_时段", r["e_timing"], "MW", P_IN, "out/error_split.csv", check=">= 0")

    # ---- history
    sh = s["history"]
    add("历史_评估天数", sh["n"], "天", P_HIST, "out/summary.json")
    add("历史_k", sh["k"], "天", P_HIST, "out/summary.json")
    for m in ("B0", "M1", "M2"):
        add(f"历史_{m}_召回率", 100 * sh[f"recall_{m}"], "%", P_HIST, "out/summary.json")
    add("历史_停机加风小LRC日", sh["outage_lowwind_days"], "天", P_HIST, "out/history_lrc_outage_lowwind.csv",
        note="2024-06-01 至 08-31，修正后 8 天（第 1 步说 7 天）")
    for m in ("actual", "B0", "M1", "M2"):
        tag = "实际余量" if m == "actual" else m
        add(f"历史_停机加风小LRC日_{tag}_进前10%", sh["outage_lowwind_in_top10"][m], "天", P_HIST,
            "out/history_lrc_outage_lowwind.csv")
    add("历史_停机加风小LRC日_高风险判断_标出", int((hl["high_risk_d1"] == 1).sum()), "天", P_HIST,
        "out/history_lrc_outage_lowwind.csv")
    for mm in ("2024-05", "2024-06", "2024-08"):
        add(f"历史_{mm}_训练天数", fits.loc[mm, "m2_days"], "天", f"拟合月 {mm}", "out/fits.csv",
            note=f"训练目标日到 {fits.loc[mm, 'train_last_day']}")

    # ---- test
    st = s["test_seen"]
    add("测试已看过_评估天数", st["n"], "天", P_TEST, "out/summary.json")
    add("测试已看过_k", st["k"], "天", P_TEST, "out/summary.json")
    xt = x[x["period"] == "test_seen"].dropna(subset=["rank_actual"])
    for m in ("B0", "M1", "M2"):
        hits = int((xt[f"top_{m}"] & xt["top_actual"]).sum())
        add(f"测试已看过_{m}_命中天数", hits, "天", P_TEST, "out/daily_predictions.csv")
        add(f"测试已看过_{m}_召回率", 100 * st[f"recall_{m}"], "%", P_TEST, "out/summary.json",
            formula=f"{{测试已看过_{m}_命中天数}} / {{测试已看过_k}}")
    stt = sec[sec["period"] == "test_seen"].set_index("method")
    for m in ("M1", "M2"):
        add(f"测试已看过_{m}_MAE", stt.loc[m, "mae"], "MW", P_TEST, "out/secondary.csv")
        add(f"测试已看过_{m}_平均偏差", stt.loc[m, "bias"], "MW", P_TEST, "out/secondary.csv")
    add("测试已看过_尖峰比例_M1_最紧", 100 * stt.loc["M1", "spike_top"], "%", P_TEST, "out/secondary.csv")
    add("测试已看过_尖峰比例_M1_其余", 100 * stt.loc["M1", "spike_rest"], "%", P_TEST, "out/secondary.csv")
    add("测试已看过_尖峰比例_B0_最紧", 100 * stt.loc["B0", "spike_top"], "%", P_TEST, "out/secondary.csv")
    su = s["test_unseen"]
    add("测试没看过_天数", su["days"], "天", P_UNSEEN, "out/summary.json")
    add("测试没看过_有标签天数", su["label"], "天", P_UNSEEN, "out/summary.json")
    add("测试没看过_有M1预测天数", su["m1"], "天", P_UNSEEN, "out/summary.json")
    add("冻结模型_训练天数", fits.loc["2026-07", "m2_days"], "天", "训练目标日 2024-01-01 至 2026-06-26",
        "out/fits.csv")

    # ---- by season (months) and event days, for RESULTS.md tables
    bm = pd.read_csv(OUT / "by_month.csv").set_index("month")
    for tag, months in (("冷季2025-05至09", ["2025-05", "2025-06", "2025-07", "2025-08", "2025-09"]),
                        ("热季2025-12至2026-03", ["2025-12", "2026-01", "2026-02", "2026-03"]),
                        ("其余6个月", ["2025-04", "2025-10", "2025-11", "2026-04", "2026-05", "2026-06"])):
        b = bm.loc[months]
        add(f"分月_{tag}_实际最紧天数", b["actual_top"].sum(), "天", P_IN, "out/by_month.csv")
        add(f"分月_{tag}_B0命中", b["hits_B0"].sum(), "天", P_IN, "out/by_month.csv")
        add(f"分月_{tag}_M1命中", b["hits_M1"].sum(), "天", P_IN, "out/by_month.csv")
        add(f"分月_{tag}_B0标出天数", b["pred_top_B0"].sum(), "天", P_IN, "out/by_month.csv")
        add(f"分月_{tag}_M1标出天数", b["pred_top_M1"].sum(), "天", P_IN, "out/by_month.csv")
    ev = pd.read_csv(OUT / "events_in_sample.csv", parse_dates=["day"]).set_index("day")
    for day, r in ev.iterrows():
        for m in ("actual", "B0", "M1", "M2"):
            tag = "实际余量" if m == "actual" else m
            add(f"事件日_{day.date()}_{tag}_排名百分位", 100 * r[f"pct_{m}"], "%", P_IN, "out/events_in_sample.csv",
                note="1 名 / 456 天；越小越紧")
    hl2 = hl.set_index("day")
    for day, r in hl2.iterrows():
        for m in ("actual", "B0", "M1"):
            tag = "实际余量" if m == "actual" else m
            add(f"历史LRC日_{day}_{tag}_排名百分位", 100 * r[f"pct_{m}"], "%", P_HIST,
                "out/history_lrc_outage_lowwind.csv", note="越小越紧；前 10% = 前 33 名")
    et = pd.read_csv(OUT / "events_test.csv", parse_dates=["day"]).set_index("day")
    for day, r in et.iterrows():
        for m in ("actual", "B0", "M1"):
            tag = "实际余量" if m == "actual" else m
            add(f"测试LRC日_{day.date()}_{tag}_排名百分位", 100 * r[f"pct_{m}"], "%", P_TEST, "out/events_test.csv",
                note="越小越紧；前 10% = 前 11 名")
    add("样本内_M1M2_同为最紧天数", dg["same_top_days_M1_M2"], "天", P_IN, "out/diag.json", check="<= 46")
    add("样本内_尖峰比例_M1减B0_最紧46天", 100 * (si.loc["M1", "spike_top"] - si.loc["B0", "spike_top"]), "%", P_IN,
        "out/secondary.csv", formula="{样本内_尖峰比例_M1_最紧46天} - {样本内_尖峰比例_B0_最紧46天}", note="百分点")
    for mm in bm.index:
        add(f"分月_{mm}_电池能力月平均", bm.loc[mm, "bat_mean"], "MW", f"{mm}（样本内）", "out/by_month.csv",
            note="bat_cap_mw（D−91 … D−2 最大放电）在 M1 最紧时段的月平均")
    fz = fits.loc["2026-07"]
    for k, tag in (("beta_out_t", "已登记停机"), ("beta_req_t", "预调度需求"), ("beta_wind_t", "风电估计"),
                   ("beta_solar_t", "光伏估计")):
        add(f"冻结模型_M2权重_{tag}", fz[k], "1", "训练目标日 2024-01-01 至 2026-06-26", "out/fits.csv",
            note="M1 里这一项的权重是 ±1")
    add("冻结模型_M2常数", fz["beta_one"], "MW", "训练目标日 2024-01-01 至 2026-06-26", "out/fits.csv")
    add("冻结模型_风光换算常数", fz["conv_one"], "MW", "训练目标日 2024-01-01 至 2026-06-26", "out/fits.csv")
    eo = pd.read_csv(OUT / "error_split_lrc_other.csv").set_index("day")
    for day in eo.index:
        per = P_TEST if eo.loc[day, "period"] == "test_seen" else P_HIST
        add(f"LRC日_{day}_未登记停机", eo.loc[day, "e_outage"], "MW", per, "out/error_split_lrc_other.csv",
            note="事后停机 − D−1 快照已登记停机，在 M1 最紧时段")
        add(f"LRC日_{day}_M1总误差", eo.loc[day, "e_total"], "MW", per, "out/error_split_lrc_other.csv",
            note="M1 − 实际余量")

    # ---- self-checks
    add("自检_T1_改变值数", sc.loc[sc["check"] == "T1", "changed"].sum(), "1", "3 个月（2024-07、2025-12、2026-08）",
        "out/selfcheck.csv", status="推算", check="== 0")
    add("自检_T2_改变值数", sc.loc[sc["check"] == "T2", "changed"].sum(), "1", "3 个月（2024-07、2025-12、2026-08）",
        "out/selfcheck.csv", status="推算", check="> 0")

    with open(HERE / "NUMBERS.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "value", "unit", "formula", "period", "source", "status", "check",
                                          "note"])
        w.writeheader()
        w.writerows(rows)
    print(pd.DataFrame(rows)[["name", "value", "unit"]].to_string(index=False))
    print(len(rows), "rows")


if __name__ == "__main__":
    main()
