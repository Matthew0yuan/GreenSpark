"""Background jobs: each runs as its own program in its own process group (python -m app.engine.jobs) and reports
progress as lines of JSON on stdout, which a thread in the API copies into the job table. A program, not a multiprocessing
child: the runs need process pools, which a daemon child cannot have, and a child that is not a daemon holds up the API's
exit. Cancelling and stopping end the whole group, pools included."""

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from .db import Store

ROOT = Path(__file__).resolve().parents[2]
RUNNING: dict = {}  # job id -> Popen
# numpy's thread pool, as in the engines (engines._one_blas_thread); process pools as big as this: the four runs of a
# saved backtest and a table's solves, whatever the host has (in Docker cpu_count is the host's, not the container's)
ENV = {"OPENBLAS_NUM_THREADS": "1", "PYTHON_CPU_COUNT": str(min(os.cpu_count() or 1, 5))}


def start(store: Store, kind: str, params: dict, on_done=None) -> int:
    """on_done(): after the job has finished well, on another thread."""
    jid = store.new_job(kind, params)
    p = subprocess.Popen([sys.executable, "-m", "app.engine.jobs", kind, json.dumps(params)], cwd=ROOT, text=True, encoding="utf-8",
                         stdout=subprocess.PIPE, start_new_session=True, env={**os.environ, **ENV})
    RUNNING[jid] = p
    store.update_job(jid, state="running")

    def watch():
        last, ended, well = -1.0, False, False
        for line in p.stdout:
            msg = json.loads(line)
            if msg[0] == "progress":
                if msg[1] - last >= 0.01 or msg[1] >= 1:
                    store.update_job(jid, progress=float(msg[1]), message=str(msg[2]))
                    last = msg[1]
            elif msg[0] == "done":
                store.update_job(jid, state="done", progress=1.0, result=msg[1])
                ended = well = True
            else:
                store.update_job(jid, state="failed", message=str(msg[1])[-2000:])
                ended = True
        p.wait()
        job = store.job(jid)
        if not ended and job and job["state"] == "running":
            store.update_job(jid, state="cancelled" if jid not in RUNNING else "failed", message="the job's process ended")
        RUNNING.pop(jid, None)
        if well and on_done:
            on_done()

    threading.Thread(target=watch, daemon=True).start()
    return jid


def running(store: Store, kind: str) -> dict | None:
    """The job of this kind still running, if any."""
    for jid in list(RUNNING):
        job = store.job(jid)
        if job and job["kind"] == kind and job["state"] == "running":
            return job
    return None


def _end(p: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(p.pid, signal.SIGTERM)
        else:  # Windows: the program alone
            p.terminate()
    except ProcessLookupError:
        pass


def cancel(store: Store, jid: int) -> bool:
    p = RUNNING.pop(jid, None)
    if p is None:
        return False
    _end(p)
    store.update_job(jid, state="cancelled", message="cancelled by the operator")
    return True


def stop(store: Store) -> None:
    """The API is closing: end every job."""
    for jid in list(RUNNING):
        p = RUNNING.pop(jid, None)
        if p is not None:
            _end(p)
            store.update_job(jid, state="cancelled", message="the server stopped")
