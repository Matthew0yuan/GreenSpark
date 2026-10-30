"""Outage features from the Real-Time Outage snapshot that was public at the issue time.

For each issue day the snapshot is the one scripts/download_outages.py picked (newest file listed
before 09:00, normally 08:30). Nothing later is used: each snapshot holds the version of every outage as
it stood then, so revised end times, capacities and statuses only enter once a later snapshot shows them.

Per target interval t (30 min):
  out_loss_mw      capacity a Scheduled Facility loses in t: Capacity Credits for t's Capacity Year minus
                   the lowest Remaining Available Capacity (RAC) of its Approved/Accepted outages that
                   overlap t, floored at 0, summed over facilities (overlapping records are not added up).
                   Semi- and Non-Scheduled (wind, solar) are left out: their RAC is not on the same basis
                   as their credits, and their output depends on the weather anyway.
  out_loss_forced_mw / out_loss_planned_mw   the same split by the type of the binding record
  out_n_gen, out_n_full   generating facilities with an outage overlapping t / with RAC = 0
  out_pending_mw   the loss from planned outages not yet approved (Awaiting Assessment, Requires More Info)
  out_new24_mw     the loss from records first submitted in the 24 hours before the issue
  out_rev_mw       out_loss_mw minus the same quantity from the previous day's snapshot (newly reported
                   outages and delayed returns raise it; early returns lower it)
Per issue: out_now_mw (out_loss_mw for the interval containing the issue time), out_forced24_n (generating
facilities with a forced-outage record submitted in the last 24 hours), out_age_h (issue - snapshot label).
"""

import numpy as np
import pandas as pd

from vpp.data import RAW

ACTIVE = {"Approved", "Accepted"}
PENDING = {"AwaitingAssessment", "RequiresMoreInfo"}
GENERATING = {"Scheduled Facility", "Semi-Scheduled Facility", "Non-Scheduled Facility"}
STEP = pd.Timedelta(minutes=5)
HALF = pd.Timedelta(minutes=30)
PER_TARGET = ["out_loss_mw", "out_loss_forced_mw", "out_loss_planned_mw", "out_n_gen", "out_n_full", "out_pending_mw",
              "out_new24_mw"]


def capacity_credits() -> pd.DataFrame:
    """Capacity Credits (MW) per facility (rows) and Capacity Year start (columns, e.g. 2025 = 2025-26)."""
    files = sorted((RAW / "facilities").glob("capacity-credits-since-market-start-up-to-capacity-year-*.csv"))
    cc = pd.read_csv(files[-1]).set_index("Facility")
    years = [c for c in cc.columns if len(c) == 7 and c[4] == "-"]
    cc = cc[years].apply(pd.to_numeric, errors="coerce")
    cc.columns = [int(c[:4]) for c in years]
    return cc


def facility_class() -> pd.Series:
    f = pd.read_csv(RAW / "facilities" / "facilities.csv")
    return f.set_index("Facility Code")["Facility Class"]


def capacity_year(t: pd.DatetimeIndex) -> np.ndarray:
    """Capacity Year (start year) of each time: years run from 1 October."""
    return np.where(t.month >= 10, t.year, t.year - 1)


def load_snapshot(issue_date: pd.Timestamp) -> pd.DataFrame | None:
    path = RAW / "realtime-outages" / "extracts" / f"{issue_date:%Y%m%d}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["rac"] = pd.to_numeric(df["FacilityRAC"], errors="coerce")
    return df


class OutageFeatures:
    def __init__(self):
        self.cc = capacity_credits()
        self.cls = facility_class()

    def _loss(self, rec: pd.DataFrame, targets: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(loss MW, binding record is forced, facility has an outage, facility RAC = 0) per facility x target."""
        if rec.empty:
            z = np.zeros((0, len(targets)))
            return z, z.astype(bool), z.astype(bool), z.astype(bool)
        t0 = targets.to_numpy()[None, :]
        over = (rec["CommencementInterval"].to_numpy()[:, None] < t0 + HALF.to_timedelta64()) & \
               ((rec["EndInterval"] + STEP).to_numpy()[:, None] > t0)
        rac = np.where(over, rec["rac"].fillna(0.0).to_numpy()[:, None], np.inf)
        fac, inv = np.unique(rec["Facility"].to_numpy(), return_inverse=True)
        lowest = np.full((len(fac), len(targets)), np.inf)
        np.minimum.at(lowest, inv, rac)
        # the record that sets the lowest RAC decides whether the loss counts as forced or planned
        forced = np.zeros_like(lowest, dtype=bool)
        is_forced = (rec["OutageType"] == "Forced").to_numpy()
        for k in range(len(rec)):
            hit = over[k] & (rac[k] <= lowest[inv[k]])
            forced[inv[k]] |= hit & is_forced[k]
        years = capacity_year(targets)
        cc = self.cc.reindex(fac)
        credit = np.stack([cc[y].to_numpy() if y in cc.columns else np.full(len(fac), np.nan) for y in years], axis=1)
        loss = np.where(np.isfinite(lowest), np.clip(np.nan_to_num(credit) - lowest, 0.0, None), 0.0)
        return loss, forced, np.isfinite(lowest), lowest == 0.0

    def classes(self, snap: pd.DataFrame) -> pd.Series:
        """Facility Class; facilities missing from today's list (retired, renamed) by their technology fields."""
        cls = snap["Facility"].map(self.cls)
        firm = snap["FTT_NonIntermittentGenerationSystem_RAC"].notna() | snap["FTT_ElectricStorageResource_Capacity_RAC"].notna()
        cls = cls.fillna(pd.Series(np.where(firm, "Scheduled Facility", np.where(
            snap["FTT_IntermittentGenerationSystem_RAC"].notna(), "Semi-Scheduled Facility", None)), index=snap.index))
        return cls

    def per_target(self, snap: pd.DataFrame | None, issue: pd.Timestamp, targets: pd.DatetimeIndex) -> pd.DataFrame:
        if snap is None:
            return pd.DataFrame(np.nan, index=targets, columns=PER_TARGET)
        cls = self.classes(snap)
        gen = snap[cls.isin(GENERATING).to_numpy()]
        sched = gen[cls[gen.index].eq("Scheduled Facility").to_numpy()]
        active = sched[sched["Status"].isin(ACTIVE)]
        loss, forced, _, _ = self._loss(active, targets)
        _, _, has, full = self._loss(gen[gen["Status"].isin(ACTIVE)], targets)
        pending, _, _, _ = self._loss(sched[sched["Status"].isin(PENDING) & (sched["OutageType"] != "Forced")], targets)
        new = active[active["FirstSubmissionDate"] > issue - pd.Timedelta(hours=24)]
        new_loss, _, _, _ = self._loss(new, targets)
        return pd.DataFrame({
            "out_loss_mw": loss.sum(0), "out_loss_forced_mw": (loss * forced).sum(0), "out_loss_planned_mw": (loss * ~forced).sum(0),
            "out_n_gen": has.sum(0), "out_n_full": full.sum(0), "out_pending_mw": pending.sum(0), "out_new24_mw": new_loss.sum(0),
        }, index=targets)

    def for_issue(self, issue_date: pd.Timestamp, issue: pd.Timestamp, targets: pd.DatetimeIndex, lag_days: int = 0) -> pd.DataFrame:
        """Features for one issue from the snapshot of `lag_days` days earlier (1 = the DELAYED run)."""
        snap = load_snapshot(issue_date - pd.Timedelta(days=lag_days))
        prev = load_snapshot(issue_date - pd.Timedelta(days=lag_days + 1))
        f = self.per_target(snap, issue, targets)
        f["out_rev_mw"] = f["out_loss_mw"] - self.per_target(prev, issue, targets)["out_loss_mw"]
        f["src_out_prev_available_at"] = prev["listed_at"].iloc[0] if prev is not None and len(prev) else pd.NaT
        if snap is None or snap.empty:
            f["out_now_mw"] = f["out_forced24_n"] = f["out_age_h"] = np.nan
            f["src_out_snapshot"], f["src_out_available_at"] = None, pd.NaT
            return f
        f["src_out_snapshot"], f["src_out_available_at"] = snap["snapshot_file"].iloc[0], snap["listed_at"].iloc[0]
        f["out_now_mw"] = self.per_target(snap, issue, pd.DatetimeIndex([issue.floor("30min")]))["out_loss_mw"].iloc[0]
        cls = self.classes(snap)
        recent = snap[(snap["OutageType"] == "Forced") & snap["Status"].isin(ACTIVE) & cls.isin(GENERATING).to_numpy()
                      & (snap["FirstSubmissionDate"] > issue - pd.Timedelta(hours=24))]
        f["out_forced24_n"] = recent["Facility"].nunique()
        f["out_age_h"] = (issue - snap["snapshot_label"].iloc[0]).total_seconds() / 3600
        return f
