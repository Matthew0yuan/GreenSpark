"""Added after seeing describe.py (PLAN change log): is the ST PASA revision's AUC for the tightest 10% a season effect?
Within-month AUC: each day's column value ranked against days of the same calendar month only (percentile), then the
AUC of that percentile; plus the column's monthly mean and the tightest days per month. In-sample only.

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/tightness_revisions/describe_within_month.py
Writes out/describe_within_month.csv
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import describe as ds  # noqa: E402

COLS = ["st_ref_rev_max_1621_mw", "st_ref_rev_mean_1621_mw", "out_thermal_forced_max_1621_mw", "wx_temp_perth_max_day_rev_c"]


def main() -> None:
    rng = np.random.default_rng(ds.SEED + 1)
    d = ds.table()
    s = d[(d["period"] == "in_sample") & d["m1_mw"].notna()].copy()
    s["top_actual"] = s["top_actual"].astype(bool)
    rows = []
    for c in COLS:
        pct = s.groupby("month")[c].rank(pct=True)
        f = s.assign(_p=pct)
        stat = lambda g: ds.auc(g["_p"].to_numpy(float), g["top_actual"].to_numpy(bool))
        lo, hi = ds.boot(f, stat, rng)
        rows.append({"column": c, "auc_within_month": stat(f), "ci_low": lo, "ci_high": hi})
    res = pd.DataFrame(rows)
    res.to_csv(ds.OUT / "describe_within_month.csv", index=False)
    by = s.groupby("month").agg(days=("top_actual", "size"), tightest=("top_actual", "sum"),
                                st_ref_rev_max=("st_ref_rev_max_1621_mw", "mean"))
    pd.set_option("display.width", 200)
    print(res.round(3).to_string(index=False))
    print(by.round(1).to_string())


if __name__ == "__main__":
    main()
