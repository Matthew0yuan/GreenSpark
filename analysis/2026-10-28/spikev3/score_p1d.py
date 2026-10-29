"""Rolling updates scoring (PLAN.md section 7.3). In-sample: S60, S90, S180, D90, C60, C90 each against F with the rule
of score_p1 (Delta = log loss(F) - log loss(candidate); day-block and 7-day block lower bounds > 0; at least 3 of 5
quarters). Chosen per task: the passing candidate with the lowest in-sample log loss, else E. Test periods (report
only, production style): every model against F; the chosen one also against official and base60.

Output: out/p1d_score.csv, out/p1d_summary.csv, out/p1d_reliability.csv, out/decision_p1d.json, NUMBERS.csv (P1D_ rows)
Run:    .venv/bin/python analysis/2026-10-28/spikev3/score_p1d.py
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
from score_p1 import IN, PERIODS, PNAME, TAG  # noqa: E402

OUT = HERE / "out"
BASE = "F"
CANDS = ["S60", "S90", "S180", "D90", "C60", "C90"]
MODELS = ["F", "E", *CANDS, "official", "base60"]


def wide(pred: pd.DataFrame) -> pd.DataFrame:
    w = pred.pivot_table(index="row", columns="model", values="p_cal", aggfunc="first")
    meta = pred.drop_duplicates("row").set_index("row")[["target_date", "spike"]]
    return meta.join(w).dropna(subset=["spike", *MODELS])


def compare(part: pd.DataFrame, a: str, b: str, y, quarters: bool) -> dict:
    dl = md.logloss(part[a].to_numpy(), y) - md.logloss(part[b].to_numpy(), y)
    r = dict(zip(("delta", "day_lo", "day_hi"), md.paired(dl, part["target_date"], "day", 2000)))
    if quarters:
        _, r["week_lo"], r["week_hi"] = md.paired(dl, part["target_date"], "week", 1000)
        q = pd.Series(dl).groupby(part["target_date"].dt.to_period("Q").to_numpy()).mean()
        r["quarters_positive"] = int((q > 0).sum())
        r["pass"] = bool(r["delta"] > 0 and r["day_lo"] > 0 and r["week_lo"] > 0 and r["quarters_positive"] >= 3)
    return r


def main() -> None:
    pred = pd.read_parquet(OUT / "p1d_predictions.parquet")
    pred["target_date"] = pd.to_datetime(pred["target_date"])
    rows, srows, rel, decision = [], [], [], {"chosen": {}, "passed": {}}
    for d in (0, 1):
        sub = pred[pred["days_ahead"] == d]
        w = wide(sub).reset_index()
        tr = sub[(sub["model"] == BASE) & (sub["target_date"] <= md.TRAIN_END)]
        rate = float(tr["spike"].mean())
        ins = w[(w["target_date"] >= PERIODS[IN][0]) & (w["target_date"] <= PERIODS[IN][1])]
        y = ins["spike"].to_numpy(float)
        passed = []
        for m in CANDS:
            r = compare(ins, BASE, m, y, True)
            rows.append({"days_ahead": d, "period": IN, "model": m, "versus": BASE, **r})
            if r["pass"]:
                passed.append(m)
        ll = {m: float(md.logloss(ins[m].to_numpy(), y).mean()) for m in passed}
        chosen = min(ll, key=ll.get) if passed else "E"
        decision["passed"][str(d)], decision["chosen"][str(d)] = passed, chosen
        for pname, (lo, hi) in PERIODS.items():
            part = w[(w["target_date"] >= lo) & (w["target_date"] <= hi)]
            y = part["spike"].to_numpy(float)
            for m in MODELS:
                srows.append({"days_ahead": d, "period": pname, "model": m, **md.summary(part[m].to_numpy(), y, rate)})
                if len(part) > 1000:
                    rel.append(md.reliability(part[m].to_numpy(), y, part["target_date"]).assign(days_ahead=d, period=pname, model=m))
            if pname == IN or y.sum() == 0:
                continue
            for m in MODELS[1:]:
                rows.append({"days_ahead": d, "period": pname, "model": m, "versus": BASE, **compare(part, BASE, m, y, False)})
            for vs in ("official", "base60"):
                if chosen != vs:
                    rows.append({"days_ahead": d, "period": pname, "model": chosen, "versus": vs,
                                 **compare(part, vs, chosen, y, False)})
    res, summ = pd.DataFrame(rows), pd.DataFrame(srows)
    res.to_csv(OUT / "p1d_score.csv", index=False)
    summ.to_csv(OUT / "p1d_summary.csv", index=False)
    pd.concat(rel, ignore_index=True).to_csv(OUT / "p1d_reliability.csv", index=False)
    (OUT / "decision_p1d.json").write_text(json.dumps(decision, indent=1) + "\n")
    with pd.option_context("display.width", 230, "display.max_rows", 300):
        print(res.round(5).to_string(index=False))
        print(summ[["days_ahead", "period", "model", "spikes", "rate", "mean_p", "logloss", "brier_skill", "ap", "recall@5%"]]
              .round(4).to_string(index=False))
    print(json.dumps(decision, indent=1))
    numbers(res, summ)


def numbers(res: pd.DataFrame, summ: pd.DataFrame) -> None:
    path = HERE / "NUMBERS.csv"
    old = pd.read_csv(path)
    old = old[~old["name"].str.startswith("P1D_")]
    out = []

    def add(name, value, unit, period, formula="", note=""):
        out.append({"name": name, "value": value, "unit": unit, "formula": formula, "period": period,
                    "source": "out/p1d_score.csv, out/p1d_summary.csv", "status": "回测", "check": "",
                    "note": note or "生产方式滚动：测试期会用到 2026-06-30 以后已确定的标签"})
    for _, r in summ.iterrows():
        k = f"P1D_{TAG[r['period']]}_D{r['days_ahead']}_{r['model']}"
        add(f"{k}_对数损失", r["logloss"], "1", PNAME[r["period"]])
        add(f"{k}_布里尔技巧分", r["brier_skill"], "1", PNAME[r["period"]])
        add(f"{k}_AP", r["ap"], "1", PNAME[r["period"]])
        add(f"{k}_平均预测概率", 100 * r["mean_p"], "%", PNAME[r["period"]])
        add(f"{k}_实际尖峰率", 100 * r["rate"], "%", PNAME[r["period"]])
    for _, r in res.iterrows():
        t = TAG[r["period"]]
        k = f"P1D_{t}_D{r['days_ahead']}_{r['model']}对{r['versus']}_对数损失改善"
        add(k, r["delta"], "1", PNAME[r["period"]],
            f"{{P1D_{t}_D{r['days_ahead']}_{r['versus']}_对数损失}} - {{P1D_{t}_D{r['days_ahead']}_{r['model']}_对数损失}}")
        add(f"{k}_日块下限", r["day_lo"], "1", PNAME[r["period"]])
        add(f"{k}_日块上限", r["day_hi"], "1", PNAME[r["period"]])
        if pd.notna(r.get("week_lo")):
            add(f"{k}_7天块下限", r["week_lo"], "1", PNAME[r["period"]])
            add(f"{k}_为正的季度数", r["quarters_positive"], "季", PNAME[r["period"]])
    pd.concat([old, pd.DataFrame(out)], ignore_index=True).to_csv(path, index=False)
    print(f"{len(out)} rolling numbers -> NUMBERS.csv")


if __name__ == "__main__":
    main()
