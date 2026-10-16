import sys
import numpy as np
import pandas as pd

SP = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16"
PROC = "/Users/matthew.yuan/Desktop/HGS/data/processed/"
sys.path.insert(0, SP)
import dispatch_lib as dl  # noqa: E402

rng = np.random.default_rng(0)
def ci(x):
    b = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(4000)]
    return f"{x.mean():+.1f} [{np.percentile(b, 2.5):+.1f}, {np.percentile(b, 97.5):+.1f}]"

rev = pd.read_parquet(SP + "/exp_revenue.parquet")
rev["total"] = rev.energy + rev.fcess
print(f"=== A) Test period {rev.day.min().date()} .. {rev.day.max().date()}, {rev[rev.da == 0].day.nunique()} days, $/day")
for oblig in (False, True):
    for da in (0, 1):
        r = rev[(rev.oblig == oblig) & rev.da.isin([-1, da])]
        days = r[r.da == da].day.unique()
        r = r[r.day.isin(days)]
        w = r.pivot_table(index="day", columns=["method", "market"], values=["total", "energy", "fcess"])
        pj = w["total"]["perfect", "joint"]
        print(f"\n-- capacity obligation={oblig}, days_ahead={da}")
        for m in ["perfect", "baseline", "profile28", "ours"]:
            for mk in ["energy", "joint"]:
                t = w["total"][m, mk]
                print(f"  {m:9s} {mk:6s}: {t.mean():6.1f}  (energy {w['energy'][m, mk].mean():6.1f}, FCESS {w['fcess'][m, mk].mean():5.1f})"
                      f"  capture {t.mean() / pj.mean():5.1%}")
        print("  perfect joint - perfect energy:", ci((pj - w["total"]["perfect", "energy"]).to_numpy()))
        print("  ours joint - ours energy      :", ci((w["total"]["ours", "joint"] - w["total"]["ours", "energy"]).to_numpy()))
        print("  ours joint - baseline joint   :", ci((w["total"]["ours", "joint"] - w["total"]["baseline", "joint"]).to_numpy()))
        print("  ours joint - profile28 joint  :", ci((w["total"]["ours", "joint"] - w["total"]["profile28", "joint"]).to_numpy()))

tr = pd.read_parquet(SP + "/exp_trend.parquet")
tr["total"] = tr.energy + tr.fcess
tr["q"] = tr.day.dt.to_period("Q")
print("\n=== B) Perfect-price opportunity by quarter ($/day)")
t0 = tr[(~tr.oblig) & (tr.s0 == 0)].pivot_table(index="q", columns="market", values=["total", "energy", "fcess"])
t1 = tr[(tr.oblig) & (tr.s0 == 0)].pivot_table(index="q", columns="market", values="total")
th = tr[(~tr.oblig) & (tr.s0 == 1)].pivot_table(index="q", columns="market", values="total")
out = pd.DataFrame({
    "days": tr[(~tr.oblig) & (tr.s0 == 0) & (tr.market == "joint")].groupby("q").size(),
    "energy_only": t0["total"]["energy"], "joint": t0["total"]["joint"],
    "joint_energy_part": t0["energy"]["joint"], "joint_fcess_part": t0["fcess"]["joint"],
    "fcess_gain": t0["total"]["joint"] - t0["total"]["energy"],
    "oblig_energy_only": t1["energy"], "oblig_joint": t1["joint"],
    "fcess_gain_s0_half": th["joint"] - th["energy"],
})
print(out.round(1).to_string())

# C) FCESS enablement cost from 5-min price x requirement
df5 = pd.read_parquet(PROC + "wem_5min.parquet")
cost = sum(df5[k] * df5[f"req_{k}_mw"] for k in dl.FCESS) * (5 / 60)
e_cost = (df5.energy.clip(lower=None) * 0)  # placeholder (energy cost not needed)
qc = cost.groupby(cost.index.to_period("Q")).sum() / 1e6
print("\n=== C) FCESS enablement cost (price x requirement), $M per quarter")
print(qc.round(2).to_string())
for q in ["2026Q1", "2026Q2", "2026Q3"]:
    q = pd.Period(q); print(f"  {q} vs {q - 4}: {qc[q] / qc[q - 4] - 1:+.0%}")
