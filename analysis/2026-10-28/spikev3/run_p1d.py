"""spikev3 rolling updates (PLAN.md section 7): the candidates of 7.2 for both tasks, production style.

  F      spikev3 as frozen at the cut-off (out/p1_predictions.parquet)
  E      monthly refit on all history, Platt on the last 12 months, no cut-off
  S60/S90/S180   monthly refit on the last 60/90/180 days, uncalibrated
  D90    monthly refit on all history weighted 0.5 ** (age / 90 days), uncalibrated
  C60/C90        E's model, Platt on the last 60/90 days
  official       vpp.spike_risk (frozen, uncalibrated), reference
  base60         the spike rate of the last 60 days, reference
Checks: vpp.spikev3.rolling.walk_forward with train_end = 2026-06-30 reproduces F's raw probabilities row for row, and E
equals F in every month up to 2026-07 (raw and calibrated); otherwise the run stops.

Output: out/p1d_predictions.parquet, out/p1d_meta.json
Run:    .venv/bin/python analysis/2026-10-28/spikev3/run_p1d.py
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

from vpp.data import PROCESSED  # noqa: E402
from vpp.spikev3 import models as md  # noqa: E402
from vpp.spikev3 import rolling as rl  # noqa: E402

sys.path.insert(0, str(HERE))
from run_p1b import KEEP, base_cols  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
WINDOWS = (60, 90, 180)
HALF_LIFE = 90
CAL_WINDOWS = (60, 90)
LAST_SAME = pd.Timestamp("2026-07-01")      # E equals F for refit months up to here


def official(part: pd.DataFrame, d: int) -> pd.DataFrame:
    off = pd.read_parquet(PROCESSED / f"spike_risk_history_0900{'' if d == 0 else '_d1'}.parquet")
    off = off[off["days_ahead"] == d].set_index(["issue_date", "ts"])["p_spike"]
    p = off.reindex(pd.MultiIndex.from_arrays([part["issue_date"], part["target_time"]])).to_numpy()
    return pd.DataFrame({"row": part.index, "p_raw": p, "p_cal": p}).dropna()


def main() -> None:
    t0 = time.time()
    table = pd.read_parquet(SCARCITY / "spike_table_ext.parquet")
    data = table.join(pd.read_parquet(OUT / "p1_features.parquet"))
    p1 = pd.read_parquet(OUT / "p1_predictions.parquet")
    meta, preds = {}, []
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        cols = base_cols(d)
        f = p1[(p1["model"] == "spikev3") & (p1["days_ahead"] == d)][["row", "p_raw", "p_cal", "refit_month"]]
        preds.append(f.assign(model="F"))

        # reproduction of F with the new code, then E (no cut-off)
        t = time.time()
        rep = rl.walk_forward(part, "F_rep", cols, train_end=md.TRAIN_END).set_index("row")["p_raw"]
        gap = float(np.abs(rep.reindex(f["row"]).to_numpy() - f["p_raw"].to_numpy()).max())
        e = rl.walk_forward(part, "E", cols)
        e["p_cal"] = md.calibrate(e, part, "platt", rl.NO_CUT)
        same = e[e["refit_month"] <= LAST_SAME].set_index("row")
        fs = f.set_index("row").reindex(same.index)
        gap_e = float(max(np.abs(same["p_raw"] - fs["p_raw"]).max(), np.abs(same["p_cal"] - fs["p_cal"]).max()))
        meta[f"d{d}"] = {"reproduction_F_max_diff": gap, "E_equals_F_to_2026-07_max_diff": gap_e}
        print(f"days_ahead {d}: F reproduction max |diff| {gap:.1e}; E vs F to 2026-07 {gap_e:.1e} ({time.time() - t:.0f} s)",
              flush=True)
        if gap >= 1e-9 or gap_e >= 1e-9:
            sys.exit("reproduction failed: stop (PLAN 7.2)")
        preds.append(e)

        for w in WINDOWS:
            t = time.time()
            s = rl.walk_forward(part, f"S{w}", cols, window=w)
            s["p_cal"] = s["p_raw"]
            preds.append(s)
            ext = s.drop_duplicates("refit_month")
            meta[f"d{d}"][f"S{w}_months_extended"] = int((ext["train_days"] > w).sum())
            meta[f"d{d}"][f"S{w}_train_pos_median"] = float(ext["train_pos"].median())
            print(f"  S{w}: {len(s):,} rows, {(ext['train_days'] > w).sum()} of {len(ext)} months extended, "
                  f"median spikes in window {ext['train_pos'].median():.0f} ({time.time() - t:.0f} s)", flush=True)
        t = time.time()
        dd = rl.walk_forward(part, f"D{HALF_LIFE}", cols, half_life=HALF_LIFE)
        dd["p_cal"] = dd["p_raw"]
        preds.append(dd)
        print(f"  D{HALF_LIFE}: {len(dd):,} rows ({time.time() - t:.0f} s)", flush=True)
        for w in CAL_WINDOWS:
            c = e.drop(columns=["p_cal"]).assign(model=f"C{w}")
            c["p_cal"], used = rl.calibrate(c, part, w)
            preds.append(c)
            meta[f"d{d}"][f"C{w}_months_extended"] = int(sum(1 for v in used.values() if v and v > w))
            meta[f"d{d}"][f"C{w}_months_raw"] = int(sum(1 for v in used.values() if v is None))
        b = rl.base_rate(part, 60)
        preds.append(b.assign(p_cal=b["p_raw"], model="base60"))
        preds.append(official(part, d).assign(model="official"))

    out = pd.concat(preds, ignore_index=True).join(data[KEEP], on="row")
    out.to_parquet(OUT / "p1d_predictions.parquet", index=False)
    meta["seconds"] = round(time.time() - t0)
    (OUT / "p1d_meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
