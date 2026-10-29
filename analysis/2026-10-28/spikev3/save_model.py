"""Save spikev3 as the research spike model (user's decision 2026-10-28: keep the frozen spikev3 as the best model).

Per task (D+0 = C_strict + G0 + G1, D+1 = C_strict + G0):
  booster    LightGBM (vpp.spike_risk.CLF_PARAMS, n_jobs=1, seed 0) on every label with target day <= 2026-06-30,
             i.e. the walk-forward's model for the refit months 2026-08 onward
  calibrator Platt on spikev3's own out-of-sample predictions with target days 2025-10-01 .. 2026-06-30, i.e. the
             walk-forward's calibrator for the refit month 2026-10
Checks (the run stops otherwise): the saved files, loaded through vpp.spikev3.saved, reproduce the stored spikev3 raw
probabilities of 2026-08 .. 2026-10 and the calibrated probabilities of 2026-10 (out/p1_predictions.parquet).

Output: model/spikev3_d0.txt, model/spikev3_d1.txt, model/spikev3.json, NUMBERS.csv (rows starting SAVE_)
Run:    .venv/bin/python analysis/2026-10-28/spikev3/save_model.py
"""

import json
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
from sklearn.linear_model import LogisticRegression  # noqa: E402

from vpp.spike_risk import CLF_PARAMS  # noqa: E402
from vpp.spikev3 import models as md  # noqa: E402
from vpp.spikev3 import saved  # noqa: E402

sys.path.insert(0, str(HERE))
from run_p1b import base_cols  # noqa: E402

OUT = HERE / "out"
MODEL = HERE / "model"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
END = md.TRAIN_END + pd.Timedelta(days=1)
CAL_MONTH = pd.Timestamp("2026-10-01")
CAL_LO = CAL_MONTH - pd.DateOffset(months=md.CAL_MONTHS)


def main() -> None:
    MODEL.mkdir(exist_ok=True)
    data = pd.read_parquet(SCARCITY / "spike_table_ext.parquet").join(pd.read_parquet(OUT / "p1_features.parquet"))
    p1 = pd.read_parquet(OUT / "p1_predictions.parquet")
    spec, nums = {}, []
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        cols = base_cols(d)
        tr = (part["target_date"] < END) & part["spike"].notna()
        y = part.loc[tr, "spike"].astype(int)
        clf = lgb.LGBMClassifier(**{**CLF_PARAMS, "n_jobs": 1, "random_state": 0}).fit(
            part.loc[tr, cols], y, sample_weight=np.where(y == 1, 1.0, 1.0))
        clf.booster_.save_model(str(MODEL / f"spikev3_d{d}.txt"))

        v3 = p1[(p1["model"] == "spikev3") & (p1["days_ahead"] == d)]
        tday = pd.to_datetime(v3["target_date"])
        h = ((tday >= CAL_LO) & (tday < END) & v3["spike"].notna()).to_numpy()
        q = np.clip(v3["p_raw"].to_numpy(), md.EPS, 1 - md.EPS)
        lr = LogisticRegression(C=1e6).fit(np.log(q / (1 - q))[h][:, None], v3["spike"].to_numpy()[h].astype(int))
        spec[f"d{d}"] = {
            "booster": f"spikev3_d{d}.txt", "features": cols, "label": "30-minute RTP >= $200/MWh",
            "issue": "09:00 AWST on the issue day; D+0 covers 09:00-08:00, D+1 the next trading day",
            "train": {"target_days": [str(part.loc[tr, "target_date"].min().date()), str(md.TRAIN_END.date())],
                      "rows": int(tr.sum()), "spikes": int(y.sum())},
            "platt": {"coef": float(lr.coef_[0, 0]), "intercept": float(lr.intercept_[0]),
                      "fit_target_days": [str(CAL_LO.date()), str(md.TRAIN_END.date())], "rows": int(h.sum()),
                      "spikes": int(v3["spike"].to_numpy()[h].sum())},
        }
    (MODEL / "spikev3.json").write_text(json.dumps(spec, indent=1) + "\n")

    # reproduce the stored walk-forward predictions from the saved files
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        v3 = p1[(p1["model"] == "spikev3") & (p1["days_ahead"] == d)].set_index("row")
        late = v3[v3["refit_month"] >= "2026-08-01"]
        oct_ = v3[v3["refit_month"] == CAL_MONTH]
        g_raw = float(np.abs(saved.predict(part.loc[late.index], d, calibrated=False) - late["p_raw"].to_numpy()).max())
        g_cal = float(np.abs(saved.predict(part.loc[oct_.index], d) - oct_["p_cal"].to_numpy()).max())
        print(f"days_ahead {d}: raw 2026-08..10 {len(late):,} rows max |diff| {g_raw:.1e}; "
              f"calibrated 2026-10 {len(oct_):,} rows max |diff| {g_cal:.1e}", flush=True)
        if g_raw >= 1e-9 or g_cal >= 1e-9:
            sys.exit("the saved model does not reproduce spikev3: stop")
        per = "测试期 2026-08-01 至 2026-10-22 发出的行（原始）、2026-10 发出的行（校准后）"
        for name, v in (("原始概率复现最大差", g_raw), ("校准后概率复现最大差", g_cal)):
            nums.append({"name": f"SAVE_D{d}_{name}", "value": v, "unit": "1", "formula": "", "period": per,
                         "source": "save_model.py", "status": "回测", "check": "", "note": "保存的模型对存下的 spikev3 预测"})
    path = HERE / "NUMBERS.csv"
    old = pd.read_csv(path)
    pd.concat([old[~old["name"].str.startswith("SAVE_")], pd.DataFrame(nums)], ignore_index=True).to_csv(path, index=False)
    print(json.dumps({k: {"features": len(v["features"]), "train": v["train"], "platt": v["platt"]} for k, v in spec.items()},
                     indent=1))


if __name__ == "__main__":
    main()
