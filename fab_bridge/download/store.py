"""SQLite index of downloaded assets (SPEC.md §9).

The index is a cache, never the source of truth: if it is damaged it is
renamed and rebuilt by scanning the library. Short transactions and a fresh
connection per call keep worker threads and the UI out of each other's way.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing

from ..core import log

log = log.get("store")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS assets(
  uid TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  seller TEXT,
  listing_type TEXT,
  category TEXT,
  last_quality TEXT,
  last_format TEXT,
  updated_at REAL
);

CREATE TABLE IF NOT EXISTS files(
  uid TEXT NOT NULL,
  format TEXT NOT NULL,
  quality TEXT NOT NULL,
  path TEXT NOT NULL,
  bytes INTEGER NOT NULL DEFAULT 0,
  filename TEXT,
  imported_at REAL,
  deleted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(uid, format, quality)
);
"""


class Index:
    def __init__(self, root: str):
        self.root = root
        self._path = os.path.join(root, ".fab_index.sqlite")
        self._lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        os.makedirs(self.root, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=15)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def ensure(self) -> bool:
        """Create the schema, recreating the file if it is unreadable."""
        with self._lock:
            try:
                with closing(self._connect()) as connection:
                    connection.executescript(_SCHEMA)
                    connection.execute(
                        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                        (str(SCHEMA_VERSION),),
                    )
                    connection.commit()
                return True
            except sqlite3.DatabaseError:
                log.warning("index is corrupt; recreating %s", self._path)
                try:
                    os.replace(self._path, f"{self._path}.corrupt-{int(time.time())}")
                except OSError:
                    pass
                try:
                    with closing(self._connect()) as connection:
                        connection.executescript(_SCHEMA)
                        connection.commit()
                    return True
                except sqlite3.DatabaseError:
                    return False

    # -- writes -----------------------------------------------------------
    def record(
        self,
        uid: str,
        title: str,
        *,
        seller: str = "",
        listing_type: str = "",
        category: str = "",
        format_code: str,
        quality: str,
        path: str,
        size: int,
        filename: str = "",
    ) -> None:
        now = time.time()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO assets(uid, title, seller, listing_type, category,"
                    " last_quality, last_format, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (uid, title, seller, listing_type, category, quality, format_code, now),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO files(uid, format, quality, path, bytes, filename,"
                    " imported_at, deleted) VALUES(?,?,?,?,?,?,?,0)",
                    (uid, format_code, quality, path, int(size), filename, now),
                )
        except sqlite3.DatabaseError as exc:
            log.warning("index write failed: %s", type(exc).__name__)

    def mark_deleted(self, uid: str, format_code: str, quality: str) -> None:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "UPDATE files SET deleted=1 WHERE uid=? AND format=? AND quality=?",
                    (uid, format_code, quality),
                )
        except sqlite3.DatabaseError:
            pass

    # -- reads ------------------------------------------------------------
    def lookup(self, uid: str, format_code: str, quality: str) -> str | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT path FROM files WHERE uid=? AND format=? AND quality=? AND deleted=0",
                    (uid, format_code, quality),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        if row and row[0] and os.path.isdir(row[0]):
            return str(row[0])
        return None

    def downloaded_count(self) -> int:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute("SELECT COUNT(*) FROM files WHERE deleted=0").fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row[0] or 0) if row else 0

    def total_bytes(self) -> int:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT COALESCE(SUM(bytes), 0) FROM files WHERE deleted=0"
                ).fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row[0] or 0) if row else 0
