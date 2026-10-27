"""The engine adapter: the only code in app/ that imports vpp. Runs inside the engine worker processes
(app.backend.engines), never in the API process: vpp keeps process-wide state (vpp.backtest._DAYS, lru caches)."""
