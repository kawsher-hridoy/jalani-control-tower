"""SQLite persistence for decisions, recommendations and incidents. Failures are buffered, never fatal."""
import json
import sqlite3
import threading
import time


class Store:
    def __init__(self, path: str):
        self.path = path
        self.error: str | None = None
        self.buffer: list[tuple[str, tuple]] = []
        self.lock = threading.Lock()
        self.conn: sqlite3.Connection | None = None
        self._connect()

    def _connect(self) -> None:
        try:
            self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=2)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, epoch INT, tick INT, data TEXT);
                CREATE TABLE IF NOT EXISTS recommendations (id TEXT PRIMARY KEY, epoch INT, updated REAL, data TEXT);
                CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, epoch INT, updated REAL, data TEXT);
            """)
            self.conn.commit()
            self.error = None
        except sqlite3.Error as exc:
            self.error = str(exc)

    def _exec(self, sql: str, args: tuple) -> None:
        with self.lock:
            try:
                if self.conn is None:
                    self._connect()
                for queued in self.buffer:
                    self.conn.execute(*queued)
                self.buffer.clear()
                self.conn.execute(sql, args)
                self.conn.commit()
                self.error = None
            except (sqlite3.Error, AttributeError) as exc:
                self.error = str(exc)
                if len(self.buffer) < 5000:
                    self.buffer.append((sql, args))

    def add_decision(self, d: dict) -> None:
        self._exec("INSERT OR REPLACE INTO decisions (id, epoch, tick, data) VALUES (?,?,?,?)",
                   (d["id"], d["epoch"], d["tick"], json.dumps(d)))

    def upsert(self, table: str, obj: dict) -> None:
        self._exec(f"INSERT OR REPLACE INTO {table} (id, epoch, updated, data) VALUES (?,?,?,?)",
                   (obj["id"], obj.get("epoch", 0), time.time(), json.dumps(obj)))

    def recent_decisions(self, limit: int = 500) -> list[dict]:
        try:
            rows = self.conn.execute("SELECT data FROM decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [json.loads(r[0]) for r in rows]
        except (sqlite3.Error, AttributeError):
            return []
