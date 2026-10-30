"""The app's own state in SQLite (var/app.db): the operator's call decisions and background jobs. Nothing the engine
reads or writes lives here; deleting the file resets the operator's decisions."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

VAR = Path(__file__).resolve().parents[2] / "var"
_LOCK = threading.Lock()


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or VAR / "app.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS decisions (offer TEXT, day TEXT, call INTEGER, start TEXT, updated_at TEXT, "
                "PRIMARY KEY (offer, day))")
    con.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, kind TEXT, params TEXT, state TEXT, "
                "progress REAL, message TEXT, result TEXT, created_at TEXT, updated_at TEXT)")
    con.commit()
    return con


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Store:
    def __init__(self, path: Path | None = None):
        self.con = connect(path)

    def decisions(self, offer: str) -> dict:
        with _LOCK:
            rows = self.con.execute("SELECT day, call, start FROM decisions WHERE offer = ?", (offer,)).fetchall()
        return {d: {"call": bool(c), "start": s} for d, c, s in rows}

    def set_decision(self, offer: str, day: str, call: bool, start: str | None) -> None:
        with _LOCK:
            self.con.execute("INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?, ?)", (offer, day, int(call), start, now()))
            self.con.commit()

    def clear_decision(self, offer: str, day: str) -> None:
        with _LOCK:
            self.con.execute("DELETE FROM decisions WHERE offer = ? AND day = ?", (offer, day))
            self.con.commit()

    def clear_all(self, offer: str) -> None:
        with _LOCK:
            self.con.execute("DELETE FROM decisions WHERE offer = ?", (offer,))
            self.con.commit()

    def new_job(self, kind: str, params: dict) -> int:
        with _LOCK:
            cur = self.con.execute("INSERT INTO jobs (kind, params, state, progress, created_at, updated_at) "
                                   "VALUES (?, ?, 'queued', 0, ?, ?)", (kind, json.dumps(params), now(), now()))
            self.con.commit()
            return int(cur.lastrowid)

    def update_job(self, jid: int, **kw) -> None:
        if "result" in kw:
            kw["result"] = json.dumps(kw["result"], allow_nan=False)
        sets = ", ".join(f"{k} = ?" for k in kw)
        with _LOCK:
            self.con.execute(f"UPDATE jobs SET {sets}, updated_at = ? WHERE id = ?", (*kw.values(), now(), jid))
            self.con.commit()

    def job(self, jid: int) -> dict | None:
        with _LOCK:
            r = self.con.execute("SELECT id, kind, params, state, progress, message, result, created_at, updated_at "
                                 "FROM jobs WHERE id = ?", (jid,)).fetchone()
        if r is None:
            return None
        keys = ("id", "kind", "params", "state", "progress", "message", "result", "created_at", "updated_at")
        out = dict(zip(keys, r))
        out["params"] = json.loads(out["params"] or "{}")
        out["result"] = json.loads(out["result"]) if out["result"] else None
        return out
