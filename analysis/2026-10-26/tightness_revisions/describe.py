"""Univariate description (PLAN.md section 5; descriptive only, no pass/fail). In-sample 2025-04-01 .. 2026-06-30 only.

D1 Spearman of each revision column with step 2's M1 error (M1 margin - actual margin) and its two parts at M1's
   tightest half-hour t*: unregistered outage = actual thermal outage - registered (out_t); residual-demand error =
   actual residual - forecast residual (res_t). Positive parts = M1 put the margin too high.
D2 AUC of each column alone for the actual tightest 10% (46 days), direction fixed by mechanism.
D3 the columns on the 2024 Jun-Aug outage-plus-low-wind LRC days (history) and the in-sample LRC days, with each
   value's in-sample percentile.
Intervals: month-block bootstrap, 2,000 draws.

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/tightness_revisions/describe.py
Writes out/describe_corr.csv, out/describe_auc.csv, out/describe_events.csv
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "out"
FC = ROOT / "analysis" / "2026-10-26" / "tightness_forecast" / "out"
INP = ROOT / "analysis" / "2026-10-26" / "tightness_inputs" / "out"
SEED, B = 20261028, 2000
COLS = ["out_thermal_rev_max_1621_mw", "out_thermal_rev_mean_1621_mw", "out_thermal_new24_max_1621_mw",
        "out_thermal_forced_max_1621_mw", "out_thermal_pending_max_1621_mw", "out_forced24_n",
        "st_ref_rev_max_1621_mw", "st_ref_rev_mean_1621_mw", "st_short_energy_1621_n", "st_short_raise_1621_n",
        "st_minus_pd_max_1621_mw", "wx_temp_perth_max_day_rev_c", "wx_wind_1621_rev_kmh"]
TIGHTER_IF_LOWER = {"wx_wind_1621_rev_kmh"}          # PLAN 5: less wind than the previous vintage = tighter


def ranks(a: np.ndarray) -> np.ndarray:
    return pd.Series(a).rank().to_numpy()


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 10 or np.nanstd(x[m]) == 0:
        return np.nan
    return float(np.corrcoef(ranks(x[m]), ranks(y[m]))[0, 1])


def auc(score: np.ndarray, pos: np.ndarray) -> float:
    m = ~np.isnan(score)
    s, p = score[m], pos[m]
    n1, n0 = int(p.sum()), int((~p).sum())
    if n1 == 0 or n0 == 0:
        return np.nan
    r = ranks(s)
    return float((r[p].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def boot(df: pd.DataFrame, stat, rng) -> tuple[float, float]:
    months = df["month"].to_numpy()
    uniq = np.unique(months)
    idx = {m: np.flatnonzero(months == m) for m in uniq}
    vals = []
    for _ in range(B):
        take = np.concatenate([idx[m] for m in rng.choice(uniq, size=len(uniq), replace=True)])
        vals.append(stat(df.iloc[take]))
    v = np.array(vals, float)
    v = v[~np.isnan(v)]
    return (float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))) if len(v) else (np.nan, np.nan)


def table() -> pd.DataFrame:
    rev = pd.read_parquet(OUT / "revisions_d1.parquet").set_index("day")
    p = pd.read_csv(FC / "daily_predictions.csv", parse_dates=["day"]).set_index("day")
    x30 = pd.read_parquet(INP / "inputs_d1_30min.parquet", columns=["day", "slot", "lab_outage_mw", "lab_residual_mw"])
    at = x30.set_index(["day", "slot"])
    key = pd.MultiIndex.from_arrays([p.index, p["t_star"]])
    p["lab_outage_t"] = at["lab_outage_mw"].reindex(key).to_numpy()
    p["lab_residual_t"] = at["lab_residual_mw"].reindex(key).to_numpy()
    p["e_total"] = p["m1_mw"] - p["margin_min_1621_mw"]
    p["e_outage"] = p["lab_outage_t"] - p["out_t"]
    p["e_residual"] = p["lab_residual_t"] - p["res_t"]
    d = p[["period", "margin_min_1621_mw", "m1_mw", "e_total", "e_outage", "e_residual", "top_actual", "lrc"]].join(rev[COLS])
    d["month"] = d.index.to_period("M").astype(str)
    return d


def main() -> None:
    rng = np.random.default_rng(SEED)
    d = table()
    s = d[(d["period"] == "in_sample") & d["m1_mw"].notna()].copy()
    s["top_actual"] = s["top_actual"].astype(bool)
    print(f"in-sample days with M1: {len(s)}; tightest 10%: {int(s['top_actual'].sum())}")

    corr = []
    for c in COLS:
        for target in ("e_total", "e_outage", "e_residual"):
            stat = lambda f, c=c, t=target: spearman(f[c].to_numpy(float), f[t].to_numpy(float))
            lo, hi = boot(s, stat, rng)
            ok = s[[c, target]].notna().all(axis=1)
            corr.append({"column": c, "target": target, "days": int(ok.sum()), "spearman": stat(s), "ci_low": lo, "ci_high": hi,
                         "excludes_0": bool(lo > 0 or hi < 0)})
    corr = pd.DataFrame(corr)
    corr.to_csv(OUT / "describe_corr.csv", index=False)

    aucs = []
    for c in COLS:
        sign = -1.0 if c in TIGHTER_IF_LOWER else 1.0
        stat = lambda f, c=c, sign=sign: auc(sign * f[c].to_numpy(float), f["top_actual"].to_numpy(bool))
        lo, hi = boot(s, stat, rng)
        aucs.append({"column": c, "direction": "lower = tighter" if sign < 0 else "higher = tighter", "days": int(s[c].notna().sum()),
                     "auc": stat(s), "ci_low": lo, "ci_high": hi, "share_nonzero": float((s[c].fillna(0) != 0).mean())})
    aucs = pd.DataFrame(aucs)
    aucs.to_csv(OUT / "describe_auc.csv", index=False)

    hist = pd.read_csv(FC / "history_lrc_outage_lowwind.csv", parse_dates=["day"])["day"]
    ins = d.index[(d["period"] == "in_sample") & d["lrc"].astype(bool)]
    ev = []
    for day, kind in [(x, "2024 winter LRC (outage + low wind), history") for x in hist] + [(x, "in-sample LRC") for x in ins]:
        row = {"day": day.date(), "kind": kind, "m1_error_mw": d.at[day, "e_total"] if day in d.index else np.nan}
        for c in COLS:
            v = d.at[day, c] if day in d.index else np.nan
            row[c] = v
            ref = s[c].dropna()
            row[f"{c}_pct"] = float((ref < v).mean() + 0.5 * (ref == v).mean()) if pd.notna(v) and len(ref) else np.nan
        ev.append(row)
    ev = pd.DataFrame(ev)
    ev.to_csv(OUT / "describe_events.csv", index=False)

    pd.set_option("display.width", 250)
    print(corr.round(3).to_string(index=False))
    print(aucs.round(3).to_string(index=False))
    print(ev[["day", "kind", "m1_error_mw"] + [f"{c}_pct" for c in COLS]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
