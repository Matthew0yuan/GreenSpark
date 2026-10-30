"""Counts for RESULTS.md and NUMBERS.csv, read from out/ (no new computation of inputs or labels).

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/summarize.py
"""

import csv
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_inputs as bi  # noqa: E402

OUT = HERE / "out"
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 80)

d = pd.read_parquet(OUT / "inputs_d1.parquet").set_index("day")
cov = pd.read_csv(OUT / "coverage.csv").set_index("period")
aud = pd.read_csv(OUT / "leak_audit.csv")
ld = pd.read_csv(OUT / "lrc_days.csv", parse_dates=["day"]).set_index("day")
adv = pd.read_csv(OUT / "lrc_advisories.csv")
new = pd.read_csv(bi.NEW_ADVISORIES, encoding="cp1252")

PER = {"history": "历史诊断 2024-01-01 至 2025-03-31（456 天）", "in_sample": "样本内 2025-04-01 至 2026-06-30（456 天）",
       "test_seen": "测试期已看过 2026-07-01 至 2026-10-15（107 天）", "test_unseen": "测试期没看过 2026-10-16 至 2026-10-22（7 天）"}
ALL = "目标日 2024-01-01 至 2026-10-22（1026 天）"
rows = []


def num(name, value, unit, period, source, status="推算", formula="", check="", note=""):
    rows.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period, "source": source,
                 "status": status, "check": check, "note": note})


num("目标日数", len(d), "天", ALL, "out/inputs_d1.parquet")
for p, label in PER.items():
    c = cov.loc[p]
    num(f"{p}_天数", int(c["days"]), "天", label, "out/coverage.csv")
    num(f"{p}_输入齐全天数", int(c["inputs_complete"]), "天", label, "out/coverage.csv", check="<= " + str(int(c["days"])))
    num(f"{p}_有余量标签天数", int(c["label_complete"]), "天", label, "out/coverage.csv")
    num(f"{p}_输入齐全且有标签天数", int(c["inputs_and_label"]), "天", label, "out/coverage.csv")
    num(f"{p}_输入齐全比例", round(100 * c["inputs_complete"] / c["days"], 1), "%", label, "out/coverage.csv",
        formula=f"{{{p}_输入齐全天数}} / {{{p}_天数}}")
    num(f"{p}_LRC日", int(c["lrc_days"]), "天", label, "out/coverage.csv")
    for src in bi.SOURCES:
        num(f"{p}_缺{src}天数", int(c["days"] - c[f"with_{src}"]), "天", label, "out/coverage.csv", check=">= 0")

# pre-dispatch runs public after 09:00 (missing by rule)
late_pd = d.index[d["pd_req_max_1621_mw"].isna() & (d.index <= "2026-10-16")]
num("预调度09点后才公开_表内天数", len(late_pd), "天", ALL, "out/inputs_d1.parquet",
    note="D=" + "、".join(f"{x:%Y-%m-%d}" for x in late_pd) + "；另 2 天（D=2023-12-21、12-22）在表的范围之前")

# provenance: which copy is used, typically
inrange = d[d["period"] != "test_unseen"]
num("ST_PASA用D减2报告的天数", int((inrange["st_report_age_days"] == 2).sum()), "天", "目标日 2024-01-01 至 2026-10-15（1019 天）",
    "out/inputs_d1.parquet", note=f"有 ST PASA 的 {int(inrange['st_report_age_days'].notna().sum())} 天里")
num("ST_PASA有值天数_至1015", int(inrange["st_report_age_days"].notna().sum()), "天", "目标日 2024-01-01 至 2026-10-15（1019 天）", "out/inputs_d1.parquet")
num("ST_PASA用更早报告的天数", int((inrange["st_report_age_days"] > 2).sum()), "天", "目标日 2024-01-01 至 2026-10-15（1019 天）",
    "out/inputs_d1.parquet", formula="{ST_PASA有值天数_至1015} - {ST_PASA用D减2报告的天数}", check=">= 0")
x30 = pd.read_parquet(OUT / "inputs_d1_30min.parquet", columns=["day"])
num("每天窗口半小时数", len(bi.SLOTS), "个/天", "D 日 16:00–21:00", "PLAN.md 第 1 节", status="假设", note="16:00、16:30 … 20:30")
num("30分钟表行数", len(x30), "个", ALL, "out/inputs_d1_30min.parquet", formula="{目标日数} * {每天窗口半小时数}")
num("日表列数", d.shape[1] + 1, "个", ALL, "out/inputs_d1.parquet", note="含 day 列")
num("预调度09点后才公开_全部", 8, "天", "预调度档案 2023-10-01 至 2026-10-15", "data/pit/predispatch.parquet（explore2.py 列出）",
    note="D−1 = 2023-12-20、12-21、2024-02-23、08-18、08-28、08-29、08-30、10-04")
snap_0830 = inrange["out_snapshot"].dropna().str.endswith("0830.csv")
num("停机快照为0830的天数", int(snap_0830.sum()), "天", "目标日 2024-01-01 至 2026-10-15（1019 天）", "out/inputs_d1.parquet",
    note=f"有快照的 {len(snap_0830)} 天里")
num("停机有快照天数_至1015", len(snap_0830), "天", "目标日 2024-01-01 至 2026-10-15（1019 天）", "out/inputs_d1.parquet")
run_0800 = inrange["pd_run"].dropna().str.endswith("0800")
num("预调度为0800运行的天数", int(run_0800.sum()), "天", "目标日 2024-01-01 至 2026-10-15（1019 天）", "out/inputs_d1.parquet",
    note=f"有预调度的 {len(run_0800)} 天里")
wx_run = inrange["wx_newest_run"].dropna()
wx_ok = sum(r.endswith(f"{(day - pd.Timedelta(days=2)):%Y%m%d}12Z") for day, r in wx_run.items())
num("天气最新起报为D减2的12Z天数", wx_ok, "天", "目标日 2024-01-01 至 2026-10-15（1019 天）", "out/inputs_d1.parquet",
    note=f"有天气的 {len(wx_run)} 天里；12Z = AWST 20:00")

# LRC
num("新公告文件条数", len(new), "条", "发布时间 2026-05-19 07:27 至 2026-10-25 11:51", "raw/...marketAdvisoryReport__market-advisories.csv", status="已核实")
num("新公告文件LRC条数", int(adv["source"].str.contains("fetched").sum()), "条", "发布时间 2026-05-19 至 2026-10-25", "out/lrc_advisories.csv")
num("第1步文件LRC条数", int(adv["source"].str.contains("step 1").sum()), "条", "发布时间 2023-10-01 至 2026-05-18", "out/lrc_advisories.csv")
num("修正后LRC日_全部", int(ld["lrc"].sum()), "天", "2023-11-21 至 2026-10-13", "out/lrc_days.csv")
num("修正后LRC日_表内", int(d["lrc"].sum()), "天", ALL, "out/inputs_d1.parquet")
for st in ("removed_by_fix", "added_by_fix", "added_from_new_file"):
    s = ld[ld["status"] == st]
    num(f"LRC_{st}", len(s), "天", "2023-11-21 至 2026-10-13", "out/lrc_days.csv", note="、".join(f"{x:%Y-%m-%d}" for x in s.index))
issued_late = (d.loc[d["lrc"], "lrc_first_issued"] > d.loc[d["lrc"], "issue_at"]).sum()
num("LRC首条公告晚于发出时刻的天数", int(issued_late), "天", ALL, "out/leak_audit.csv P6")

# audit
def a(check, source, col):
    r = aud[(aud["check"] == check) & (aud["source"] == source)]
    r = r[r["period"] == "all"] if "all" in set(r["period"]) else r
    return int(r[col].iloc[0])


for src in bi.SOURCES:
    num(f"A1_{src}_有值天数", a("A1", src, "with_value"), "天", ALL, "out/leak_audit.csv", status="回测")
    num(f"A1_{src}_违规天数", a("A1", src, "violations"), "天", ALL, "out/leak_audit.csv", status="回测")
for src, label in (("predispatch", "预调度"), ("stpasa", "ST_PASA"), ("weather: Perth temperature", "Perth气温"),
                   ("weather: Midwest wind", "Midwest风速"), ("outages", "停机")):
    num(f"A3_{label}_比对值数", a("A3", src, "compared"), "个", "随机抽样日", "out/leak_audit.csv", status="回测")
    num(f"A3_{label}_不一致", a("A3", src, "mismatches"), "个", "随机抽样日", "out/leak_audit.csv", status="回测")
num("A4_预调度_比对值数", a("A4", "predispatch", "compared"), "个", "目标日 2024-01-01 至 2026-10-16", "out/leak_audit.csv", status="回测")
num("A4_ST_PASA_不一致", a("A4", "stpasa: Reference", "mismatches"), "个", "目标日 2024-01-01 至 2026-10-16", "out/leak_audit.csv", status="回测",
    note="都在 2024-03-18：2024-03-15 和 03-16 两份报告同一时刻公开，spike_table 按时段混用两份，本表统一用较新的 03-16")
for src, label in (("outages: snapshot file", "快照文件"), ("outages: thermal <= all scheduled", "火电停机不超过全部"),
                   ("weather: Perth temperature", "Perth气温")):
    num(f"A4_{label}_比对值数", a("A4", src, "compared"), "个", "目标日 2024-01-01 至 2026-10-16", "out/leak_audit.csv", status="回测")
    num(f"A4_{label}_不一致", a("A4", src, "mismatches"), "个", "目标日 2024-01-01 至 2026-10-16", "out/leak_audit.csv", status="回测")
num("A4_预调度_不一致", a("A4", "predispatch", "mismatches"), "个", "目标日 2024-01-01 至 2026-10-16", "out/leak_audit.csv", status="回测")
num("A4_ST_PASA_比对值数", a("A4", "stpasa: Reference", "compared"), "个", "目标日 2024-01-01 至 2026-10-16", "out/leak_audit.csv", status="回测")
num("A5_比对值数", a("A5", "all inputs", "compared"), "个", "40 个随机目标日", "out/leak_audit.csv", status="回测")
num("A5_改变值数", a("A5", "all inputs", "changed"), "个", "40 个随机目标日", "out/leak_audit.csv", status="回测")
num("P7_改变值数", a("P7", "battery shift(1)", "changed"), "个", "40 个随机目标日", "out/leak_audit.csv", status="回测")
for pc, src in (("P1", "outages"), ("P2", "battery"), ("P3", "predispatch"), ("P4", "weather"), ("P5", "stpasa")):
    num(f"{pc}_有值天数", a(pc, src, "with_value"), "天", ALL, "out/leak_audit.csv", status="回测")
    num(f"{pc}_报错天数", a(pc, src, "violations"), "天", ALL, "out/leak_audit.csv", status="回测")
    num(f"{pc}_报错比例", round(100 * a(pc, src, "violations") / a(pc, src, "with_value"), 1), "%", ALL, "out/leak_audit.csv",
        status="回测", formula=f"{{{pc}_报错天数}} / {{{pc}_有值天数}}")
num("C1_比对天数", a("C1", "label vs step 1", "compared"), "天", "目标日 2024-01-01 至 2026-10-14 中第 1 步最低点在窗口内的日子", "out/leak_audit.csv", status="回测")
num("C1_不一致天数", a("C1", "label vs step 1", "mismatches"), "天", "同上", "out/leak_audit.csv", status="回测")

# the winter 2024 LRC days (outages plus low wind / semi-scheduled output)
w = ld[(ld.index >= "2024-06-01") & (ld.index <= "2024-08-31") & ld["lrc"]]
w = w[w["reason"].str.contains("outage", case=False) & w["reason"].str.contains("wind|Semi-Scheduled", case=False)]
t = d.loc[w.index]
cols = {"predispatch": "pd_req_max_1621_mw", "outages": "out_thermal_max_1621_mw", "battery": "bat_cap_mw",
        "stpasa": "st_ref_max_1621_mw", "weather": "wx_wind_midwest_1621_kmh"}
tab = pd.DataFrame({k: t[c].notna() for k, c in cols.items()})
tab["all_inputs"] = t["inputs_complete"]
tab["label"] = t["label_complete"]
tab["st_age"] = t["st_report_age_days"]
tab["status"] = w["status"]
print(tab.to_string())
num("2024冬LRC停机加风小日", len(w), "天", "2024-06-01 至 2024-08-31", "out/lrc_days.csv", note="、".join(f"{x:%Y-%m-%d}" for x in w.index))
num("2024冬LRC停机加风小日_输入齐全", int(t["inputs_complete"].sum()), "天", "2024-06-01 至 2024-08-31", "out/inputs_d1.parquet")

with open(HERE / "NUMBERS.csv", "w", newline="", encoding="utf-8") as f:
    wr = csv.DictWriter(f, fieldnames=["name", "value", "unit", "formula", "period", "source", "status", "check", "note"])
    wr.writeheader()
    wr.writerows(rows)
print(pd.DataFrame(rows)[["name", "value", "unit", "note"]].to_string(index=False))
print(cov.to_string())
