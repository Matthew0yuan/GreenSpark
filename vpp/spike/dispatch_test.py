"""Does a better spike forecast earn more? The Q2B dispatcher with only its spike-risk input swapped.

Everything else is vpp.backtest's main scenario (battery, $30/MWh wear, example site, capacity obligation,
75 $/MWh hurdle, price forecast, day-to-day state of charge); settlement at actual prices. The risk enters
the existing risk reserve: keep RESERVE_MWH stored in intervals whose risk >= THRESHOLD (vpp.dispatch). The
reserve setting (1.0 MWh at risk >= 20%) is the one chosen on validation on 2026-10-16 (reports/findings.md
section 11) and is not re-tuned per input.

Risk inputs, all for the 48 intervals of trading day D planned at 08:15 on D:
  none        no reserve (the strategy as it runs today)
  existing    vpp.spike_risk p_spike (issued "08:15" on D with the 08:00 run)
  dayahead    this work's D+1 forecast issued 09:00 on D-1 (calibrated), the only new one public by 08:15
  perfect     1 where the interval actually spiked: an upper bound, not a strategy

Run:  python -m vpp.spike.dispatch_test MODEL   (MODEL = the D+1 model chosen on validation, e.g. B4)
"""

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp.data import PROCESSED
from vpp.evaluate import period_of
from vpp.spike.metrics import OUT

THRESHOLD, RESERVE_MWH = 0.20, 1.0
STRATS = {
    "forecast": dp.Strategy("forecast"),
    "forecast+offer(value)": dp.Strategy("forecast+offer(value)", offer=True, offer_rule="value"),
}


def risk_inputs(days: list[dict], model: str) -> dict[str, list[np.ndarray]]:
    pred = pd.read_parquet(PROCESSED / "spike_predictions.parquet")
    p = pred[(pred["model"] == model) & (pred["days_ahead"] == 1)]
    day_ahead = p.set_index("target_time")["p_cal"].fillna(p.set_index("target_time")["p_raw"])
    rtp = pd.read_parquet(PROCESSED / "wem_30min.parquet")["energy"]
    out = {"none": [], "existing": [], "dayahead": [], "perfect": []}
    for d in days:
        ts = pd.date_range(d["day"] + bt.H, periods=dp.N, freq="30min")
        out["none"].append(None)
        out["existing"].append(d["risk"])
        out["dayahead"].append(day_ahead.reindex(ts).fillna(0.0).to_numpy())
        out["perfect"].append((rtp.reindex(ts) >= 200).astype(float).to_numpy())
    return out


_DAYS, _RISK = [], {}


def _init(days, risk):
    global _DAYS, _RISK
    _DAYS, _RISK = days, risk


def _run(task: tuple[str, str]) -> list[dict]:
    strat_name, risk_name = task
    strat = STRATS[strat_name]
    prefs = bt.PREFS if risk_name == "none" else replace(bt.PREFS, risk_threshold=THRESHOLD, reserve_mwh=RESERVE_MWH)
    asset, site = bt.LEGACY["main"].asset, bt.SITE
    rows, soc = [], 0.0
    for d, risk in zip(_DAYS, _RISK[risk_name]):
        pe, pf = d["prices"]["forecast"]
        summary, _ = dp.run_day(asset, strat, prefs, pe, pf, d["rtp"], d["act_f"], d["p5"],
                                [ct.capacity_obligation(asset.battery, d["window"])], risk, soc, d["p5_f"], site,
                                d["load_fc"], d["load_act"])
        soc = summary["soc_end"]
        rows.append({"strategy": strat_name, "risk": risk_name, "day": d["day"], **summary})
    return rows


def main(model: str) -> None:
    days = bt.load_days()
    risk = risk_inputs(days, model)
    tasks = [(s, r) for s in STRATS for r in risk]
    with ProcessPoolExecutor(min(len(tasks), max(1, (os.cpu_count() or 2) - 1)), initializer=_init, initargs=(days, risk)) as pool:
        daily = pd.DataFrame([row for rows in pool.map(_run, tasks) for row in rows])
    daily["period"] = period_of(daily["day"])
    daily.to_csv(OUT / "dispatch_daily.csv", index=False)
    rows = []
    for (s, period), g in daily.groupby(["strategy", "period"]):
        w = g.pivot(index="day", columns="risk", values="total")
        for r in ["existing", "dayahead", "perfect"]:
            lo, hi = bt.bootstrap_ci((w[r] - w["none"]).to_numpy())
            rows.append({"strategy": s, "period": period, "risk": r, "days": len(w), "total_none": w["none"].mean(),
                         "total": w[r].mean(), "gain": (w[r] - w["none"]).mean(), "lo": lo, "hi": hi,
                         "days_better": int(((w[r] - w["none"]) > 0.5).sum()), "days_worse": int(((w[r] - w["none"]) < -0.5).sum())})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "dispatch_summary.csv", index=False)
    print(res.round(2).to_string())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "B4")
