"""R-A review: re-run forecast.main(), selfcheck.main(), diag.main() with OUT redirected to review/rerun/ and compare
with the delivered out/ (never written). Then F4: scrambled future on EVERY month (not just the three of T1): labels
of target days after the month's training end + 9,999 -> that month's M1 / M2 must not move; and the whole test
period with every label from 2026-06-27 on scrambled.
Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

TASK = Path("/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-26/tightness_forecast")
sys.path.insert(0, str(TASK))
RERUN = TASK / "review" / "rerun"
RERUN.mkdir(parents=True, exist_ok=True)

import forecast as fc  # noqa: E402

fc.OUT = RERUN
fc.main()
import selfcheck  # noqa: E402

selfcheck.main()
import diag  # noqa: E402

diag.main()


def same_csv(a: Path, b: Path) -> str:
    x, y = pd.read_csv(a), pd.read_csv(b)
    if list(x.columns) != list(y.columns) or x.shape != y.shape:
        return f"shape/columns differ {x.shape} vs {y.shape}"
    bad = []
    for c in x.columns:
        if pd.api.types.is_numeric_dtype(x[c]) and pd.api.types.is_numeric_dtype(y[c]) and not pd.api.types.is_bool_dtype(x[c]):
            eq = (x[c].isna() & y[c].isna()) | ((x[c] - y[c]).abs() <= 1e-9)
        else:
            eq = (x[c].isna() & y[c].isna()) | (x[c].astype(str) == y[c].astype(str))
        if not eq.all():
            bad.append(f"{c}:{int((~eq).sum())}")
    return "identical" if not bad else "differs " + ", ".join(bad)


rows = [{"file": p.name, "result": same_csv(TASK / "out" / p.name, p)} for p in sorted(RERUN.glob("*.csv"))]
import json  # noqa: E402

a, b = json.load(open(TASK / "out" / "summary.json")), json.load(open(RERUN / "summary.json"))
rows.append({"file": "summary.json", "result": "identical" if a == b else "differs"})
a, b = json.load(open(TASK / "out" / "diag.json")), json.load(open(RERUN / "diag.json"))
rows.append({"file": "diag.json", "result": "identical" if a == b else "differs"})

# F4 every month
d, h = fc.load()
total_vals = total_changed = 0
for m in pd.period_range(d.index.min(), d.index.max(), freq="M"):
    fm = m if m <= fc.FROZEN_MONTH else fc.FROZEN_MONTH
    te = fc.month_train_end(fm)
    days = d.index[(d.index >= m.start_time) & (d.index <= m.end_time)]
    base = fc.predict(days, d, h, fc.fit(d, h, te))
    d2, h2 = selfcheck.scrambled(d, h, te)
    scr = fc.predict(days, d2, h2, fc.fit(d2, h2, te))
    if len(base) == 0:
        continue
    n_ch, n_val = selfcheck.changed(base, scr)
    total_vals += n_val
    total_changed += n_ch
rows.append({"file": "F4 scrambled future, every month", "result": f"{total_changed} of {total_vals} predicted values changed"})
# test period from walk_forward with everything after 2026-06-26 scrambled
p0, _ = fc.walk_forward(d, h)
d2, h2 = selfcheck.scrambled(d, h, pd.Timestamp("2026-06-26"))
p1, _ = fc.walk_forward(d2, h2)
t = p0.index[p0.index >= "2026-07-01"]
n_ch, n_val = selfcheck.changed(p0.loc[t], p1.loc[t])
rows.append({"file": "F4 test period, labels after 2026-06-26 scrambled", "result": f"{n_ch} of {n_val} changed"})
out = pd.DataFrame(rows)
out.to_csv(TASK / "review" / "f2_rerun.csv", index=False)
pd.set_option("display.width", 250)
print(out.to_string(index=False))
