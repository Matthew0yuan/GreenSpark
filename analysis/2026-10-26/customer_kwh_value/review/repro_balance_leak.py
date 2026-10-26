"""R-B2 review: (1) the energy balance and ΔN of the default window, from my own run of the household step (vpp.policy
step and baseline only); (2) does any input of the in-sample household physics use data after 2026-06-30? The
existing load_market() normalises rooftop PV by a CENTRED one-year rolling maximum and the hot/cold load factor by a
calendar-month median over ALL data (test months included). I rebuild both from data before 2026-07-01 08:00 only
and measure how much the 09:00 (ex-ante) values of test days move.

Run (from the repository root):  .venv/bin/python analysis/2026-10-26/customer_kwh_value/review/repro_balance_leak.py
Writes nothing; prints.
"""

import sys

sys.dont_write_bytecode = True

from dataclasses import replace  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import site_bill as sb  # noqa: E402,F401
from vpp.data import PROCESSED  # noqa: E402
from vpp.evaluate import TRAIN_END  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

HH = ps.Household()
N, DT, eta = ps.N, ps.DT, HH.eta
S = int(round(2 * (17.5 - 8)))      # 17:30
S0, NEV = S - 4, 4
RES = 0.2 * HH.usable_kwh


def run(m: ps.Market):
    pv, load = ps.household_series(m, HH)
    b = ps.simulate(pv, load, HH)
    nd = m.n_days
    base = np.arange(nd) * N
    soc = b.soc[base + S0].copy()
    out = {k: np.zeros(nd) for k in ("avail", "pv", "home", "export", "loss", "retained", "dimp", "dexp", "dloss",
                                     "q", "sb", "fo", "fe", "wear", "w")}
    dnet = np.zeros((nd, ps.HORIZON))
    rate = np.zeros(nd)
    for k in range(S0, ps.HORIZON):
        i = base + k
        mode = ps.STANDBY if k < S else ps.EVENT if k < S + NEV else ps.SELF
        if k == S:
            rate = np.minimum(HH.power_kw, np.maximum(soc - RES, 0) * eta / (NEV * DT))
            out["avail"] = soc - RES
        soc, imp, exp, dis, ch, _ = ps.step(soc, b.pv[i], b.load[i], np.full(nd, mode), rate, HH, RES)
        _, bimp, bexp, bdis, bch, _ = ps.step(b.soc[i], b.pv[i], b.load[i], np.full(nd, ps.SELF), np.zeros(nd), HH, 0.0)
        out["dimp"] += (imp - b.imp[i]) * DT
        out["dexp"] += (exp - b.exp[i]) * DT
        out["dloss"] += ((ch - bch) * (1 - eta) + (dis - bdis) * (1 / eta - 1)) * DT
        out["wear"] += (dis - b.dis[i]) * DT
        dnet[:, k] = ((exp - imp) - (b.exp[i] - b.imp[i])) * DT
        fi = ps.feed_in(np.array(k))
        if mode == ps.STANDBY:
            out["sb"] += np.maximum(imp - b.imp[i], 0) * DT
            out["fo"] += (exp - b.exp[i]) * DT * fi
        elif mode == ps.EVENT:
            out["q"] += exp * DT
            out["pv"] += b.pv[i] * DT
            out["home"] += (b.load[i] - imp) * DT
            out["export"] += exp * DT
            out["loss"] += dis * (1 / eta - 1) * DT
            out["fe"] += (exp - b.exp[i]) * DT * fi
            if k == S + NEV - 1:
                out["retained"] = soc - RES
        else:
            out["fo"] += (exp - b.exp[i]) * DT * fi
        out["w"] += dnet[:, k] * m.rtp[i] / 1000
    out["gap"] = soc - b.soc[base + ps.HORIZON]
    out["w"] += out["gap"] * eta * m.slot_price[14:26].mean() / 1000
    out["q"] = np.minimum(out["q"], HH.usable_kwh)
    out["sb"] = np.minimum(out["sb"], HH.usable_kwh)
    out["wear"] = np.maximum(out["wear"], 0)
    out["dnet"] = dnet
    return out


def exante(m, fc, o):
    month = m.days.month.to_numpy()
    build = m.days <= TRAIN_END
    keys = ("q", "dimp", "fo", "fe", "gap", "sb", "wear")
    mean = {k: np.array([np.nan] + [o[k][build & (month == mo)].mean() for mo in range(1, 13)])[month] for k in keys}
    prof = np.zeros((13, ps.HORIZON))
    for mo in range(1, 13):
        prof[mo] = o["dnet"][build & (month == mo)].mean(axis=0)
    price = np.concatenate([np.where(np.isnan(fc.price), 0.0, fc.price) + fc.p_spike * fc.uplift,
                            np.broadcast_to(m.slot_price[:ps.HORIZON - N], (m.n_days, ps.HORIZON - N))], axis=1)
    w = (prof[month] * price).sum(axis=1) / 1000 + mean["gap"] * eta * m.slot_price[14:26].mean() / 1000
    R = ps.RETAIL
    q = mean["q"]
    off = R * mean["sb"]
    v_self = (R * mean["dimp"] - mean["fo"] - mean["gap"] * eta * R - off) / q
    p_c = v_self - mean["fe"] / q + 0.05 * mean["wear"] / q + 0.5 / q
    p_z = w / q - off / q - 0.05 / q
    return {"q": q, "p_c": 100 * p_c, "v_energy": 100 * w / q, "p_z_S1": 100 * p_z}


m, fc = ps.load_market(), ps.load_forecasts()
o = run(m)
print("=== 1. energy balance, default window, all", m.n_days, "days, means per responding customer per call (kWh)")
for k in ("avail", "pv", "home", "export", "loss", "retained", "dimp", "dexp", "dloss", "gap"):
    print(f"  {k:9s} {o[k].mean():.3f}")
ev = o["avail"] + o["pv"] - (o["home"] + o["export"] + o["loss"] + o["retained"])
hz = (o["dimp"] - o["dexp"]) - (o["dloss"] + o["gap"])
print(f"  max |event residual| {np.abs(ev).max():.2e}, max |horizon residual| {np.abs(hz).max():.2e}")
print(f"  mean ΔN {o['dnet'].sum(axis=1).mean():.3f} kWh; mean Q all days {o['q'].mean():.3f}; "
      f"in-sample Q {o['q'][(m.days >= '2025-07-01') & (m.days <= '2026-06-30')].mean():.3f}")
print(f"  max event power: export {(o['export'] / 2).max():.3f} kW avg over the event (inverter 5 kW)")

print("\n=== 2. look-ahead in the household inputs: rebuild PV cap and load factor from data before 2026-07-01 08:00")
df = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["demand_mw", "dpv_mw"])
cut = pd.Timestamp("2026-07-01 08:00")
ts = pd.date_range(m.days[0] + ps.H, periods=m.n_days * N, freq="30min")
dpv = df["dpv_mw"]
cap_full = dpv.rolling(365 * N, center=True, min_periods=N).max().reindex(ts)
dpv_tr = dpv[dpv.index < cut]
cap_tr = dpv_tr.rolling(365 * N, center=True, min_periods=N).max().reindex(ts)
in_ts = ts < cut
rel = (cap_full[in_ts] / cap_tr[in_ts] - 1).abs()
print(f"  PV capacity proxy, in-sample slots: max relative change {rel.max():.4f}; share of slots changed "
      f"{(rel > 1e-12).mean():.3f}")
under = (df["demand_mw"] + df["dpv_mw"])
dmax = under.groupby((under.index - ps.H).normalize()).max()
med_full = dmax.groupby(dmax.index.month).median()
dmax_tr = dmax[dmax.index < pd.Timestamp("2026-07-01")]
med_tr = dmax_tr.groupby(dmax_tr.index.month).median()
print("  month median of daily peak underlying demand, full vs before 2026-07-01 (MW):")
print("   ", {mo: (round(float(med_full[mo]), 0), round(float(med_tr[mo]), 0)) for mo in range(1, 13)})
# rebuild the Market arrays for the in-sample slots only (test-day slots keep the original inputs)
a = df.reindex(ts)
pv_cf_tr = (a["dpv_mw"] / cap_tr).clip(0, 1).fillna(0.0).to_numpy()
fac_tr = (dmax_tr / dmax_tr.groupby(dmax_tr.index.month).transform("median")).clip(0.7, 1.8).reindex(m.days)
fac_full = (dmax / dmax.groupby(dmax.index.month).transform("median")).clip(0.7, 1.8).reindex(m.days).fillna(1.0)
fac_new = fac_tr.fillna(fac_full).to_numpy()
pv_new = np.where(in_ts, pv_cf_tr, m.pv_cf[:m.n_days * N])
pad = lambda x: np.r_[x, x[-N:-N + 14]]  # noqa: E731
m2 = replace(m, pv_cf=pad(pv_new), load_factor=pad(np.repeat(fac_new, N)))
o2 = run(m2)
x1, x2 = exante(m, fc, o), exante(m, fc, o2)
test = m.days > TRAIN_END
for k in ("q", "p_c", "v_energy", "p_z_S1"):
    d = np.abs(x1[k][test] - x2[k][test])
    print(f"  test-day 09:00 {k:9s}: max |full - truncated| {np.nanmax(d):.3f}, mean {np.nanmean(d):.3f} "
          f"({'kWh' if k == 'q' else 'c/kWh'}); mean value full {np.nanmean(x1[k][test]):.2f}, truncated {np.nanmean(x2[k][test]):.2f}")
