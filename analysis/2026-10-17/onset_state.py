"""The spike itself: the system state in its first 30 minutes, and sudden unit losses around the onset.

Unit losses come from the outage register (build_outages.py -> forced_starts.csv: forced outages of coal and
gas units taking >= 50 MW, latest version). Records of one facility less than 30 min apart are merged into one
episode (a trip is usually followed by a string of 5-min "return to service delayed" records); an episode is a
  trip            if any of its descriptions mentions a trip
  non-compliance  if all are "Energy: NonCompliance" records (output short of the dispatch target)
  other           otherwise (technical limits, slow ramps, extensions)
Per window: episodes starting in [t0 - 60 min, t0 + 30 min) ("at the onset") and in [t0 - 5 h, t0 - 60 min)
("earlier"), plus the 5-min state in [t0, t0 + 30 min). Compared with the matched controls (matched AUC, share of
windows with at least one episode) per period. The register is ex post: forced outages were first submitted a
median 17 h after they began.

Run:  python onset_state.py   -> out/onset_state.csv, out/unit_loss_episodes.csv
"""

import numpy as np
import pandas as pd

from analyze import PERIODS, Study, boot_ci, matched_ranks
from common import OUT

AT = (-60, 30)
EARLIER = (-300, -60)


def episodes() -> pd.DataFrame:
    f = pd.read_csv(OUT / "forced_starts.csv", parse_dates=["start", "end", "first_submitted", "notified"])
    f["kind"] = np.where(f["Description"].str.contains("trip", case=False, na=False), "trip",
                         np.where(f["Description"].str.contains("NonCompliance", na=False), "noncompliance", "other"))
    rows = []
    for fac, g in f.sort_values("start").groupby("Facility"):
        cur = None
        for r in g.itertuples():
            if cur is not None and r.start <= cur["end"] + pd.Timedelta(minutes=30):
                cur["end"] = max(cur["end"], r.end)
                cur["loss_mw"] = max(cur["loss_mw"], r.loss_mw)
                cur["kinds"].add(r.kind)
                continue
            if cur is not None:
                rows.append(cur)
            cur = {"facility": fac, "fuel": r.fuel, "start": r.start, "end": r.end, "loss_mw": r.loss_mw, "kinds": {r.kind},
                   "first_submitted": r.first_submitted, "description": r.Description}
        rows.append(cur)
    e = pd.DataFrame(rows)
    e["kind"] = np.where(e["kinds"].apply(lambda k: "trip" in k), "trip",
                         np.where(e["kinds"].apply(lambda k: k == {"noncompliance"}), "noncompliance", "other"))
    e["submitted_after_h"] = (e["first_submitted"] - e["start"]).dt.total_seconds() / 3600
    return e.drop(columns="kinds")


def main() -> None:
    st = Study()
    ep = episodes()
    ep.to_csv(OUT / "unit_loss_episodes.csv", index=False)
    print(f"episodes: {len(ep)} ({ep['kind'].value_counts().to_dict()}); trips by fuel {ep[ep['kind'] == 'trip']['fuel'].value_counts().to_dict()}")
    t0 = st.win["t0"]
    feats = {}
    starts = ep["start"].to_numpy()
    for name, (lo, hi) in (("at", AT), ("earlier", EARLIER)):
        a = (t0 + pd.Timedelta(minutes=lo)).to_numpy()
        b = (t0 + pd.Timedelta(minutes=hi)).to_numpy()
        for kind in ("trip", "noncompliance", "other", "any"):
            sel = ep if kind == "any" else ep[ep["kind"] == kind]
            s = np.sort(sel["start"].to_numpy())
            n = np.searchsorted(s, b, side="left") - np.searchsorted(s, a, side="left")
            feats[f"loss_{kind}_{name}_n"] = pd.Series(n.astype(float), index=st.win.index)
    del starts
    m = st.mats
    onset = lambda v: m[v][[c for c in m[v].columns if 0 <= c < 30]].mean(axis=1)  # noqa: E731
    before = lambda v: m[v][[c for c in m[v].columns if -60 <= c < -30]].mean(axis=1)  # noqa: E731
    for v in ("unscheduled_demand_mw", "residual_mw", "wind_mw", "battery_mw", "peaker_mw", "coal_mw", "gas_base_mw", "dpv_mw",
              "reg_raise", "cont_raise", "req_cont_raise_mw", "energy"):
        feats[f"{v}_onset"] = onset(v)
    for v in ("wind_mw", "coal_mw", "gas_base_mw", "peaker_mw", "battery_mw", "unscheduled_demand_mw"):
        feats[f"{v}_jump_onset"] = onset(v) - before(v)
    F = pd.DataFrame(feats)
    rows = []
    for period in PERIODS:
        s = st.sets[st.sets["period"] == period]
        if s.empty:
            continue
        cw, kw = s["case_window"].unique(), s["control_window"].unique()
        regime = "old" if period == "train" else "new"
        w = st.win
        ordinary = w.index[(w["regime"] == regime) & (w["period"] == period)
                           & ((w["kind"] == "ordinary") | ((w["kind"] == "top") & ~w["top_regime"]))]
        for f in F.columns:
            r = matched_ranks(F[f], s)
            lo, hi = boot_ci(r.to_numpy())
            row = {"feature": f, "period": period, "cases": len(cw), "case_median": F[f].reindex(cw).median(),
                   "control_median": F[f].reindex(kw).median(), "ordinary_median": F[f].reindex(ordinary).median(),
                   "matched_auc": r.mean(), "lo": lo, "hi": hi}
            if f.startswith("loss_"):
                row.update(case_share=(F[f].reindex(cw) > 0).mean(), control_share=(F[f].reindex(kw) > 0).mean(),
                           ordinary_share=(F[f].reindex(ordinary) > 0).mean())
            rows.append(row)
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "onset_state.csv", index=False)
    pd.set_option("display.width", 250)
    print(out[out["feature"].str.startswith("loss_")][["feature", "period", "cases", "case_share", "control_share", "ordinary_share",
                                                       "matched_auc", "lo", "hi"]].round(3).to_string())
    print(out[~out["feature"].str.startswith("loss_")][["feature", "period", "case_median", "control_median", "ordinary_median",
                                                        "matched_auc", "lo", "hi"]].round(3).to_string())


if __name__ == "__main__":
    main()
