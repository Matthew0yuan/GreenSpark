"""Writes NUMBERS.csv from the scripts' outputs in out/ (no number typed by hand).

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/make_numbers.py
"""

import csv
import json

import _common
import pandas as pd

OUT = _common.OUT
BUILD = "价值表 570 个交易日 2025-04-01 至 2026-10-22（训练窗口不超过 2026-06-30）"
FWD = "测试期 2026-10-16 至 2026-10-22（7 天）"
G5 = "样本内 2025-04-01 至 2026-06-30（456 天）+ 测试期 2026-07-01 至 2026-10-15（107 天）"
ROWS = []


def row(name, value, unit, period, source, status="推算", formula="", check="", note=""):
    ROWS.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period, "source": source,
                 "status": status, "check": check, "note": note})


def main() -> None:
    ref = json.loads((OUT / "ref" / "build_ref.json").read_text())
    for k, label in (("A", "h75 不分状态"), ("B", "h50 分状态")):
        row(f"原代码构建时间 {label}", ref["tables"][k]["seconds"], "秒", BUILD, "out/ref/build_ref.json",
            note="原代码（改动前），本机 10 核，量一次")
    dfl = json.loads((OUT / "check_defaults.json").read_text())
    for k, label in (("A", "h75 不分状态"), ("B", "h50 分状态")):
        t = dfl["tables"][k]
        row(f"默认参数重建 {label} 数组个数", t["n_arrays"], "个", BUILD, "out/check_defaults.json")
        row(f"默认参数重建 {label} 和对照表不同的数组", len(t["differ"]), "个", BUILD, "out/check_defaults.json", check="== 0")
        row(f"默认参数重建 {label} 构建时间", t["seconds"], "秒", BUILD, "out/check_defaults.json")
    bp = json.loads((OUT / "build_params.json").read_text())
    for name, g in bp["groups"].items():
        row(f"构建时间 {name}（h50 分状态）", g["seconds"], "秒", BUILD, "out/build_params.json", note=g["file"])
        bad = sum(v["rising_cells"] + v["negative_cells"] + v["nan_cells"] for v in g["checks"].values())
        row(f"{name} 表随存电量上升、为负或缺值的格子", bad, "格", BUILD, "out/build_params.json", check="== 0",
            note="curve、marg、curve_state、marg_state")
        for s, v in g["mean_curve"].items():
            if s in ("5", "20"):
                row(f"{name} 08:00 存电价值（第 {s} 段，570 天平均）", round(v, 2), "A$/MWh", BUILD, "out/build_params.json")
    for label, d in bp["direction"].items():
        c = d["curve"]
        row(f"方向 {label}：08:00 曲线格子数", c["cells"], "格", BUILD, "out/build_params.json")
        row(f"方向 {label}：前者低于后者超过 1e-3 的格子", c["cells_lower"], "格", BUILD, "out/build_params.json")
        row(f"方向 {label}：低的格子占比", round(100 * c["share_lower"], 2), "%", BUILD, "out/build_params.json",
            formula=f"{{方向 {label}：前者低于后者超过 1e-3 的格子}} / {{方向 {label}：08:00 曲线格子数}}")
        row(f"方向 {label}：最大降幅", round(max(c["largest_drop"], 0.0), 3), "A$/MWh", BUILD, "out/build_params.json")
    diag = json.loads((OUT / "diag_efficiency.json").read_text())
    nc = diag["no charging (load x100)"]
    row("诊断 不能充电时往返 0.88 低于 0.85 的格子", nc["cells_088_lower"], "格", BUILD, "out/diag_efficiency.json",
        note="看结果之后加的诊断；负荷 ×100，h50 不分状态")
    g10 = json.loads((OUT / "g5b_default.json").read_text())
    v = g10["vs_reference"]
    row("G5B 默认参数 逐日比较天数", v["days_compared"], "天", G5, "out/g5b_default.json", status="回测")
    row("G5B 参考文件天数", v["reference_days"], "天", G5, "reports/v2_g5_daily.csv", status="回测")
    for k, x in v["max_abs_diff"].items():
        unit = "MWh" if k == "soc_end" else "A$"
        row(f"G5B 默认参数 {k} 最大绝对差", x, unit, G5, "out/g5b_default.json", status="回测", check="<= 1e-6")
    a = pd.read_csv(OUT / "g5b_default.csv", parse_dates=["day"]).set_index("day")
    b = pd.read_csv(OUT / "g5b_power_mw8.csv", parse_dates=["day"]).set_index("day")
    sel = lambda x: x[(x.index >= "2026-10-16") & (x.index <= "2026-10-22")]  # noqa: E731
    a7, b7 = sel(a), sel(b)
    row("测试期最后 7 天 天数", len(b7), "天", FWD, "out/g5b_power_mw8.csv", status="回测")
    for k in ("total", "energy", "fcess"):
        row(f"G5B 10 MW 7 天 {k} 合计", round(float(a7[k].sum()), 2), "A$", FWD, "out/g5b_default.csv", status="回测")
        row(f"G5B 8 MW 7 天 {k} 合计", round(float(b7[k].sum()), 2), "A$", FWD, "out/g5b_power_mw8.csv", status="回测")
        row(f"G5B 8 MW 减 10 MW 7 天 {k}", round(float(b7[k].sum() - a7[k].sum()), 2), "A$", FWD,
            "out/g5b_*.csv", status="回测", formula=f"{{G5B 8 MW 7 天 {k} 合计}} - {{G5B 10 MW 7 天 {k} 合计}}")
    row("G5B 8 MW 7 天 每天市场收入", round(float(b7["total"].sum()) / len(b7), 2), "A$/天", FWD, "out/g5b_power_mw8.csv",
        status="回测", formula="{G5B 8 MW 7 天 total 合计} / {测试期最后 7 天 天数}")
    g8 = json.loads((OUT / "g5b_power_mw8.json").read_text())
    row("G5B 8 MW 全部天数 电网侧最大半小时平均功率", round(g8["max_mw"]["max_grid_mw"], 4), "MW",
        f"2025-04-01 至 2026-10-22（{g8['days']} 天）", "out/g5b_power_mw8.json", status="回测",
        note="超过 8 MW：调度器的电表上限层（IRCR 高峰时段）让电池顶满工厂负荷而不按功率封顶，10 MW 默认回测也有（待办）")
    tb = json.loads((OUT / "time_builds.json").read_text())
    for name, g in tb["groups"].items():
        row(f"构建时间中位数 {name}（重复 {tb['repeats']} 次）", g["median"], "秒", BUILD, "out/time_builds.json",
            note=f"最短 {g['min']}，最长 {g['max']}；机器同时有别的 agent 在跑")
    pdays = json.loads((OUT / "power_days.json").read_text())
    for tag, label in (("default", "10 MW"), ("power_mw8", "8 MW")):
        p = pdays[tag]
        row(f"G5B {label} 有半小时超过额定功率的天数", p["days_over"], "天", f"2025-04-01 至 2026-10-22（{p['days']} 天）",
            "out/power_days.json", status="回测", note="电网侧能量 / 0.5 h；原因是调度器的电表上限层不按功率封顶（待办）")
        row(f"G5B {label} 测试期最后 7 天超过额定功率的天数", p["test_week_over"], "天", FWD, "out/power_days.json",
            status="回测", check="== 0")
    mm = json.loads((OUT / "mismatch.json").read_text())
    pre = mm["before (out/pre_silent.json)"]["days"][0]
    own = mm["after"]["d positive control: 8 MW, its own table"]
    row("2025-04-01 8 MW 用 10 MW 表（原代码）市场收入", round(pre["total"], 2), "A$", "样本内 2025-04-01（1 天）",
        "out/pre_silent.json", status="回测")
    row("2025-04-01 8 MW 用自己的表 市场收入", round(own["total"], 2), "A$", "样本内 2025-04-01（1 天）",
        "out/mismatch.json", status="回测")
    row("2025-04-01 8 MW 用错表的差", round(pre["total"] - own["total"], 2), "A$", "样本内 2025-04-01（1 天）",
        "out/*.json", status="回测",
        formula="{2025-04-01 8 MW 用 10 MW 表（原代码）市场收入} - {2025-04-01 8 MW 用自己的表 市场收入}")
    with open(_common.HERE / "NUMBERS.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "value", "unit", "formula", "period", "source", "status", "check", "note"])
        w.writeheader()
        w.writerows(ROWS)
    print(f"{len(ROWS)} rows")


if __name__ == "__main__":
    main()
