"""Background jobs: each runs in its own process and reports progress through a queue that a thread in the API
copies into the job table; cancelling terminates the process."""

import multiprocessing as mp
import threading
import traceback

from .db import Store

RUNNING: dict = {}  # job id -> Process


def _target(kind: str, params: dict, q) -> None:
    try:
        from app.engine import jobs
        fn = {"storage-backtest": jobs.storage_backtest}[kind]
        result = fn(params, lambda share, msg: q.put(("progress", share, msg)))
        q.put(("done", result, None))
    except Exception:  # noqa: BLE001 - reported on the job
        q.put(("failed", traceback.format_exc(limit=4), None))


def start(store: Store, kind: str, params: dict) -> int:
    jid = store.new_job(kind, params)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_target, args=(kind, params, q), daemon=True)
    p.start()
    RUNNING[jid] = p
    store.update_job(jid, state="running")

    def watch():
        last = -1.0
        while True:
            try:
                msg = q.get(timeout=1.0)
            except Exception:  # noqa: BLE001 - queue.Empty
                if not p.is_alive():
                    job = store.job(jid)
                    if job and job["state"] == "running":
                        store.update_job(jid, state="cancelled" if jid not in RUNNING else "failed",
                                         message="the job's process ended")
                    break
                continue
            if msg[0] == "progress":
                if msg[1] - last >= 0.01 or msg[1] >= 1:
                    store.update_job(jid, progress=float(msg[1]), message=str(msg[2]))
                    last = msg[1]
            elif msg[0] == "done":
                store.update_job(jid, state="done", progress=1.0, result=msg[1])
                break
            else:
                store.update_job(jid, state="failed", message=str(msg[1])[-2000:])
                break
        RUNNING.pop(jid, None)

    threading.Thread(target=watch, daemon=True).start()
    return jid


def cancel(store: Store, jid: int) -> bool:
    p = RUNNING.pop(jid, None)
    if p is None:
        return False
    p.terminate()
    store.update_job(jid, state="cancelled", message="cancelled by the operator")
    return True
