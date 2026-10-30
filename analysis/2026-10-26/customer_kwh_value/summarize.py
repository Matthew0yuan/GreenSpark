"""Prints compact views of out/*.csv for writing RESULTS.md (reads only this folder's outputs).

Run (from the repository root):  .venv/bin/python analysis/2026-10-26/customer_kwh_value/summarize.py
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
WIN = "17:30-19:30"
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)


def show(title, df):
    print(f"\n=== {title}")
    print(df.to_string(index=False))


mon = pd.read_csv(OUT / "monthly.csv")
dd = pd.read_csv(OUT / "daily.csv")
alloc = pd.read_csv(OUT / "season_ircr.csv")
ann = pd.read_csv(OUT / "annual_breakeven.csv")

m = mon[mon["window"] == WIN]
cust = m[m["scenario"] == "S2b"][["period", "month", "days", "flagged_days", "E_Q_kwh", "E_dN_kwh", "a_self_use_c",
                                  "event_debs_c", "wear_c", "hassle_c", "d_customer_min_c", "offset_c", "ops_c",
                                  "a_self_use_actual_c", "d_customer_min_actual_c", "Q_actual_kwh"]]
show("customer side by month (scenario-free), default window", cust.round(2))

for sc in ("S1", "S2a", "S2b", "S3"):
    z = m[m["scenario"] == sc][["period", "month", "days_with_0900_forecast", "flagged_days", "called_days_C",
                                "b_energy_0900_c", "b_energy_actual_c", "b_energy_0900_flagged_c", "c_ircr_0900_flagged_c",
                                "d_zenith_max_0900_unflagged_c", "d_zenith_max_0900_flagged_c",
                                "d_zenith_max_0900_best_day_c", "d_customer_min_c",
                                "days_zenith_max_above_customer_min_0900", "d_zenith_max_actual_called_c"]]
    show(f"Zenith side by month, {sc}, default window", z.round(2))

a = alloc[alloc["window"] == WIN]
show("IRCR allocation, default window", a[["scenario", "method", "season", "calls", "Q_sum_kwh", "Q_mean_kwh_per_call",
                                           "ircr_kw_reduction", "rcp_A$_per_MW_yr", "value_full_A$", "value_zenith_A$",
                                           "zenith_c_per_kwh", "zenith_A$_per_call",
                                           "zenith_c_per_kwh_at_2027_28_rcp"]].round(2))
pooled = alloc[(alloc["season"] == "三季合并") & (alloc["method"] == "C")]
show("IRCR pooled by window (C rule)", pooled[["scenario", "window", "calls", "Q_mean_kwh_per_call", "zenith_c_per_kwh",
                                                "zenith_c_per_kwh_at_2027_28_rcp"]].round(2))

show("annual break-even", ann[["policy", "scenario", "period", "calls", "Q_sum_kwh_per_responding", "W_to_zenith_A$",
                               "ircr_to_zenith_A$_per_responding", "offset_A$", "ops_event_A$",
                               "annual_costs_A$_per_responding", "customer_costs_net_of_offset_A$",
                               "zenith_max_c_per_kwh_year", "customer_min_c_per_kwh_year", "paid_c_per_billed_kwh",
                               "zenith_A$_per_enrolled_check", "customer_A$_per_enrolled_check"]].round(2))

# by window: in-sample flagged days and all forecast days
ins = dd[dd["period"].str.startswith("样本内")]
rows = []
for (sc, win), g in ins.groupby(["scenario", "window"]):
    f = g[g["has_0900_forecast"]]
    fl = f[f["flagged"]]
    rows.append({"scenario": sc, "window": win, "E_Q": g["E_Q_kwh"].mean(), "self_use": g["a_self_use_c"].mean(),
                 "cust_min_all": g["d_customer_min_c"].mean(),
                 "cust_min_flag": fl["d_customer_min_c"].mean() if len(fl) else np.nan,
                 "energy_0900_all": f["b_energy_0900_c"].mean(),
                 "energy_actual_all": (g["b_energy_actual_c"] * g["Q_actual_kwh"]).sum() / g["Q_actual_kwh"].sum(),
                 "ircr_flag": fl["c_ircr_0900_c"].mean() if len(fl) else np.nan,
                 "zen_max_flag": fl["d_zenith_max_0900_c"].mean() if len(fl) else np.nan,
                 "zen_max_unflag": f[~f["flagged"]]["d_zenith_max_0900_c"].mean(),
                 "days_pos": int((f["surplus_0900_c"] > 0).sum())})
show("in-sample by window", pd.DataFrame(rows).round(2))

# whole-period summaries, default window
rows = []
for (per, sc), g in dd[dd["window"] == WIN].groupby(["period", "scenario"]):
    f = g[g["has_0900_forecast"]]
    fl, un = f[f["flagged"]], f[~f["flagged"]]
    err = f["b_energy_0900_c"] - f["b_energy_actual_c"]
    rows.append({"period": per, "scenario": sc, "days": len(g), "fc_days": len(f), "flag": len(fl),
                 "self_use": g["a_self_use_c"].mean(), "cust_min": g["d_customer_min_c"].mean(),
                 "cust_min_min": g["d_customer_min_c"].min(), "cust_min_max": g["d_customer_min_c"].max(),
                 "energy_0900": f["b_energy_0900_c"].mean(), "energy_actual_Qw":
                     (g["b_energy_actual_c"] * g["Q_actual_kwh"]).sum() / g["Q_actual_kwh"].sum(),
                 "energy_0900_max": f["b_energy_0900_c"].max(),
                 "energy_err_mean": err.mean(), "energy_err_MAE": err.abs().mean(),
                 "zen_unflag": un["d_zenith_max_0900_c"].mean(), "zen_unflag_max": un["d_zenith_max_0900_c"].max(),
                 "zen_flag": fl["d_zenith_max_0900_c"].mean() if len(fl) else np.nan,
                 "zen_flag_min": fl["d_zenith_max_0900_c"].min() if len(fl) else np.nan,
                 "cust_min_flag": fl["d_customer_min_c"].mean() if len(fl) else np.nan,
                 "days_pos": int((f["surplus_0900_c"] > 0).sum()),
                 "actual_energy_days_above_custmin": int((g["b_energy_actual_c"] > g["d_customer_min_actual_c"]).sum())})
show("period summary, default window", pd.DataFrame(rows).round(2))

# hot-season split of in-sample energy value: flagged vs not, Dec-Mar vs rest
g = dd[(dd["window"] == WIN) & (dd["scenario"] == "S1") & dd["period"].str.startswith("样本内") & dd["has_0900_forecast"]]
for name, sel in (("Dec-Mar", g["hot_season"]), ("Apr-Nov", ~g["hot_season"])):
    x = g[sel]
    print(f"{name}: days {len(x)}, energy_0900 mean {x['b_energy_0900_c'].mean():.2f}, actual Q-weighted "
          f"{(x['b_energy_actual_c'] * x['Q_actual_kwh']).sum() / x['Q_actual_kwh'].sum():.2f}, "
          f"max actual {x['b_energy_actual_c'].max():.2f}, days actual > cust min "
          f"{int((x['b_energy_actual_c'] > x['d_customer_min_actual_c']).sum())}")
top = g.sort_values("b_energy_actual_c", ascending=False).head(8)[["day", "b_energy_0900_c", "b_energy_actual_c",
                                                                   "d_customer_min_actual_c", "Q_actual_kwh"]]
show("top actual energy days, in-sample, default window", top.round(2))
