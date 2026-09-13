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
from datetime import datetime
from uuid import uuid4
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from vehicle_dataset_manager.database import schema


class Database:
    """Manages a single SQLite connection plus schema migration."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._transaction_depth = 0
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
    def backup_and_clear(self) -> Path:
        """Back up a quiescent DB, then atomically clear known application data.

        Keep schema and monotonically increasing IDs to avoid stale UI IDs
        targeting a later import. Files outside this database are untouched.
        """
        tables = (
            "ini_sources", "image_sources", "archive_members", "import_jobs",
            "reviews", "vehicle_members", "plates", "detections", "ocr_results",
            "duplicates", "processing_jobs", "dataset_exports", "images",
            "vehicles", "archives",
        )
        with self._lock:
            if self._transaction_depth or self._conn.in_transaction:
                raise RuntimeError("資料庫仍有未完成交易，請等目前工作完成。")
            actual = {row[0] for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if actual != set(tables) | {"schema_version"}:
                raise RuntimeError("資料庫結構與預期不符，已取消清除以保護資料。")
            folder = self.db_path.resolve().parent / "backups"
            folder.mkdir(exist_ok=True)
            backup = folder / f"before-clear-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex}.sqlite3"
            partial = backup.with_suffix(".partial")
            # Exclusive creation ensures an existing backup is never overwritten.
            with partial.open("xb"):
                pass
            target = sqlite3.connect(partial)
            try:
                self._conn.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("資料庫備份驗證失敗，未清除資料。")
            finally:
                target.close()
            partial.rename(backup)
            with self.transaction():
                for table in tables:
                    self._conn.execute(f'DELETE FROM "{table}"')
            return backup

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
            if not self._transaction_depth:
                self._conn.commit()

    @contextmanager
    def transaction(self):
        """Serialize an atomic checkpoint, including repository writes."""
        with self._lock:
            name = f"checkpoint_{self._transaction_depth}"
            self._conn.execute(f"SAVEPOINT {name}")
            self._transaction_depth += 1
            try:
                yield
            except BaseException:
                self._conn.execute(f"ROLLBACK TO {name}")
                raise
            finally:
                self._transaction_depth -= 1
                self._conn.execute(f"RELEASE {name}")

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return cur.fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def lastrowid(self, cur: sqlite3.Cursor) -> int:
        return int(cur.lastrowid)
