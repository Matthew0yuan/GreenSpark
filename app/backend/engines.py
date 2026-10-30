"""The engine worker processes. vpp keeps process-wide state (vpp.backtest._DAYS, lru caches), so each engine lives in
its own long-lived process: one for the storage network (and its evidence), one for the customer batteries. The API
awaits their futures; a request never runs vpp in the API process."""

import asyncio
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor

# spawn, as on Windows: on Linux the default (fork) copies the API process mid-request, threads and locks included,
# and the live schedule starts new engines from its own thread (reload)
SPAWN = multiprocessing.get_context("spawn")

_ENGINE = None  # the engine object inside a worker process


def _one_blas_thread():
    """numpy and scipy each bundle OpenBLAS, which reserves memory for every CPU thread as it loads: about 0.6 GB per copy
    on a 20-thread machine, so 1.2 GB per engine that the engines never use (replays take the same time with one
    thread; measured 2026-10-30). Must run before vpp imports numpy."""
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


def _init_storage():
    global _ENGINE
    _one_blas_thread()
    from app.engine.storage import Storage
    _ENGINE = Storage()


def _init_fleet():
    global _ENGINE
    _one_blas_thread()
    from app.engine.fleet import Fleet
    _ENGINE = Fleet()


def _call(method: str, kwargs: dict):
    if method == "evidence":
        from app.engine.evidence import evidence
        return evidence(**kwargs)
    return getattr(_ENGINE, method)(**kwargs)


def _ping():
    return True


class Engine:
    """One worker process with its engine; `ready` once its data are loaded."""

    def __init__(self, name: str, init):
        self.name, self.state, self.error = name, "starting", None
        self.done = threading.Event()
        self.pool = ProcessPoolExecutor(max_workers=1, initializer=init, mp_context=SPAWN)
        fut = self.pool.submit(_ping)
        fut.add_done_callback(self._warmed)
        self.lock = threading.Lock()

    def _warmed(self, fut):
        try:
            fut.result()
            self.state = "ready"
        except Exception as e:  # noqa: BLE001 - shown on /meta
            self.state, self.error = "failed", repr(e)
        self.done.set()

    async def call(self, method: str, **kwargs):
        return await asyncio.wrap_future(self.pool.submit(_call, method, kwargs))

    def submit(self, method: str, **kwargs):
        """From a thread that is not the event loop (the live schedule)."""
        return self.pool.submit(_call, method, kwargs)

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


ENGINES: dict = {}


def start() -> dict:
    if not ENGINES:
        ENGINES["storage"] = Engine("storage", _init_storage)
        ENGINES["fleet"] = Engine("fleet", _init_fleet)
    return ENGINES


def stop() -> None:
    for e in ENGINES.values():
        e.shutdown()
    ENGINES.clear()


RELOAD = {"state": None, "at": None, "error": None}


def reload() -> None:
    """New engine processes on newly built live data; the running ones serve until the new are ready, then stop. If a
    new one fails, the old ones stay."""
    new = {"storage": Engine("storage", _init_storage), "fleet": Engine("fleet", _init_fleet)}
    RELOAD.update(state="starting", error=None)

    def swap():
        for e in new.values():
            e.done.wait()
        failed = [f"{k}: {e.error}" for k, e in new.items() if e.state != "ready"]
        if failed:
            for e in new.values():
                e.shutdown()
            RELOAD.update(state="failed", error="; ".join(failed))
            return
        old = dict(ENGINES)
        ENGINES.update(new)
        for e in old.values():
            e.shutdown()
        RELOAD.update(state="done")

    threading.Thread(target=swap, daemon=True, name="engine-reload").start()
