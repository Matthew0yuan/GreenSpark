"""What spikev3 learned (PLAN.md section 6.3), report only. Per task, the monthly walk-forward of spikev3 is refitted
exactly as vpp.spikev3.models.walk_forward, and each month's model gives LightGBM's per-feature contributions
(pred_contrib, tree SHAP values in log-odds before calibration) for its own month's rows. Checked: the contributions
sum to the stored raw probability of every row.

Reported per period (in-sample 2025-04-01..2026-06-30, test 2026-07-01..10-15): each feature's mean |contribution| (how
much it is used) and mean contribution (which way it pushes), also on spike and non-spike rows; the same by group.

Output: out/p1c_shap_features.csv, out/p1c_shap_groups.csv
Run:    .venv/bin/python analysis/2026-10-28/spikev3/explain_p1c.py
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.spike import models as old  # noqa: E402
from vpp.spike import protocol as pc  # noqa: E402
from vpp.spike_risk import CLF_PARAMS  # noqa: E402
from vpp.spikev3 import features as fx  # noqa: E402
from vpp.spikev3 import models as md  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
PERIODS = {"in-sample 2025-04-01..2026-06-30": ("2025-04-01", "2026-06-30"),
           "test 2026-07-01..10-15": ("2026-07-01", "2026-10-15")}
FIRST_MONTH = pd.Timestamp("2025-03-01")
GROUPS = {
    "AEMO预调度价格": ["aemo_energy", "aemo_reg_raise", "aemo_reg_lower", "aemo_cont_raise", "aemo_cont_lower",
                   "aemo_day_max", "aemo_err1", "aemo_err7"],
    "预调度数量和余量": ["pd_req", "pd_demand", "pd_dpv", "pd_inservice", "pd_avail", "pd_inject", "pd_withdraw",
                  "pd_deficit", "c_margin_avail", "pd_headroom", "c_day_margin_min", "c_net_demand"],
    "天气": ["wx_temp", "wx_ghi", "wx_cloud", "wx_wind_midwest", "wx_wind_merredin", "wx_tmax_day", "wx_ghi_day",
           "wx_wind_evening"],
    "原有历史价格": ["p_prev1", "p_prev7_mean", "p_day_max_prev1", "spk_days_7"],
    "时间": ["slot", "dow", "month"],
    "G0近期尖峰频率": fx.G0,
    "G1 STEM": fx.G1,
}


def contributions(part: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    params = {**CLF_PARAMS, "n_jobs": 1, "random_state": 0}

    def one(month: pd.Timestamp) -> pd.DataFrame | None:
        test = (part["issue_date"] >= month) & (part["issue_date"] < month + pd.offsets.MonthBegin(1))
        if not test.any():
            return None
        end = min(month - pd.Timedelta(days=pc.EMBARGO_DAYS), md.TRAIN_END + pd.Timedelta(days=1))
        tr = (part["target_date"] < end) & part["spike"].notna()
        y = part.loc[tr, "spike"].astype(int)
        clf = lgb.LGBMClassifier(**params).fit(part.loc[tr, cols], y, sample_weight=np.where(y == 1, 1.0, 1.0))
        c = clf.booster_.predict(part.loc[test, cols], pred_contrib=True)
        return pd.DataFrame(c, index=part.index[test], columns=cols + ["bias"])

    months = pd.date_range(FIRST_MONTH, part["issue_date"].max(), freq="MS")
    with ThreadPoolExecutor(os.cpu_count() or 1) as pool:
        return pd.concat([x for x in pool.map(one, months) if x is not None])


def main() -> None:
    table = pd.read_parquet(SCARCITY / "spike_table_ext.parquet")
    data = table.join(pd.read_parquet(OUT / "p1_features.parquet"))
    pred = pd.read_parquet(OUT / "p1_predictions.parquet")
    passed = json.loads((OUT / "decision_p1.json").read_text())["passed"]
    group_of = {c: g for g, cs in GROUPS.items() for c in cs}
    frows, grows = [], []
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        cols = old.cols_of(["existing"]) + [c for g in passed[str(d)] for c in fx.GROUPS[g]]
        missing = [c for c in cols if c not in group_of]
        assert not missing, missing
        c = contributions(part, cols)
        ref = pred[(pred["model"] == "spikev3") & (pred["days_ahead"] == d)].set_index("row")["p_raw"].reindex(c.index)
        gap = float(np.abs(1 / (1 + np.exp(-c.sum(axis=1))) - ref).max())
        print(f"days_ahead {d}: {len(c):,} rows, {len(cols)} features; contributions vs stored p_raw max |diff| {gap:.1e}",
              flush=True)
        assert gap < 1e-6, "contributions do not reproduce spikev3"
        meta = part.loc[c.index, ["target_date", "spike"]]
        for pname, (lo, hi) in PERIODS.items():
            m = ((meta["target_date"] >= lo) & (meta["target_date"] <= hi) & meta["spike"].notna()).to_numpy()
            x, y = c[m], meta.loc[m, "spike"].to_numpy()
            for f in cols + ["bias"]:
                frows.append({"days_ahead": d, "period": pname, "feature": f, "group": group_of.get(f, "常数项"),
                              "mean_abs": float(x[f].abs().mean()), "mean": float(x[f].mean()),
                              "mean_spike": float(x.loc[y == 1, f].mean()), "mean_nonspike": float(x.loc[y == 0, f].mean())})
            gsum = pd.DataFrame({g: x[[f for f in cs if f in x]].sum(axis=1) for g, cs in GROUPS.items()
                                 if any(f in x for f in cs)}).assign(常数项=x["bias"])
            for g in gsum:
                grows.append({"days_ahead": d, "period": pname, "group": g, "mean_abs": float(gsum[g].abs().mean()),
                              "mean": float(gsum[g].mean()), "mean_spike": float(gsum.loc[y == 1, g].mean()),
                              "mean_nonspike": float(gsum.loc[y == 0, g].mean())})
    fr, gr = pd.DataFrame(frows), pd.DataFrame(grows)
    fr.to_csv(OUT / "p1c_shap_features.csv", index=False)
    gr.to_csv(OUT / "p1c_shap_groups.csv", index=False)
    rel = []
    for d in (0, 1):
        v = pred[(pred["model"] == "spikev3") & (pred["days_ahead"] == d) & pred["spike"].notna()]
        for pname, (lo, hi) in PERIODS.items():
            x = v[(v["target_date"] >= lo) & (v["target_date"] <= hi)]
            rel.append(md.reliability(x["p_cal"].to_numpy(), x["spike"].to_numpy(float), pd.to_datetime(x["target_date"]))
                       .assign(days_ahead=d, period=pname))
    rel = pd.concat(rel, ignore_index=True)
    rel.to_csv(OUT / "p1c_reliability.csv", index=False)
    print(rel.round(4).to_string(index=False))
    ins, tst = PERIODS
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        for d in (0, 1):
            f = fr[(fr["days_ahead"] == d) & (fr["period"] == ins) & (fr["feature"] != "bias")]
            print(f"\n== days_ahead {d}: top 12 features by mean |contribution| (in-sample)")
            print(f.nlargest(12, "mean_abs")[["feature", "group", "mean_abs", "mean_spike", "mean_nonspike"]].round(3).to_string(index=False))
            g = gr[gr["days_ahead"] == d].pivot_table(index="group", columns="period", values="mean")
            g["change"] = g[tst] - g[ins]
            print(f"\n== days_ahead {d}: mean contribution by group (log-odds)")
            print(g.round(3).sort_values("change").to_string())


if __name__ == "__main__":
    main()
