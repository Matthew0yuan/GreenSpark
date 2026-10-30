"""5-min output per fuel group from AEMO Facility SCADA (data/raw/facility-scada, scripts/download_wem.py).

'Average MWh' is a facility's energy in the 5-min dispatch interval (label = interval start, the same as
wem_5min; checked below against operational demand), so MW = MWh x 12. Facilities are grouped by
common.fuel_of. AEMO publishes each trading day the next morning, so everything here is an ex-post
diagnostic, not a signal that was visible before a spike.

Columns: <fuel>_mw (battery = net, + discharging), battery_dis_mw / battery_chg_mw (sum of the positive / the
negative parts per facility), coal_units_on (coal units > 30 MW), peaker_units_on (> 5 MW), largest_mw (the
largest single coal or gas output, what the contingency raise requirement covers), total_mw.

Run:  python build_scada.py   -> out/scada_5min.parquet, out/scada_facilities.csv
"""

import numpy as np
import pandas as pd

from common import FUELS, OUT, PROCESSED, RAW, fuel_of


def read_month(path) -> pd.DataFrame:
    d = pd.read_csv(path, usecols=["Dispatch Interval", "Facility Code", "Average MWh"], engine="pyarrow")
    d.columns = ["ts", "fac", "mwh"]
    d["ts"] = pd.to_datetime(d["ts"], format="%d/%m/%Y %H:%M:%S")
    return d


def main() -> None:
    files = sorted((RAW / "facility-scada").glob("FacilityScada-*.csv"))
    d = pd.concat([read_month(f) for f in files], ignore_index=True)
    d = d.drop_duplicates(["ts", "fac"], keep="last")
    mw = d.pivot(index="ts", columns="fac", values="mwh") * 12.0
    mw = mw.reindex(pd.date_range(mw.index.min(), mw.index.max(), freq="5min", name="ts"))
    fuel = pd.Series({c: fuel_of(c) for c in mw.columns})
    # a few single-interval glitches (KEMERTON_GT11/12 -203 MW on 2023-12-07 13:15, the 0.6 MW BREMER_BAY_WF1
    # at up to 96 MW on 2025-05-26): clip each facility to 1.05 x its 99.95th percentile, and non-storage
    # facilities at -30 MW
    upper = mw.quantile(0.9995) * 1.05
    lower = pd.Series(np.where(fuel == "battery", -np.inf, -30.0), index=mw.columns)
    mw = mw.clip(lower=lower, upper=upper, axis=1)

    # facility list with its output profile, so the fuel mapping can be checked by eye
    h = mw.index.hour
    prof = pd.DataFrame({"fuel": fuel, "first": mw.apply(lambda s: s.first_valid_index()),
                         "last": mw.apply(lambda s: s.last_valid_index()), "mean_mw": mw.mean(), "max_mw": mw.max(),
                         "min_mw": mw.min(), "night_mw": mw[(h >= 0) & (h < 4)].mean(), "noon_mw": mw[(h >= 11) & (h < 14)].mean()})
    prof.sort_values(["fuel", "mean_mw"]).to_csv(OUT / "scada_facilities.csv")

    out = pd.DataFrame(index=mw.index)
    filled = mw.fillna(0.0)
    for f in FUELS:
        out[f"{f}_mw"] = filled.loc[:, fuel.index[fuel == f]].sum(axis=1)
    bat = filled.loc[:, fuel.index[fuel == "battery"]]
    out["battery_dis_mw"] = bat.clip(lower=0).sum(axis=1)
    out["battery_chg_mw"] = (-bat.clip(upper=0)).sum(axis=1)
    out["coal_units_on"] = (filled.loc[:, fuel.index[fuel == "coal"]] > 30).sum(axis=1)
    out["peaker_units_on"] = (filled.loc[:, fuel.index[fuel == "peaker"]] > 5).sum(axis=1)
    out["largest_mw"] = filled.loc[:, fuel.index[fuel.isin(["coal", "gas_base", "peaker"])]].max(axis=1)
    out["total_mw"] = filled.sum(axis=1)
    out["n_reporting"] = mw.notna().sum(axis=1)
    out.to_parquet(OUT / "scada_5min.parquet")
    # Unit trips are taken from the outage register (build_outages.py), not from these curves: a fast fall in
    # output was tried as a trip test, but the Pinjarra cogeneration units are dispatched from ~70 MW to near zero
    # within 15 min hundreds of times (mostly 08:00-10:00), which SCADA alone cannot tell apart from a trip.

    # alignment: total output against operational demand with SCADA shifted by -1/0/+1 intervals; the
    # correlation of the 5-min changes peaks at the right shift
    dem = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["demand_mw"])["demand_mw"]
    for k in (-1, 0, 1):
        a, b = dem.diff().align(out["total_mw"].shift(k).diff(), join="inner")
        print(f"shift {k:+d}: corr of 5-min changes {a.corr(b):.3f}")
    gap = (out["total_mw"] - dem).dropna()
    print(f"total output - operational demand: median {gap.median():.0f} MW, IQR {gap.quantile(.25):.0f}..{gap.quantile(.75):.0f}")
    print(f"{len(out):,} intervals {out.index[0]} -> {out.index[-1]}, {mw.shape[1]} facilities")
    print(prof.groupby("fuel").agg(n=("mean_mw", "size"), mean_mw=("mean_mw", "sum")).round(0).to_string())


if __name__ == "__main__":
    main()
