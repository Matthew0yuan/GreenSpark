"""What a v2 run is set up with (reports/v2_plan.md, sections 5.2-5.4). Every feature off is V1: v1's
forecast+offer(value) on the route, result for result."""

from dataclasses import dataclass


@dataclass(frozen=True)
class V2Config:
    name: str = "v1"
    # ② site reserve for tomorrow (S2), in place of v1's overnight reserve for a flagged IRCR day
    site_reserve: bool = False
    site_load_basis: str = "plan"     # "plan": load forecast x (1 + load margin), as v1's IRCR reserve; "expected"
    # what energy stored at the end of the plan is worth: "buyback" (v1) or "relative" (the relative value curve, S3)
    value: str = "buyback"
    curve_shift: bool = True          # the curve starts above what ① and ② already need at 08:00
    level_stat: str = "median"        # the curve's price level over the previous 28 days: "median" or "mean"
    ladder: bool = False              # layered offers from the curve, ② protected (S3)
    spike_reserve_mwh: float = 0.0    # kept for spikes 16:00-20:00 every day (v1's "|res=")
    offer_lag: int = 1                # 5-min steps back the standing offer's price is taken from
    usable_mwh: float | None = None   # stored energy the dispatcher may use (a state-of-charge window); None: all
    plan_days: int = 1                # 2: from 09:00 plans run to the end of tomorrow (vpp.v2.horizon, R3)
    record: bool = False              # keep decision records (vpp.v2.record)


V1 = V2Config()
