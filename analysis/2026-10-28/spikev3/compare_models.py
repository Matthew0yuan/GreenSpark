"""Report only (no choice): the spike models side by side on the same half-hours, both tasks, formal split.

  climatology   the training rows' spike rate for each refit month (target days before M - 2 days, <= 2026-06-30)
  official      vpp.spike_risk, the 09:00 model the dispatcher uses (data/processed/spike_risk_history_0900[_d1]),
                walk-forward to 2026-06-30, uncalibrated
  C_strict      the official features rebuilt point-in-time at 09:00, Platt-calibrated (out/p1_predictions)
  spikev3       out/p1_predictions
  aemo_price, yesterday   ranking-only scores: AEMO 08:00 pre-dispatch price, yesterday's same half-hour price
Metrics: log loss, Brier skill against climatology, AP, precision and recall of the top 5% of half-hours, mean p.
Day-block intervals (n=2000, seed 0) for spikev3 against official and C_strict (log loss and AP).
The old interleaved-split study (vpp/spike, labels to 2026-07-16) is left out: it saw part of the test period.

Output: out/compare_models.csv, out/compare_deltas.csv, NUMBERS.csv (rows starting CMP_)
Run:    .venv/bin/python analysis/2026-10-28/spikev3/compare_models.py
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

from vpp.data import PROCESSED  # noqa: E402
from vpp.spike import metrics as sm  # noqa: E402
from vpp.spike import protocol as pc  # noqa: E402
from vpp.spikev3 import models as md  # noqa: E402

sys.path.insert(0, str(HERE))
from score_p1 import PERIODS, PNAME, TAG  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
PROB = ["climatology", "official", "C_strict", "spikev3"]
RANK = ["aemo_price", "yesterday"]
NAME = {"climatology": "常数基准", "official": "官方模型", "C_strict": "C_strict", "spikev3": "spikev3",
        "aemo_price": "AEMO预调度价", "yesterday": "昨天同时段价"}


def climatology(t: pd.DataFrame) -> pd.Series:
    month = t["issue_date"].dt.to_period("M").dt.to_timestamp()
    out = pd.Series(np.nan, index=t.index)
    for m, idx in t.groupby(month).groups.items():
        end = min(m - pd.Timedelta(days=pc.EMBARGO_DAYS), md.TRAIN_END + pd.Timedelta(days=1))
        tr = (t["target_date"] < end) & t["spike"].notna()
        if tr.sum():
            out[idx] = t.loc[tr, "spike"].mean()
    return out


def frame(d: int, table: pd.DataFrame, pred: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    t = table[table["days_ahead"] == d].copy()
    t["climatology"] = climatology(t)
    for m in ("C_strict", "spikev3"):
        p = pred[(pred["model"] == m) & (pred["days_ahead"] == d)].set_index("row")["p_cal"]
        t[m] = p.reindex(t.index)
    off = pd.read_parquet(PROCESSED / f"spike_risk_history_0900{'' if d == 0 else '_d1'}.parquet")
    off = off[off["days_ahead"] == d].set_index(["issue_date", "ts"])
    key = pd.MultiIndex.from_arrays([t["issue_date"], t["target_time"]])
    t["official"] = off["p_spike"].reindex(key).to_numpy()
    act = off["actual_energy"].reindex(key).to_numpy()
    has = ~np.isnan(act) & t["spike"].notna().to_numpy()
    agree = float(((act[has] >= pc.SPIKE) == (t["spike"].to_numpy()[has] == 1)).mean())
    t["aemo_price"], t["yesterday"] = t["aemo_energy"], t["p_prev1"]
    t = t.dropna(subset=["spike", *PROB, *RANK])
    return t, agree


def topk(s: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    k = max(1, int(round(0.05 * len(s))))
    top = np.argsort(-s, kind="stable")[:k]
    return float(y[top].mean()), float(y[top].sum() / max(y.sum(), 1))


def main() -> None:
    cols = ["issue_date", "target_time", "target_date", "days_ahead", "slot", "spike", "rtp", "aemo_energy", "p_prev1"]
    table = pd.read_parquet(SCARCITY / "spike_table_ext.parquet", columns=cols)
    pred = pd.read_parquet(OUT / "p1_predictions.parquet")
    rows, deltas = [], []
    for d in (0, 1):
        t, agree = frame(d, table, pred)
        print(f"days_ahead {d}: {len(t):,} rows with every model; official label agreement {agree:.4%}", flush=True)
        for pname, (lo, hi) in PERIODS.items():
            w = t[(t["target_date"] >= lo) & (t["target_date"] <= hi)].reset_index(drop=True)
            y = w["spike"].to_numpy(float)
            ref = float(((w["climatology"] - y) ** 2).mean())
            for m in PROB + RANK:
                s = w[m].to_numpy(float)
                prec, rec = topk(s, y)
                r = {"days_ahead": d, "period": pname, "model": m, "n": len(w), "days": w["target_date"].nunique(),
                     "spikes": int(y.sum()), "rate": float(y.mean()), "ap": sm.average_precision_score(y, s) if y.sum() else np.nan,
                     "prec@5%": prec, "recall@5%": rec}
                if m in PROB:
                    r.update(mean_p=float(s.mean()), logloss=float(md.logloss(s, y).mean()),
                             brier_skill=1 - float(((s - y) ** 2).mean()) / ref)
                rows.append(r)
            if y.sum() == 0:
                continue
            for b in ("official", "C_strict"):
                dl = md.logloss(w[b].to_numpy(), y) - md.logloss(w["spikev3"].to_numpy(), y)
                ll = md.paired(dl, w["target_date"], "day", 2000)
                ap = sm.bootstrap_ap(w, "spikev3", b, n=2000)
                deltas.append({"days_ahead": d, "period": pname, "versus": b, "logloss_gain": ll[0], "ll_lo": ll[1],
                               "ll_hi": ll[2], "ap_gain": ap[0], "ap_lo": ap[1], "ap_hi": ap[2]})
    res, dlt = pd.DataFrame(rows), pd.DataFrame(deltas)
    res.to_csv(OUT / "compare_models.csv", index=False)
    dlt.to_csv(OUT / "compare_deltas.csv", index=False)
    with pd.option_context("display.width", 220, "display.max_rows", 100):
        print(res.round(4).to_string(index=False))
        print(dlt.round(4).to_string(index=False))
    numbers(res, dlt)


def numbers(res: pd.DataFrame, dlt: pd.DataFrame) -> None:
    path = HERE / "NUMBERS.csv"
    old = pd.read_csv(path)
    old = old[~old["name"].str.startswith("CMP_")]
    out = []

    def add(name, value, unit, period, note=""):
        out.append({"name": name, "value": value, "unit": unit, "formula": "", "period": period,
                    "source": "out/compare_models.csv, out/compare_deltas.csv", "status": "回测", "check": "", "note": note})
    for _, r in res.iterrows():
        k = f"CMP_{TAG[r['period']]}_D{r['days_ahead']}_{NAME[r['model']]}"
        add(f"{k}_AP", r["ap"], "1", PNAME[r["period"]])
        add(f"{k}_前5%召回率", 100 * r["recall@5%"], "%", PNAME[r["period"]])
        add(f"{k}_前5%精确率", 100 * r["prec@5%"], "%", PNAME[r["period"]])
        if pd.notna(r.get("logloss")):
            add(f"{k}_对数损失", r["logloss"], "1", PNAME[r["period"]])
            add(f"{k}_布里尔技巧分", r["brier_skill"], "1", PNAME[r["period"]], "相对常数基准")
            add(f"{k}_平均预测概率", 100 * r["mean_p"], "%", PNAME[r["period"]])
        add(f"{k}_实际尖峰率", 100 * r["rate"], "%", PNAME[r["period"]])
    for _, r in dlt.iterrows():
        k = f"CMP_{TAG[r['period']]}_D{r['days_ahead']}_spikev3对{NAME[r['versus']]}"
        for c, u in (("logloss_gain", "对数损失改善"), ("ll_lo", "对数损失改善下限"), ("ll_hi", "对数损失改善上限"),
                     ("ap_gain", "AP增益"), ("ap_lo", "AP增益下限"), ("ap_hi", "AP增益上限")):
            add(f"{k}_{u}", r[c], "1", PNAME[r["period"]])
    pd.concat([old, pd.DataFrame(out)], ignore_index=True).to_csv(path, index=False)
    print(f"{len(out)} comparison numbers -> NUMBERS.csv")


if __name__ == "__main__":
    main()
