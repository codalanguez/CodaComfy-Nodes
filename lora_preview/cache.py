"""Bounded, disposable SQLite cache for LoRA browser data (not model tensors)."""
import sqlite3
from pathlib import Path
from threading import RLock
from time import time
from weakref import WeakValueDictionary


class DiskCache:
    def __init__(self, path, ttl=86400, max_bytes=256 * 1024 * 1024, max_entries=8192):
        self.path = Path(path)
        self.ttl, self.max_bytes, self.max_entries = ttl, max_bytes, max_entries
        self.lock = RLock()
        self.work_locks = WeakValueDictionary()
        self.ready = False

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            if not self.ready:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, created REAL, value BLOB)")
                db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, generation INTEGER)")
                db.execute("INSERT OR IGNORE INTO state VALUES (1, 0)")
                db.commit()
                self.ready = True
        except sqlite3.Error:
            db.close()
            raise
        return db

    def read(self, key):
        with self.lock:
            db = self.connect()
            try:
                generation = db.execute("SELECT generation FROM state WHERE id=1").fetchone()[0]
                row = db.execute("SELECT created, value FROM entries WHERE key=?", (key,)).fetchone()
                if row and time() - row[0] < self.ttl:
                    return row[1], generation
                return None, generation
            finally:
                db.close()

    def write(self, key, value, generation):
        if len(value) > self.max_bytes:
            return
        with self.lock:
            db = self.connect()
            try:
                with db:
                    # A refresh during computation must not repopulate stale data.
                    current = db.execute("SELECT generation FROM state WHERE id=1").fetchone()[0]
                    if current != generation:
                        return
                    db.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?)", (key, time(), value))
                    db.execute("DELETE FROM entries WHERE created < ?", (time() - self.ttl,))
                    while True:
                        count, size = db.execute("SELECT count(*), coalesce(sum(length(value)),0) FROM entries").fetchone()
                        if count <= self.max_entries and size <= self.max_bytes:
                            break
                        db.execute("DELETE FROM entries WHERE key IN (SELECT key FROM entries ORDER BY created LIMIT 1)")
            finally:
                db.close()

    def get_or_compute(self, key, compute):
        # Coalesce requests for the same item; independent items can load in parallel.
        with self.lock:
            work_lock = self.work_locks.get(key)
            if work_lock is None:
                work_lock = RLock()
                self.work_locks[key] = work_lock
        with work_lock:
            try:
                value, generation = self.read(key)
            except (OSError, sqlite3.Error):
                return compute()  # Cache failures never make the browser unusable.
            if value is not None:
                return value
            value = compute()
            try:
                self.write(key, value, generation)
            except (OSError, sqlite3.Error):
                pass
            return value

    def clear(self):
        with self.lock:
            db = self.connect()
            try:
                with db:
                    db.execute("DELETE FROM entries")
                    db.execute("UPDATE state SET generation=generation+1 WHERE id=1")
            finally:
                db.close()
