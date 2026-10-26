"""Read-only diagnostics behind site_summary.json: where v1 changed under the current code, how far the meter goes
above the 9 MW CMD, and what drives the worst in-sample day."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

E5, V1 = "rw50+state t5+roll", "v1"
pd.set_option("display.width", 220)
rep = pd.read_csv(HERE / "site_summary_replay_ledger.csv", parse_dates=["day"])
pub = pd.read_csv(ROOT / "reports/v2_test_daily.csv", parse_dates=["day"])
cols = ["energy", "fcess", "wear", "ledger_total", "max_import_mw", "excess_cmd_mw", "soc_end"]
for d in ("2025-12-05", "2025-12-08", "2026-02-02"):
    for name in (V1, E5):
        a = pub[(pub["strategy"] == name) & (pub["day"] == d)][cols].iloc[0]
        b = rep[(rep["strategy"] == name) & (rep["day"] == d)][cols].iloc[0]
        print(d, name, "published:", a.round(3).to_dict(), "\n           current:  ", b.round(3).to_dict())
rb = pd.read_csv(HERE / "site_summary_replay_bills.csv")
pb = pd.read_csv(ROOT / "reports/v2_test_bills.csv")
for name in (V1, E5):
    x = rb[rb["strategy"] == name].set_index("month")["saving"] - pb[pb["strategy"] == name].set_index("month")["saving"]
    print(name, "bill saving change by month (current - published):", x[x.abs() > 1e-6].round(2).to_dict())

log = pd.read_parquet(HERE / "site_summary_replay_log.parquet")
log["period"] = np.where(log["day"] < "2026-07-01", "in-sample", np.where(log["day"] < "2026-10-16", "test-seen", "test-unseen"))
for name in (E5, V1):
    x = log[log["strategy"] == name]
    ex = x["meter_mw"] - 9.0
    print(f"\n{name}: half-hours with meter above 9 MW by size of the excess")
    print(pd.DataFrame({f"> {t:g} MW": (ex > t).groupby(x["period"]).sum() for t in (1e-6, 1e-3, 0.01, 0.05, 0.1)}))

# the worst in-sample day
m = pd.read_csv(HERE / "site_summary_replay_money.csv", index_col=0, parse_dates=True)
d = (m[E5] - m[V1])
print("\nworst 5 in-sample days (e5 - v1 money):")
print(d[d.index < "2026-07-01"].nsmallest(5).round(1))
w = "2026-04-28"
print(rep[rep["day"] == w].set_index("strategy").loc[[V1, E5], ["energy", "fcess", "wear", "ledger_total", "discharged_mwh", "max_import_mw"]].round(1))
x = log[(log["day"] == w) & log["strategy"].isin([V1, E5])].pivot_table(index="slot", columns="strategy", values=["energy_mwh", "rtp"])
x = x[(x[("rtp", V1)] > 300)]
print("half-hours with RTP > 300 on", w, "\n", x.round(2))
