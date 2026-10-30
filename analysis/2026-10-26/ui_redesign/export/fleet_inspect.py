"""Read-only look at the saved policy outputs used by fleet_export.py (prints only, writes nothing).

Run from the repository root:  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/fleet_inspect.py
"""
import sys

sys.dont_write_bytecode = True
from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
P = ROOT / "reports" / "policy"
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 80)
pd.set_option("display.max_rows", 200)

bt = pd.read_csv(P / "policy_backtest.csv")
bt = bt[bt["case"].isin(["B reference", "C optimised"])]
cols = ["scenario", "case", "period", "days", "enrolled", "zenith_profit", "customer_benefit", "activations", "ircr_calls",
        "energy_calls", "remaining_allowance", "energy_kwh", "net_export_kwh", "violations_total", "covered_2025",
        "covered_2023", "covered_2024"]
print(bt[cols].to_string())
pcols = [c for c in bt.columns if c.startswith("policy_") or c.startswith("rule_")]
print(bt.drop_duplicates(["scenario", "case"])[["scenario", "case"] + pcols].T.to_string())
money = ["revenue_wholesale", "revenue_capacity", "revenue_fcess", "compensation_pay_energy", "compensation_pay_event",
         "compensation_annual_fee", "compensation_offset", "costs_ops_event", "costs_ops_customer", "costs_fixed",
         "customer_payments", "customer_bill", "customer_wear", "customer_hassle", "fit_zen", "fit_cust"]
print(bt[["scenario", "case", "period"] + money].to_string())

for name in ("policy_stress.csv", "policy_sensitivity.csv", "policy_cap_curve.csv"):
    print("==", name)
    print(pd.read_csv(P / name).head(60).to_string())

K = ROOT / "analysis" / "2026-10-26" / "customer_kwh_value" / "out"
s = pd.read_csv(K / "season_ircr.csv")
print(s[(s["window"] == "17:30-19:30")].to_string())
print(pd.read_csv(K / "annual_breakeven.csv").to_string())
print(pd.read_csv(K / "mechanism.csv").to_string())
