"""The factory's own bill items the battery behind its meter can change: the network demand charge
(Western Power RT7, contract maximum demand) and the capacity cost allocated to it (IRCR).

RT7, High Voltage Contract Maximum Demand, Western Power 2026-27 price list (ERA-approved 15 May 2026;
reports/tool_model.md section 4.1), urban zone substation, 10 km from it [researched]:
  CMD >= 7,000 kVA   demand 33.997 c/kVA/day x CMD + demand-length 1.865 c/kVA.km/day x km x CMD
                     + administration 11,770.090 c/day
  CMD  < 7,000 kVA   68,033.118 c/day for the first 1,000 kVA + (28.576 + 2.178 x km) c/kVA/day x
                     (CMD - 1,000) + administration 6,758.157 c/day
  There is no energy charge. If a month's highest half-hourly demand PD exceeds the CMD, the excess
  network usage charge is ENUM x (PD - CMD) / CMD x that month's demand and demand-length charges
  (ENUM = 1, 2.5 at a few substations). Demand is kVA: MW / power factor (0.95 assumed).
IRCR (ESM Rules Appendix 5, glossary) [verified]: a meter's contribution is twice its median metered
  consumption (MWh per trading interval, so the median MW) in the 12 Peak SWIS Trading Intervals of
  the preceding Hot Season: the 3 intervals with the highest Total Sent Out Generation on each of the
  4 trading days with the highest maximum demand, 1 Dec - 1 Apr. Net export counts as 0. Operational
  demand (demand_mw) stands in for Total Sent Out Generation. It sets the capacity cost of the next
  capacity year, valued here at that year's Reserve Capacity Price per MW (vpp.contracts.RCP).

Peak-day rule (2026-10-19): AEMO's maximum energy requirement forecast for the trading day
(vpp.spike.features pd_day_req_max) in the 08:00 pre-dispatch run, public by 09:00. A day is flagged
the day before if the previous day's run forecasts at least IRCR_DAY_AHEAD_MW for it - the battery
then keeps what the factory's 17:00-20:00 load needs stored overnight (vpp.backtest) - or on the day
if its own run forecasts at least IRCR_THRESHOLD_MW. Both thresholds were chosen on the 2023-24 and
2024-25 Hot Seasons by the rule fixed beforehand (at least 3 of the 4 peak days flagged in each
season, fewest flags): same day 3,900 MW (7 and 11 flags, 4 and 3 hits), day before 4,025 MW (5 and
8 flags, 3 and 3 hits). Caveats: the 2025-26 season (in the backtest) was seen while choosing, and the
overnight reserve was added after seeing that the first version left the battery empty on its peak
days. 2025-26 had lower demand, and these thresholds flag only 2 / 0 of its days; IRCR_WIDE (3,600 /
3,500 MW: all 4 peak days in all three seasons, 13-23 flags a season) is a sensitivity set after
seeing it, not a validated rule.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from vpp.data import PROCESSED

PF = 0.95
IRCR_THRESHOLD_MW = 3900.0   # same day
IRCR_DAY_AHEAD_MW = 4025.0   # the day before
IRCR_WIDE = (3600.0, 3500.0)  # sensitivity (same day, day before), set after seeing 2025-26
IRCR_SLOTS = (18, 24)  # 17:00-20:00: all 12 peak intervals of 2023-24 .. 2025-26 start 17:00-19:00


@dataclass(frozen=True)
class RT7:
    cmd_kva: float
    km: float = 10.0
    enum: float = 1.0
    pf: float = PF

    def daily_demand(self) -> float:
        """$ per day of the demand and demand-length charges at the contract maximum demand."""
        if self.cmd_kva >= 7000:
            return (33.997 + 1.865 * self.km) / 100 * self.cmd_kva
        return 680.33118 + (28.576 + 2.178 * self.km) / 100 * max(0.0, self.cmd_kva - 1000)

    def daily_fixed(self) -> float:
        return 117.70090 if self.cmd_kva >= 7000 else 67.58157

    def month(self, peak_mw: float, days: int) -> dict:
        """One month's network charges given its highest half-hourly import (MW)."""
        demand = self.daily_demand() * days
        pd_kva = peak_mw / self.pf * 1000
        enuc = self.enum * max(0.0, pd_kva - self.cmd_kva) / self.cmd_kva * demand
        return {"demand": demand, "fixed": self.daily_fixed() * days, "enuc": enuc, "total": demand + self.daily_fixed() * days + enuc}

    def enuc_per_mw(self, days: int = 30) -> float:
        """$ of excess network usage charge per MW over the CMD in a month (the dispatcher's CMD penalty)."""
        return self.enum * self.daily_demand() * days / self.cmd_kva * 1000 / self.pf


def cmd_kva(peak_mw: float, step: float = 100.0) -> float:
    """CMD a site would nominate for a highest half-hourly demand of peak_mw: kVA rounded up."""
    return float(np.ceil(peak_mw / PF * 1000 / step) * step)


def monthly_bills(meter: pd.Series, rt7: RT7) -> pd.DataFrame:
    """Network charges per calendar month of a 30-min import series (MW, interval-start index)."""
    peak = meter.clip(lower=0).groupby(meter.index.to_period("M")).max()
    days = meter.groupby(meter.index.to_period("M")).size() / 48
    return pd.DataFrame([{**rt7.month(peak[m], days[m]), "month": m, "peak_mw": peak[m], "days": days[m]} for m in peak.index]).set_index("month")


def ircr_intervals(demand: pd.Series, season: int) -> pd.DatetimeIndex:
    """The 12 Peak SWIS Trading Intervals of the Hot Season starting 1 Dec `season` (demand_mw as the stand-in)."""
    s = demand[(demand.index >= f"{season}-12-01 08:00") & (demand.index < f"{season + 1}-04-01 08:00")]
    day = (s.index - pd.Timedelta(hours=8)).normalize()
    top_days = s.groupby(day).max().nlargest(4).index
    out = [s[day == d].nlargest(3).index for d in top_days]
    return pd.DatetimeIndex(np.concatenate([x.to_numpy() for x in out])).sort_values()


def ircr_mw(meter: pd.Series, intervals: pd.DatetimeIndex) -> float:
    """IRCR contribution (MW): median import over the peak intervals, net export as 0."""
    return float(np.median(meter.reindex(intervals).clip(lower=0).to_numpy()))


def peak_day_predictor(days_ahead: int = 0) -> pd.Series:
    """AEMO's forecast maximum energy requirement of each trading day, from the 08:00 run public by 09:00
    on the day (days_ahead 0) or on the day before (days_ahead 1), indexed by the day forecast."""
    t = pd.read_parquet(PROCESSED / "spike_table.parquet", columns=["target_date", "days_ahead", "pd_day_req_max"])
    return t[t["days_ahead"] == days_ahead].groupby("target_date")["pd_day_req_max"].max()


def flag_days(threshold: float = IRCR_THRESHOLD_MW, predictor: pd.Series | None = None) -> pd.DatetimeIndex:
    """Hot Season trading days (Dec-Mar) the rule flags as possible system peak days."""
    p = peak_day_predictor() if predictor is None else predictor
    hot = p.index.month.isin([12, 1, 2, 3])
    return p.index[hot & (p >= threshold).to_numpy()]
