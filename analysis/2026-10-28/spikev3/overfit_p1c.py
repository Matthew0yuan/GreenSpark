"""spikev3 overfitting checks (PLAN.md sections 6.1 and 6.2), in-sample only (2025-04-01 .. 2026-06-30).

1. Placebo: for each issue day D, the features of a random issue day 60-365 days earlier (same task and slot), seeds
   1-20; G0 (11 features, both tasks) and G1 (4 features, D+0) added to C_strict, trained and scored like step 1.
   Reads: how many of the 60 placebo comparisons pass, and where the real G0 / G1 improvements rank among them.
2. Split: re-run the selection on 2025-04-01 .. 2025-12-31 (calibration choice, 5 groups x 2 tasks, at least 2 of 3
   quarters), then score the selected model against C_strict and the official model on 2026-01-01 .. 2026-06-30.

Output: out/p1c_placebo.csv, out/p1c_split.csv, out/decision_p1c.json
Run:    .venv/bin/python analysis/2026-10-28/spikev3/overfit_p1c.py
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.data import PROCESSED  # noqa: E402
from vpp.spike import models as old  # noqa: E402
from vpp.spikev3 import features as fx  # noqa: E402
from vpp.spikev3 import models as md  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
BASE = old.cols_of(["existing"])
IN = ("2025-04-01", "2026-06-30")
SEL = ("2025-04-01", "2025-12-31")
CHK = ("2026-01-01", "2026-06-30")
SEEDS = range(1, 21)
LAG = (60, 365)
PLACEBO = {"G0": (0, 1), "G1": (0,)}


def delta_stats(base: np.ndarray, cand: np.ndarray, y: np.ndarray, days: pd.Series, min_q: int) -> dict:
    """Same rule as score_p1: Delta, day and 7-day block intervals, quarters with Delta > 0, pass."""
    dl = md.logloss(base, y) - md.logloss(cand, y)
    r = dict(zip(("delta", "day_lo", "day_hi"), md.paired(dl, days, "day", 2000)))
    _, r["week_lo"], r["week_hi"] = md.paired(dl, days, "week", 1000)
    q = pd.Series(dl).groupby(pd.DatetimeIndex(days).to_period("Q").to_numpy()).mean()
    r["quarters_positive"], r["quarters"] = int((q > 0).sum()), len(q)
    r["pass"] = bool(r["delta"] > 0 and r["day_lo"] > 0 and r["week_lo"] > 0 and r["quarters_positive"] >= min_q)
    return r


def placebo_cols(part: pd.DataFrame, cols: list[str], seed: int) -> pd.DataFrame:
    """Each issue day's rows take the values of a random issue day 60-365 days earlier (same slot)."""
    rng = np.random.default_rng(seed)
    days = pd.DatetimeIndex(sorted(part["issue_date"].unique()))
    src = {}
    for d in days:
        pool = days[(days >= d - pd.Timedelta(days=LAG[1])) & (days <= d - pd.Timedelta(days=LAG[0]))]
        src[d] = pool[rng.integers(len(pool))] if len(pool) else pd.NaT
    lookup = pd.Series(np.arange(len(part)), index=pd.MultiIndex.from_arrays([part["issue_date"], part["slot"]]))
    key = pd.MultiIndex.from_arrays([part["issue_date"].map(src), part["slot"]])
    pos = lookup.reindex(key).to_numpy()
    vals = part[cols].to_numpy(float)
    out = np.full(vals.shape, np.nan)
    ok = ~np.isnan(pos)
    out[ok] = vals[pos[ok].astype(int)]
    return pd.DataFrame(out, index=part.index, columns=[f"pz_{c}" for c in cols])


def window(w: pd.DataFrame, lo: str, hi: str) -> pd.DataFrame:
    return w[(w["target_date"] >= lo) & (w["target_date"] <= hi)]


def official(part: pd.DataFrame, d: int) -> pd.Series:
    off = pd.read_parquet(PROCESSED / f"spike_risk_history_0900{'' if d == 0 else '_d1'}.parquet")
    off = off[off["days_ahead"] == d].set_index(["issue_date", "ts"])["p_spike"]
    return pd.Series(off.reindex(pd.MultiIndex.from_arrays([part["issue_date"], part["target_time"]])).to_numpy(),
                     index=part.index)


def main() -> None:
    t0 = time.time()
    table = pd.read_parquet(SCARCITY / "spike_table_ext.parquet")
    data = table.join(pd.read_parquet(OUT / "p1_features.parquet"))
    pred = pd.read_parquet(OUT / "p1_predictions.parquet")
    meta = json.loads((OUT / "p1_meta.json").read_text())
    pcal = pred.pivot_table(index="row", columns="model", values="p_cal", aggfunc="first")
    praw = pred.pivot_table(index="row", columns="model", values="p_raw", aggfunc="first")
    decision = {}

    # ---------------------------------------------------------------- 1. placebo
    rows = []
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        w = part[["target_date", "spike"]].join(pcal[["C_strict", "C+G0", "C+G1"]]).dropna(subset=["spike", "C_strict"])
        ins = window(w, *IN)
        y = ins["spike"].to_numpy(float)
        for g, tasks in PLACEBO.items():
            if d not in tasks:
                continue
            r = delta_stats(ins["C_strict"].to_numpy(), ins[f"C+{g}"].to_numpy(), y, ins["target_date"], 3)
            rows.append({"days_ahead": d, "group": g, "seed": 0, "kind": "real", **r})
            for s in SEEDS:
                t = time.time()
                pz = placebo_cols(part, fx.GROUPS[g], s)
                full = part.join(pz)
                p = md.walk_forward(full, f"pz_{g}_{s}", BASE + list(pz.columns))
                p["p_cal"] = md.calibrate(p, full, meta["calibration"])
                cand = p.set_index("row")["p_cal"].reindex(ins.index).to_numpy()
                r = delta_stats(ins["C_strict"].to_numpy(), cand, y, ins["target_date"], 3)
                rows.append({"days_ahead": d, "group": g, "seed": s, "kind": "placebo",
                             "missing": float(pz.loc[ins.index].isna().all(axis=1).mean()), **r})
                print(f"placebo d{d} {g} seed {s}: delta {r['delta']:+.5f} pass {r['pass']} ({time.time() - t:.0f} s)",
                      flush=True)
    plc = pd.DataFrame(rows)
    plc.to_csv(OUT / "p1c_placebo.csv", index=False)
    fake = plc[plc["kind"] == "placebo"]
    decision["placebo"] = {"comparisons": len(fake), "passed": int(fake["pass"].sum()),
                           "too_loose": bool(fake["pass"].sum() >= 4)}
    for (d, g), real in plc[plc["kind"] == "real"].groupby(["days_ahead", "group"]):
        f = fake[(fake["days_ahead"] == d) & (fake["group"] == g)]["delta"]
        decision["placebo"][f"{g}_d{d}"] = {"real_delta": float(real["delta"].iloc[0]),
                                            "placebo_above_real": int((f >= real["delta"].iloc[0]).sum()),
                                            "placebo_max": float(f.max()),
                                            "distinguishable": bool((f < real["delta"].iloc[0]).all())}

    # ---------------------------------------------------------------- 2. split
    srows = []
    cs = pred[pred["model"] == "C_strict"].reset_index(drop=True)
    td = pd.to_datetime(cs["target_date"])
    sel = ((td >= SEL[0]) & (td <= SEL[1]) & cs["spike"].notna()).to_numpy()
    choice = {}
    for m in md.METHODS:
        p = np.empty(len(cs))
        for dd, idx in cs.groupby("days_ahead").indices.items():
            p[idx] = md.calibrate(cs.iloc[idx], data[data["days_ahead"] == dd], m)
        choice[m] = float(md.logloss(p[sel], cs["spike"].to_numpy(float)[sel]).mean())
    method = min(choice, key=choice.get)
    decision["split"] = {"calibration_choice": choice, "calibration": method}
    print(f"split: calibration on the selection part {choice} -> {method}", flush=True)

    def calibrated(model: str, d: int) -> pd.Series:
        sub = pred[(pred["model"] == model) & (pred["days_ahead"] == d)].reset_index(drop=True)
        if method == meta["calibration"]:
            return sub.set_index("row")["p_cal"]
        return pd.Series(md.calibrate(sub, data[data["days_ahead"] == d], method), index=sub["row"])

    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        w = part[["target_date", "target_time", "issue_date", "spike"]].copy()
        for m in ["C_strict"] + [f"C+{g}" for g in fx.GROUPS]:
            w[m] = calibrated(m, d).reindex(w.index)
        w = w.dropna(subset=["spike", "C_strict"])
        a = window(w, *SEL)
        y = a["spike"].to_numpy(float)
        passed = []
        for g in fx.GROUPS:
            r = delta_stats(a["C_strict"].to_numpy(), a[f"C+{g}"].to_numpy(), y, a["target_date"], 2)
            srows.append({"days_ahead": d, "part": "selection", "model": f"C+{g}", "versus": "C_strict", **r})
            if r["pass"]:
                passed.append(g)
        decision["split"][f"passed_d{d}"] = passed
        if not passed:
            decision["split"][f"verdict_d{d}"] = "选择段没有选出任何组"
            continue
        v3 = json.loads((OUT / "decision_p1.json").read_text())["passed"][str(d)]
        if sorted(passed) == sorted(v3) and method == meta["calibration"]:
            h1 = pcal["spikev3"]
        else:
            cols = BASE + [c for g in passed for c in fx.GROUPS[g]]
            p = md.walk_forward(part, "spikev3-H1", cols)
            h1 = pd.Series(md.calibrate(p, part, method), index=p["row"])
        w["spikev3-H1"] = h1.reindex(w.index)
        w["official"] = official(part, d)
        b = window(w.dropna(subset=["spikev3-H1", "official"]), *CHK)
        y = b["spike"].to_numpy(float)
        for vs in ("C_strict", "official"):
            dl = md.logloss(b[vs].to_numpy(), y) - md.logloss(b["spikev3-H1"].to_numpy(), y)
            r = dict(zip(("delta", "day_lo", "day_hi"), md.paired(dl, b["target_date"], "day", 2000)))
            srows.append({"days_ahead": d, "part": "check", "model": "spikev3-H1", "versus": vs, "spikes": int(y.sum()),
                          "groups": "+".join(passed), **r})
            if vs == "C_strict":
                decision["split"][f"verdict_d{d}"] = ("能推广" if r["day_lo"] > 0 else
                                                      "不能推广" if r["delta"] <= 0 else "无法判断")
    pd.DataFrame(srows).to_csv(OUT / "p1c_split.csv", index=False)
    decision["seconds"] = round(time.time() - t0)
    (OUT / "decision_p1c.json").write_text(json.dumps(decision, indent=1, ensure_ascii=False) + "\n")
    with pd.option_context("display.width", 220, "display.max_rows", 200):
        print(pd.DataFrame(srows).round(5).to_string(index=False))
    print(json.dumps(decision, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
