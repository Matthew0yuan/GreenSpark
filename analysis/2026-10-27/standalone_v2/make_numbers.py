"""NUMBERS.csv for RESULTS.md: per period, each way of running the standalone network (A$/day), the pre-registered
e5 - v1 comparison and the adopted dispatcher's gain over the 09:00 plan alone, with month-block 95% intervals.

  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/make_numbers.py
"""

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

from vpp.v2 import backtest as vb  # noqa: E402

from run import PERIODS  # noqa: E402

NAMES = {"forecast+offer(value)": "v1", "rw50+state t5+roll": "e5", "forecast": "09:00 计划不改", "perfect": "完美预知价格"}
LABEL = {PERIODS[0]: "样本内 2025-04-01 至 2026-06-30（456 天）", PERIODS[1]: "测试期看过 2026-07-01 至 2026-10-15（107 天）",
         PERIODS[2]: "测试期没看过 2026-10-16 至 2026-10-22（7 天）"}


def ci(diff: pd.Series):
    return vb.month_ci(diff) if diff.index.to_period("M").nunique() > 1 else (float("nan"), float("nan"))


def main() -> None:
    daily = pd.concat([pd.read_csv(HERE / "daily.csv"), pd.read_csv(HERE / "refs_daily.csv")], ignore_index=True)
    v = daily.pivot_table(index=["period", "day"], columns="strategy", values="total")
    rows = []
    for p, short in zip(PERIODS, ("样本内", "测试看过", "测试没看过")):
        g = v.loc[p]
        g.index = pd.to_datetime(g.index)
        per = LABEL[p]
        p = short
        for s, name in NAMES.items():
            rows.append([f"{name} 每天 {p}", round(g[s].mean(), 2), "A$/天", "", per, "daily.csv / refs_daily.csv total",
                         "回测", "", "电能 + FCESS − 损耗"])
        for a, b, tag in (("rw50+state t5+roll", "forecast+offer(value)", "e5 − v1"),
                          ("forecast+offer(value)", "forecast", "v1 − 09:00 计划不改")):
            d = g[a] - g[b]
            lo, hi = ci(d)
            rows.append([f"{tag} {p}", round(d.mean(), 2), "A$/天", "", per, "按天相减", "回测", "",
                         f"整月重抽样 95% 区间 [{lo:.1f}, {hi:.1f}]；{int((d > 0).sum())} 天前者更多"])
        rows.append([f"v1 占完美预知 {p}", round(100 * g["forecast+offer(value)"].mean() / g["perfect"].mean(), 1), "%",
                     "", per, "v1 / perfect", "推算", "", ""])
    with open(HERE / "NUMBERS.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "value", "unit", "formula", "period", "source", "status", "check", "note"])
        w.writerows(rows)
    for r in rows:
        print(r[0], r[1], r[2], r[8])


if __name__ == "__main__":
    main()
