"""Scores and the pass rule of PLAN section 5, on out/predictions.parquet.

In-sample = target trading days 2025-04-01 .. 2026-06-30 (decides). Test, reported once and never used to
decide: A = 2026-07-01 .. 10-15 (already looked at in earlier work), B = 10-16 .. 10-22 (never looked at).
All AP numbers are on one comparable sample per task: rows every compared model scores.

Output: out/score_main.csv, out/score_quarters.csv, out/score_test.csv, out/score_secondary.csv,
        out/score_any5.csv, out/score_seeds.csv (if out/predictions_seeds.parquet exists)
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/score.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_models as rm  # noqa: E402
from vpp.spike import metrics as mt  # noqa: E402
from vpp.spike import protocol as pc  # noqa: E402

OUT = rm.OUT
BASE = "C_strict"
CANDIDATES = ["C+fx", "C+pdx", "C+stem", "C+all"]
IN_END = pd.Timestamp("2026-06-30")
SEGMENTS = {"test A 2026-07-01..10-15": ("2026-07-01", "2026-10-15"), "test B 2026-10-16..10-22": ("2026-10-16", "2026-10-22")}
N_DAY, N_WEEK = 2000, 1000


def frame(pred: pd.DataFrame, models: list[str], label: str = "spike") -> pd.DataFrame:
    pc.TEST_MONTHS = rm.TEST_MONTHS
    w = mt.wide(pred, models, label)
    w, _ = mt.comparable(w, models)
    return w


def ap(w: pd.DataFrame, m: str, label: str = "spike") -> float:
    return average_precision_score(w[label], w[m]) if w[label].sum() else np.nan


def gain(w: pd.DataFrame, a: str, b: str, label: str = "spike", week: bool = True) -> dict:
    rec = {"n": len(w), "days": w["target_date"].nunique(), "spikes": int(w[label].sum()),
           f"ap_{b}": ap(w, b, label), "ap_cand": ap(w, a, label)}
    if w[label].sum() == 0:
        return rec
    rec["gain"], rec["day_lo"], rec["day_hi"] = mt.bootstrap_ap(w, a, b, label, n=N_DAY, block="day", seed=0)
    if week:
        _, rec["week_lo"], rec["week_hi"] = mt.bootstrap_ap(w, a, b, label, n=N_WEEK, block="week", seed=0)
    return rec


def in_sample(w: pd.DataFrame) -> pd.DataFrame:
    return w[(w["period"] == "validation") & (w["target_date"] <= IN_END)]


def segment(w: pd.DataFrame, lo: str, hi: str) -> pd.DataFrame:
    return w[(w["target_date"] >= lo) & (w["target_date"] <= hi)]


def main() -> None:
    pred = pd.read_parquet(OUT / "predictions.parquet")
    main_models = [BASE] + CANDIDATES
    w = frame(pred, main_models)
    rows, qrows, trows = [], [], []
    for d in (0, 1):
        wd = w[w["days_ahead"] == d]
        ins = in_sample(wd)
        q = ins["target_date"].dt.to_period("Q")
        for c in CANDIDATES:
            rec = {"days_ahead": d, "model": c, "period": "in-sample 2025-04-01..2026-06-30", **gain(ins, c, BASE)}
            pos = 0
            for k, g in ins.groupby(q):
                gq = ap(g, c) - ap(g, BASE)
                pos += gq > 0
                qrows.append({"days_ahead": d, "model": c, "quarter": str(k), "days": g["target_date"].nunique(),
                              "spikes": int(g["spike"].sum()), "ap_base": ap(g, BASE), "ap_cand": ap(g, c), "gain": gq})
            rec["quarters_positive"] = int(pos)
            rec["quarters"] = int(q.nunique())
            rec["pass"] = bool(rec["day_lo"] > 0 and rec["week_lo"] > 0 and pos >= 3)
            rows.append(rec)
            for name, (lo, hi) in SEGMENTS.items():
                trows.append({"days_ahead": d, "model": c, "period": name, **gain(segment(wd, lo, hi), c, BASE, week=False)})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "score_main.csv", index=False)
    pd.DataFrame(qrows).to_csv(OUT / "score_quarters.csv", index=False)
    pd.DataFrame(trows).to_csv(OUT / "score_test.csv", index=False)
    cols = ["days_ahead", "model", "n", "spikes", f"ap_{BASE}", "ap_cand", "gain", "day_lo", "day_hi", "week_lo", "week_hi",
            "quarters_positive", "pass"]
    print("== in-sample (decides)\n" + res[cols].round(4).to_string(index=False))
    print("== test (report only)\n" + pd.DataFrame(trows).round(4).to_string(index=False))

    sec = []
    w2 = frame(pred, ["C+price_extra", "C+price_extra+all"])
    w3 = frame(pred, [BASE, "C+stem", "C+stem1330"])
    for d in (0, 1):
        jobs = [(w2[w2["days_ahead"] == d], "C+price_extra+all", "C+price_extra")]
        if d == 1:
            jobs += [(w3[w3["days_ahead"] == 1], "C+stem1330", BASE), (w3[w3["days_ahead"] == 1], "C+stem1330", "C+stem")]
        for wd, a, b in jobs:
            for name, part in [("in-sample 2025-04-01..2026-06-30", in_sample(wd))] + [(n, segment(wd, lo, hi)) for n, (lo, hi) in SEGMENTS.items()]:
                g = gain(part, a, b, week=name.startswith("in-sample"))
                sec.append({"days_ahead": d, "model": a, "versus": b, "period": name, **{k.replace(f"ap_{b}", "ap_versus"): v for k, v in g.items()}})
    sec = pd.DataFrame(sec)
    sec.to_csv(OUT / "score_secondary.csv", index=False)
    print("== secondary (report only)\n" + sec.round(4).to_string(index=False))

    a5 = []
    w5 = frame(pred, main_models, label="any5")
    for d in (0, 1):
        wd = w5[w5["days_ahead"] == d]
        for c in CANDIDATES:
            for name, part in [("in-sample 2025-04-01..2026-06-30", in_sample(wd))] + [(n, segment(wd, lo, hi)) for n, (lo, hi) in SEGMENTS.items()]:
                a5.append({"days_ahead": d, "model": c, "period": name, **gain(part, c, BASE, label="any5", week=False)})
    a5 = pd.DataFrame(a5)
    a5.to_csv(OUT / "score_any5.csv", index=False)
    print("== any5 label (report only)\n" + a5.round(4).to_string(index=False))

    seeds = OUT / "predictions_seeds.parquet"
    if seeds.exists():
        ps = pd.read_parquet(seeds)
        srows = []
        for s in (1, 2, 3, 4):
            names = sorted({m.rsplit("_seed", 1)[0] for m in ps["model"] if m.endswith(f"_seed{s}")})
            if BASE not in names:
                continue
            p = ps[ps["model"].str.endswith(f"_seed{s}")].assign(model=lambda x: x["model"].str.replace(f"_seed{s}", "", regex=False))
            ws = frame(p, names)
            for d in (0, 1):
                ins = in_sample(ws[ws["days_ahead"] == d])
                for c in [n for n in names if n != BASE]:
                    srows.append({"seed": s, "days_ahead": d, "model": c, "gain": ap(ins, c) - ap(ins, BASE)})
        pd.DataFrame(srows).to_csv(OUT / "score_seeds.csv", index=False)
        print("== seeds 1-4, in-sample AP gain\n" + pd.DataFrame(srows).round(4).to_string(index=False))


if __name__ == "__main__":
    main()
