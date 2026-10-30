"""spikev3 step 1 (PLAN.md section 3): the new feature groups, the walk-forward models and the calibration choice.

1. Features G1, G2, G4, G5 for every row of the extended spike table (G0 is in the table already).
2. Reproduction: vpp.spikev3.models.walk_forward on C_strict must equal scarcity_signals' C_strict predictions
   row for row (max |diff| < 1e-9), or the run stops.
3. Models per task (days_ahead 0 and 1): C_strict and C_strict + each group.
4. Calibration: raw / Platt / isotonic on C_strict; the lowest in-sample log loss (both tasks pooled) is used for
   every model.
With --combined: spikev3 per task = C_strict + the groups that passed for that task (out/decision_p1.json from
score_p1.py), trained and calibrated the same way and appended to the predictions.

Output: out/p1_features.parquet, out/p1_predictions.parquet, out/p1_calibration_choice.csv, out/p1_meta.json
Run:    .venv/bin/python analysis/2026-10-28/spikev3/run_p1.py [--combined]
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.spike import models as old  # noqa: E402
from vpp.spikev3 import features as fx  # noqa: E402
from vpp.spikev3 import models as md  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
IN_LO, IN_HI = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-06-30")
BASE = old.cols_of(["existing"])
KEEP = ["issue_date", "target_time", "target_date", "days_ahead", "slot", "spike", "rtp"]


def build_features(table: pd.DataFrame) -> pd.DataFrame:
    t = time.time()
    stem = fx.load_stem()
    f1 = fx.g1_stem(table, stem)
    assert f1["_stem_public_ok"].all(), "a STEM record used before it was public"
    f2 = fx.g2_norm(table)
    supply = fx.Supply()
    f4 = fx.g4_rsi(table, supply)
    f5 = fx.g5_wind(table, pd.read_parquet(SCARCITY / "pit" / "weather.parquet"))
    f = pd.concat([f1.drop(columns="_stem_public_ok"), f2, f4, f5], axis=1)
    f.to_parquet(OUT / "p1_features.parquet")
    years = sorted(supply.unmapped_share.index)
    meta = {"unmapped_credit_share_by_year": {int(y): float(supply.unmapped_share[y]) for y in years if y >= 2023}}
    print(f"features: {f.shape[1]} columns in {time.time() - t:.0f} s; missing share (from 2025-04-01): "
          f"{f[table['issue_date'] >= IN_LO].isna().mean().round(3).to_dict()}", flush=True)
    return f, meta


def train(data: pd.DataFrame, jobs: dict) -> pd.DataFrame:
    preds = []
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        for name, cols in jobs.items():
            t = time.time()
            p = md.walk_forward(part, name, cols)
            preds.append(p)
            print(f"  days_ahead {d} {name}: {len(p):,} predictions, {time.time() - t:.0f} s", flush=True)
    return pd.concat(preds, ignore_index=True)


def in_sample(pred: pd.DataFrame, data: pd.DataFrame) -> np.ndarray:
    td = data.loc[pred["row"], "target_date"]
    return ((td >= IN_LO) & (td <= IN_HI) & data.loc[pred["row"], "spike"].notna()).to_numpy()


def calibrate_all(pred: pd.DataFrame, data: pd.DataFrame, method: str) -> np.ndarray:
    out = np.empty(len(pred))
    for (d, m), idx in pred.groupby([data.loc[pred["row"], "days_ahead"].to_numpy(), "model"]).indices.items():
        part = pred.iloc[idx]
        out[idx] = md.calibrate(part, data[data["days_ahead"] == d], method)
    return out


def main() -> None:
    t0 = time.time()
    table = pd.read_parquet(SCARCITY / "spike_table_ext.parquet")
    if "--combined" in sys.argv:
        combined(table)
        return
    feats, meta = build_features(table)
    data = table.join(feats)

    # reproduction of scarcity_signals' C_strict (same table, same split, same settings)
    ref = pd.read_parquet(SCARCITY / "predictions.parquet")
    ref = ref[ref["model"] == "C_strict"].set_index("row")["p_raw"]
    c = train(data, {"C_strict": BASE})
    mine = c.set_index("row")["p_raw"]
    both = mine.index.intersection(ref.index)
    gap = float(np.abs(mine[both] - ref[both]).max())
    meta["reproduction"] = {"rows_compared": len(both), "rows_ref": len(ref), "max_abs_diff": gap}
    print(f"reproduction of C_strict: {len(both):,} of {len(ref):,} rows, max |diff| {gap:.2e}", flush=True)
    if gap >= 1e-9 or len(both) != len(ref):
        sys.exit("reproduction failed: stop (PLAN 3.2)")

    pred = pd.concat([c, train(data, {f"C+{g}": BASE + cols for g, cols in fx.GROUPS.items()})], ignore_index=True)

    # calibration choice on C_strict, in-sample, both tasks pooled
    cs = pred[pred["model"] == "C_strict"].reset_index(drop=True)
    ins = in_sample(cs, data)
    y = data.loc[cs["row"], "spike"].to_numpy(float)
    rows = []
    for m in md.METHODS:
        p = calibrate_all(cs, data, m)
        rows.append({"method": m, "in_sample_logloss": float(md.logloss(p[ins], y[ins]).mean()), "rows": int(ins.sum())})
    choice = pd.DataFrame(rows).sort_values("in_sample_logloss")
    choice.to_csv(OUT / "p1_calibration_choice.csv", index=False)
    method = choice["method"].iloc[0]
    print(choice.to_string(index=False), f"\n-> calibration: {method}", flush=True)

    pred["p_cal"] = calibrate_all(pred, data, method)
    pred = pred.join(data[KEEP], on="row")
    pred.to_parquet(OUT / "p1_predictions.parquet", index=False)
    meta.update(calibration=method, models=sorted(pred["model"].unique()), seconds=round(time.time() - t0))
    (OUT / "p1_meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"saved {len(pred):,} predictions in {time.time() - t0:.0f} s")


def combined(table: pd.DataFrame) -> None:
    decision = json.loads((OUT / "decision_p1.json").read_text())
    meta = json.loads((OUT / "p1_meta.json").read_text())
    data = table.join(pd.read_parquet(OUT / "p1_features.parquet"))
    pred = pd.read_parquet(OUT / "p1_predictions.parquet")
    pred = pred[pred["model"] != "spikev3"]
    new = []
    for d in (0, 1):
        groups = decision["passed"][str(d)]
        if not groups:
            print(f"days_ahead {d}: no group passed, spikev3 = C_strict")
            continue
        cols = BASE + [c for g in groups for c in fx.GROUPS[g]]
        part = data[data["days_ahead"] == d]
        p = md.walk_forward(part, "spikev3", cols)
        p["p_cal"] = md.calibrate(p, part, meta["calibration"])
        new.append(p.join(data[KEEP], on="row"))
        print(f"days_ahead {d}: spikev3 = C_strict + {groups}", flush=True)
    if new:
        pd.concat([pred, *new], ignore_index=True).to_parquet(OUT / "p1_predictions.parquet", index=False)


if __name__ == "__main__":
    main()
