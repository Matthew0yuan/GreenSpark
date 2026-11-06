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


def _use_data(data: str | None) -> None:
    """Which data folder this engine reads; vpp reads it once, when it is imported. Given here and not taken from the API
    process: during a switch (app.backend.main._switch) the API stays on the old folder until the new engines are ready."""
    if data is None:
        os.environ.pop("GS_DATA", None)
    else:
        os.environ["GS_DATA"] = data


def _init_storage(data: str | None):
    global _ENGINE
    _one_blas_thread()
    _use_data(data)
    from app.engine.storage import Storage
    _ENGINE = Storage()


def _init_fleet(data: str | None):
    global _ENGINE
    _one_blas_thread()
    _use_data(data)
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

    def __init__(self, name: str, init, data: str | None):
        self.name, self.state, self.error = name, "starting", None
        self.done = threading.Event()
        self.pool = ProcessPoolExecutor(max_workers=1, initializer=init, initargs=(data,), mp_context=SPAWN)
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

    def shutdown(self, finish_queued: bool = False):
        self.pool.shutdown(wait=False, cancel_futures=not finish_queued)


ENGINES: dict = {}


def start() -> dict:
    if not ENGINES:
        data = os.environ.get("GS_DATA")
        ENGINES["storage"] = Engine("storage", _init_storage, data)
        ENGINES["fleet"] = Engine("fleet", _init_fleet, data)
    return ENGINES


def stop() -> None:
    for e in ENGINES.values():
        e.shutdown()
    ENGINES.clear()


RELOAD = {"state": None, "at": None, "error": None}


_SAME = object()


def reload(on_done=None, data=_SAME) -> None:
    """New engine processes on newly built live data, or on another data folder (data: its path, None for the research
    data); the running ones serve until the new are ready, then finish what is queued and stop. If a new one fails, the
    old ones stay. on_done(the error, or None) when it is over."""
    data = os.environ.get("GS_DATA") if data is _SAME else data
    new = {"storage": Engine("storage", _init_storage, data), "fleet": Engine("fleet", _init_fleet, data)}
    RELOAD.update(state="starting", error=None)

    def swap():
        for e in new.values():
            e.done.wait()
        failed = [f"{k}: {e.error}" for k, e in new.items() if e.state != "ready"]
        if failed:
            for e in new.values():
                e.shutdown()
            RELOAD.update(state="failed", error="; ".join(failed))
            if on_done:
                on_done(RELOAD["error"])
            return
        old = dict(ENGINES)
        ENGINES.update(new)
        for e in old.values():
            e.shutdown(finish_queued=True)
        RELOAD.update(state="done")
        if on_done:
            on_done(None)

    threading.Thread(target=swap, daemon=True, name="engine-reload").start()
