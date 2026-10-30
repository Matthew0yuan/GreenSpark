"""30-min outage state of Scheduled Facilities, ex post and as known at the time.

Trading day D is read from the snapshot extract of D+3 (the 08:30 snapshot that data/raw/realtime-outages
keeps per day; fallback D+2, D+4..D+6). That snapshot holds the latest version of every outage overlapping
[D+3 09:00 - 7 d, D+3 09:00 + 9 d], so D is covered and its forced outages have had ~2.5 days to be submitted.
Loss per facility and interval = Capacity Credits of its Capacity Year minus the lowest Remaining Available
Capacity (RAC) of its Approved/Accepted outages overlapping the interval, floored at 0 (as vpp.spike.outages),
then summed per fuel and per type of the binding record.
  out_*   ex post: every record in that snapshot
  kn_*    known then: only records first submitted before the interval starts (what the 30-min snapshot of
          that time showed, up to later revisions of the same record)

Also every forced outage of a coal or gas Scheduled Facility that takes >= 50 MW of energy capacity (Capacity
Credits minus RAC; a missing RAC counts as 0), in its latest version across all snapshots -> forced_starts.csv.
These are the unit trips (and other sudden losses) used in onset_state.py.

Run:  python build_outages.py   -> out/outages_30min.parquet, out/forced_starts.csv
"""

import numpy as np
import pandas as pd

from common import OUT, fuel_of
from vpp.spike.outages import ACTIVE, OutageFeatures, capacity_credits, capacity_year, load_snapshot

STEP = np.timedelta64(5, "m")
HALF = np.timedelta64(30, "m")


def day_state(rec: pd.DataFrame, targets: pd.DatetimeIndex, cc: pd.DataFrame) -> pd.DataFrame:
    t = targets.to_numpy()[None, :]
    over = (rec["CommencementInterval"].to_numpy()[:, None] < t + HALF) & ((rec["EndInterval"].to_numpy() + STEP)[:, None] > t)
    first = rec["FirstSubmissionDate"].to_numpy()[:, None]
    fac, inv = np.unique(rec["Facility"].to_numpy(), return_inverse=True)
    years = capacity_year(targets)
    ccf = cc.reindex(fac)
    credit = np.stack([ccf[y].to_numpy() if y in ccf.columns else np.full(len(fac), np.nan) for y in years], axis=1)
    fuel = np.array([fuel_of(f) for f in fac])
    is_forced = (rec["OutageType"] == "Forced").to_numpy()[:, None]
    out = {}
    for basis, mask in (("out", over), ("kn", over & (first <= t))):
        rac = np.where(mask, rec["rac"].fillna(0.0).to_numpy()[:, None], np.inf)
        lowest = np.full((len(fac), len(targets)), np.inf)
        np.minimum.at(lowest, inv, rac)
        hit = mask & (rac <= lowest[inv])
        forced = np.zeros_like(lowest, dtype=bool)
        np.logical_or.at(forced, inv, hit & is_forced)
        loss = np.where(np.isfinite(lowest), np.clip(np.nan_to_num(credit) - lowest, 0.0, None), 0.0)
        gas = np.isin(fuel, ["gas_base", "peaker"])[:, None]
        coal = (fuel == "coal")[:, None]
        out[f"{basis}_total_mw"] = loss.sum(0)
        out[f"{basis}_forced_mw"] = (loss * forced).sum(0)
        out[f"{basis}_planned_mw"] = (loss * ~forced).sum(0)
        out[f"{basis}_coal_mw"] = (loss * coal).sum(0)
        out[f"{basis}_gas_mw"] = (loss * gas).sum(0)
        out[f"{basis}_forced_coal_mw"] = (loss * forced * coal).sum(0)
        out[f"{basis}_forced_gas_mw"] = (loss * forced * gas).sum(0)
        out[f"{basis}_battery_mw"] = (loss * (fuel == "battery")[:, None]).sum(0)
        out[f"{basis}_n_forced"] = ((loss > 1.0) & forced).sum(0)
    return pd.DataFrame(out, index=targets)


def forced_starts(of: OutageFeatures, cc: pd.DataFrame, days: pd.DatetimeIndex) -> pd.DataFrame:
    keep = []
    for d in days:
        snap = load_snapshot(d)
        if snap is None:
            continue
        cls = of.classes(snap)
        f = snap[(snap["OutageType"] == "Forced").to_numpy() & cls.eq("Scheduled Facility").to_numpy()
                 & snap["Status"].isin(ACTIVE).to_numpy()]
        keep.append(f.assign(seen=d))
    f = pd.concat(keep, ignore_index=True)
    f["version"] = pd.to_numeric(f["OutageVersion"], errors="coerce")
    f = f.sort_values(["seen", "version"]).drop_duplicates("OutageNumber", keep="last")
    f["fuel"] = f["Facility"].map(fuel_of)
    f = f[f["fuel"].isin(["coal", "gas_base", "peaker"])]
    years = capacity_year(pd.DatetimeIndex(f["CommencementInterval"]))
    credit = np.array([cc.at[fac, y] if fac in cc.index and y in cc.columns else np.nan for fac, y in zip(f["Facility"], years)])
    f["loss_mw"] = np.clip(np.nan_to_num(credit) - f["rac"].fillna(0.0).to_numpy(), 0, None)
    f = f[f["loss_mw"] >= 50]
    return f.rename(columns={"CommencementInterval": "start", "EndInterval": "end", "FirstSubmissionDate": "first_submitted",
                             "DateTimeofNotification_ForcedOutage": "notified"})[
        ["OutageNumber", "Facility", "fuel", "start", "end", "loss_mw", "first_submitted", "notified", "Description"]]


def main() -> None:
    of = OutageFeatures()
    cc = capacity_credits()
    days = pd.date_range("2023-10-01", "2026-10-15")
    fs = forced_starts(of, cc, pd.date_range("2023-10-01", "2026-10-16"))
    fs.sort_values("start").to_csv(OUT / "forced_starts.csv", index=False)
    print(f"forced outages >= 50 MW on coal/gas units: {len(fs)} ({fs['fuel'].value_counts().to_dict()})")
    parts, source = [], []
    for d in days:
        snap = None
        for lag in (3, 2, 4, 5, 6):
            snap = load_snapshot(d + pd.Timedelta(days=lag))
            if snap is not None:
                break
        targets = pd.date_range(d + pd.Timedelta(hours=8), d + pd.Timedelta(hours=32), freq="30min", inclusive="left")
        if snap is None:
            parts.append(pd.DataFrame(np.nan, index=targets, columns=["out_total_mw"]))
            source.append((d, None))
            continue
        cls = of.classes(snap)
        rec = snap[cls.eq("Scheduled Facility").to_numpy() & snap["Status"].isin(ACTIVE).to_numpy()]
        parts.append(day_state(rec, targets, cc))
        source.append((d, snap["snapshot_file"].iloc[0]))
    df = pd.concat(parts).sort_index()
    df.index.name = "ts"
    df.to_parquet(OUT / "outages_30min.parquet")
    src = pd.DataFrame(source, columns=["trading_day", "snapshot"])
    src.to_csv(OUT / "outages_30min_sources.csv", index=False)
    print(f"{len(df):,} intervals, {src['snapshot'].isna().sum()} days without a snapshot")
    print(df.describe().T[["mean", "50%", "max"]].round(0).to_string())


if __name__ == "__main__":
    main()
