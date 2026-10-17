"""Leak audit, metrics, ablations and error analysis for the spike experiments.

Choices are made on validation only, so the report runs in two steps:
  python -m vpp.spike.report validation        audit + validation tables
  python -m vpp.spike.report test              the same tables for test, after the choices are frozen
Outputs in reports/spike/ (one CSV per table, see the README there).
"""

import sys

import numpy as np
import pandas as pd

from vpp.data import PROCESSED
from vpp.spike import metrics as mt
from vpp.spike import protocol as pc
from vpp.spike.features import GROUPS
from vpp.spike.registry import FEATURES, NOT_USED
from vpp.spike.sources import PIT

OUT = mt.OUT
# chosen on validation (2026-10-17): the existing classifier's features under this protocol plus the extra
# price history, the only addition whose validation gain had a 95% interval above 0 (D+1, day and week blocks)
CHOSEN = "C+price_extra"
ABLATION = [("B1", "B0"), ("B2", "B1"), ("B3", "B2"), ("B4", "B3"), ("B4", "B2"), ("B5_weather", "B4"), ("B5_aemo_price", "B4"),
            ("B5_aemo_error", "B4"), ("B5_all", "B4"), ("B5_all", "B2"), ("B4", "existing_C"), ("B5_all", "existing_C"),
            ("B4", "aemo_price"), ("B5_all", "aemo_price"), ("existing_C", "B2"), ("B2", "yesterday"), ("B5_all", "yesterday"),
            # round 2: the new sources on top of the existing classifier rebuilt under this protocol
            ("C_strict", "existing_C"), ("C+stpasa", "C_strict"), ("C+outage", "C_strict"), ("C+stpasa+outage", "C_strict"),
            ("C+price_extra", "C_strict"), ("C_strict_w12", "C_strict"), ("C+stpasa+outage_w12", "C+stpasa+outage"),
            ("C+stpasa+outage_w12", "C_strict_w12"), ("C_strict", "yesterday"), ("C_strict", "aemo_price"),
            ("C_strict", "existing_price")]


# ---------------------------------------------------------------- audit

def audit(table: pd.DataFrame, pred: pd.DataFrame) -> pd.DataFrame:
    rows = []

    def add(check, value, expect="0", note=""):
        rows.append({"check": check, "value": value, "expect": expect, "note": note})

    for src in ("pd", "st", "wx", "out", "out_prev"):
        col = f"src_{src}_available_at"
        late = (pd.to_datetime(table[col]) > table["issue_time"]).sum()
        add(f"rows using a {src} record with available_at > issue_time", int(late))
        add(f"rows without any {src} record", int(table[col].isna().sum()), "-", f"of {len(table):,}")
    add("issues on the fallback pre-dispatch run (age > 2h)", int(table.loc[table["pd_age_h"] > 2, "issue_date"].nunique()), "-",
        "D+0 uses D-1's run; D+1 has no AEMO values those days")
    add("issues with no outage snapshot", int(table.loc[table["src_out_available_at"].isna(), "issue_date"].nunique()), "-")

    # brute force: the pre-dispatch value used is the newest run public at the issue
    pdr = pd.read_parquet(PIT / "predispatch.parquet")
    pdr = pdr[(pdr["variable"] == "requirement")]
    rng = np.random.default_rng(0)
    sample = table.iloc[rng.choice(len(table), 500, replace=False)]
    bad = 0
    for _, r in sample.iterrows():
        c = pdr[(pdr["target_time"] == r["target_time"]) & (pdr["available_at"] <= r["issue_time"])]
        want = c.sort_values("run_at")["value"].iloc[-1] if len(c) else np.nan
        bad += not ((np.isnan(want) and np.isnan(r["pd_req"])) or abs(want - r["pd_req"]) < 1e-9)
    add("pd_req differs from a brute-force as-of lookup (500 random rows)", bad)

    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    ts = sample["issue_date"] - pd.Timedelta(days=1) + pc.DAY_START + sample["slot"] * pd.Timedelta(minutes=30)
    want = df30["energy"].reindex(ts).to_numpy()
    add("p_prev1 differs from RTP at the same slot of D-1 (500 random rows)", int((np.abs(want - sample["p_prev1"].to_numpy()) > 1e-9).sum()))
    feats = [c for g in GROUPS.values() for c in g]
    same = [c for c in feats if table[c].equals(table["rtp"]) or table[c].equals(table["spike"])]
    add("features identical to a label", len(same), "0", ",".join(same))

    fit = pred[pred["refit_month"].notna()]
    late = (pd.to_datetime(fit["train_last_day"]) >= pd.to_datetime(fit["refit_month"]) - pd.Timedelta(days=pc.EMBARGO_DAYS)).sum()
    add("predictions from a model trained on labels within EMBARGO_DAYS of its month", int(late))
    add("predictions from a model trained on labels after TRAIN_END", int((pd.to_datetime(fit["train_last_day"]) > pc.TRAIN_END).sum()))
    add("predictions issued before their model's month", int((fit["issue_date"] < pd.to_datetime(fit["refit_month"])).sum()))
    return pd.DataFrame(rows)


def source_audit(table: pd.DataFrame) -> pd.DataFrame:
    from vpp.data import RAW
    rows = []

    def add(source, item, value, note=""):
        rows.append({"source": source, "item": item, "value": value, "note": note})

    pdr = pd.read_parquet(PIT / "predispatch.parquet", columns=["record_id", "version", "evidence_at", "variable", "facility_or_service", "target_time"])
    runs = pdr.drop_duplicates("record_id")
    lag = (runs["evidence_at"] - pd.to_datetime(runs["record_id"].str[:8]) - pd.Timedelta(hours=8)).dt.total_seconds() / 60
    add("predispatch", "runs (one per trading day)", len(runs), f"{runs['record_id'].min()[:8]} .. {runs['record_id'].max()[:8]}")
    add("predispatch", "runs not labelled 08:00 (08:00 missing from the archive)", int((runs["record_id"].str[8:] != "0800").sum()))
    add("predispatch", "issue ID minus 08:00, minutes: share 15 / 30 / later",
        f"{(lag.round() == 15).mean():.1%} / {(lag.round() == 30).mean():.1%} / {(lag.round() > 30).mean():.1%}")
    add("predispatch", "public copy after issue ID (current folder 2026-10-16)", "20 min on 34/34 runs", "older runs: assumed the same")
    dpv = pdr[pdr["variable"] == "dpv_level"]
    add("predispatch", "dpvLevel first present", str(dpv["target_time"].min()), "not used")

    st = pd.read_csv(PIT / "stpasa_audit.csv", parse_dates=["report_day", "available_at", "run_at"])
    add("stpasa", "files listed (ZIP/XLSX)", len(st))
    add("stpasa", "parsed / unparsed", f"{st['error'].isna().sum()} / {st['error'].notna().sum()}", "unparsed: 2023-09-29..2023-10-15 layouts")
    for lay, g in st.dropna(subset=["layout"]).groupby("layout"):
        add("stpasa", f"layout {lay}", len(g), f"{g['report_day'].min():%Y-%m-%d} .. {g['report_day'].max():%Y-%m-%d}")
    days = pd.date_range(st["report_day"].min(), st["report_day"].max())
    add("stpasa", "report days without a report", len(days.difference(pd.DatetimeIndex(st["report_day"]))))
    late = (st["available_at"] - st["run_at"]).dt.total_seconds() / 3600
    add("stpasa", "available_at minus 11:00 of the report day, hours: median / 95% / max",
        f"{late.median():.2f} / {late.quantile(0.95):.1f} / {late.max():.0f}", "max = 2023 re-uploads (treated as public only then)")

    man = pd.read_csv(RAW / "realtime-outages" / "manifest.csv", parse_dates=["issue_time", "label", "listed_at"])
    add("outages", "issue days with a snapshot", int(man["status"].isin(["downloaded", "cached"]).sum()), f"of {len(man)}")
    add("outages", "errors", int(man["status"].astype(str).str.startswith("error").sum()))
    age = (man["issue_time"] - man["label"]).dt.total_seconds() / 60
    add("outages", "snapshot used: share 08:30 / older", f"{(age == 30).mean():.1%} / {(age > 30).mean():.1%}")
    lst = (man["listed_at"] - man["label"]).dt.total_seconds() / 60
    add("outages", "listing minus label, minutes: median / max", f"{lst.median():.0f} / {lst.max():.0f}")
    add("outages", "header variants", man["header_sha"].nunique(), "columns missing: " + str(man["missing_columns"].dropna().unique()[:3]))
    add("outages", "records modified after their snapshot's extract time", int((pd.to_datetime(man["max_modified"]) >
                                                                                pd.to_datetime(man["extract_datetimes"].str[:19].str.replace("T", " "))).sum()))
    add("outages", "rows kept per snapshot: median", float(man["rows_kept"].median()))
    sizes = pd.read_csv(RAW / "realtime-outages" / "listing.csv").set_index("name")["size"]
    add("outages", "bytes of the snapshots used (listing sizes)", f"{sizes.reindex(man['file']).sum() / 1e9:.1f} GB",
        "raw files not kept; sha256 and row counts recorded for the 31-day pilot only (the full run's manifest write failed "
        "and was rebuilt from the extracts plus each file's header line)")

    wx = pd.read_parquet(PIT / "weather.parquet", columns=["version", "target_time"])
    for v, g in wx.groupby("version"):
        add("weather", f"{v} first valid time", str(g["target_time"].min()))
    add("weather", "Open-Meteo availability of ECMWF IFS 0.25 06Z 2026-10-16", "7.35 h after initialisation", "assumed 8h; delayed run 14h")
    older = table["src_wx_run"].str.endswith("18Z") & (pd.to_datetime(table["src_wx_run"].str[13:21], format="%Y%m%d", errors="coerce")
                                                        == table["issue_date"] - pd.Timedelta(days=2))
    add("weather", "rows on the older vintage (18Z of D-2: targets 02:00-08:00 AWST, where 18Z of D-1 is not out by 09:00)",
        f"{older.mean():.1%}", "the price models' day1/day2 features use the 18Z D-1 run there")
    add("prices", "trading days with prices", int(pc.trading_day(pd.read_parquet(PROCESSED / "wem_30min.parquet").index).nunique()))
    add("table", "rows / issues", f"{len(table):,} / {table['issue_date'].nunique()}")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- metrics

def ablation(w: pd.DataFrame, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    rows = []
    for (d, period), g in w.groupby(["days_ahead", "period"]):
        for a, b in pairs:
            if a not in g or b not in g:
                continue
            ap = mt.bootstrap_ap(g, a, b)
            wk = mt.bootstrap_ap(g, a, b, block="week", n=1000)
            rec = {"days_ahead": d, "period": period, "model": a, "versus": b, "ap_gain": ap[0], "ap_lo": ap[1], "ap_hi": ap[2],
                   "ap_lo_week": wk[1], "ap_hi_week": wk[2]}
            if a in mt.PROB_MODELS and b in mt.PROB_MODELS:
                ll = mt.bootstrap_logloss(g, a, b)
                rec.update(logloss_gain=ll[0], ll_lo=ll[1], ll_hi=ll[2])
            month = g.groupby(g["target_date"].dt.to_period("M"))
            per = month.apply(lambda x: mt.average_precision_score(x["spike"], x[a]) - mt.average_precision_score(x["spike"], x[b])
                              if x["spike"].sum() else np.nan)
            rec.update(months=int(per.notna().sum()), months_better=int((per > 0).sum()))
            rows.append(rec)
    return pd.DataFrame(rows)


def univariate(table: pd.DataFrame, period: str) -> pd.DataFrame:
    """AUC of each feature alone (direction-free: max(AUC, 1-AUC), sign kept) per task, on one period."""
    t = table[(table["target_date"] >= pc.EVAL_START) & table["spike"].notna()]
    t = t[pc.period_of(t["target_date"]) == period]
    rows = []
    for d, g in t.groupby("days_ahead"):
        for name in FEATURES:
            ok = g[name].notna()
            if ok.sum() < 100 or g.loc[ok, name].nunique() < 2 or g.loc[ok, "spike"].nunique() < 2:
                continue
            auc = mt.roc_auc_score(g.loc[ok, "spike"], g.loc[ok, name])
            rows.append({"days_ahead": d, "feature": name, "group": FEATURES[name][0], "auc": max(auc, 1 - auc),
                         "higher_means_more_spikes": auc >= 0.5, "coverage": ok.mean()})
    return pd.DataFrame(rows).sort_values(["days_ahead", "auc"], ascending=[True, False])


def registry(table: pd.DataFrame) -> pd.DataFrame:
    t = table[table["target_date"] >= pc.OOS_START]
    period = np.where(t["target_date"] < pc.EVAL_START, "warm-up", pc.period_of(t["target_date"]))
    base = GROUPS["time"] + GROUPS["price"]
    rows = []
    for name, (group, formula, unit, mech, source, main, note) in FEATURES.items():
        x = t[name]
        rec = {"feature": name, "group": group, "formula": formula, "unit": unit, "mechanism": mech, "source_and_availability": source,
               "in_main_backtest": main, "note": note, "missing_all": table[name].isna().mean()}
        for p in ("warm-up", "validation", "test"):
            rec[f"missing_{p}"] = x[period == p].isna().mean()
        if group not in ("time", "price"):
            corr = t[[name] + base].rank().corr()[name].drop(name).abs().dropna()
            if len(corr):
                rec["max_rank_corr_with_time_price"], rec["most_similar"] = corr.max(), corr.idxmax()
            rec["share_nonzero"] = (x.fillna(0) != 0).mean()
        rows.append(rec)
    for name, why in NOT_USED.items():
        rows.append({"feature": name, "group": "not used", "in_main_backtest": False, "note": why})
    return pd.DataFrame(rows)


SIGNALS = ["p_prev1", "spk_days_7", "h_since_spike", "pd_req", "pd_headroom", "pd_day_headroom_min", "pd_req_ramp3h", "st_spread",
           "st_ref_rev", "out_loss_mw", "out_rev_mw", "out_now_mw", "wx_temp", "aemo_energy", "aemo_day_max"]


def error_analysis(table: pd.DataFrame, pred: pd.DataFrame, model: str, period: str, days_ahead: int = 1, budget: float = 0.05,
                   n: int = 4) -> pd.DataFrame:
    """Representative missed events, false-alarm days and caught events, with the signals visible at the
    issue (the row of the event's peak interval, or the day's riskiest interval for false alarms)."""
    p = pred[(pred["model"] == model) & (pred["days_ahead"] == days_ahead)].copy()
    p["score"] = mt.score_of(p)
    p = p[(p["target_date"] >= pc.EVAL_START) & p["spike"].notna()]
    p = p[pc.period_of(p["target_date"]) == period]
    flag = mt.budget_flags(p["score"], budget)
    rtp = pd.read_parquet(PROCESSED / "wem_30min.parquet")["energy"]
    ev = mt.events(rtp).reindex(p["target_time"]).to_numpy()
    p = p.assign(event=ev, flag=flag.to_numpy())
    sig = table.loc[p["row"], SIGNALS].set_index(p.index)
    p = p.join(sig)
    cases = []
    e = p.dropna(subset=["event"])
    for eid, g in e.groupby("event"):
        peak = g.loc[g["rtp"].idxmax()]
        cases.append({"case": "caught" if g["flag"].any() else "missed", "event": int(eid), "start": g["target_time"].min(),
                      "intervals": len(g), "peak_rtp": g["rtp"].max(), "max_score": g["score"].max(), **peak[SIGNALS].to_dict(),
                      "issue_time": peak["issue_time"], "target_time": peak["target_time"]})
    c = pd.DataFrame(cases)
    out = [c[c["case"] == "missed"].nlargest(n, "peak_rtp"), c[c["case"] == "caught"].nlargest(n, "peak_rtp")]
    day = p.groupby("target_date").agg(max_score=("score", "max"), spikes=("spike", "sum"))
    fa = day[day["spikes"] == 0].nlargest(n, "max_score")
    for d, r in fa.iterrows():
        g = p[p["target_date"] == d]
        top = g.loc[g["score"].idxmax()]
        out.append(pd.DataFrame([{"case": "false alarm day", "start": d, "intervals": int(g["flag"].sum()), "peak_rtp": g["rtp"].max(),
                                  "max_score": r["max_score"], **top[SIGNALS].to_dict(), "issue_time": top["issue_time"],
                                  "target_time": top["target_time"]}]))
    res = pd.concat(out, ignore_index=True)
    # where the period's spike-free rows sit, for reading the signals above
    ref = p[p["spike"] == 0][SIGNALS].median().to_dict()
    res = pd.concat([res, pd.DataFrame([{"case": "median of non-spike rows", **ref}])], ignore_index=True)
    return res.assign(model=model, days_ahead=days_ahead, budget=budget, period=period)


def sensitivity() -> None:
    """Validation only: seeds and positive weights of the chosen model and C_strict, and the delayed-availability
    run against the central one (same comparable rows)."""
    def ll(q, y):
        q = np.clip(q, 1e-6, 1 - 1e-6)
        return float(-(y * np.log(q) + (1 - y) * np.log(1 - q)).mean())

    base = pd.read_parquet(PROCESSED / "spike_predictions.parquet")
    var = pd.concat([pd.read_parquet(f) for f in PROCESSED.glob("spike_predictions_variants_*.parquet")], ignore_index=True)
    p = pd.concat([base[base["model"].isin(["C_strict", CHOSEN])], var], ignore_index=True)
    p = p[(p["target_date"] >= pc.EVAL_START) & p["spike"].notna()]
    p = p[pc.period_of(p["target_date"]) == "validation"]
    rows = [{"model": m, "days_ahead": d, "ap_raw": mt.average_precision_score(g["spike"], g["p_raw"]),
             "logloss_raw": ll(g["p_raw"].to_numpy(), g["spike"].to_numpy()),
             "logloss_cal": ll(g["p_cal"].to_numpy(), g["spike"].to_numpy()) if g["p_cal"].notna().all() else np.nan}
            for (m, d), g in p.groupby(["model", "days_ahead"])]
    pd.DataFrame(rows).to_csv(OUT / "variants_validation.csv", index=False)

    models = ["B2", "B3", "B4", "B5_weather", "B5_all", "C_strict", "C+stpasa", "C+outage", "C+stpasa+outage", "C+price_extra"]
    rows = []
    for run, f in (("central", "spike_predictions"), ("delayed", "spike_predictions_delayed")):
        w, _ = mt.comparable(mt.wide(pd.read_parquet(PROCESSED / f"{f}.parquet"), models), models)
        w = w[w["period"] == "validation"]
        for d, g in w.groupby("days_ahead"):
            rows.append({"run": run, "days_ahead": d, "n": len(g), **{m: mt.average_precision_score(g["spike"], g[m]) for m in models}})
    pd.DataFrame(rows).to_csv(OUT / "delayed_validation.csv", index=False)


def export_chosen() -> pd.DataFrame:
    """The chosen model's forecasts in the hand-over format for the dispatcher (data/processed/spike_forecast.parquet)."""
    p = pd.read_parquet(PROCESSED / "spike_predictions.parquet")
    p = p[p["model"] == CHOSEN].copy()
    p["spike_probability"] = mt.score_of(p)
    p["period"] = np.where(p["target_date"] < pc.EVAL_START, "calibration warm-up", pc.period_of(p["target_date"]))
    cols = ["issue_time", "target_time", "lead_hours", "days_ahead", "spike_probability", "spike", "spike300", "any5", "rtp", "max5",
            "model_version", "refit_month", "train_last_day", "n_train", "period"]
    out = p[cols].sort_values(["issue_time", "target_time"]).reset_index(drop=True)
    out.to_parquet(PROCESSED / "spike_forecast.parquet", index=False)
    return out


def main(period: str) -> None:
    if period == "sensitivity":
        export_chosen()
        return sensitivity()
    OUT.mkdir(parents=True, exist_ok=True)
    table = pd.read_parquet(PROCESSED / "spike_table.parquet")
    pred = pd.read_parquet(PROCESSED / "spike_predictions.parquet")
    if period == "validation":
        a = audit(table, pred)
        a.to_csv(OUT / "audit_leakage.csv", index=False)
        print(a.to_string())
        s = source_audit(table)
        s.to_csv(OUT / "audit_sources.csv", index=False)
        print(s.to_string())
        registry(table).to_csv(OUT / "feature_registry.csv", index=False)
    univariate(table, period).to_csv(OUT / f"univariate_{period}.csv", index=False)
    models = [m for m in mt.PROB_MODELS + mt.SCORE_MODELS if m in set(pred["model"])]
    w = mt.wide(pred, models)
    w, excluded = mt.comparable(w, models)
    excluded.to_csv(OUT / "comparable_sample_exclusions.csv")
    rtp = pd.read_parquet(PROCESSED / "wem_30min.parquet")["energy"]
    ev = mt.events(rtp)
    probs = [m for m in mt.PROB_MODELS if m in models]
    thr = mt.best_f1_thresholds(w, probs)
    w = w[w["period"] == period]
    res = mt.table(w, models, ev, thresholds=thr)
    res.to_csv(OUT / f"metrics_{period}.csv", index=False)
    mt.by_group(w, models, w["target_date"].dt.to_period("M").astype(str)).to_csv(OUT / f"by_month_{period}.csv", index=False)
    lead = pd.cut(w["lead_hours"], mt.LEAD_BINS, right=False).astype(str)
    mt.by_group(w, models, lead).to_csv(OUT / f"by_lead_{period}.csv", index=False)
    hour = w["target_time"].dt.hour
    hgrp = pd.Series(np.select([hour.between(a, b - 1) for a, b in mt.HOURS.values()], list(mt.HOURS), default=""), index=w.index)
    mt.by_group(w, models, hgrp).to_csv(OUT / f"by_hour_{period}.csv", index=False)
    mt.reliability(w, probs).to_csv(OUT / f"reliability_{period}.csv", index=False)
    ab = ablation(w, ABLATION)
    ab.to_csv(OUT / f"ablation_{period}.csv", index=False)
    ea = pd.concat([error_analysis(table, pred, CHOSEN, period, d) for d in (0, 1)], ignore_index=True)
    ea.to_csv(OUT / f"error_analysis_{period}.csv", index=False)
    show = ["days_ahead", "model", "n", "spikes", "events", "rate", "ap", "auc", "brier", "logloss", "prec@5%", "recall@5%", "event_recall@5%"]
    print(res[show].round(4).to_string())
    print(ab.round(4).to_string())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "validation")
