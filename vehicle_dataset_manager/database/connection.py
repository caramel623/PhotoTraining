"""Thin SQLite connection manager.

One :class:`Database` instance wraps a single connection. ``check_same_thread``
is disabled and a ``threading.RLock`` guards statements, which is sufficient for
a local desktop app with a small number of background workers. Each write is
committed explicitly; reads use the same connection for simplicity and
consistency within a request.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from vehicle_dataset_manager.database import schema


class Database:
    """Manages a single SQLite connection plus schema migration."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row
        self.migrate()

    # -- lifecycle -------------------------------------------------------
    def migrate(self) -> int:
        with self._lock:
            return schema.migrate(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- raw helpers -----------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, [tuple(p) for p in seq])

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return cur.fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def lastrowid(self, cur: sqlite3.Cursor) -> int:
        return int(cur.lastrowid)