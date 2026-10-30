"""Reference runs for the app's Evidence page (reported only, nothing chosen on them): on the same standalone route
sa, the 09:00 plan executed without real-time offers ("forecast") and the plan with the day's actual prices known
ahead ("perfect", an upper bound no dispatcher can reach). v1's engine, as registration_switch scenario A.

  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/run_refs.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vpp import backtest as bt  # noqa: E402
from vpp import contracts as ct  # noqa: E402

from run import period  # noqa: E402


def main() -> None:
    sc = bt._sc("sa", ct.Route("sa", metering="standalone"), ("forecast", "perfect"))
    assert sc is not None, bt.REFUSED
    days = bt.load_days("0900")
    daily, _, _ = bt.run_tasks([(sc, name, False) for name in sc.strategies], days, bt.q2b_aux())
    daily["period"] = period(daily["day"])
    daily.to_csv(HERE / "refs_daily.csv", index=False)
    print(daily.groupby(["period", "strategy"])["total"].agg(["count", "mean"]).round(1).to_string())


if __name__ == "__main__":
    main()
