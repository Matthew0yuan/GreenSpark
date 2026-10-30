"""PLAN.md "怎么跑": retrain everything that learns, without the held-out days, in this worktree (its data/processed).
  .venv/bin/python analysis/2026-10-28/random_holdout/pipeline.py   (from the worktree root)
Writes train_log.csv (every monthly refit: training days, held-out days removed) and pipeline_log.txt here."""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from vpp import aemo_correction as ac  # noqa: E402

LOG = HERE / "pipeline_log.txt"


def note(msg: str) -> None:
    print(msg, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def step(name: str, fn) -> None:
    t0, n0 = time.time(), len(ac.TRAIN_LOG)
    fn()
    for r in ac.TRAIN_LOG[n0:]:
        r.setdefault("step", name)
    note(f"{name}: {time.time() - t0:.0f} s, {len(ac.TRAIN_LOG) - n0} monthly refits")


def cli(*args: str) -> None:
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
    note(f"python -m {' '.join(args)}: exit {r.returncode}, {time.time() - t0:.0f} s\n{tail}")
    if r.returncode:
        raise SystemExit(f"stopped at {' '.join(args)}")


def main() -> None:
    from vpp import forecast as fc
    from vpp import spike_risk as sr
    from vpp.v2 import risk as rk
    note(f"--- {pd.Timestamp.now():%Y-%m-%d %H:%M} pipeline in {ROOT}")
    step("forecast", lambda: fc.main([]))
    step("spike_risk", lambda: sr.main([]))
    step("risk history d1", lambda: rk.history(True, 1))
    step("risk history d0", lambda: rk.history(True, 0))
    pd.DataFrame(ac.TRAIN_LOG).to_csv(HERE / "train_log.csv", index=False)
    cli("vpp.v2.value", "--hurdle", "50", "--state", "--rebuild")
    cli("vpp.v2.value", "--hurdle", "50", "--state", "--standalone", "--rebuild")
    note("pipeline done")


if __name__ == "__main__":
    main()
