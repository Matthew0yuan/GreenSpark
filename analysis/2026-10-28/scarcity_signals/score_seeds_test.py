"""Report only: AP gain of C+stem over C_strict on test segment A for seeds 1-4 (seed 0 is in score_test.csv).

Output: out/score_seeds_test.csv
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/score_seeds_test.py
"""
import sys
from pathlib import Path
import pandas as pd
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import score as sc  # noqa: E402

ps = pd.read_parquet(sc.OUT / "predictions_seeds.parquet")
rows = []
for s in (1, 2, 3, 4):
    p = ps[ps["model"].str.endswith(f"_seed{s}")].assign(model=lambda x: x["model"].str.replace(f"_seed{s}", "", regex=False))
    w = sc.frame(p, [sc.BASE, "C+stem"])
    for d in (0, 1):
        part = sc.segment(w[w["days_ahead"] == d], "2026-07-01", "2026-10-15")
        rows.append({"seed": s, "days_ahead": d, "period": "test A 2026-07-01..10-15", "gain": sc.ap(part, "C+stem") - sc.ap(part, sc.BASE)})
out = pd.DataFrame(rows)
out.to_csv(sc.OUT / "score_seeds_test.csv", index=False)
print(out.round(4).to_string(index=False))
