"""Report only: every spike model tried, ranked by log loss in the test period 2026-07-01..10-15, from the stored
summaries (no retraining). Checks that every source scored the same rows (row count and spikes per task agree).

Output: out/rank_test.csv, NUMBERS.csv (rows starting RANK_)
Run:    .venv/bin/python analysis/2026-10-28/spikev3/rank_test.py
"""

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
TEST = "test 2026-07-01..10-15"
INS = "in-sample 2025-04-01..2026-06-30"
SOURCES = {  # file: {model in file: (name in the ranking, kind)}
    "compare_models.csv": {"climatology": ("常数基准（训练期平均尖峰率）", "基准"), "official": ("官方模型 vpp/spike_risk", "现有"),
                           "C_strict": ("C_strict（官方特征按 09:00 重建）", "现有"),
                           "aemo_price": ("AEMO 预调度价（只排序）", "基准"), "yesterday": ("昨天同时段价（只排序）", "基准")},
    "p1_summary.csv": {"spikev3": ("spikev3（保存的模型）", "采用"), "C+G0": ("C_strict + 近期尖峰频率", "第 1 步单组"),
                       "C+G1": ("C_strict + STEM 拆开", "第 1 步单组"), "C+G2": ("C_strict + 余量和平时比", "第 1 步单组"),
                       "C+G4": ("C_strict + 剩余供给指数", "第 1 步单组"), "C+G5": ("C_strict + 风电预报修订", "第 1 步单组")},
    "p1b_summary.csv": {"spikev3+A": ("spikev3 + 三组一起加", "补充"), "spikev3+B": ("spikev3 + 合成紧张分数", "补充"),
                        "spikev3+C": ("spikev3 + 近期意外", "补充")},
    "p1d_summary.csv": {"E": ("每月全部历史重训*", "滚动"), "S60": ("只用最近 60 天*", "滚动"), "S90": ("只用最近 90 天*", "滚动"),
                        "S180": ("只用最近 180 天*", "滚动"), "D90": ("近期加权（半衰期 90 天）*", "滚动"),
                        "C60": ("最近 60 天重新校准*", "滚动"), "C90": ("最近 90 天重新校准*", "滚动"),
                        "base60": ("近 60 天平均尖峰率*", "基准")},
}


def main() -> None:
    rows = []
    for f, models in SOURCES.items():
        s = pd.read_csv(OUT / f)
        for m, (name, kind) in models.items():
            for _, r in s[(s["model"] == m) & s["period"].isin([TEST, INS])].iterrows():
                rows.append({"days_ahead": r["days_ahead"], "period": r["period"], "name": name, "kind": kind, "source": f,
                             "n": r["n"], "spikes": r["spikes"], "logloss": r.get("logloss"), "ap": r["ap"],
                             "recall@5%": r["recall@5%"], "mean_p": r.get("mean_p"), "rate": r["rate"]})
    t = pd.DataFrame(rows)
    same = t.groupby(["days_ahead", "period"])[["n", "spikes"]].nunique()
    assert (same == 1).all().all(), f"sources scored different rows:\n{same}"
    out = []
    for (d, p), g in t.groupby(["days_ahead", "period"]):
        g = g.copy()
        g["rank_logloss"] = g["logloss"].rank(method="min")
        g["rank_ap"] = g["ap"].rank(method="min", ascending=False)
        out.append(g)
    t = pd.concat(out).sort_values(["days_ahead", "period", "rank_logloss", "rank_ap"])
    t.to_csv(OUT / "rank_test.csv", index=False)
    with pd.option_context("display.width", 220, "display.max_rows", 200):
        print(t[t["period"] == TEST][["days_ahead", "rank_logloss", "name", "logloss", "rank_ap", "ap", "recall@5%", "mean_p", "rate"]]
              .round(4).to_string(index=False))
        print(t[t["period"] == INS][["days_ahead", "rank_logloss", "name", "logloss", "ap"]].round(4).to_string(index=False))
    path = HERE / "NUMBERS.csv"
    old = pd.read_csv(path)
    nums = []
    for _, r in t.iterrows():
        per = ("测试期 2026-07-01 至 2026-10-15（107 天，已看过）" if r["period"] == TEST else "样本内 2025-04-01 至 2026-06-30（456 天）")
        tag = "测试A" if r["period"] == TEST else "样本内"
        for col, lab in (("rank_logloss", "对数损失名次"), ("rank_ap", "AP名次")):
            if pd.notna(r[col]):
                nums.append({"name": f"RANK_{tag}_D{r['days_ahead']}_{r['name']}_{lab}", "value": r[col], "unit": "1",
                             "formula": "", "period": per, "source": "out/rank_test.csv", "status": "回测", "check": "",
                             "note": "名次 1 最好；带 * 的是生产方式滚动"})
    pd.concat([old[~old["name"].str.startswith("RANK_")], pd.DataFrame(nums)], ignore_index=True).to_csv(path, index=False)
    print(f"{len(nums)} ranking numbers -> NUMBERS.csv")


if __name__ == "__main__":
    main()
