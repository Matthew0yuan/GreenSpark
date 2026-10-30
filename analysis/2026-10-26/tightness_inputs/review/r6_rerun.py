"""R-A review: re-run the delivered build_inputs.main() and leak_audit.main() with OUT redirected to review/rerun/, then
compare every output with the delivered out/ files (the delivered out/ is never written).
Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

TASK = Path("/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-26/tightness_inputs")
sys.path.insert(0, str(TASK))
RERUN = TASK / "review" / "rerun"
RERUN.mkdir(parents=True, exist_ok=True)

import build_inputs as bi  # noqa: E402

bi.OUT = RERUN
bi.main()
import leak_audit as la  # noqa: E402

la.main()


def same_frame(a: pd.DataFrame, b: pd.DataFrame) -> str:
    if list(a.columns) != list(b.columns) or a.shape != b.shape:
        return f"shape/columns differ {a.shape} vs {b.shape}"
    bad = []
    for c in a.columns:
        x, y = a[c], b[c]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y) and not pd.api.types.is_bool_dtype(x):
            eq = (x.isna() & y.isna()) | ((x - y).abs() <= 1e-9)
        else:
            eq = (x.isna() & y.isna()) | (x.astype(str) == y.astype(str))
        if not eq.all():
            bad.append(f"{c}:{int((~eq).sum())}")
    return "identical" if not bad else "differs " + ", ".join(bad)


rows = []
for f in ("inputs_d1.parquet", "inputs_d1_30min.parquet"):
    rows.append({"file": f, "result": same_frame(pd.read_parquet(TASK / "out" / f), pd.read_parquet(RERUN / f))})
for f in ("lrc_days.csv", "lrc_advisories.csv", "coverage.csv", "leak_audit.csv"):
    rows.append({"file": f, "result": same_frame(pd.read_csv(TASK / "out" / f), pd.read_csv(RERUN / f))})
out = pd.DataFrame(rows)
out.to_csv(TASK / "review" / "r6_rerun.csv", index=False)
print(out.to_string(index=False))
