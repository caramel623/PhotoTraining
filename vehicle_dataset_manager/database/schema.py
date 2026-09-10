"""SQLite schema definition and migrations.

Uses a ``schema_version`` table with an ordered list of migration steps so the
database can evolve across releases without data loss. Every migration is a
list of DDL statements executed inside a single transaction.
"""
from __future__ import annotations

import sqlite3

_CURRENT_VERSION = 3

#: Ordered migrations. Each entry is a list of SQL statements.
_MIGRATIONS: list[list[str]] = [
    [
        "PRAGMA journal_mode=WAL;",
        """
        CREATE TABLE IF NOT EXISTS images (
            image_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            original_filename   TEXT NOT NULL,
            original_archive    TEXT,
            archive_year        INTEGER,
            source_path         TEXT,
            camera_id           TEXT,
            captured_datetime   TEXT,
            date                TEXT,
            time                TEXT,
            vehicle_type        TEXT,
            speed               REAL,
            direction           TEXT,
            plate_text_raw      TEXT,
            plate_text_normalized TEXT,
            plate_confidence    REAL,
            vehicle_bbox        TEXT,
            plate_bbox          TEXT,
            processing_status   TEXT NOT NULL DEFAULT 'pending',
            review_status       TEXT NOT NULL DEFAULT 'unreviewed',
            quality_flags       TEXT DEFAULT '[]',
            sha256              TEXT,
            perceptual_hash     TEXT,
            width               INTEGER,
            height              INTEGER,
            error               TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            UNIQUE (source_path, original_filename)
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_images_status ON images (processing_status);",
        "CREATE INDEX IF NOT EXISTS idx_images_plate ON images (plate_text_normalized);",
        "CREATE INDEX IF NOT EXISTS idx_images_archive ON images (original_archive);",
        "CREATE INDEX IF NOT EXISTS idx_images_sha ON images (sha256);",

        """
        CREATE TABLE IF NOT EXISTS vehicles (
            vehicle_id        TEXT PRIMARY KEY,
            plate_normalized  TEXT,
            verification      TEXT NOT NULL DEFAULT 'automatic_only',
            source            TEXT NOT NULL DEFAULT 'plate_exact',
            label_priority    TEXT,
            note              TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_vehicles_plate ON vehicles (plate_normalized);",

        """
        CREATE TABLE IF NOT EXISTS vehicle_members (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id    TEXT NOT NULL,
            image_id      INTEGER NOT NULL,
            label_source  TEXT,
            confidence    REAL,
            created_at    TEXT NOT NULL,
            UNIQUE (vehicle_id, image_id),
            FOREIGN KEY (vehicle_id) REFERENCES vehicles (vehicle_id) ON DELETE CASCADE,
            FOREIGN KEY (image_id) REFERENCES images (image_id) ON DELETE CASCADE
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_members_image ON vehicle_members (image_id);",

        """
        CREATE TABLE IF NOT EXISTS plates (
            plate_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id            INTEGER NOT NULL,
            plate_text_raw      TEXT,
            plate_text_normalized TEXT,
            confidence          REAL,
            quality             TEXT,
            bbox                TEXT,
            created_at          TEXT NOT NULL,
            FOREIGN KEY (image_id) REFERENCES images (image_id) ON DELETE CASCADE
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_plates_norm ON plates (plate_text_normalized);",

        """
        CREATE TABLE IF NOT EXISTS detections (
            detection_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id        INTEGER NOT NULL,
            kind            TEXT NOT NULL,
            class_name      TEXT,
            confidence      REAL,
            bbox            TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (image_id) REFERENCES images (image_id) ON DELETE CASCADE
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_det_image ON detections (image_id);",

        """
        CREATE TABLE IF NOT EXISTS ocr_results (
            ocr_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id          INTEGER NOT NULL,
            text_raw          TEXT,
            text_normalized   TEXT,
            confidence        REAL,
            engine            TEXT,
            created_at        TEXT NOT NULL,
            FOREIGN KEY (image_id) REFERENCES images (image_id) ON DELETE CASCADE
        );
        """,

        """
        CREATE TABLE IF NOT EXISTS reviews (
            review_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id        INTEGER NOT NULL,
            vehicle_id      TEXT,
            review_status   TEXT NOT NULL,
            reviewer        TEXT,
            note            TEXT,
            reviewed_at     TEXT NOT NULL,
            FOREIGN KEY (image_id) REFERENCES images (image_id) ON DELETE CASCADE,
            FOREIGN KEY (vehicle_id) REFERENCES vehicles (vehicle_id) ON DELETE SET NULL
        );
        """,

        """
        CREATE TABLE IF NOT EXISTS processing_jobs (
            job_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type      TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            total         INTEGER DEFAULT 0,
            processed     INTEGER DEFAULT 0,
            skipped       INTEGER DEFAULT 0,
            failed        INTEGER DEFAULT 0,
            pending       INTEGER DEFAULT 0,
            archive_path  TEXT,
            message       TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );
        """,

        """
        CREATE TABLE IF NOT EXISTS duplicates (
            dup_id              INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id            INTEGER NOT NULL,
            duplicate_of_id     INTEGER,
            dup_type            TEXT NOT NULL,
            score               REAL,
            created_at          TEXT NOT NULL,
            FOREIGN KEY (image_id) REFERENCES images (image_id) ON DELETE CASCADE,
            FOREIGN KEY (duplicate_of_id) REFERENCES images (image_id) ON DELETE CASCADE
        );
        """,

        """
        CREATE TABLE IF NOT EXISTS dataset_exports (
            export_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            exported_at     TEXT NOT NULL,
            output_dir      TEXT NOT NULL,
            split_config    TEXT,
            image_count     INTEGER DEFAULT 0,
            vehicle_count   INTEGER DEFAULT 0,
            created_by      TEXT
        );
        """,
    ],
    [
        "ALTER TABLE images ADD COLUMN speed_limit REAL",
        "ALTER TABLE images ADD COLUMN location TEXT",
        "ALTER TABLE images ADD COLUMN device_serial TEXT",
    ],
    [
        "ALTER TABLE images ADD COLUMN vehicle_crop_path TEXT",
    ],
]

def _get_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database up to the current version. Returns new version."""
    conn.execute("PRAGMA foreign_keys=ON;")
    current = _get_version(conn)
    for idx in range(current, len(_MIGRATIONS)):
        target_version = idx + 1
        statements = _MIGRATIONS[idx]
        try:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (target_version, _now()),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
    return _get_version(conn)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()