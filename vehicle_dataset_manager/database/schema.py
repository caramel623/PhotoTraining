"""SQLite schema definition and migrations.

Uses a ``schema_version`` table with an ordered list of migration steps so the
database can evolve across releases without data loss. Every migration is a
list of DDL statements executed inside a single transaction.
"""
from __future__ import annotations

import sqlite3

_CURRENT_VERSION = 5

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
    [
        "ALTER TABLE images ADD COLUMN vehicle_crop_bbox TEXT",
    ],
    [
        "ALTER TABLE images ADD COLUMN ini_present INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE images ADD COLUMN ini_path TEXT",
        "ALTER TABLE images ADD COLUMN ini_archive_member TEXT",
        "ALTER TABLE images ADD COLUMN ini_parse_status TEXT NOT NULL DEFAULT 'missing'",
        "ALTER TABLE images ADD COLUMN ini_encoding TEXT",
        "ALTER TABLE images ADD COLUMN ini_raw_metadata TEXT",
        "ALTER TABLE images ADD COLUMN ini_plate_text TEXT",
        "ALTER TABLE images ADD COLUMN ini_sha256 TEXT",
        "ALTER TABLE images ADD COLUMN ini_parser_version INTEGER",
        "ALTER TABLE images ADD COLUMN manual_plate_text TEXT",
        "ALTER TABLE images ADD COLUMN ocr_plate_text TEXT",
        "ALTER TABLE images ADD COLUMN ocr_plate_normalized TEXT",
        "ALTER TABLE images ADD COLUMN plate_source TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE images ADD COLUMN plate_validation_status TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE images ADD COLUMN label_confidence REAL",
        "ALTER TABLE images ADD COLUMN label_trust_level TEXT",
        "ALTER TABLE images ADD COLUMN metadata_conflict INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE images ADD COLUMN conflict_type TEXT",
        "ALTER TABLE images ADD COLUMN certificate_id TEXT",
        "ALTER TABLE images ADD COLUMN image_sequence TEXT",
        "ALTER TABLE images ADD COLUMN operator_name TEXT",
        "ALTER TABLE images ADD COLUMN direction_text TEXT",
        "ALTER TABLE images ADD COLUMN direction_code TEXT",
        "ALTER TABLE images ADD COLUMN violation_type TEXT",
        "ALTER TABLE images ADD COLUMN amount TEXT",
        "ALTER TABLE images ADD COLUMN vehicle_type_code TEXT",
        "ALTER TABLE images ADD COLUMN violation_code TEXT",
        "ALTER TABLE images ADD COLUMN vehicle_speed REAL",
        """
        CREATE TABLE IF NOT EXISTS archives (
            archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_path TEXT NOT NULL UNIQUE,
            archive_filename TEXT NOT NULL,
            file_size INTEGER,
            mtime_ns INTEGER,
            sha256 TEXT,
            last_scanned_at TEXT,
            parser_version INTEGER,
            import_version TEXT,
            duplicate_of_archive_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (duplicate_of_archive_id) REFERENCES archives (archive_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_archives_sha ON archives (sha256)",
        """
        CREATE TABLE IF NOT EXISTS archive_members (
            member_id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_id INTEGER NOT NULL,
            member_path TEXT NOT NULL,
            member_type TEXT NOT NULL,
            size INTEGER,
            sha256 TEXT,
            last_seen_at TEXT NOT NULL,
            UNIQUE (archive_id, member_path),
            FOREIGN KEY (archive_id) REFERENCES archives (archive_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS image_sources (
            source_id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            archive_id INTEGER NOT NULL,
            member_path TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE (image_id, archive_id, member_path),
            FOREIGN KEY (image_id) REFERENCES images (image_id) ON DELETE CASCADE,
            FOREIGN KEY (archive_id) REFERENCES archives (archive_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS import_jobs (
            import_job_id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            images_found INTEGER NOT NULL DEFAULT 0,
            images_new INTEGER NOT NULL DEFAULT 0,
            images_existing INTEGER NOT NULL DEFAULT 0,
            ini_found INTEGER NOT NULL DEFAULT 0,
            ini_new INTEGER NOT NULL DEFAULT 0,
            ini_existing INTEGER NOT NULL DEFAULT 0,
            ini_matched INTEGER NOT NULL DEFAULT 0,
            ini_missing INTEGER NOT NULL DEFAULT 0,
            ini_parse_error INTEGER NOT NULL DEFAULT 0,
            ini_with_plate INTEGER NOT NULL DEFAULT 0,
            ini_without_plate INTEGER NOT NULL DEFAULT 0,
            unmatched_ini INTEGER NOT NULL DEFAULT 0,
            metadata_updated INTEGER NOT NULL DEFAULT 0,
            conflicts INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            message TEXT,
            FOREIGN KEY (archive_id) REFERENCES archives (archive_id) ON DELETE CASCADE
        )
        """,
        """CREATE TABLE ini_sources (
            image_id INTEGER NOT NULL REFERENCES images(image_id),
            archive_id INTEGER NOT NULL REFERENCES archives(archive_id),
            member_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            parser_version INTEGER NOT NULL,
            parse_status TEXT NOT NULL,
            raw_metadata TEXT NOT NULL,
            plate_text TEXT,
            PRIMARY KEY(image_id,archive_id,member_path)
        )""",
        "UPDATE images SET ocr_plate_text=plate_text_raw, "
        "ocr_plate_normalized=plate_text_normalized, "
        "plate_source=CASE WHEN plate_text_normalized IS NULL THEN 'unknown' ELSE 'ocr' END",
        "UPDATE images SET manual_plate_text=COALESCE(plate_text_raw,plate_text_normalized), plate_source='manual' "
        "WHERE image_id IN (SELECT image_id FROM vehicle_members WHERE label_source='manual')",
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
                if stmt.lstrip().upper().startswith("PRAGMA"):
                    conn.execute(stmt)
            conn.execute("BEGIN")
            for stmt in statements:
                if not stmt.lstrip().upper().startswith("PRAGMA"):
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
