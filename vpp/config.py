"""What a battery run is set up with, kept apart by category (reports/q2b_plan.md, section 3).

  Battery    physical: what the battery itself can do (power, stored energy, efficiency)
  Limits     compliance: operating limits set by rules and agreements rather than by physics
  Asset      the battery with its limits and its wear cost (economic), as the dispatcher runs it
  RiskPrefs  risk preferences: margins for forecast error that steer decisions and are never settled;
             set by the client (the backtest's values were chosen on the validation period)

Elsewhere: the connection's import and export limits are an agreement with the network (vpp.site.Site);
contracts and market obligations, and the rules behind them, are vpp.contracts.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Battery:
    """Physical limits only."""
    power_mw: float = 1.0
    energy_mwh: float = 2.0
    round_trip: float = 0.85

    @property
    def eta(self) -> float:
        """One-way efficiency."""
        return self.round_trip ** 0.5


@dataclass(frozen=True)
class Limits:
    """Operating limits that come from rules and agreements, not physics (compliance)."""
    cycles_per_day: float = 1.0     # most full discharges a day: an operating limit set by the user, as a warranty might [assumed]
    fcess_sustain_h: float = 0.25   # enabled FCESS must be deliverable for this long (market rule) [assumed]


@dataclass(frozen=True)
class Asset:
    """The battery as the dispatcher runs it: physics, operating limits and the cost of wear (economic)."""
    battery: Battery = field(default_factory=Battery)
    limits: Limits = field(default_factory=Limits)
    wear_per_mwh: float = 30.0      # A$ per MWh discharged: cells ~A$180k/MWh over ~6,000 cycles [assumed]


@dataclass(frozen=True)
class RiskPrefs:
    """Risk preferences: margins for forecast error. They steer decisions and are never settled."""
    hurdle_per_mwh: float = 0.0          # $/MWh discharged added to the wear in decisions (plan, offer)
    load_margin: float = 0.16            # plans assume the site may draw this much more than forecast (2x example noise)
    capacity_floor: str = "sustain"      # capacity credits: "sustain" keeps what delivering to the window's end needs,
                                         # stricter than the rule; "refund_test" keeps what the 5-min refund test needs
    risk_threshold: float | None = None  # keep reserve_mwh stored in intervals with spike risk >= this
    reserve_mwh: float = 0.0
    reserve_value: float = 200.0         # $ per MWh of the largest shortfall below that reserve (an economic floor) [assumed]
    # the "dro" planner (vpp.dispatch.Strategy): how far the scenarios' probabilities may be off, as a factor on
    # each scenario's weight and on the expected number of half-hours at or above the spike price. Before the
    # cutoff the scenarios' monthly spike frequency was 0.59 .. 1.99 x the actual (reports/findings.md, section 18),
    # so the actual was 1 / 1.99 .. 1 / 0.59 x the scenarios'
    ambiguity: tuple = (0.50, 1.70)
