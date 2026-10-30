"""Which precursors do the top-20% spikes share? Matched case-control comparisons and models.

Cases     the top-20% events (DEF: top_regime by default, `--global` for top_global), from extract.py.
Controls  10 per case: same 30-min slot, +/- 28 days, same period, no spike in that interval and the next 2 h.
Features  features.py: the 5 h before a cutoff (0 = the onset), each tagged RT / FC / EP / CAL.

Periods (vpp.evaluate): train = trading days before 2025-04-01 (old regime); validation / test = the
interleaved months from 2025-04-01 (new regime). Fit sets: "T" = train; "V" = validation days up to
TRAIN_END (2026-07-16). Each fit set is scored out of fold on itself (folds = calendar months) and out of
sample elsewhere. Choices (the L1 penalty) are made on validation; test is reported once.

Scores
  matched AUC   per case, the share of its own controls it out-scores (ties half), averaged over cases:
                the chance that the case looks more extreme than a normal day at the same time. 0.5 = none.
  pooled AUC/AP cases against all controls of the period (AP at random = 1/11 ~ 0.09)
  intervals     95% bootstrap over matched sets (2,000 draws)

Run:  python analyze.py [--global]   -> out/<name>[_global].csv
"""

import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, silhouette_score
from sklearn.tree import DecisionTreeClassifier, _tree

from common import OUT, PROCESSED, SEED, pc
from features import LEVEL_VARS, group_of, matrices, summarize

DEF = "top_global" if "--global" in sys.argv else "top_regime"
SUFFIX = "_global" if DEF == "top_global" else ""
CUTOFFS = [0, -30, -60, -120, -180, -240, -300]
N_BOOT = 2000
PERIODS = ["train", "validation", "test"]
LGB = dict(objective="binary", n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50, subsample=0.8,
           subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=SEED, n_jobs=8)
L1_GRID = [0.003, 0.01, 0.03, 0.1, 0.3, 1.0]
NEED = sorted(set(LEVEL_VARS) | {"battery_dis_mw", "req_energy_mw", "pd_req", "pd_price", "pd_headroom", "pd_reg_raise",
                                 "pd_cont_raise", "wx_temp", "wx_ghi", "wx_cloud", "wx_wind_midwest", "wx_wind_merredin"})
RNG = np.random.default_rng(SEED)


# ---------------------------------------------------------------- data

def load():
    panel = pd.read_parquet(OUT / "panel.parquet")
    win = pd.read_csv(OUT / "windows.csv", parse_dates=["t0", "t_end", "trading_day"]).set_index("window_id")
    matched = pd.read_csv(OUT / "matched.csv")
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    return win, matched, matrices(panel, NEED), df30


class Study:
    def __init__(self):
        self.win, matched, self.mats, df30 = load()
        self.rtp, self.esroi = df30["energy"], df30["esroi"]
        self.sets = matched[matched[DEF]][["case_window", "control_window"]].reset_index(drop=True)
        self.sets["period"] = self.win["period"].reindex(self.sets["case_window"]).to_numpy()
        self.sets["case_day"] = self.win["trading_day"].reindex(self.sets["case_window"]).to_numpy()
        self.sets["control_day"] = self.win["trading_day"].reindex(self.sets["control_window"]).to_numpy()
        self.cases = self.win[(self.win["kind"] == "top") & self.win[DEF]]
        self._feat = {}

    def features(self, cutoff: int = 0) -> tuple[pd.DataFrame, dict]:
        if cutoff not in self._feat:
            self._feat[cutoff] = summarize(self.mats, self.win.reset_index(), self.rtp, self.esroi, cutoff)
        return self._feat[cutoff]

    def fit_sets(self) -> dict[str, pd.DataFrame]:
        """Matched sets used for fitting: T = train; V = validation cases and controls up to TRAIN_END."""
        s = self.sets
        v = s[(s["period"] == "validation") & (s["case_day"] <= pc.TRAIN_END) & (s["control_day"] <= pc.TRAIN_END)]
        return {"T": s[s["period"] == "train"], "V": v}

    @staticmethod
    def design(sets: pd.DataFrame) -> pd.DataFrame:
        """Unique windows of some matched sets with label y (1 = case) and the calendar month as CV group."""
        rows = pd.concat([pd.DataFrame({"window_id": sets["case_window"].unique(), "y": 1}),
                          pd.DataFrame({"window_id": sets["control_window"].unique(), "y": 0})])
        return rows.drop_duplicates("window_id").set_index("window_id")


# ---------------------------------------------------------------- scoring

def matched_ranks(score: pd.Series, sets: pd.DataFrame) -> pd.Series:
    """Per case: share of its controls with a lower score (ties count half); NaN pairs skipped."""
    vc = score.reindex(sets["case_window"]).to_numpy(dtype=float)
    vk = score.reindex(sets["control_window"]).to_numpy(dtype=float)
    s = np.where(np.isnan(vc) | np.isnan(vk), np.nan, (vk < vc) + 0.5 * (vk == vc))
    return pd.Series(s).groupby(sets["case_window"].to_numpy()).mean()


def boot_ci(per_case: np.ndarray, stat=np.nanmean) -> tuple[float, float]:
    per_case = per_case[~np.isnan(per_case)]
    if len(per_case) < 3:
        return np.nan, np.nan
    idx = RNG.integers(0, len(per_case), (N_BOOT, len(per_case)))
    m = stat(per_case[idx], axis=1)
    return tuple(np.nanpercentile(m, [2.5, 97.5]))


def evaluate(score: pd.Series, sets: pd.DataFrame) -> dict:
    """Matched AUC with a bootstrap interval, plus pooled AUC / AP over the unique windows of the sets."""
    r = matched_ranks(score, sets)
    lo, hi = boot_ci(r.to_numpy())
    d = Study.design(sets).join(score.rename("s")).dropna()
    out = {"cases": int(r.notna().sum()), "matched_auc": r.mean(), "lo": lo, "hi": hi,
           "top1": float((r == 1.0).mean()), "pooled_auc": np.nan, "ap": np.nan}
    if d["y"].nunique() == 2:
        out["pooled_auc"] = roc_auc_score(d["y"], d["s"])
        out["ap"] = average_precision_score(d["y"], d["s"])
        # AP interval: resample matched sets (a case with its own controls)
        by_case = sets.groupby("case_window")["control_window"].apply(list)
        aps = []
        for _ in range(300):
            pick = RNG.choice(by_case.index.to_numpy(), len(by_case))
            ctl = np.concatenate([by_case[c] for c in pick])
            yy = np.r_[np.ones(len(pick)), np.zeros(len(ctl))]
            ss = np.r_[score.reindex(pick).to_numpy(), score.reindex(ctl).to_numpy()]
            ok = ~np.isnan(ss)
            aps.append(average_precision_score(yy[ok], ss[ok]))
        out["ap_lo"], out["ap_hi"] = np.percentile(aps, [2.5, 97.5])
    return out


# ---------------------------------------------------------------- 1. univariate

def univariate(st: Study) -> pd.DataFrame:
    F, avail = st.features(0)
    rows = []
    for period in PERIODS:
        s = st.sets[st.sets["period"] == period]
        if s.empty:
            continue
        cw, kw = s["case_window"].unique(), s["control_window"].unique()
        for f in F.columns:
            if f == "cal_hour":
                continue
            x = F[f]
            r = matched_ranks(x, s)
            if r.notna().sum() < 3:
                continue
            lo, hi = boot_ci(r.to_numpy())
            # share of cases beyond the median of their own controls, in the direction of the effect
            ctl_med = pd.Series(x.reindex(s["control_window"]).to_numpy()).groupby(s["case_window"].to_numpy()).median()
            above = (x.reindex(ctl_med.index) > ctl_med).where(x.reindex(ctl_med.index).notna() & ctl_med.notna())
            below = (x.reindex(ctl_med.index) < ctl_med).where(x.reindex(ctl_med.index).notna() & ctl_med.notna())
            rows.append({"feature": f, "group": group_of(f), "avail": avail[f], "period": period, "cases": int(r.notna().sum()),
                         "case_median": x.reindex(cw).median(), "control_median": x.reindex(kw).median(),
                         "matched_auc": r.mean(), "lo": lo, "hi": hi,
                         "share_beyond_own_median": above.mean() if r.mean() >= 0.5 else below.mean()})
    u = pd.DataFrame(rows)
    w = u.pivot(index="feature", columns="period", values=["matched_auc", "lo", "hi"])
    sig = lambda p: ((w[("lo", p)] > 0.5) | (w[("hi", p)] < 0.5)) if ("lo", p) in w else False  # noqa: E731
    sign = lambda p: np.sign(w[("matched_auc", p)] - 0.5) if ("matched_auc", p) in w else 0  # noqa: E731
    if all(("matched_auc", p) in w for p in PERIODS):
        consistent = (sign("train") == sign("validation")) & (sign("validation") == sign("test"))
        u["consistent_3periods"] = u["feature"].map(consistent)
        u["sig_train_and_val"] = u["feature"].map(sig("train") & sig("validation") & (sign("train") == sign("validation")))
        # the bar for a common feature: interval clear of 0.5 in all three periods, all on the same side
        u["sig_all3"] = u["feature"].map(sig("train") & sig("validation") & sig("test") & consistent)
    return u


# ---------------------------------------------------------------- 2. models

def month_folds(index: pd.Index, win: pd.DataFrame) -> np.ndarray:
    return win["trading_day"].reindex(index).dt.strftime("%Y-%m").to_numpy()


def fit_predict(kind: str, X: pd.DataFrame, y: np.ndarray, Xs: list[pd.DataFrame], C: float = 0.1):
    if kind == "lgb":
        m = lgb.LGBMClassifier(**LGB).fit(X, y)
        return m, [pd.Series(m.predict_proba(x)[:, 1], index=x.index) for x in Xs]
    med = X.median()
    mu, sd = X.fillna(med).mean(), X.fillna(med).std().replace(0, 1)
    z = lambda d: ((d.fillna(med) - mu) / sd).fillna(0.0)  # noqa: E731
    m = LogisticRegression(C=C, l1_ratio=1.0, solver="liblinear", max_iter=2000, random_state=SEED).fit(z(X), y)
    m.feature_names_, m.scale_ = list(X.columns), sd
    return m, [pd.Series(m.decision_function(z(x)), index=x.index) for x in Xs]


def oof(kind: str, X: pd.DataFrame, y: np.ndarray, groups: np.ndarray, C: float = 0.1) -> pd.Series:
    out = pd.Series(np.nan, index=X.index)
    for g in np.unique(groups):
        te = groups == g
        if y[~te].sum() < 5:
            continue
        _, (p,) = fit_predict(kind, X[~te], y[~te], [X[te]], C)
        out[X.index[te]] = p.to_numpy()
    return out


def scores_for(st: Study, kind: str, F: pd.DataFrame, cols: list[str], fit: str, C: float) -> pd.Series:
    """Scores for every window: out of fold inside the fit set, out of sample elsewhere."""
    fs = st.fit_sets()[fit]
    d = st.design(fs)
    X, y = F.loc[d.index, cols], d["y"].to_numpy()
    _, (all_scores,) = fit_predict(kind, X, y, [F[cols]], C)
    o = oof(kind, X, y, month_folds(d.index, st.win), C)
    s = all_scores.copy()
    s[o.index] = o
    return s


def models(st: Study) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    F, avail = st.features(0)
    sets_ = feature_sets(avail)
    rows, shap_rows, coef_rows = [], [], []
    fits = st.fit_sets()
    for fit in ("T", "V"):
        if fits[fit]["case_window"].nunique() < 10:
            continue
        for fsname, cols in sets_.items():
            # L1 penalty: chosen on validation (T: out of sample; V: out of fold)
            best = None
            for C in L1_GRID:
                s = scores_for(st, "l1", F, cols, fit, C)
                auc = matched_ranks(s, st.sets[st.sets["period"] == "validation"]).mean()
                if best is None or auc > best[1]:
                    best = (C, auc, s)
            for kind in ("lgb", "l1"):
                s = scores_for(st, kind, F, cols, fit, best[0]) if kind == "lgb" else best[2]
                for period in PERIODS:
                    ps = st.sets[st.sets["period"] == period]
                    if ps.empty:
                        continue
                    where = "in fit set (out of fold)" if (fit == "T" and period == "train") or (fit == "V" and period == "validation") else "out of sample"
                    rows.append({"fit": fit, "features": fsname, "model": kind, "C": best[0] if kind == "l1" else np.nan,
                                 "period": period, "scored": where, **evaluate(s, ps)})
            # explanations from the model fitted on the whole fit set
            d = st.design(fits[fit])
            X, y = F.loc[d.index, cols], d["y"].to_numpy()
            m, _ = fit_predict("lgb", X, y, [X])
            contrib = m.booster_.predict(X, pred_contrib=True)[:, :-1]
            for j, f in enumerate(cols):
                xv = X[f].to_numpy(dtype=float)
                ok = ~np.isnan(xv)
                direction = np.corrcoef(xv[ok], contrib[ok, j])[0, 1] if ok.sum() > 10 and np.std(xv[ok]) > 0 else np.nan
                shap_rows.append({"fit": fit, "features": fsname, "feature": f, "group": group_of(f), "avail": avail[f],
                                  "mean_abs_shap": np.abs(contrib[:, j]).mean(), "direction": direction})
            m1, _ = fit_predict("l1", X, y, [X], best[0])
            for f, c in zip(m1.feature_names_, m1.coef_[0]):
                if abs(c) > 1e-9:
                    coef_rows.append({"fit": fit, "features": fsname, "C": best[0], "feature": f, "group": group_of(f),
                                      "avail": avail[f], "coef_per_sd": c})
    return pd.DataFrame(rows), pd.DataFrame(shap_rows), pd.DataFrame(coef_rows)


def feature_sets(avail: dict) -> dict[str, list[str]]:
    act = [f for f, a in avail.items() if a in ("RT", "FC", "CAL") and f != "cal_hour"]
    return {"actionable": act, "all": [f for f in avail if f != "cal_hour"]}


# ---------------------------------------------------------------- 3. lead time

def lead_time(st: Study, top_features: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, uni = [], []
    for c in CUTOFFS:
        F, avail = st.features(c)
        cols = feature_sets(avail)["actionable"]
        for fit in ("T", "V"):
            if st.fit_sets()[fit]["case_window"].nunique() < 10:
                continue
            s = scores_for(st, "lgb", F, cols, fit, 0.1)
            for period in ("validation", "test"):
                ps = st.sets[st.sets["period"] == period]
                if ps["case_window"].nunique() >= 3:
                    rows.append({"cutoff_min": c, "fit": fit, "period": period, **evaluate(s, ps)})
        for f in top_features:
            if f not in F:
                continue
            for period in PERIODS:
                ps = st.sets[st.sets["period"] == period]
                if ps.empty:
                    continue
                r = matched_ranks(F[f], ps)
                lo, hi = boot_ci(r.to_numpy())
                uni.append({"cutoff_min": c, "feature": f, "period": period, "matched_auc": r.mean(), "lo": lo, "hi": hi})
    return pd.DataFrame(rows), pd.DataFrame(uni)


# ---------------------------------------------------------------- 4. rules

def rules(st: Study) -> tuple[str, pd.DataFrame]:
    F, avail = st.features(0)
    cols = feature_sets(avail)["actionable"]
    text, rows = [], []
    for fit, fs in st.fit_sets().items():
        if fs["case_window"].nunique() < 10:
            continue
        d = st.design(fs)
        X = F.loc[d.index, cols]
        med = X.median()
        tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=max(15, len(d) // 60), class_weight="balanced",
                                      random_state=SEED).fit(X.fillna(med), d["y"])
        paths = leaf_paths(tree, cols)
        text.append(f"=== fit set {fit}: {int(d['y'].sum())} cases, {int((d['y'] == 0).sum())} controls")
        allw = F[cols].fillna(med)
        leaf = pd.Series(tree.apply(allw), index=F.index)
        for period in PERIODS:
            ps = st.sets[st.sets["period"] == period]
            if ps.empty:
                continue
            dd = st.design(ps)
            lf = leaf.reindex(dd.index)
            for k, path in paths.items():
                inleaf = lf == k
                rows.append({"fit": fit, "leaf": k, "rule": path, "period": period,
                             "cases": int((inleaf & (dd["y"] == 1)).sum()), "controls": int((inleaf & (dd["y"] == 0)).sum()),
                             "case_coverage": (inleaf & (dd["y"] == 1)).sum() / max(1, (dd["y"] == 1).sum()),
                             "control_coverage": (inleaf & (dd["y"] == 0)).sum() / max(1, (dd["y"] == 0).sum())})
        for k, path in paths.items():
            text.append(f"leaf {k}: {path}")
    return "\n".join(text), pd.DataFrame(rows)


def leaf_paths(tree, names: list[str]) -> dict[int, str]:
    t = tree.tree_
    out = {}

    def walk(node, conds):
        if t.feature[node] == _tree.TREE_UNDEFINED:
            out[node] = " 且 ".join(conds) if conds else "(全部)"
            return
        f, thr = names[t.feature[node]], t.threshold[node]
        walk(t.children_left[node], conds + [f"{f} <= {thr:.4g}"])
        walk(t.children_right[node], conds + [f"{f} > {thr:.4g}"])

    walk(0, [])
    return out


# ---------------------------------------------------------------- 5. clusters of the cases

def clusters(st: Study, feats: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """k-means on each case's matched percentiles (its rank among its own 10 controls, 0..1, minus 0.5) plus
    the onset hour (sin/cos, x 0.5 so the hour weighs like one feature). Bounded and comparable across
    features, so zero-inflated ones (outage changes) cannot dominate."""
    F, _ = st.features(0)
    D = pd.DataFrame({f: matched_ranks(F[f], st.sets) - 0.5 for f in feats})
    hour = st.win["hour"].reindex(D.index)
    D["hour_sin"], D["hour_cos"] = 0.5 * np.sin(2 * np.pi * hour / 24), 0.5 * np.cos(2 * np.pi * hour / 24)
    D = D.fillna(0.0)
    per = st.win["period"].reindex(D.index)
    peak = pd.read_csv(OUT / "events.csv").set_index("event_id")["peak"]
    rows, prof = [], []
    for regime, fitp in (("old", ["train"]), ("new", ["validation"])):
        Xf = D[per.isin(fitp).to_numpy()]
        if len(Xf) < 12:
            continue
        sil = {k: silhouette_score(Xf, KMeans(k, n_init=20, random_state=SEED).fit_predict(Xf)) for k in range(2, 7)}
        k_best = max(sil, key=sil.get)
        km = KMeans(k_best, n_init=20, random_state=SEED).fit(Xf)
        Xr = D[per.isin(["train"] if regime == "old" else ["validation", "test"]).to_numpy()]
        lab = pd.Series(km.predict(Xr), index=Xr.index)
        for k in range(2, 7):
            rows.append({"regime": regime, "k": k, "silhouette": sil[k], "chosen": k == k_best})
        for c in range(k_best):
            idx = lab.index[lab == c]
            r = {"regime": regime, "cluster": c, "cases": len(idx),
                 "by_period": per.reindex(idx).value_counts().to_dict(),
                 "median_hour": float(np.median(st.win["hour"].reindex(idx))),
                 "hours": st.win["hour"].reindex(idx).round(1).value_counts().head(4).to_dict(),
                 "median_peak": float(np.median(st.win.loc[idx, "event_id"].map(peak))),
                 "months": st.win.loc[idx, "t0"].dt.month.value_counts().head(4).to_dict()}
            for f in feats:
                r[f"pct_{f}"] = D.loc[idx, f].mean() + 0.5   # mean matched percentile in the cluster
            prof.append(r)
    return pd.DataFrame(rows), pd.DataFrame(prof)


# ---------------------------------------------------------------- 6. top vs ordinary spikes

def top_vs_ordinary(st: Study) -> tuple[pd.DataFrame, pd.DataFrame]:
    F, avail = st.features(0)
    ev = st.win[st.win["kind"].isin(["top", "ordinary"])].copy()
    ev["y"] = ev[DEF].astype(int)
    cols = feature_sets(avail)["actionable"] + ["cal_hour"]
    uni = []
    for period in PERIODS:
        e = ev[ev["period"] == period]
        if e["y"].sum() < 3:
            continue
        for f in cols:
            x = F[f].reindex(e.index)
            ok = x.notna().to_numpy()
            if ok.sum() > 10 and e["y"][ok].nunique() == 2:
                uni.append({"feature": f, "group": group_of(f), "period": period, "top": int(e["y"].sum()),
                            "ordinary": int((e["y"] == 0).sum()), "auc": roc_auc_score(e["y"][ok], x[ok]),
                            "top_median": x[e["y"] == 1].median(), "ordinary_median": x[e["y"] == 0].median()})
    rows = []
    fit_sets = {"T": ev[ev["period"] == "train"],
                "V": ev[(ev["period"] == "validation") & (ev["trading_day"] <= pc.TRAIN_END)]}
    for fit, fe in fit_sets.items():
        if fe["y"].sum() < 10:
            continue
        X, y = F.loc[fe.index, cols], fe["y"].to_numpy()
        _, (s_all,) = fit_predict("lgb", X, y, [F.loc[ev.index, cols]])
        o = oof("lgb", X, y, fe["trading_day"].dt.strftime("%Y-%m").to_numpy())
        s_all[o.index] = o
        for period in PERIODS:
            e = ev[ev["period"] == period]
            if e["y"].sum() < 3:
                continue
            s = s_all.reindex(e.index)
            aucs = []
            for _ in range(N_BOOT // 4):
                b = RNG.integers(0, len(e), len(e))
                if e["y"].iloc[b].nunique() == 2:
                    aucs.append(roc_auc_score(e["y"].iloc[b], s.iloc[b]))
            rows.append({"fit": fit, "period": period, "top": int(e["y"].sum()), "ordinary": int((e["y"] == 0).sum()),
                         "auc": roc_auc_score(e["y"], s), "lo": np.percentile(aucs, 2.5), "hi": np.percentile(aucs, 97.5),
                         "ap": average_precision_score(e["y"], s), "base_rate": e["y"].mean(),
                         "scored": "in fit set (out of fold)" if (fit, period) in (("T", "train"), ("V", "validation")) else "out of sample"})
    return pd.DataFrame(uni), pd.DataFrame(rows)


def main() -> None:
    st = Study()
    print(f"definition {DEF}: {len(st.cases)} cases; sets per period {st.sets.groupby('period')['case_window'].nunique().to_dict()}")
    u = univariate(st)
    u.to_csv(OUT / f"univariate{SUFFIX}.csv", index=False)
    print("univariate done")
    if DEF == "top_global":
        return
    m, shap, coef = models(st)
    m.to_csv(OUT / "models.csv", index=False)
    shap.to_csv(OUT / "shap.csv", index=False)
    coef.to_csv(OUT / "l1_coefficients.csv", index=False)
    print("models done")
    act = u[(u["avail"] != "EP") & (u["period"] == "validation")].assign(d=lambda x: (x["matched_auc"] - 0.5).abs())
    top_feats = act.sort_values("d", ascending=False)["feature"].head(12).tolist()
    lt, ltu = lead_time(st, top_feats)
    lt.to_csv(OUT / "lead_time.csv", index=False)
    ltu.to_csv(OUT / "lead_time_univariate.csv", index=False)
    print("lead time done")
    txt, rl = rules(st)
    (OUT / "rules.txt").write_text(txt, encoding="utf-8")
    rl.to_csv(OUT / "rules.csv", index=False)
    key = ["energy_max5h", "p5_n150_5h", "energy_d5h", "unscheduled_demand_mw_last30", "unscheduled_demand_mw_d5h", "dpv_mw_d5h",
           "wind_mw_last30", "wind_mw_d5h", "coal_mw_d5h", "peaker_mw_last30", "battery_mw_last30", "bat_dis_mwh_5h",
           "out_forced_mw_d5h", "out_total_mw_last30", "pd_headroom_t0", "pd_price_t0", "surprise_req_last30", "reg_raise_max5h",
           "hist_spk_7d"]
    sil, prof = clusters(st, key)
    sil.to_csv(OUT / "cluster_silhouette.csv", index=False)
    prof.to_csv(OUT / "cluster_profiles.csv", index=False)
    print("clusters done")
    tu, tm = top_vs_ordinary(st)
    tu.to_csv(OUT / "top_vs_ordinary_univariate.csv", index=False)
    tm.to_csv(OUT / "top_vs_ordinary_models.csv", index=False)
    print("top vs ordinary done")


if __name__ == "__main__":
    main()
