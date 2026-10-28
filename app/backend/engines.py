"""The engine worker processes. vpp keeps process-wide state (vpp.backtest._DAYS, lru caches), so each engine lives in
its own long-lived process: one for the storage network (and its evidence), one for the customer batteries. The API
awaits their futures; a request never runs vpp in the API process."""

import asyncio
import threading
from concurrent.futures import ProcessPoolExecutor

_ENGINE = None  # the engine object inside a worker process


def _init_storage():
    global _ENGINE
    from app.engine.storage import Storage
    _ENGINE = Storage()


def _init_fleet():
    global _ENGINE
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
        self.pool = ProcessPoolExecutor(max_workers=1, initializer=init)
        fut = self.pool.submit(_ping)
        fut.add_done_callback(self._warmed)
        self.lock = threading.Lock()

    def _warmed(self, fut):
        try:
            fut.result()
            self.state = "ready"
        except Exception as e:  # noqa: BLE001 - shown on /meta
            self.state, self.error = "failed", repr(e)

    async def call(self, method: str, **kwargs):
        return await asyncio.wrap_future(self.pool.submit(_call, method, kwargs))

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
