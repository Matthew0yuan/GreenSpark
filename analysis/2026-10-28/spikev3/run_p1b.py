"""spikev3 step 1b (PLAN.md section 5): feature combinations and recent surprise, each added to spikev3.

1. Base = spikev3 per task (D+0 = C_strict + G0 + G1, D+1 = C_strict + G0, from out/decision_p1.json). It is
   retrained here and must equal the spikev3 rows of out/p1_predictions.parquet (max |diff| < 1e-9), or the run stops.
2. Features: B stress (vpp.spikev3.combos.stress) and C sur_28, sur_60 (combos.surprise on spikev3's calibrated
   predictions), per task.
3. Candidates per task: spikev3+A (G2 + G4 + G5), spikev3+B (stress), spikev3+C (sur_28, sur_60); walk-forward and
   calibration (out/p1_meta.json) as in step 1.
With --combined: per task where two or more candidates passed (out/decision_p1b.json from score_p1b.py), train
spikev3 + all passing candidates and append it as "spikev3+pass".

Output: out/p1b_features.parquet, out/p1b_stress_params.csv, out/p1b_predictions.parquet, out/p1b_meta.json
Run:    .venv/bin/python analysis/2026-10-28/spikev3/run_p1b.py [--combined]
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
from vpp.spikev3 import combos as cb  # noqa: E402
from vpp.spikev3 import features as fx  # noqa: E402
from vpp.spikev3 import models as md  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
BASE = old.cols_of(["existing"])
KEEP = ["issue_date", "target_time", "target_date", "days_ahead", "slot", "spike", "rtp"]
FX = ["fx_rr_jumps7", "fx_cr_jumps7", "pdx_rr_ratio", "pdx_cr_ratio"]
CANDS = {"A": fx.G2 + fx.G4 + fx.G5, "B": ["stress"], "C": [f"sur_{w}" for w in cb.WINDOWS]}


def base_cols(d: int) -> list[str]:
    groups = json.loads((OUT / "decision_p1.json").read_text())["passed"][str(d)]
    return BASE + [c for g in groups for c in fx.GROUPS[g]]


def load() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    table = pd.read_parquet(SCARCITY / "spike_table_ext.parquet")
    sf = pd.read_parquet(SCARCITY / "scarcity_features.parquet", columns=FX)
    assert len(sf) == len(table) and (sf.index == table.index).all()
    data = table.join(pd.read_parquet(OUT / "p1_features.parquet")).join(sf)
    pred = pd.read_parquet(OUT / "p1_predictions.parquet")
    meta = json.loads((OUT / "p1_meta.json").read_text())
    return data, pred, meta


def main() -> None:
    t0 = time.time()
    data, pred, meta = load()
    if "--combined" in sys.argv:
        combined(data, meta)
        return
    v3 = pred[pred["model"] == "spikev3"]
    out_meta = {"calibration": meta["calibration"], "signals": cb.SIGNALS, "shares": list(cb.SHARES)}

    feats, params, preds = [], [], [v3]
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        cols = base_cols(d)
        assert set(cols) >= set(BASE) and len(cols) == len(set(cols))

        # 1. reproduction of spikev3
        t = time.time()
        p = md.walk_forward(part, "spikev3", cols).set_index("row")["p_raw"]
        ref = v3[v3["days_ahead"] == d].set_index("row")["p_raw"]
        gap = float(np.abs(p.reindex(ref.index) - ref).max())
        out_meta[f"reproduction_d{d}"] = {"rows": len(ref), "max_abs_diff": gap, "n_cols": len(cols)}
        print(f"days_ahead {d}: spikev3 reproduction {len(ref):,} rows, max |diff| {gap:.2e} ({time.time() - t:.0f} s)",
              flush=True)
        if gap >= 1e-9 or p.reindex(ref.index).isna().any():
            sys.exit("reproduction of spikev3 failed: stop (PLAN 5.1)")

        # 2. features
        s, prm = cb.stress(part)
        sur = cb.surprise(part, v3[v3["days_ahead"] == d])
        f = sur.assign(stress=s)
        feats.append(f)
        params.append(prm.assign(days_ahead=d))
        ins = (part["issue_date"] >= "2025-04-01") & (part["issue_date"] <= "2026-06-30")
        out_meta[f"missing_in_sample_d{d}"] = f[ins].isna().mean().round(4).to_dict()
        out_meta[f"signals_used_d{d}"] = prm.groupby("month")["signal"].size().describe().round(2).to_dict()
        print(f"  features missing (in-sample issues): {out_meta[f'missing_in_sample_d{d}']}; "
              f"signals with weight per month: {out_meta[f'signals_used_d{d}']}", flush=True)

        # 3. candidates
        full = part.join(f)
        for k, extra in CANDS.items():
            t = time.time()
            c = md.walk_forward(full, f"spikev3+{k}", cols + extra)
            c["p_cal"] = md.calibrate(c, full, meta["calibration"])
            preds.append(c.join(data[KEEP], on="row"))
            print(f"  spikev3+{k}: {len(c):,} predictions, {time.time() - t:.0f} s", flush=True)

    pd.concat(feats).sort_index().to_parquet(OUT / "p1b_features.parquet")
    pd.concat(params, ignore_index=True).to_csv(OUT / "p1b_stress_params.csv", index=False)
    pd.concat(preds, ignore_index=True).to_parquet(OUT / "p1b_predictions.parquet", index=False)
    out_meta["seconds"] = round(time.time() - t0)
    (OUT / "p1b_meta.json").write_text(json.dumps(out_meta, indent=1, default=str) + "\n")
    print(f"done in {time.time() - t0:.0f} s")


def combined(data: pd.DataFrame, meta: dict) -> None:
    decision = json.loads((OUT / "decision_p1b.json").read_text())
    data = data.join(pd.read_parquet(OUT / "p1b_features.parquet"))
    pred = pd.read_parquet(OUT / "p1b_predictions.parquet")
    pred = pred[pred["model"] != "spikev3+pass"]
    new = []
    for d in (0, 1):
        passed = decision["passed"][str(d)]
        if len(passed) < 2:
            print(f"days_ahead {d}: {len(passed)} candidate(s) passed, nothing to combine")
            continue
        part = data[data["days_ahead"] == d]
        cols = base_cols(d) + [c for k in passed for c in CANDS[k]]
        p = md.walk_forward(part, "spikev3+pass", cols)
        p["p_cal"] = md.calibrate(p, part, meta["calibration"])
        new.append(p.join(data[KEEP], on="row"))
        print(f"days_ahead {d}: spikev3+pass = spikev3 + {passed}", flush=True)
    if new:
        pd.concat([pred, *new], ignore_index=True).to_parquet(OUT / "p1b_predictions.parquet", index=False)


if __name__ == "__main__":
    main()
