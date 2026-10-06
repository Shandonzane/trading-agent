"""SQLite trade journal: every decision, order and daily equity snapshot."""
import json
import sqlite3
from datetime import datetime, timezone

from .config import ROOT

DB = ROOT / "data" / "journal.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (ts TEXT, strategy TEXT, symbol TEXT, action TEXT, reason TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS orders (ts TEXT, strategy TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, broker TEXT, status TEXT, broker_id TEXT);
CREATE TABLE IF NOT EXISTS lots (strategy TEXT, symbol TEXT, qty REAL, entry_price REAL, entry_date TEXT, PRIMARY KEY (strategy, symbol));
CREATE TABLE IF NOT EXISTS equity (ts TEXT, broker TEXT, equity REAL, cash REAL);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    path = path or DB
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def decision(con, strategy, symbol, action, reason, detail=None):
    con.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                (_now(), strategy, symbol, action, reason, json.dumps(detail or {})))
    con.commit()


def order(con, strategy, symbol, side, qty, price, broker, status, broker_id=""):
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                (_now(), strategy, symbol, side, qty, price, broker, status, broker_id))
    con.commit()


def get_lot(con, strategy, symbol):
    r = con.execute("SELECT qty, entry_price, entry_date FROM lots WHERE strategy=? AND symbol=?",
                    (strategy, symbol)).fetchone()
    return {"qty": r[0], "entry_price": r[1], "entry_date": r[2]} if r else None


def set_lot(con, strategy, symbol, qty, entry_price=None, entry_date=None):
    if qty <= 0:
        con.execute("DELETE FROM lots WHERE strategy=? AND symbol=?", (strategy, symbol))
    else:
        con.execute("INSERT OR REPLACE INTO lots VALUES (?,?,?,?,?)",
                    (strategy, symbol, qty, entry_price, entry_date))
    con.commit()


def snapshot(con, broker, equity, cash):
    con.execute("INSERT INTO equity VALUES (?,?,?,?)", (_now(), broker, equity, cash))
    con.commit()
