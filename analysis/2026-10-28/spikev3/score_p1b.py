"""spikev3 step 1b scoring (PLAN.md section 5.4). Same scoring as score_p1.py with spikev3 as the base:
Delta = log loss(spikev3) - log loss(candidate) > 0 means more accurate probabilities. A candidate passes for a task
(in-sample only) if Delta's day-block (n=2000) and 7-day block (n=1000) 95% interval lower bounds are > 0 and Delta > 0
in at least 3 of the 5 in-sample quarters. The test periods, Brier skill, AP, mean probability against the observed
rate and reliability are reported only. After run_p1b.py --combined, spikev3+pass is confirmed the same way.

Output: out/p1b_score.csv, out/p1b_quarters.csv, out/p1b_summary.csv, out/p1b_reliability.csv, out/decision_p1b.json,
        NUMBERS.csv (step 1b rows replaced)
Run:    .venv/bin/python analysis/2026-10-28/spikev3/score_p1b.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import pandas as pd  # noqa: E402

from vpp.spikev3 import models as md  # noqa: E402

sys.path.insert(0, str(HERE))
from score_p1 import IN, PERIODS, PNAME, TAG, wide  # noqa: E402

OUT = HERE / "out"
BASE = "spikev3"
CANDS = ["spikev3+A", "spikev3+B", "spikev3+C"]


def main() -> None:
    pred = pd.read_parquet(OUT / "p1b_predictions.parquet")
    pred["target_date"] = pd.to_datetime(pred["target_date"])
    combined = "spikev3+pass" in set(pred["model"])
    cands = CANDS + (["spikev3+pass"] if combined else [])
    rows, qrows, srows, rel = [], [], [], []
    train_rate = {}
    for d in (0, 1):
        tr = pred[(pred["model"] == BASE) & (pred["days_ahead"] == d) & (pred["target_date"] <= md.TRAIN_END)]
        train_rate[d] = float(tr["spike"].mean())
    for d in (0, 1):
        models = [BASE] + [m for m in cands if ((pred["model"] == m) & (pred["days_ahead"] == d)).any()]
        w = wide(pred[pred["days_ahead"] == d], models)
        for pname, (lo, hi) in PERIODS.items():
            part = w[(w["target_date"] >= lo) & (w["target_date"] <= hi)]
            y = part["spike"].to_numpy(float)
            for m in models:
                srows.append({"days_ahead": d, "period": pname, "model": m, **md.summary(part[m].to_numpy(), y, train_rate[d])})
                if pname == IN:
                    rel.append(md.reliability(part[m].to_numpy(), y, part["target_date"]).assign(days_ahead=d, model=m))
            if not len(part) or y.sum() == 0:
                continue
            lb = md.logloss(part[BASE].to_numpy(), y)
            for m in models[1:]:
                delta = lb - md.logloss(part[m].to_numpy(), y)
                rec = {"days_ahead": d, "period": pname, "model": m, "n": len(part), "spikes": int(y.sum()),
                       "logloss_base": float(lb.mean()), "logloss_cand": float(md.logloss(part[m].to_numpy(), y).mean())}
                rec["delta"], rec["day_lo"], rec["day_hi"] = md.paired(delta, part["target_date"], "day", 2000)
                if pname == IN:
                    _, rec["week_lo"], rec["week_hi"] = md.paired(delta, part["target_date"], "week", 1000)
                    q = part["target_date"].dt.to_period("Q")
                    pos = 0
                    for k, idx in pd.Series(delta, index=part.index).groupby(q.to_numpy()):
                        qrows.append({"days_ahead": d, "model": m, "quarter": str(k), "delta": float(idx.mean())})
                        pos += idx.mean() > 0
                    rec["quarters_positive"] = int(pos)
                    rec["pass"] = bool(rec["delta"] > 0 and rec["day_lo"] > 0 and rec["week_lo"] > 0 and pos >= 3)
                rows.append(rec)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "p1b_score.csv", index=False)
    pd.DataFrame(qrows).to_csv(OUT / "p1b_quarters.csv", index=False)
    summ = pd.DataFrame(srows)
    summ.to_csv(OUT / "p1b_summary.csv", index=False)
    pd.concat(rel, ignore_index=True).to_csv(OUT / "p1b_reliability.csv", index=False)

    ins = res[res["period"] == IN]
    passed = {str(d): [m.split("+")[1] for m in ins[(ins["days_ahead"] == d) & ins["pass"] & ins["model"].isin(CANDS)]["model"]]
              for d in (0, 1)}
    decision = {"base": BASE, "passed": passed, "comparisons": int(ins["model"].isin(CANDS).sum())}
    if combined:
        decision["pass_confirmed"] = {str(d): bool(ins[(ins["days_ahead"] == d) & (ins["model"] == "spikev3+pass")]["pass"].any())
                                      for d in (0, 1)}
    (OUT / "decision_p1b.json").write_text(json.dumps(decision, indent=1) + "\n")

    cols = ["days_ahead", "period", "model", "spikes", "logloss_base", "logloss_cand", "delta", "day_lo", "day_hi",
            "week_lo", "week_hi", "quarters_positive", "pass"]
    with pd.option_context("display.width", 230, "display.max_rows", 200):
        print(res[[c for c in cols if c in res]].to_string(index=False, float_format=lambda x: f"{x:.5f}"))
        print("\n== summary")
        print(summ.round(4).to_string(index=False))
    print("\n== decision\n" + json.dumps(decision, indent=1))
    numbers(res, summ)


def numbers(res: pd.DataFrame, summ: pd.DataFrame) -> None:
    """Replace the step-1b rows of NUMBERS.csv (other rows kept)."""
    path = HERE / "NUMBERS.csv"
    old = pd.read_csv(path)
    old = old[~old["name"].str.startswith("P1B_")]
    out = []

    def add(name, value, unit, period, formula="", note=""):
        out.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period,
                    "source": "out/p1b_score.csv, out/p1b_summary.csv", "status": "回测", "check": "", "note": note})
    for _, r in summ.iterrows():
        k = f"P1B_{TAG[r['period']]}_D{r['days_ahead']}_{r['model']}"
        add(f"{k}_对数损失", r["logloss"], "1", PNAME[r["period"]])
        add(f"{k}_布里尔技巧分", r["brier_skill"], "1", PNAME[r["period"]])
        add(f"{k}_AP", r["ap"], "1", PNAME[r["period"]])
        add(f"{k}_平均预测概率", 100 * r["mean_p"], "%", PNAME[r["period"]])
        add(f"{k}_实际尖峰率", 100 * r["rate"], "%", PNAME[r["period"]])
    for _, r in res.iterrows():
        t = TAG[r["period"]]
        k = f"P1B_{t}_D{r['days_ahead']}_{r['model']}_对数损失改善"
        add(k, r["delta"], "1", PNAME[r["period"]],
            f"{{P1B_{t}_D{r['days_ahead']}_{BASE}_对数损失}} - {{P1B_{t}_D{r['days_ahead']}_{r['model']}_对数损失}}",
            "spikev3 减候选，正数表示候选的概率更准")
        add(f"{k}_日块下限", r["day_lo"], "1", PNAME[r["period"]])
        add(f"{k}_日块上限", r["day_hi"], "1", PNAME[r["period"]])
        if pd.notna(r.get("week_lo")):
            add(f"{k}_7天块下限", r["week_lo"], "1", PNAME[r["period"]])
            add(f"{k}_7天块上限", r["week_hi"], "1", PNAME[r["period"]])
            add(f"{k}_为正的季度数", r["quarters_positive"], "季", PNAME[r["period"]])
    pd.concat([old, pd.DataFrame(out)], ignore_index=True).to_csv(path, index=False)
    print(f"{len(out)} step-1b numbers -> NUMBERS.csv")


if __name__ == "__main__":
    main()
