"""Persistencia SQLite (esquema único del simulador)."""
import json
import sqlite3
import threading
from contextlib import contextmanager

from .config import PERIOD_DAYS

SCHEMA = """
CREATE TABLE IF NOT EXISTS pools(
  id TEXT PRIMARY KEY, capacity_mb REAL NOT NULL, is_overflow INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sims(
  iccid TEXT PRIMARY KEY, msisdn TEXT NOT NULL, terminal_id TEXT NOT NULL, merchant TEXT NOT NULL,
  city TEXT NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'activa', profile TEXT NOT NULL,
  pool_id TEXT NOT NULL REFERENCES pools(id), origin_pool TEXT NOT NULL, last_move_day INTEGER,
  daily_mb REAL NOT NULL DEFAULT 0, period_mb REAL NOT NULL DEFAULT 0, anomaly INTEGER NOT NULL DEFAULT 0,
  base_rate REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage_daily(
  period INTEGER NOT NULL, day INTEGER NOT NULL, iccid TEXT NOT NULL, pool_id TEXT NOT NULL, mb REAL NOT NULL,
  PRIMARY KEY(period, day, iccid));
CREATE INDEX IF NOT EXISTS ix_usage_pool ON usage_daily(period, pool_id);
CREATE TABLE IF NOT EXISTS moves(
  id INTEGER PRIMARY KEY AUTOINCREMENT, iccid TEXT NOT NULL, from_pool TEXT NOT NULL, to_pool TEXT NOT NULL,
  state TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT, batch_id TEXT, reverts_id INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, idem_key TEXT, actor TEXT,
  period INTEGER NOT NULL, created_day INTEGER NOT NULL, updated_day INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_moves_state ON moves(state);
CREATE INDEX IF NOT EXISTS ix_moves_iccid ON moves(iccid);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, severity TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open',
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, message TEXT NOT NULL, period INTEGER NOT NULL,
  abs_day INTEGER NOT NULL, updated_day INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, abs_day INTEGER NOT NULL, period INTEGER NOT NULL,
  actor TEXT NOT NULL, event TEXT NOT NULL, entity TEXT, entity_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS idem(
  key TEXT PRIMARY KEY, iccid TEXT NOT NULL, pool_id TEXT NOT NULL, response TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
TABLES = ("pools", "sims", "usage_daily", "moves", "alerts", "audit", "idem", "kv")


class Database:
    def __init__(self, path=":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._depth = 0
        self.period = 1
        self.day = 0
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    @property
    def abs_day(self):
        return (self.period - 1) * PERIOD_DAYS + self.day

    @contextmanager
    def tx(self):
        with self.lock:
            if self._depth == 0:
                self.conn.execute("BEGIN")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute("COMMIT")

    def exec(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params)

    def all(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        with self.lock:
            r = self.conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    def scalar(self, sql, params=()):
        with self.lock:
            r = self.conn.execute(sql, params).fetchone()
            return r[0] if r else None

    def kv_get(self, key, default=None):
        v = self.scalar("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(v) if v is not None else default

    def kv_set(self, key, value):
        self.exec("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))

    def wipe(self):
        with self.tx():
            for t in reversed(TABLES):      # sims antes que pools (FK)
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.execute("DELETE FROM sqlite_sequence")
        self.period, self.day = 1, 0
