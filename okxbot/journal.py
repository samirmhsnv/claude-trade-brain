"""Persistent state (survives restarts) + an SQLite journal of every decision and trade."""
import json
import os
import sqlite3
from datetime import datetime, timezone

from .config import SETTINGS

os.makedirs(SETTINGS.data_dir, exist_ok=True)
STATE_PATH = os.path.join(SETTINGS.data_dir, "state.json")
DB_PATH = os.path.join(SETTINGS.data_dir, "journal.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"peak_equity": None, "day": None, "day_start_equity": None, "paused_until": None,
            "block_entries_until": None, "positions": {}, "last_scan": None, "start_equity": None,
            "started_at": now_iso(), "brain_cost_usd": 0.0}


def save_state(st: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2, default=str)
    os.replace(tmp, STATE_PATH)


def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, symbol TEXT, data TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS equity(ts TEXT PRIMARY KEY, equity REAL)""")
    return con


def log(kind: str, symbol: str | None = None, **data) -> None:
    with _db() as con:
        con.execute("INSERT INTO events(ts, kind, symbol, data) VALUES (?,?,?,?)",
                    (now_iso(), kind, symbol, json.dumps(data, default=str)))


def log_equity(eq: float) -> None:
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO equity VALUES (?,?)", (now_iso(), eq))


def events(kind: str | None = None) -> list[dict]:
    with _db() as con:
        q = "SELECT ts, kind, symbol, data FROM events" + (" WHERE kind=?" if kind else "") + " ORDER BY id"
        rows = con.execute(q, (kind,) if kind else ()).fetchall()
    return [{"ts": t, "kind": k, "symbol": s, **json.loads(d)} for t, k, s, d in rows]


def equity_curve() -> list[tuple[str, float]]:
    with _db() as con:
        return con.execute("SELECT ts, equity FROM equity ORDER BY ts").fetchall()
