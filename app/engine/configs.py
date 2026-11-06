"""A battery and CMD set in Settings, other than the tested 40 MWh / 9 MW (the user's request, 2026-11-05). Each has its
own value table and its own saved backtest (app.engine.jobs.storage_config) in var/configs/<key>/, in the same files as
the tested ones (common.SAVED_DIR). A day's start state, the month's highest import and the network bill all come from
the saved backtest, so another configuration's would be wrong, and can make a day infeasible: a configuration not built,
or built on other code or data, is refused (NotBuilt, HTTP 409), never computed from another's.

Standard library only: the API process reads it too, and never imports vpp."""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
ENERGY_MWH, CMD_MW = 40.0, 9.0  # the tested battery and CMD (app.engine.storage.Storage asserts them against vpp)
ENERGY_RANGE = (10.0, 80.0)  # MWh, whole numbers: the value table's stored energy is in 1 MWh segments
CMD_RANGE, CMD_STEP = (5.0, 12.0), 0.5  # MW; 12 MW is the factory's import limit (vpp.site.FACTORY)
KEEP = 12  # built configurations kept on disk, about 93 MB each (table 90 MB, backtest 3 MB): the oldest go first


class NotBuilt(Exception):
    """This configuration has no usable saved backtest (HTTP 409)."""


class Config(NamedTuple):
    """The battery's energy and the CMD, None where tested (of() makes them; a standalone connection has no CMD)."""
    energy_mwh: float | None = None
    cmd_mw: float | None = None

    @property
    def tested(self) -> bool:
        return self.energy_mwh is None and self.cmd_mw is None

    @property
    def energy(self) -> float:
        return ENERGY_MWH if self.energy_mwh is None else self.energy_mwh

    @property
    def cmd(self) -> float:
        return CMD_MW if self.cmd_mw is None else self.cmd_mw


TESTED = Config()


def of(connection: str, energy_mwh: float | None = None, cmd_mw: float | None = None) -> Config:
    """The Config of these values, ValueError out of range. A tested value is None however it was given, so the tested
    table and backtest are never built over."""
    if energy_mwh is not None:
        energy_mwh = float(energy_mwh)
        lo, hi = ENERGY_RANGE
        if not lo <= energy_mwh <= hi or energy_mwh != round(energy_mwh):
            raise ValueError(f"battery energy must be a whole number of MWh from {lo:g} to {hi:g}, not {energy_mwh:g}")
    if connection != "factory":
        cmd_mw = None
    elif cmd_mw is not None:
        cmd_mw = float(cmd_mw)
        lo, hi = CMD_RANGE
        if not lo <= cmd_mw <= hi or cmd_mw / CMD_STEP != round(cmd_mw / CMD_STEP):
            raise ValueError(f"CMD must be from {lo:g} to {hi:g} MW in steps of {CMD_STEP:g}, not {cmd_mw:g}")
    return Config(None if energy_mwh == ENERGY_MWH else energy_mwh, None if cmd_mw == CMD_MW else cmd_mw)


def root() -> Path:
    """GS_CONFIGS: another folder (tests)."""
    return Path(os.environ.get("GS_CONFIGS") or ROOT / "var" / "configs")


def key(connection: str, setup: Config) -> str:
    return f"{connection}_{setup.energy:g}mwh" + (f"_cmd{setup.cmd:g}" if connection == "factory" else "")


def rel(connection: str, setup: Config) -> str:
    """The backtest's place under root(): one for each data folder, so the replay and live data do not replace each other's."""
    return f"{'live' if os.environ.get('GS_DATA') else 'replay'}/{key(connection, setup)}"


def folder(connection: str, setup: Config) -> Path:
    return root() / rel(connection, setup)


def fingerprint() -> str:
    """What a saved backtest depends on: the code, which data folder, and its inputs as they are now. Not a git commit:
    the Docker image has no .git, and a commit does not see uncommitted code or changed data."""
    h = hashlib.sha256()
    for top in ("vpp", "app/engine"):
        for p in sorted((ROOT / top).rglob("*.py")):
            h.update(p.relative_to(ROOT).as_posix().encode())
            h.update(p.read_bytes())
    data = Path(os.environ.get("GS_DATA") or ROOT / "data") / "processed"  # as vpp.data
    h.update(str(data.resolve()).encode())
    for p in sorted(data.glob("*.parquet")):
        s = p.stat()
        h.update(f"{p.name} {s.st_size} {s.st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def manifest(connection: str, setup: Config) -> dict | None:
    p = folder(connection, setup) / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _label(connection: str, setup: Config) -> str:
    return f"{setup.energy:g} MWh" + (f", CMD {setup.cmd:g} MW" if connection == "factory" else "")


def problem(connection: str, setup: Config, m: dict | None = None) -> str | None:
    """Why this configuration cannot be used, None if it can."""
    if setup.tested:
        return None
    m = m or manifest(connection, setup)
    what = _label(connection, setup)
    if m is None:
        return f"No backtest for {what} yet: run it in Settings."
    if m["fingerprint"] != fingerprint():
        return f"The backtest for {what} is out of date (the code or data changed): run it again in Settings."
    if not Path(m["table"]).exists():
        return f"The value table for {what} is gone: run the backtest again in Settings."
    return None


def require(connection: str, setup: Config) -> dict | None:
    """The configuration's manifest (None if tested), or NotBuilt."""
    if setup.tested:
        return None
    m = manifest(connection, setup)
    why = problem(connection, setup, m)
    if why:
        raise NotBuilt(why)
    return m


def prune(keep: int = KEEP) -> None:
    """Remove all but the newest built configurations, with their value tables."""
    built = sorted(root().glob("*/*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in built[keep:]:
        table = Path(json.loads(p.read_text(encoding="utf-8"))["table"])
        table.unlink(missing_ok=True)
        table.with_suffix(".json").unlink(missing_ok=True)
        shutil.rmtree(p.parent, ignore_errors=True)
