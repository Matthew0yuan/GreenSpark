"""Print the secondary tables of the main run (results/): the energy-requirement proxy label, the cost sweep,
sensitivities, reliability and the ranks of the peak days.

Run:  .venv/Scripts/python.exe analysis/2026-10-24/peak_day_probability/inspect_tables.py
"""

from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent / "results"
KEEP = ["fold", "label", "method", "policy", "budget", "cost", "calls", "covered_days", "covered_intervals", "median_cut",
        "false_calls", "dec_calls", "exhausted_on"]


def main() -> None:
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_rows", 500)
    c = pd.read_csv(OUT / "season_call_metrics.csv")
    print("== energy-requirement proxy label, fold A, budget 30 (main cost)")
    x = c[(c["label"] == "proxy_er") & (c["budget"].isin([30, 4])) & ((c["cost"] == 0.03) | c["cost"].isna())]
    print(x[[k for k in KEEP if k in x]].to_string(index=False))
    print("\n== cost sweep, online DP, primary label")
    x = c[(c["label"] != "proxy_er") & (c["policy"] == "DP") & c["method"].isin(["S1", "M1p", "M2", "M3"])]
    print(x.pivot_table(index=["fold", "method", "budget"], columns="cost", values=["calls", "covered_days"]).to_string())
    s = pd.read_csv(OUT / "sensitivity_prob_metrics.csv")
    print("\n== sensitivity: Brier (same day / day ahead), primary label")
    s = s[s["label"].isin(["proxy_od", "official", "primary"])]
    print(s.pivot_table(index=["config", "method"], columns=["fold", "decision"], values="brier").round(4).to_string())
    sc = pd.read_csv(OUT / "sensitivity_call_metrics.csv")
    print("\n== sensitivity: calls at budget 30, primary label")
    sc = sc[(sc["label"] != "proxy_er") & sc["policy"].isin(["DP", "FCFS"]) & (sc["budget"] == 30)]
    print(sc.pivot_table(index=["config", "method", "policy"], columns="fold", values=["calls", "covered_days"]).to_string())
    print("\n== reliability, both folds, same day")
    print(pd.read_csv(OUT / "reliability.csv").round(3).to_string(index=False))
    print("\n== hindsight ranks needed")
    print(pd.read_csv(OUT / "peak_ranks.csv").to_string(index=False))


if __name__ == "__main__":
    main()
