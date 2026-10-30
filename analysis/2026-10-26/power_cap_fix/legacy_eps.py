"""The legacy30 row that differs (no site, perfect, 2025-04-04): run the legacy route with run_day wrapped to record
every step's MW before the caps and the connection (pw_offer) and after (pw), and list steps beyond the power."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from vpp import backtest as bt
from vpp import dispatch as dp

steps = []
orig = dp.run_day


def wrapped(*a, **k):
    P = (k["asset"] if "asset" in k else a[0]).battery.power_mw
    k["on_step"] = lambda x: steps.append((x["step"], x["pw_offer"], x["pw"], P))
    return orig(*a, **k)


def main():
    dp.run_day = wrapped
    days = bt.load_days()[:30]
    bt._init_worker(days, None)
    sc = bt.LEGACY["no site"]
    bt._run((sc, "perfect", False))
    day_index = [d["day"] for d in days].index(__import__("pandas").Timestamp("2025-04-04"))
    per_day = len(steps) // len(days)
    day_steps = steps[day_index * per_day:(day_index + 1) * per_day]
    over = [(s, a, b) for s, a, b, P in day_steps if abs(a) > P or abs(b) > P]
    print(f"{len(steps)} steps over {len(days)} days; P = {steps[0][3]}; 2025-04-04 steps with |MW| > P before or after:")
    for s, a, b in over:
        print(f"  step {s}: before caps/connection {a!r}, executed {b!r}")
    allover = [(i // per_day, s, a) for i, (s, a, b, P) in enumerate(steps) if abs(a) > P]
    print(f"all days: {len(allover)} steps beyond P before the clamp; largest excess {max((abs(a) - steps[0][3] for _, _, a in allover), default=0):.3e} MW")


if __name__ == "__main__":
    main()
