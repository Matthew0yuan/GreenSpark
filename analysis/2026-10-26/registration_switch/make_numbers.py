"""NUMBERS.csv for RESULTS.md, read from summary.csv and diffs.csv (run.py's output).

  .venv/Scripts/python.exe analysis/2026-10-26/registration_switch/make_numbers.py
"""

import csv
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
P = {"in": "样本内 2025-04-01 至 2026-06-30", "seen": "测试期（看过）2026-07-01 至 2026-10-15",
     "unseen": "测试期（没看过）2026-10-16 至 2026-10-22"}
DAYS = {"in": 456, "seen": 107, "unseen": 7}
SC = {"A": "A Scheduled + FCESS", "B": "B Scheduled, no FCESS", "C": "C Non-Scheduled, lag 1", "D": "D Non-Scheduled, lag 2"}
ST = {"计划加计划外买卖": "forecast+offer(value)", "只按计划": "forecast", "完美预知": "perfect"}
SRC = "analysis/2026-10-26/registration_switch/run.py -> summary.csv"
NOTE = "10 MW / 40 MWh 单独接电网，损耗 $30/MWh；每天平均合计（电能 + FCESS − 损耗）"


def main() -> None:
    s = pd.read_csv(HERE / "summary.csv").set_index(["period", "scenario", "strategy"])
    rows = []
    for pk, pname in P.items():
        period = f"{pname}（{DAYS[pk]} 天）"
        for sk, sname in SC.items():
            for zh, st in ST.items():
                if st == "perfect" and sk in "CD":
                    continue  # the same as B's
                v = float(s.loc[(pname, sname, st), "total"])
                rows.append([f"{pk}_{sk}_{zh}", f"{v:.2f}", "A$/天", "", period, SRC, "回测", "", f"情景 {sname}；{zh}；{NOTE}"])
        n = lambda sk: f"{{{pk}_{sk}_计划加计划外买卖}}"  # noqa: E731
        for label, x, y in (("FCESS 的作用", "A", "B"), ("Non-Scheduled 滞后 1 步少赚", "B", "C"),
                            ("Non-Scheduled 滞后 2 步少赚", "B", "D")):
            a = float(s.loc[(pname, SC[x], ST["计划加计划外买卖"]), "total"])
            b = float(s.loc[(pname, SC[y], ST["计划加计划外买卖"]), "total"])
            rows.append([f"{pk}_{x}-{y}", f"{a - b:.2f}", "A$/天", f"{n(x)} - {n(y)}", period, SRC, "回测", "",
                         f"{label}；95% 区间见 diffs.csv"])
        c = float(s.loc[(pname, SC["C"], ST["计划加计划外买卖"]), "total"])
        b = float(s.loc[(pname, SC["B"], ST["计划加计划外买卖"]), "total"])
        rows.append([f"{pk}_C占B", f"{100 * c / b:.2f}", "%", f"{n('C')} / {n('B')}", period, SRC, "推算", "",
                     "Non-Scheduled（滞后 1 步）保住了 Scheduled（无 FCESS）收入的比例"])
    with open(HERE / "NUMBERS.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "value", "unit", "formula", "period", "source", "status", "check", "note"])
        w.writerows(rows)
    print(f"{len(rows)} rows")


if __name__ == "__main__":
    main()
