"""Diagnostic for the rolling updates (report only): for a few refit months, the mean probability a model gives its own
training rows and the next month's rows, against the actual spike rates; and the monthly spike rate with the base60
level, to see whether the last 60 days predict the next month.

Output: out/p1d_diag_fit.csv, out/p1d_diag_months.csv, NUMBERS.csv (rows starting P1DX_)
Run:    .venv/bin/python analysis/2026-10-28/spikev3/diag_p1d.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.spike_risk import CLF_PARAMS  # noqa: E402
from vpp.spikev3 import rolling as rl  # noqa: E402

sys.path.insert(0, str(HERE))
from run_p1b import base_cols  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
MONTHS = ["2025-07-01", "2025-11-01", "2026-03-01", "2026-09-01"]


def main() -> None:
    data = pd.read_parquet(SCARCITY / "spike_table_ext.parquet").join(pd.read_parquet(OUT / "p1_features.parquet"))
    part = data[data["days_ahead"] == 0]
    cols = base_cols(0)
    rows = []
    for m in map(pd.Timestamp, MONTHS):
        test = (part["issue_date"] >= m) & (part["issue_date"] < m + pd.offsets.MonthBegin(1)) & part["spike"].notna()
        for name, window in (("E", None), ("S60", 60), ("S180", 180)):
            tr, _ = rl.window_mask(part["target_date"], part["spike"], rl.cutoff(m), window)
            y = part.loc[tr, "spike"].astype(int)
            clf = lgb.LGBMClassifier(**{**CLF_PARAMS, "n_jobs": 1, "random_state": 0}).fit(part.loc[tr, cols], y)
            rows.append({"month": m.date(), "model": name, "train_rows": int(tr.sum()), "train_spikes": int(y.sum()),
                         "train_rate": float(y.mean()), "train_mean_p": float(clf.predict_proba(part.loc[tr, cols])[:, 1].mean()),
                         "next_rate": float(part.loc[test, "spike"].mean()),
                         "next_mean_p": float(clf.predict_proba(part.loc[test, cols])[:, 1].mean())})
    fit = pd.DataFrame(rows)
    fit.to_csv(OUT / "p1d_diag_fit.csv", index=False)
    pred = pd.read_parquet(OUT / "p1d_predictions.parquet")
    b = pred[(pred["model"] == "base60") & (pred["days_ahead"] == 0) & pred["spike"].notna()]
    mon = b.groupby(pd.to_datetime(b["issue_date"]).dt.to_period("M")).agg(actual=("spike", "mean"), base60=("p_cal", "first"))
    mon.to_csv(OUT / "p1d_diag_months.csv")
    with pd.option_context("display.width", 200):
        print(fit.round(4).to_string(index=False))
        print(mon.loc["2025-04":].round(4).to_string())
        print("corr(base60, actual) monthly from 2025-04:", round(mon.loc["2025-04":].corr().iloc[0, 1], 3))


def numbers() -> None:
    """NUMBERS.csv rows starting P1DX_ (replaced)."""
    path = HERE / "NUMBERS.csv"
    old = pd.read_csv(path)
    old = old[~old["name"].str.startswith("P1DX_")]
    out = []
    fit = pd.read_csv(OUT / "p1d_diag_fit.csv")
    for _, r in fit.iterrows():
        k = f"P1DX_D0_{r['month']}_{r['model']}"
        per = f"更新月 {r['month']}（训练行：目标日早于该月减 2 天；下个月：该月发出的行）"
        for col, lab, unit, mult in (("train_spikes", "训练尖峰行数", "次", 1), ("train_rate", "训练尖峰率", "%", 100),
                                     ("train_mean_p", "训练行平均预测", "%", 100), ("next_rate", "当月实际尖峰率", "%", 100),
                                     ("next_mean_p", "当月平均预测", "%", 100)):
            out.append({"name": f"{k}_{lab}", "value": mult * r[col], "unit": unit, "formula": "", "period": per,
                        "source": "out/p1d_diag_fit.csv", "status": "回测", "check": "", "note": "诊断，只报告"})
    mon = pd.read_csv(OUT / "p1d_diag_months.csv")
    for _, r in mon[mon["issue_date"] >= "2025-04"].iterrows():
        for col, lab in (("actual", "实际尖峰率"), ("base60", "近60天尖峰率")):
            out.append({"name": f"P1DX_D0_{r['issue_date']}_{lab}", "value": 100 * r[col], "unit": "%", "formula": "",
                        "period": f"发出月 {r['issue_date']}", "source": "out/p1d_diag_months.csv", "status": "回测",
                        "check": "", "note": "诊断，只报告；近60天尖峰率是该月的 base60 常数"})
    m = mon[mon["issue_date"] >= "2025-04"]
    out.append({"name": "P1DX_D0_近60天尖峰率与当月实际的相关系数", "value": float(np.corrcoef(m["actual"], m["base60"])[0, 1]),
                "unit": "1", "formula": "", "period": "发出月 2025-04 至 2026-10（19 个月）", "source": "out/p1d_diag_months.csv",
                "status": "回测", "check": "", "note": "诊断，只报告"})
    pd.concat([old, pd.DataFrame(out)], ignore_index=True).to_csv(path, index=False)
    print(f"{len(out)} diagnostic numbers -> NUMBERS.csv")


if __name__ == "__main__":
    main()
    numbers()
