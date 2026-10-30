"""Report only: what the AP change means in practice. For a fixed alert budget (flag the top k share of
target half-hours by score, protocol BUDGETS), the share of flagged half-hours that were spikes (precision)
and the share of spike half-hours flagged (recall), base model against candidate.

Output: out/score_budgets.csv
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/score_budgets.py
"""
import sys
from pathlib import Path
import pandas as pd
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import score as sc  # noqa: E402
from vpp.spike import metrics as mt  # noqa: E402

pred = pd.read_parquet(sc.OUT / "predictions.parquet")
rows = []
for d, cand in ((0, "C+stem"), (1, "C+stem1330")):
    w = sc.frame(pred, [sc.BASE, cand])
    w = w[w["days_ahead"] == d]
    for name, part in (("in-sample 2025-04-01..2026-06-30", sc.in_sample(w)), ("test A 2026-07-01..10-15", sc.segment(w, "2026-07-01", "2026-10-15"))):
        y = part["spike"]
        for k in (0.02, 0.05, 0.10):
            rec = {"days_ahead": d, "candidate": cand, "period": name, "budget": k, "rows": len(part), "flagged": int(round(k * len(part))),
                   "spikes": int(y.sum()), "spike_rate": y.mean()}
            for m, tag in ((sc.BASE, "base"), (cand, "cand")):
                f = mt.budget_flags(part[m], k)
                rec[f"hits_{tag}"] = int(y[f].sum())
                rec[f"precision_{tag}"] = y[f].mean()
                rec[f"recall_{tag}"] = y[f].sum() / y.sum()
            rows.append(rec)
out = pd.DataFrame(rows)
out.to_csv(sc.OUT / "score_budgets.csv", index=False)
print(out.round(3).to_string(index=False))
