"""R3: the 48-hour plan (reports/v2_plan.md, section 7.4), from the project's own day-ahead forecast.

Forecast  the Q2A energy forecast for the next trading day, issued strictly at 09:00 (vpp.forecast.build with
          days_ahead=1: AEMO's 08:00 run once public, point-in-time weather, monthly walk-forward trained to
          TRAIN_END); FCESS at the 28-day profile. Its accuracy is reported against the price two days before.
Tomorrow  from 09:00 a plan runs to the end of tomorrow (96 half-hours): tomorrow's forecast prices, its load
          forecast, its CMD cap (the month's peak so far if higher) and, if tomorrow is flagged as a possible system
          peak day (public at 09:00 today), its IRCR cap; a second day's cycles; the energy left at 08:00 the day
          after valued by tomorrow's end-of-day curve at today's level from today's month table, shifted past the
          day after's CMD needs (its IRCR flag is not public yet). The site reserve is not added: tomorrow's needs
          are in the plan itself.
Uses      a reference plan in each report (output only), and, with plan_days=2, the plan the day runs on (only
          today's part is carried out; vpp.dispatch.run_day's extend).

Run:  python -m vpp.v2.horizon [--rebuild]          (forecast accuracy)
"""

import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from vpp import dispatch as dp
from vpp import forecast as fo
from vpp.data import PROCESSED
from vpp.evaluate import period_of

OUT_D1 = PROCESSED / "forecast_dispatch_0900_d1.parquet"
START = 2  # 09:00: the day-ahead forecast is issued


def forecast_d1(rebuild: bool = False) -> pd.DataFrame:
    """The strict 09:00 forecast of the next trading day (cached)."""
    if OUT_D1.exists() and not rebuild:
        return pd.read_parquet(OUT_D1)
    fc = fo.build(days_ahead=1, issue=fo.ISSUE)
    fc.to_parquet(OUT_D1)
    return fc


def tomorrow_prices(fc: pd.DataFrame) -> dict:
    """{issue day: (energy (48,), {FCESS product: planning prices (48,)})} for the next trading day."""
    out = {}
    for issue, g in fc.groupby("issue_date"):
        g = g.sort_values("ts")
        if len(g) != dp.N or g["energy"].isna().any():
            continue
        out[pd.Timestamp(issue).normalize()] = (g["energy"].to_numpy(float),
                                                {k: np.maximum(g[k].to_numpy(float), 0.0) + dp.OFFER_EPS for k in dp.FCESS})
    return out


@dataclass
class Tomorrow:
    """Tomorrow's part of a plan made from 09:00 today (run_day's extend)."""
    prices: np.ndarray                         # $/MWh, tomorrow's 48 half-hours
    fcess: dict                                # {product: planning $/MW/h}
    load: np.ndarray                           # MW the plans assume the factory draws tomorrow
    caps: dict                                 # {cap name: most net charging it allows tomorrow, cap - load (48,)}
    extra: dict = field(default_factory=dict)  # caps tomorrow has that today does not: name -> (kind, penalty)
    terminal: np.ndarray | None = None         # $ per MWh of each segment left at 08:00 the day after
    budget_mwh: float = 0.0                    # tomorrow's cycle budget

    def __call__(self, t0: int, fe, ff, kw: dict, names: list) -> tuple:
        if t0 < START:
            return fe, ff, kw
        n = dp.N
        fe2 = np.r_[np.asarray(fe, float), self.prices]
        ff2 = None if ff is None else {k: np.r_[np.asarray(ff[k], float), self.fcess[k]] for k in dp.FCESS}
        kw2 = dict(kw)
        kw2["load"] = np.r_[np.asarray(kw["load"], float), self.load]
        layers = [(np.r_[np.asarray(cap, float), self.caps.get(name, np.full(n, np.inf))], kind, pen, comp)
                  for name, (cap, kind, pen, comp) in zip(names, kw["layers"])]
        for name, (kind, pen) in self.extra.items():
            if name not in names:
                layers.append((np.r_[np.full(len(fe), np.inf), self.caps[name]], kind, pen, False))
        kw2["layers"] = layers
        if kw.get("comp_floor") is not None:
            kw2["comp_floor"] = np.r_[np.asarray(kw["comp_floor"], float), np.zeros(n)]
        kw2["soft"] = [(np.r_[np.asarray(f, float), np.zeros(n)], v) for f, v in kw.get("soft", ())]
        if kw.get("fcess_hi") is not None:
            kw2["fcess_hi"] = {k: np.r_[np.asarray(v, float), np.full(n, np.asarray(v, float)[-1])]
                               for k, v in kw["fcess_hi"].items()}
        kw2["terminal_value"] = self.terminal
        kw2["budget"] = (kw.get("budget") or 0.0) + self.budget_mwh
        return fe2, ff2, kw2


def reference(rec: dict, tomorrow: Tomorrow, asset, site, prefs, dw: float) -> dict | None:
    """The 48-hour reference plan from a decision's record: today's inputs as recorded, tomorrow added; the site
    reserve's floors left out (tomorrow's needs are in the plan). None before 09:00."""
    t0 = int(rec["t0"])
    if t0 < START:
        return None
    names = list(rec["cap_names"])
    layers = [(np.asarray(c, float), k, p, bool(comp)) for c, k, p, comp in
              zip(rec["caps"], rec["cap_kinds"], rec["cap_penalties"], rec["cap_compliance"])]
    soft = [(np.asarray(f, float), v) for n, f, v in zip(rec["soft_names"], rec["soft_floors"], rec["soft_values"])
            if not str(n).startswith(("site reserve", "peak-day reserve"))]
    kw = dict(s_init=float(rec["stored"]), budget=float(rec["budget"]), site=site, load=np.asarray(rec["load"], float),
              terminal_value=None, wear_per_mwh=dw, layers=layers, comp_floor=np.asarray(rec["hard"], float), soft=soft)
    ff = None if rec.get("fcess_prices") is None else dict(rec["fcess_prices"])
    fe2, ff2, kw2 = tomorrow(t0, np.asarray(rec["prices"], float), ff, kw, names)
    out = dp.plan(asset, fe2, ff2, **kw2)
    return {**out, "t0": t0}


def accuracy(fc: pd.DataFrame) -> pd.DataFrame:
    """Energy MAE of the day-ahead forecast and of the price two days before, by period."""
    x = fc.dropna(subset=["energy", "actual_energy"])
    err = (x["energy"] - x["actual_energy"]).abs()
    base = (x["yday_energy"] - x["actual_energy"]).abs()
    per = pd.Series(period_of(x["target_date"]), index=x.index)
    return pd.DataFrame({"days": x.groupby(per)["target_date"].nunique(), "mae": err.groupby(per).mean(),
                         "mae_two_days_before": base.groupby(per).mean()})


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    fc = forecast_d1("--rebuild" in argv)
    print(f"day-ahead forecast issued 09:00: {fc['target_date'].min().date()} .. {fc['target_date'].max().date()}; "
          f"latest input public at {fc['issue_time'].max()}")
    print(accuracy(fc).round(2).to_string())


if __name__ == "__main__":
    main()
