"""Walk-forward spike classifiers with and without the scarcity features (PLAN sections 5 and 7).

Step 1 (reproduction): the old table and the old split, C_strict only; its predictions must equal the
published ones in data/processed/spike_predictions.parquet (max |diff| < 1e-9), or the run stops.
Step 2: the extended table with the strict split (TRAIN_END 2026-06-30, test months 2026-07..10), the
models of PLAN section 5. vpp/ is not edited: only the protocol constants are set here at run time.

Output: out/reproduction.csv, out/predictions.parquet (+ out/predictions_seeds.parquet with --seeds)
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/run_models.py [--seeds MODEL ...]
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scarcity_features as sf  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402
from vpp.spike import models as md  # noqa: E402
from vpp.spike import protocol as pc  # noqa: E402

OUT = sf.OUT
TRAIN_END = pd.Timestamp("2026-06-30")
TEST_MONTHS = ("2026-07", "2026-08", "2026-09", "2026-10")
EXISTING = md.cols_of(["existing"])
PRICE_EXTRA = md.cols_of(["price_extra"])
STEM1330 = [f"{c}_1330" for c in sf.STEM]
MAIN = {
    "C_strict": EXISTING,
    "C+fx": EXISTING + sf.FX,
    "C+pdx": EXISTING + sf.PDX,
    "C+stem": EXISTING + sf.STEM,
    "C+all": EXISTING + sf.FX + sf.PDX + sf.STEM,
}
SECONDARY = {
    "C+price_extra": EXISTING + PRICE_EXTRA,
    "C+price_extra+all": EXISTING + PRICE_EXTRA + sf.FX + sf.PDX + sf.STEM,
}
D1_ONLY = {"C+stem1330": EXISTING + STEM1330}     # what-if: issue at 13:30 for STEM only
KEEP = ["issue_date", "issue_time", "target_time", "target_date", "days_ahead", "lead_hours", "slot", "rtp", "max5",
        "spike", "spike300", "any5"]


def reproduce() -> None:
    old = pd.read_parquet(PROCESSED / "spike_table.parquet")
    pub = pd.read_parquet(PROCESSED / "spike_predictions.parquet")
    pub = pub[pub["model"] == "C_strict"].set_index("row")["p_raw"]
    rows = []
    for d in (0, 1):
        p = md.walk_forward(old[old["days_ahead"] == d], "C_strict", EXISTING).set_index("row")["p_raw"]
        both = p.index.intersection(pub.index)
        diff = float(np.abs(p[both] - pub[both]).max())
        rows.append({"days_ahead": d, "rows_new": len(p), "rows_published": int((old.loc[pub.index, "days_ahead"] == d).sum()),
                     "rows_compared": len(both), "max_abs_diff": diff})
        print(f"reproduction days_ahead {d}: {len(both):,} rows compared, max |diff| {diff:.2e}", flush=True)
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "reproduction.csv", index=False)
    if (r["max_abs_diff"] >= 1e-9).any() or (r["rows_compared"] != r["rows_published"]).any():
        sys.exit("reproduction failed: stop (PLAN section 7)")


def data() -> pd.DataFrame:
    table = pd.read_parquet(OUT / "spike_table_ext.parquet")
    f = pd.read_parquet(OUT / "scarcity_features.parquet")
    assert f.index.equals(table.index)
    return table.join(f[sf.FX + sf.PDX + sf.STEM + STEM1330])


def run(t: pd.DataFrame, jobs: dict, tasks=(0, 1), seed: int = 0, suffix: str = "") -> pd.DataFrame:
    md.PARAMS = {**md.PARAMS, "random_state": seed}
    preds = []
    for d in tasks:
        part = t[t["days_ahead"] == d]
        for name, cols in jobs.items():
            p = md.walk_forward(part, name + suffix, cols)
            preds.append(p)
            print(f"days_ahead {d} {name}{suffix}: {len(p):,} predictions, refits {p['refit_month'].nunique()}", flush=True)
    return pd.concat(preds, ignore_index=True).join(t[KEEP], on="row")


def main() -> None:
    if "--seeds" in sys.argv:
        names = sys.argv[sys.argv.index("--seeds") + 1:]
        pc.TRAIN_END, pc.TEST_MONTHS = TRAIN_END, TEST_MONTHS
        t = data()
        jobs = {n: {**MAIN, **SECONDARY}[n] for n in names}
        out = pd.concat([run(t, jobs, seed=s, suffix=f"_seed{s}") for s in (1, 2, 3, 4)], ignore_index=True)
        out.to_parquet(OUT / "predictions_seeds.parquet", index=False)
        return
    reproduce()
    pc.TRAIN_END, pc.TEST_MONTHS = TRAIN_END, TEST_MONTHS
    t = data()
    pred = pd.concat([run(t, {**MAIN, **SECONDARY}), run(t, D1_ONLY, tasks=(1,))], ignore_index=True)
    pred["model_version"] = pred["model"] + f"|spike>={pc.SPIKE:.0f}|train_end={pc.TRAIN_END.date()}|seed0"
    pred.to_parquet(OUT / "predictions.parquet", index=False)
    print(f"saved {len(pred):,} predictions")


if __name__ == "__main__":
    main()
