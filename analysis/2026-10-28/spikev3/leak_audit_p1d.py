"""Leak audit of the rolling updates (PLAN.md section 7.4). Per task and kind (S60, D90, E, C60), 6 random refit months M
(seed 0): labels of target days >= M - 2 days are replaced by random 0/1; month M's predictions must not change at all.
Planted leak: the window runs to the end of M (its own labels); after the same scramble, all 6 months must change.

Output: out/p1d_leak_audit.csv; prints PASS / FAIL
Run:    .venv/bin/python analysis/2026-10-28/spikev3/leak_audit_p1d.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.spike import protocol as pc  # noqa: E402
from vpp.spikev3 import rolling as rl  # noqa: E402

sys.path.insert(0, str(HERE))
from run_p1b import base_cols  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
N_MONTHS = 6
FITS = {"S60": {"window": 60}, "D90": {"half_life": 90}, "E": {}}


def differ(a: np.ndarray, b: np.ndarray) -> int:
    return int((np.asarray(a) != np.asarray(b)).sum())


def main() -> None:
    table = pd.read_parquet(SCARCITY / "spike_table_ext.parquet")
    data = table.join(pd.read_parquet(OUT / "p1_features.parquet"))
    e_all = pd.read_parquet(OUT / "p1d_predictions.parquet")
    e_all = e_all[e_all["model"] == "E"]
    months = pd.date_range("2025-04-01", "2026-10-01", freq="MS")
    rng = np.random.default_rng(0)
    rows = []
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        cols = base_cols(d)
        e = e_all[e_all["days_ahead"] == d][["row", "p_raw", "refit_month"]].reset_index(drop=True)
        for kind in list(FITS) + ["C60"]:
            for m in pd.DatetimeIndex(sorted(rng.choice(months, N_MONTHS, replace=False))):
                cut = m - pd.Timedelta(days=pc.EMBARGO_DAYS)
                end = m + pd.offsets.MonthBegin(1)
                sc = part.copy()
                late = sc["target_date"] >= cut
                sc.loc[late & sc["spike"].notna(), "spike"] = rng.integers(0, 2, (late & sc["spike"].notna()).sum()).astype(float)
                if kind in FITS:
                    a = rl.fit_month(part, cols, m, **FITS[kind])["p_raw"].to_numpy()
                    b = rl.fit_month(sc, cols, m, **FITS[kind])["p_raw"].to_numpy()
                    pa = rl.fit_month(part, cols, m, window_end=end, **FITS[kind])["p_raw"].to_numpy()
                    pb = rl.fit_month(sc, cols, m, window_end=end, **FITS[kind])["p_raw"].to_numpy()
                else:
                    # later months' predictions are scrambled too; month M's own raw predictions are its input
                    es = e.copy()
                    on = (e["refit_month"] == m).to_numpy()
                    t = part.loc[es["row"], "target_date"].to_numpy()
                    fut = (t >= cut.to_datetime64()) & ~on
                    es.loc[fut, "p_raw"] = rng.uniform(0, 1, int(fut.sum()))
                    a = rl.calibrate(e, part, 60)[0][on]
                    b = rl.calibrate(es, sc, 60)[0][on]
                    pa = rl.calibrate(e, part, 60, window_end={m: end})[0][on]
                    pb = rl.calibrate(es, sc, 60, window_end={m: end})[0][on]
                rows += [{"days_ahead": d, "kind": kind, "month": m.date(), "check": "A", "changed": differ(a, b), "rows": len(a)},
                         {"days_ahead": d, "kind": kind, "month": m.date(), "check": "P", "changed": differ(pa, pb), "rows": len(pa)}]
                print(d, kind, m.date(), rows[-2]["changed"], rows[-1]["changed"], flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "p1d_leak_audit.csv", index=False)
    a = out[out["check"] == "A"]
    p = out[out["check"] == "P"].groupby(["kind", "days_ahead"])["changed"].apply(lambda s: int((s > 0).sum()))
    ok = (a["changed"] == 0).all() and (p == N_MONTHS).all()
    print(f"A: {int((a['changed'] == 0).sum())} of {len(a)} unchanged; P months caught: {p.to_dict()} of {N_MONTHS}")
    print("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
