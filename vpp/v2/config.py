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
    offer_end: bool = False           # the value offer also prices the last half-hour (v2_plan.md, 9.1); v1: off
    usable_mwh: float | None = None   # stored energy the dispatcher may use (a state-of-charge window); None: all
    plan_days: int = 1                # 2: from 09:00 plans run to the end of tomorrow (vpp.v2.horizon, R3)
    record: bool = False              # keep decision records (vpp.v2.record)
    # round 4 (v2_plan.md section 11): $ per MWh sold, beyond the wear, that the value table and the real-time offers
    # charge (B2); None: the plans' hurdle (75), as rounds 1-3. The plans keep their hurdle either way.
    hurdle: float | None = None
    buy: bool = False                 # round 4, B3: the layered offers also bid to buy, at the margin above
    state: bool = False               # round 4, B4: values by the market state of the day and of the next
    # round 5 (v2_plan.md section 12), t5: before 09:00 the plans' end value is shifted by what today already commits
    # only; tomorrow's site reserve (②, public at 09:00) joins the shift from 09:00
    terminal_0900: bool = False
    regime: bool = False              # round 5, c5: today's offer values also by whether the day has spiked (with state)
    replan_every: int = 0             # round 5, e5: also re-plan every this many trading intervals (planner "roll")


V1 = V2Config()
