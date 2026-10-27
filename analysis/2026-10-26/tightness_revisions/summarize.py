"""Write NUMBERS.csv from out/ (every number in RESULTS.md).

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/tightness_revisions/summarize.py
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
IS = "样本内 2025-04-01 至 2026-06-30（456 天）"
PERIOD = {"history": "历史诊断 2024-01-01 至 2025-03-31（456 天）", "in_sample": IS,
          "test_seen": "测试期已看过 2026-07-01 至 2026-10-15（107 天）", "test_unseen": "测试期没看过 2026-10-16 至 2026-10-22（7 天）"}
ALL = "目标日 2024-01-01 至 2026-10-22（1026 天）"
rows = []


def add(name, value, unit, period, source, status="推算", formula="", check="", note=""):
    rows.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period, "source": source,
                 "status": status, "check": check, "note": note})


def main() -> None:
    d = pd.read_parquet(OUT / "revisions_d1.parquet")
    cov = pd.read_csv(OUT / "coverage.csv").set_index("period")
    aud = pd.read_csv(OUT / "leak_audit.csv")
    corr = pd.read_csv(OUT / "describe_corr.csv").set_index(["column", "target"])
    auc = pd.read_csv(OUT / "describe_auc.csv").set_index("column")
    wm = pd.read_csv(OUT / "describe_within_month.csv").set_index("column")

    add("目标日数", len(d), "天", ALL, "out/revisions_d1.parquet")
    for p, label in PERIOD.items():
        add(f"{p}_天数", int(cov.at[p, "days"]), "天", label, "out/coverage.csv")
        add(f"{p}_修正量齐全天数", int(cov.at[p, "revisions_complete"]), "天", label, "out/coverage.csv", check=f"<= {int(cov.at[p, 'days'])}")

    judged = aud[aud["expect"] != "report only"]
    add("审计判定项数", len(judged), "项", ALL, "out/leak_audit.csv")
    add("审计通过项数", int(judged["pass"].sum()), "项", ALL, "out/leak_audit.csv", check=f"== {len(judged)}")
    a1 = aud[(aud["check"] == "A1") & (aud["period"] == "all")]
    add("A1_违规行数合计", int(a1["violations"].sum() + a1["unknown_time"].sum()), "天", ALL, "out/leak_audit.csv", check="== 0")
    for pc in ("P1", "P2", "P3"):
        r = aud[aud["check"] == pc].iloc[0]
        add(f"{pc}_有值天数", int(r["with_value"]), "天", ALL, "out/leak_audit.csv")
        add(f"{pc}_报错天数", int(r["violations"]), "天", ALL, "out/leak_audit.csv")
    for c in ("A5", "P4"):
        r = aud[aud["check"] == c].iloc[0]
        add(f"{c}_比较值个数", int(r["compared"]), "个", "40 个随机日", "out/leak_audit.csv")
        add(f"{c}_改变值个数", int(r["changed"]), "个", "40 个随机日", "out/leak_audit.csv")
    a4 = aud[(aud["check"] == "A4") & (aud["source"] == "stpasa: Reference revision")].iloc[0]
    add("A4_ST修正量_比较值个数", int(a4["compared"]), "个", "和 spike_table 重叠的目标日", "out/leak_audit.csv")
    add("A4_ST修正量_不一致个数", int(a4["mismatches"]), "个", "和 spike_table 重叠的目标日", "out/leak_audit.csv")

    s = d[d["period"] == "in_sample"]
    t = d[d["period"] == "test_seen"]
    add("样本内_电能短缺标记天数", int((s["st_short_energy_1621_n"] > 0).sum()), "天", IS, "out/revisions_d1.parquet")
    add("测试期已看过_电能短缺标记天数", int((t["st_short_energy_1621_n"] > 0).sum()), "天", PERIOD["test_seen"], "out/revisions_d1.parquet",
        note="只数有标记的天数，没有和任何结果对照")
    add("样本内_停机修正量非零天数", int((s["out_thermal_rev_max_1621_mw"] != 0).sum()), "天", IS, "out/revisions_d1.parquet")
    add("样本内_24小时新报非零天数", int((s["out_thermal_new24_max_1621_mw"] > 0).sum()), "天", IS, "out/revisions_d1.parquet")

    for (col, tgt), name in {("out_thermal_rev_max_1621_mw", "e_outage"): "秩相关_停机修正量_没登记停机误差",
                             ("st_ref_rev_max_1621_mw", "e_residual"): "秩相关_ST修正量_剩余需求误差",
                             ("wx_wind_1621_rev_kmh", "e_residual"): "秩相关_风速修正量_剩余需求误差",
                             ("st_minus_pd_max_1621_mw", "e_outage"): "秩相关_ST减预调度_没登记停机误差",
                             ("out_thermal_new24_max_1621_mw", "e_outage"): "秩相关_24小时新报_没登记停机误差"}.items():
        r = corr.loc[(col, tgt)]
        for k, lab in (("spearman", ""), ("ci_low", "_下限"), ("ci_high", "_上限")):
            add(f"{name}{lab}", round(float(r[k]), 3), "-", IS, "out/describe_corr.csv", note="按月整块重采样 2,000 次的 95% 区间" if lab else "")
    for col, name in {"st_ref_rev_max_1621_mw": "AUC_ST修正量", "out_thermal_forced_max_1621_mw": "AUC_强迫停机",
                      "wx_temp_perth_max_day_rev_c": "AUC_气温修正量", "out_thermal_rev_max_1621_mw": "AUC_停机修正量"}.items():
        r = auc.loc[col]
        for k, lab in (("auc", ""), ("ci_low", "_下限"), ("ci_high", "_上限")):
            add(f"{name}{lab}", round(float(r[k]), 3), "-", IS, "out/describe_auc.csv")
    r = wm.loc["st_ref_rev_max_1621_mw"]
    for k, lab in (("auc_within_month", ""), ("ci_low", "_下限"), ("ci_high", "_上限")):
        add(f"同月内AUC_ST修正量{lab}", round(float(r[k]), 3), "-", IS, "out/describe_within_month.csv", note="看完 D2 以后加的")
    for col, name in {"out_thermal_forced_max_1621_mw": "同月内AUC_强迫停机", "wx_temp_perth_max_day_rev_c": "同月内AUC_气温修正量"}.items():
        add(name, round(float(wm.at[col, "auc_within_month"]), 3), "-", IS, "out/describe_within_month.csv", note="看完 D2 以后加的")
    s_m = s.assign(month=pd.to_datetime(s["day"]).dt.to_period("M").astype(str)).groupby("month")["st_ref_rev_max_1621_mw"].mean()
    for m in ("2025-07", "2025-08", "2025-09", "2025-12", "2026-01"):
        add(f"ST修正量窗口最大值月平均_{m}", round(float(s_m[m]), 1), "MW", f"{m}（样本内）", "out/revisions_d1.parquet")
    p = pd.read_csv(HERE.parents[0] / "tightness_forecast" / "out" / "daily_predictions.csv", parse_dates=["day"])
    p = p[p["period"] == "in_sample"]
    top = p[p["top_actual"].astype(bool)]
    add("实际最紧天数", len(top), "天", IS, "tightness_forecast/out/daily_predictions.csv")
    add("实际最紧天数_2025年5至9月", int(top["day"].between("2025-05-01", "2025-09-30").sum()), "天", IS,
        "tightness_forecast/out/daily_predictions.csv", check=f"<= {len(top)}")

    pd.DataFrame(rows).to_csv(HERE / "NUMBERS.csv", index=False, encoding="utf-8")
    print(f"{len(rows)} numbers")


if __name__ == "__main__":
    main()
