"""spikev3's new feature groups (analysis/2026-10-28/spikev3/PLAN.md section 3.3), for any rows of the point-in-time
spike table (vpp.spike.features; the extended one in analysis/2026-10-28/scarcity_signals/out). Every group uses only
what was public at the row's issue time (09:00 on the issue day); the leak audit (leak_audit_p1.py) scrambles what
was not and plants a leak per group.

  G0 recent    vpp.spike.features.GROUPS["price_extra"] (already in the table)
  G1 stem      the newest STEM auction public at 09:00 (trading day D, public at D-1 13:30): price, premium over AEMO's
               08:00 pre-dispatch price for the target, offer/bid cover, the auction's highest price
  G2 norm      pre-dispatch in-service headroom against the same lead and slot over the 28 issues before
  G4 rsi       residual supply without Synergy: capacity credits of Scheduled Facilities less the 08:30 outage
               snapshot's losses (vpp.spike.outages), per owner, over AEMO's demand requirement
  G5 wind      ECMWF 100 m wind: the newest vintage public at the issue less the one before, two sites, and the
               evening mean revision
"""

import numpy as np
import pandas as pd

from vpp.data import RAW
from vpp.spike import features as sf
from vpp.spike import outages as og
from vpp.spike import protocol as pc

HALF = pd.Timedelta(minutes=30)
STEM_FIRST = pd.Timestamp("2023-10-01")              # first trading day of the reformed market
STEM_PUBLIC = pd.Timedelta(hours=13, minutes=30)     # after the start of the day before the trading day
HR_FLOOR = 50.0                                      # MW, floor under the normal headroom (ratios)
NORM_DAYS, NORM_MIN = 28, 14
SYNERGY = "WPGENER"

G0 = list(sf.GROUPS["price_extra"])
G1 = ["stem_price", "stem_premium", "stem_cover", "stem_daymax"]
G2 = ["hr_norm", "hr_daymin_norm", "hr_z"]
G4 = ["rsi", "rsi_daymin", "syn_loss_mw"]
G5 = ["wrev_midwest", "wrev_merredin", "wrev_evening"]
GROUPS = {"G0": G0, "G1": G1, "G2": G2, "G4": G4, "G5": G5}


# ---------------------------------------------------------------- G1 STEM

def load_stem() -> pd.DataFrame:
    """STEM summary rows from the reform on: trading day, slot, price, offer, bid (suspended intervals NaN), public at."""
    s = pd.concat([pd.read_csv(f) for f in sorted((RAW / "stem-summary").glob("stem-summary-*.csv"))], ignore_index=True)
    s = pd.DataFrame({
        "tdate": pd.to_datetime(s["Trading Date"]), "interval_start": pd.to_datetime(s["Trading Interval"]),
        "suspended": s["Trading Suspended"].eq("Y"), "price": s["Clearing Price ($/MWh)"].astype(float),
        "offer": s["Offer Quantity (MWh)"].astype(float), "bid": s["Bid Quantity (MWh)"].astype(float),
    })
    s["slot"] = ((s["interval_start"] - s["tdate"] - pc.DAY_START) / HALF).astype(int)
    assert s["slot"].between(0, 47).all() and not s.duplicated(["tdate", "slot"]).any()
    s.loc[s["suspended"], ["price", "offer", "bid"]] = np.nan
    s["available_at"] = s["tdate"] - pd.Timedelta(days=1) + STEM_PUBLIC
    return s[s["tdate"] >= STEM_FIRST].sort_values(["tdate", "slot"]).reset_index(drop=True)


def g1_stem(r: pd.DataFrame, stem: pd.DataFrame, auction: pd.Series | None = None) -> pd.DataFrame:
    """auction: the trading day whose STEM each row uses (default the issue day; the leak audit plants the target's)."""
    auction = r["issue_date"] if auction is None else auction
    grid = {k: stem.pivot(index="tdate", columns="slot", values=k) for k in ("price", "offer", "bid")}
    key = pd.MultiIndex.from_arrays([auction, r["slot"]])
    at = {k: g.stack(future_stack=True).reindex(key).to_numpy() for k, g in grid.items()}
    out = pd.DataFrame(index=r.index)
    out["stem_price"] = at["price"]
    out["stem_premium"] = at["price"] - r["aemo_energy"].to_numpy()
    out["stem_cover"] = at["offer"] / np.maximum(at["bid"], 1.0)
    out["stem_daymax"] = grid["price"].max(axis=1).reindex(auction).to_numpy()
    public = (auction - pd.Timedelta(days=1) + STEM_PUBLIC).to_numpy()
    out["_stem_public_ok"] = public <= r["issue_time"].to_numpy()
    return out


# ---------------------------------------------------------------- G2 headroom against normal

def g2_norm(r: pd.DataFrame) -> pd.DataFrame:
    """Per lead (days_ahead) and slot: the 28 issues before (not the row's own) set the normal level."""
    out = pd.DataFrame(index=r.index, columns=G2, dtype=float)
    for d in sorted(r["days_ahead"].unique()):
        part = r[r["days_ahead"] == d]
        g = part.pivot_table(index="issue_date", columns="slot", values="pd_headroom", aggfunc="first")
        g = g.reindex(pd.date_range(g.index.min(), g.index.max()))
        past = g.shift(1).rolling(NORM_DAYS, min_periods=NORM_MIN)
        med, mean, std = past.median(), past.mean(), past.std()
        key = pd.MultiIndex.from_arrays([part["issue_date"], part["slot"]])
        at = lambda t: t.stack(future_stack=True).reindex(key).to_numpy()  # noqa: E731
        hr = part["pd_headroom"].to_numpy()
        out.loc[part.index, "hr_norm"] = hr / np.maximum(at(med), HR_FLOOR)
        out.loc[part.index, "hr_z"] = (hr - at(mean)) / at(std).clip(min=1.0)
        dmin = part.groupby("issue_date")["pd_day_headroom_min"].first()
        dmin = dmin.reindex(pd.date_range(dmin.index.min(), dmin.index.max()))
        dmed = dmin.shift(1).rolling(NORM_DAYS, min_periods=NORM_MIN).median()
        out.loc[part.index, "hr_daymin_norm"] = part["pd_day_headroom_min"].to_numpy() / \
            np.maximum(dmed.reindex(part["issue_date"]).to_numpy(), HR_FLOOR)
    return out


# ---------------------------------------------------------------- G4 residual supply index

class Supply:
    """Capacity credits of Scheduled Facilities per Capacity Year, total and Synergy's, and per-issue outage losses."""

    def __init__(self):
        self.of = og.OutageFeatures()
        fac = pd.read_csv(RAW / "facilities" / "facilities.csv")
        self.owner = fac.set_index("Facility Code")["Participant Code"]
        cc = self.of.cc
        cls = pd.Series([self.of.cls.get(f, "Scheduled Facility") for f in cc.index], index=cc.index)
        sched = cc[cls.eq("Scheduled Facility").to_numpy()].fillna(0.0)
        syn = self.owner.reindex(sched.index).eq(SYNERGY).to_numpy()
        self.credit_total, self.credit_syn = sched.sum(), sched[syn].sum()
        self.unmapped_share = sched[self.owner.reindex(sched.index).isna().to_numpy()].sum() / self.credit_total

    def losses(self, snap: pd.DataFrame | None, targets: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
        """(all, Synergy) MW lost per target to Approved/Accepted outages of Scheduled Facilities in the snapshot."""
        if snap is None:
            return np.full(len(targets), np.nan), np.full(len(targets), np.nan)
        cls = self.of.classes(snap)
        active = snap[cls.eq("Scheduled Facility").to_numpy() & snap["Status"].isin(og.ACTIVE).to_numpy()]
        total = self.of._loss(active, targets)[0].sum(0)
        syn = self.of._loss(active[active["Facility"].map(self.owner).eq(SYNERGY).to_numpy()], targets)[0].sum(0)
        return total, syn


def g4_rsi(r: pd.DataFrame, supply: Supply, snapshot=og.load_snapshot) -> pd.DataFrame:
    """snapshot(issue_date): the outage snapshot used for an issue (the leak audit swaps in a later one)."""
    out = pd.DataFrame(index=r.index, columns=G4, dtype=float)
    for day, part in r.groupby("issue_date"):
        targets = pd.DatetimeIndex(part["target_time"])
        loss, syn = supply.losses(snapshot(day), targets)
        years = og.capacity_year(targets)
        tot = supply.credit_total.reindex(years).to_numpy() - loss
        mine = supply.credit_syn.reindex(years).to_numpy() - syn
        out.loc[part.index, "rsi"] = (tot - mine) / part["pd_req"].to_numpy()
        out.loc[part.index, "syn_loss_mw"] = syn
    out["rsi_daymin"] = out["rsi"].groupby([r["issue_date"], r["target_date"]]).transform("min")
    return out


# ---------------------------------------------------------------- G5 wind revision

def g5_wind(r: pd.DataFrame, pit_weather: pd.DataFrame, lags: dict = pc.AVAILABILITY) -> pd.DataFrame:
    """Per 30-min target the hourly values around it (as vpp.spike.features.weather_features); revision = newest public
    vintage - the previous vintage of the same valid time (always public earlier: older runs are public earlier)."""
    wx = sf.available(pit_weather, "weather", lags)
    h0 = r["target_time"].dt.floor("h")
    on_hour = (r["target_time"] == h0).to_numpy()
    out = pd.DataFrame(index=r.index)
    for loc in ("midwest", "merredin"):
        rec = wx[(wx["facility_or_service"] == loc) & (wx["variable"] == "wind_speed_100m")]
        rev = []
        for hour in (h0, h0 + pd.Timedelta(hours=1)):
            a = sf.asof(pd.DataFrame({"issue_time": r["issue_time"], "hour": hour, "_row": np.arange(len(r))}), rec,
                        on_time="hour")
            rev.append((a["value"] - a["value_prev"]).to_numpy())
        out[f"wrev_{loc}"] = np.where(on_hour, rev[0], (rev[0] + rev[1]) / 2)
    evening = ((out["wrev_midwest"] + out["wrev_merredin"]) / 2).where(r["target_time"].dt.hour.isin([17, 18, 19, 20]))
    out["wrev_evening"] = evening.groupby([r["issue_date"], r["target_date"]]).transform("mean")
    return out
