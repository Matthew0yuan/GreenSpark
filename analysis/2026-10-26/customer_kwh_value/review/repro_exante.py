"""R-B2 review: independent reproduction of the 09:00 (ex-ante) values, the monthly table, the day counts and the
09:00-vs-actual energy error, straight from vpp.policy arrays (not the reviewed kwh_value module).

Run (from the repository root):  .venv/bin/python analysis/2026-10-26/customer_kwh_value/review/repro_exante.py
Writes nothing; prints.
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.evaluate import TRAIN_END  # noqa: E402
from vpp.policy import policy_optimizer as po  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

pd.set_option("display.width", 250)
m, fc = ps.load_market(), ps.load_forecasts()
HH = ps.Household()
KEY = (2.0, 2.0, 0.2, HH, (15.0, 21.0), str(ps.LAST_DAY.date()))
fam = ps.family(*KEY)
j = fam.index(ps.ircr_start(2.0))           # 17:30-19:30
nd = m.n_days
month = m.days.month.to_numpy()
build = m.days <= TRAIN_END

# monthly means over days up to 2026-06-30
src = {"q": fam.paid_kwh[j], "imp": fam.d_import_kwh[j], "fo": fam.d_feedin_other[j], "fe": fam.d_feedin_event[j],
       "gap": fam.gap_kwh[j], "sb": fam.standby_kwh[j], "wear": np.maximum(fam.d_through_kwh[j], 0.0)}
mean = {k: np.array([np.nan] + [v[build & (month == mo)].mean() for mo in range(1, 13)]) for k, v in src.items()}
prof = np.zeros((13, ps.HORIZON))
for mo in range(1, 13):
    prof[mo] = fam.d_net[j, build & (month == mo)].mean(axis=0)
g = {k: v[month] for k, v in mean.items()}
price = np.concatenate([np.where(np.isnan(fc.price), 0.0, fc.price) + fc.p_spike * fc.uplift,
                        np.broadcast_to(m.slot_price[:ps.HORIZON - ps.N], (nd, ps.HORIZON - ps.N))], axis=1)
w_ex = (prof[month] * price).sum(axis=1) / 1000 + g["gap"] * HH.eta * m.slot_price[14:26].mean() / 1000
R = ps.RETAIL
offset = R * g["sb"]
v_self = (R * g["imp"] - g["fo"] - g["gap"] * HH.eta * R - offset) / g["q"]
p_c = v_self - g["fe"] / g["q"] + 0.05 * g["wear"] / g["q"] + 0.5 / g["q"]
v_en = np.where(fc.has_fc, w_ex / g["q"], np.nan)
pz0 = {sc: v_en * float(o.energy_to_zenith) - offset / g["q"] - o.ops_per_event / g["q"] for sc, o in po.SCENARIOS.items()}
flag = fc.flags[0.75]
ircr = {"S1": 0.0, "S2a": 0.0, "S2b": 3.150890, "S3": 1.575483}     # A$/kWh pooled, from repro_accounts.py
pz = {sc: pz0[sc] + np.where(flag, ircr[sc], 0.0) for sc in pz0}
# actual
q_act = fam.paid_kwh[j]
v_en_act = fam.w_actual[j] / q_act
offset_a = R * fam.standby_kwh[j]
v_self_a = (R * fam.d_import_kwh[j] - fam.d_feedin_other[j] - fam.gap_kwh[j] * HH.eta * R - offset_a) / q_act
p_c_a = v_self_a - fam.d_feedin_event[j] / q_act + 0.05 * np.maximum(fam.d_through_kwh[j], 0) / q_act + 0.5 / q_act

ins = (m.days >= "2025-07-01") & (m.days <= "2026-06-30")
t1 = (m.days >= "2026-07-01") & (m.days <= "2026-10-15")
t2 = (m.days >= "2026-10-16") & (m.days <= "2026-10-22")
print("days: in-sample", ins.sum(), "test seen", t1.sum(), "test unseen", t2.sum(),
      "| with 09:00 forecast:", (ins & fc.has_fc).sum(), (t1 & fc.has_fc).sum(), (t2 & fc.has_fc).sum())
print("flagged (q=0.75) in-sample:", (flag & ins).sum(), "test:", (flag & (t1 | t2)).sum(),
      "by month:", pd.Series(m.days[flag & ins].strftime("%Y-%m")).value_counts().sort_index().to_dict())

print("\n=== cross-check against daily.csv (default window, 09:00 values), max |mine - theirs| in c/kWh")
dd = pd.read_csv(HERE / "out" / "daily.csv")
dd = dd[dd["window"] == "17:30-19:30"]
idx = {d: i for i, d in enumerate(m.days.strftime("%Y-%m-%d"))}
for sc in po.SCENARIOS:
    x = dd[dd["scenario"] == sc]
    ii = x["day"].map(idx).to_numpy()
    diffs = {"a_self_use_c": np.nanmax(np.abs(100 * v_self[ii] - x["a_self_use_c"])),
             "d_customer_min_c": np.nanmax(np.abs(100 * p_c[ii] - x["d_customer_min_c"])),
             "b_energy_0900_c": np.nanmax(np.abs(100 * v_en[ii] - x["b_energy_0900_c"])),
             "d_zenith_max_0900_c": np.nanmax(np.abs(100 * pz[sc][ii] - x["d_zenith_max_0900_c"])),
             "flagged": int((flag[ii] != x["flagged"].to_numpy()).sum()) if sc != "S1" else 0}
    print(sc, {k: float(f"{v:.2e}") for k, v in diffs.items()})

print("\n=== flagged-day and other-day means, in-sample, default window (c/kWh)")
fl, un = ins & flag & fc.has_fc, ins & ~flag & fc.has_fc
for name, sel in (("flagged", fl), ("other", un)):
    print(f"{name} ({sel.sum()} days): self-use {100*v_self[sel].mean():.2f}, event DEBS {100*(g['fe']/g['q'])[sel].mean():.2f}, "
          f"wear {100*(0.05*g['wear']/g['q'])[sel].mean():.2f}, hassle {100*(0.5/g['q'])[sel].mean():.2f}, "
          f"customer min {100*p_c[sel].mean():.2f}, energy 09:00 {100*v_en[sel].mean():.2f}, "
          f"offset+ops {100*(offset/g['q'] + 0.05/g['q'])[sel].mean():.2f}, "
          + ", ".join(f"Zmax {sc} {100*pz[sc][sel].mean():.2f}" for sc in pz))
print("customer min, actual, ratio of sums on flagged in-sample days:",
      round(100 * (p_c_a * q_act)[fl].sum() / q_act[fl].sum(), 2))

print("\n=== days with Zenith max > customer min at 09:00, default window")
for sc in pz:
    print(sc, "in-sample", int(((pz[sc] - p_c) > 0)[ins & fc.has_fc].sum()),
          "test", int(((pz[sc] - p_c) > 0)[(t1 | t2) & fc.has_fc].sum()))
s1 = pz["S1"]
print("S1 in-sample: best 09:00 Zenith max", round(100 * np.nanmax(s1[ins]), 2), "on", m.days[ins][np.nanargmax(s1[ins])].date(),
      "| best 09:00 energy", round(100 * np.nanmax(v_en[ins]), 2), "| lowest customer min", round(100 * np.nanmin(p_c[ins]), 2))
pos = ins & fc.has_fc & (v_en_act > p_c_a)
print("in-sample days with actual energy value > actual customer min:", int(pos.sum()), "max",
      round(100 * np.nanmax(v_en_act[ins]), 1), "on", m.days[ins][np.nanargmax(v_en_act[ins])].date())
print("range of S1-S3 other-day monthly Zenith max (c/kWh), in-sample and test:")
rng = []
for sc in pz:
    for per, sel in (("in", ins), ("test", t1 | t2)):
        s = pd.Series(100 * pz[sc][sel & ~flag & fc.has_fc], index=m.days[sel & ~flag & fc.has_fc].strftime("%Y-%m"))
        mm = s.groupby(level=0).mean()
        rng.append((sc, per, round(mm.min(), 2), round(mm.max(), 2)))
print(rng)

print("\n=== monthly table (default window): customer min, energy 09:00, energy actual (ratio of sums), S1 Zenith max")
lab = np.where(ins, "in", np.where(t1, "test-seen", np.where(t2, "test-unseen", "")))
rows = []
for per in ("in", "test-seen", "test-unseen"):
    for mo in sorted(set(m.days[lab == per].strftime("%Y-%m"))):
        sel = (lab == per) & (m.days.strftime("%Y-%m") == mo)
        f = sel & fc.has_fc
        rows.append({"period": per, "month": mo, "flag": int((sel & flag).sum()), "cust_min": round(100 * p_c[sel].mean(), 1),
                     "en_0900": round(100 * np.nanmean(v_en[f]), 1),
                     "en_act": round(100 * fam.w_actual[j][sel].sum() / q_act[sel].sum(), 1),
                     "S1_zmax": round(100 * np.nanmean(pz["S1"][f & ~flag]), 1),
                     "S2b_flag": round(100 * np.nanmean(pz["S2b"][f & flag]), 0) if (f & flag).any() else None,
                     "S3_flag": round(100 * np.nanmean(pz["S3"][f & flag]), 0) if (f & flag).any() else None})
print(pd.DataFrame(rows).to_string(index=False))

print("\n=== 09:00 energy value minus actual (c/kWh), default window")
for name, sel in (("in-sample", ins), ("test seen", t1), ("test unseen", t2)):
    s = sel & fc.has_fc
    err = 100 * (v_en[s] - v_en_act[s])
    print(f"{name}: days {s.sum()}, 09:00 mean {100*v_en[s].mean():.2f}, actual mean (per-day) {100*v_en_act[s].mean():.2f}, "
          f"actual ratio of sums {100*fam.w_actual[j][s].sum()/q_act[s].sum():.2f}, bias {err.mean():.2f}, MAE {np.abs(err).mean():.2f}")
