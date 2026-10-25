"""Day-level look at the main run (results/), to find implementation errors and explain the numbers.

Run:  .venv/Scripts/python.exe analysis/2026-10-24/peak_day_probability/inspect_results.py
"""

from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent / "results"


def main() -> None:
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_rows", 400)
    d = pd.read_csv(OUT / "daily_probabilities.csv", parse_dates=["trading_day"]).set_index("trading_day")
    c = pd.read_csv(OUT / "daily_calls.csv", parse_dates=["trading_day"]).set_index("trading_day")
    cols = ["y", "er", "f0", "mu0", "K4_od", "T_s1", "ratio", "M1_union", "M1p", "M2", "S1", "M3", "M3_da", "exp_peaks_ahead", "lab_primary", "lab_proxy_er"]
    for y in (2024, 2025):
        s = d[d["season"] == y]
        print(f"\n===== season {y}: peak days and the 15 days of highest M3 (same day)")
        pick = s[(s["lab_primary"] > 0) | (s["lab_proxy_er"] > 0) | s["M3"].rank(ascending=False).le(15)]
        x = pick[cols].copy()
        x["call_M3_30"] = c.loc[x.index, "call:M3|DP|30"]
        x["call_M1u_30"] = c.loc[x.index, "call:M1_union|FCFS|30"]
        print(x.round(3).to_string())
        print("\nfirst 14 days of the season")
        x = s[cols].iloc[:14].copy()
        x["call_M3_30"] = c.loc[x.index, "call:M3|DP|30"]
        x["call_M1u_30"] = c.loc[x.index, "call:M1_union|FCFS|30"]
        print(x.round(3).to_string())
        print("level ratio by month:", s["ratio"].groupby(s.index.month).mean().round(3).to_dict())
    r = pd.read_csv(OUT / "revenue.csv")
    print("\n", r[r["budget"].isin([0, 4, 30])][["season", "ledger", "method", "policy", "budget", "calls", "zenith_net", "ircr_value",
                                                  "ircr_kw_reduction_per_responding_home", "pay_energy", "offset", "wholesale", "customer_net"]].round(2).to_string(index=False))
    b = pd.read_csv(OUT / "bias_model_errors.csv")
    print("\n", b.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
