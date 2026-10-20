"""Spike risk: probability that a trading interval's price reaches SPIKE, issued for the same trading
day. Spikes are too rare and too erratic to time from the morning (reports/findings.md, section 7), so
the dispatcher uses this as a risk level - how much energy to keep on offer - not as a price.

Signals known at issue time: the AEMO 08:00 run (price, capacity margin), recent prices and spikes,
ECMWF weather. LightGBM classifier with monthly walk-forward refits, like the price models. OUT is
the original "08:15" issue, OUT_0900 the strict 09:00 one (vpp.forecast), covering 09:00 .. 07:30.

Run:  .venv/bin/python -m vpp.spike_risk [--legacy]
"""

import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from vpp import aemo_correction as ac
from vpp.data import PROCESSED, trading_date
from vpp.evaluate import period_of
from vpp.forecast import FORECAST_START, ISSUE

SPIKE = 200.0  # $/MWh
OUT = PROCESSED / "spike_risk.parquet"
OUT_0900 = PROCESSED / "spike_risk_0900.parquet"
EXTRA = ["margin_avail", "margin_inservice", "day_margin_min", "day_aemo_max", "net_demand_fc", "yday_max", "spike_days_7"]
CLF_PARAMS = dict(
    objective="binary", n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0,
    n_jobs=ac.LGB_PARAMS["n_jobs"],
)


def add_signals(frame: pd.DataFrame, df30: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    f["margin_avail"] = f["aemo_available_injection_mw"] - f["aemo_req_energy_mw"]
    f["margin_inservice"] = f["aemo_in_service_injection_mw"] - f["aemo_req_energy_mw"]
    f["net_demand_fc"] = f["aemo_demand_level_mw"] - f["aemo_dpv_level_mw"]
    run = f.groupby(["issue_date", "days_ahead"])
    f["day_margin_min"] = run["margin_avail"].transform("min")
    f["day_aemo_max"] = run["aemo_energy"].transform("max")
    daily_max = df30["energy"].groupby(trading_date(df30.index)).max()
    last = f["issue_date"] - pd.Timedelta(days=1)
    f["yday_max"] = daily_max.reindex(last).to_numpy()
    f["spike_days_7"] = (daily_max >= SPIKE).astype(float).rolling(7).sum().reindex(last).to_numpy()
    return f


def walk_forward_risk(f: pd.DataFrame, days_ahead: int = 0, start: pd.Timestamp = FORECAST_START) -> pd.Series:
    data = f[(f["days_ahead"] == days_ahead) & f["actual_energy"].notna()]
    cols = ac.features_for("energy", weather=True) + EXTRA
    y = (data["actual_energy"] >= SPIKE).astype(int)

    def fit_predict(train: pd.Series, test: pd.Series) -> pd.Series:
        clf = lgb.LGBMClassifier(**CLF_PARAMS).fit(data.loc[train, cols], y[train])
        return pd.Series(clf.predict_proba(data.loc[test, cols])[:, 1], index=data.index[test])

    return ac.monthly_refits(data, days_ahead, start, fit_predict)


def main(argv: list[str] | None = None) -> None:
    legacy = "--legacy" in (sys.argv[1:] if argv is None else argv)
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
    weather = pd.read_parquet(PROCESSED / "weather_30min.parquet")
    f = add_signals(ac.build_frame(df30, runs, weather, issue=None if legacy else ISSUE), df30)
    p = walk_forward_risk(f)
    out = f.loc[p.index, ["issue_date", "ts", "days_ahead", "target_date", "actual_energy"]].assign(p_spike=p)
    if not legacy:
        out.loc[(out["ts"] < out["issue_date"] + ISSUE).to_numpy(), "p_spike"] = np.nan
    out = out.sort_values("ts").reset_index(drop=True)
    out.to_parquet(OUT if legacy else OUT_0900)
    for name in ("validation", "test"):
        x = out[(period_of(out["target_date"]) == name) & out["p_spike"].notna().to_numpy()]
        spike = x["actual_energy"] >= SPIKE
        print(f"{name}: {spike.sum()} spike intervals ({spike.mean():.1%}), AUC {roc_auc_score(spike, x['p_spike']):.3f}, "
              f"mean risk {x['p_spike'].mean():.3f}, share of intervals with risk >= 0.05/0.10/0.20: "
              f"{(x['p_spike'] >= 0.05).mean():.1%} / {(x['p_spike'] >= 0.10).mean():.1%} / {(x['p_spike'] >= 0.20).mean():.1%}")
    print(f"saved {OUT if legacy else OUT_0900}")


if __name__ == "__main__":
    main()
