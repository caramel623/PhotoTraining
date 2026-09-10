"""Data-access objects (repositories) over the :class:`Database`.

Repositories are deliberately thin: they translate domain objects to/from
SQLite rows and expose the operations the pipeline and GUI need. All JSON
fields (bboxes, quality flags) are stored as TEXT.

Key invariants:
* Resume: ``ImageRepository.claim_next_pending`` returns only rows in PENDING,
  so completed/failed/skipped images are never re-processed.
* Merge/Split: only relationships in ``vehicle_members`` change; source images
  and their rows are never rewritten.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from vehicle_dataset_manager.core import enums
from vehicle_dataset_manager.core.enums import (
    GroupSource,
    GroupVerification,
    JobState,
    OcrQuality,
    ProcessingStatus,
    ReviewStatus,
)
from vehicle_dataset_manager.database.connection import Database

log = logging.getLogger("vdm.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if value is not None else "null"

#: Verification -> training-label priority (README 39):
#: human_verified > high_confidence_plate_match > automatic_candidate.
_VERIFICATION_PRIORITY = {
    GroupVerification.AUTOMATIC_ONLY: "automatic_candidate",
    GroupVerification.PARTIALLY_VERIFIED: "high_confidence_plate_match",
    GroupVerification.VERIFIED: "human_verified",
}


def _best_label_priority(*values: Optional[str]) -> str:
    """Strongest label priority among the given values (missing -> automatic)."""
    ranked = [v for v in values if v in enums.LABEL_PRIORITY]
    if not ranked:
        return enums.LABEL_PRIORITY[-1]
    return min(ranked, key=enums.LABEL_PRIORITY.index)


def _loads(value: Optional[str], default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


# --------------------------------------------------------------------------- #
# Image repository
# --------------------------------------------------------------------------- #
@dataclass
class ImageRecord:
    image_id: int
    original_filename: str
    original_archive: Optional[str]
    archive_year: Optional[int]
    source_path: Optional[str]
    camera_id: Optional[str]
    processing_status: str
    review_status: str
    plate_text_normalized: Optional[str]
    quality_flags: list = field(default_factory=list)


class ImageRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_from_scan(
        self,
        *,
        original_filename: str,
        source_path: str,
        original_archive: Optional[str] = None,
        archive_year: Optional[int] = None,
        camera_id: Optional[str] = None,
    ) -> int:
        """Insert a freshly-scanned image as PENDING, or return the existing id.

        Uses the UNIQUE(source_path, original_filename) constraint so a re-scan
        never duplicates rows.
        """
        ts = _now()
        cur = self.db.execute(
            """
            INSERT INTO images (
                original_filename, source_path, original_archive, archive_year,
                camera_id, processing_status, review_status, quality_flags,
                created_at, updated_at
            ) VALUES (?,?,?,?,?, 'pending', 'unreviewed', '[]', ?, ?)
            ON CONFLICT (source_path, original_filename) DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (
                original_filename,
                source_path,
                original_archive,
                archive_year,
                camera_id,
                ts,
                ts,
            ),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self.db.query_one(
            "SELECT image_id FROM images WHERE source_path=? AND original_filename=?",
            (source_path, original_filename),
        )
        return int(row["image_id"]) if row else 0

    def claim_next_pending(
        self, archive_path: Optional[str] = None
    ) -> Optional[ImageRecord]:
        """Atomically pick one PENDING image and mark it PROCESSING.

        Returns None when nothing is left to process (resume-safe).
        """
        with self.db.conn:  # transaction
            sql = (
                "SELECT image_id FROM images WHERE processing_status='pending'"
            )
            params: list[Any] = []
            if archive_path:
                sql += " AND original_archive=?"
                params.append(archive_path)
            sql += " ORDER BY image_id LIMIT 1"
            row = self.db.query_one(sql, params)
            if not row:
                return None
            image_id = int(row["image_id"])
            self.db.execute(
                "UPDATE images SET processing_status='processing', updated_at=? WHERE image_id=?",
                (_now(), image_id),
            )
            return self.get(image_id)

    def get_by_source(self, source_path: str, original_filename: str) -> Optional[ImageRecord]:
        row = self.db.query_one(
            'SELECT image_id FROM images WHERE source_path=? AND original_filename=?',
            (source_path, original_filename),
        )
        return self.get(int(row['image_id'])) if row else None
    def get(self, image_id: int) -> Optional[ImageRecord]:
        row = self.db.query_one(
            """
            SELECT image_id, original_filename, original_archive, archive_year,
                   source_path, camera_id, processing_status, review_status,
                   plate_text_normalized, quality_flags
            FROM images WHERE image_id=?
            """,
            (image_id,),
        )
        return self._to_record(row) if row else None

    def _to_record(self, row) -> ImageRecord:
        return ImageRecord(
            image_id=int(row["image_id"]),
            original_filename=row["original_filename"],
            original_archive=row["original_archive"],
            archive_year=row["archive_year"],
            source_path=row["source_path"],
            camera_id=row["camera_id"],
            processing_status=row["processing_status"],
            review_status=row["review_status"],
            plate_text_normalized=row["plate_text_normalized"],
            quality_flags=_loads(row["quality_flags"], []),
        )

    def mark_completed(self, image_id: int, **fields: Any) -> None:
        self._update_fields(image_id, ProcessingStatus.COMPLETED, fields)

    def mark_failed(self, image_id: int, error: str) -> None:
        self._update_fields(image_id, ProcessingStatus.FAILED, {"error": error})

    def mark_skipped(self, image_id: int) -> None:
        self._update_fields(image_id, ProcessingStatus.SKIPPED, {})

    def set_result_fields(self, image_id: int, **fields: Any) -> None:
        self._update_fields(image_id, None, fields)

    def _update_fields(
        self, image_id: int, status: Optional[ProcessingStatus], fields: dict[str, Any]
    ) -> None:
        allowed = {
            "plate_text_raw", "plate_text_normalized", "plate_confidence",
            "vehicle_bbox", "plate_bbox", "processing_status", "review_status",
            "quality_flags", "camera_id", "captured_datetime", "date", "time",
            "vehicle_type", "speed", "direction", "speed_limit", "location",
            "device_serial", "sha256", "perceptual_hash",
            "width", "height", "error", "vehicle_crop_path",
        }
        cols = []
        vals: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("quality_flags", "vehicle_bbox", "plate_bbox") and not isinstance(value, str):
                value = _dumps(value)
            cols.append(f"{key}=?")
            vals.append(value)
        if status is not None:
            cols.append("processing_status=?")
            vals.append(str(status))
        if not cols:
            return
        cols.append("updated_at=?")
        vals.append(_now())
        vals.append(image_id)
        self.db.execute(f"UPDATE images SET {', '.join(cols)} WHERE image_id=?", vals)
        self.db.commit()

    def retry_failed(self, archive_path: Optional[str] = None) -> int:
        """Set FAILED images back to PENDING so they can be re-processed."""
        sql = "UPDATE images SET processing_status='pending', updated_at=? WHERE processing_status='failed'"
        params: list = [_now()]
        if archive_path:
            sql += " AND original_archive=?"
            params.append(archive_path)
        cur = self.db.execute(sql, params)
        self.db.commit()
        return cur.rowcount

    def reset_stuck_processing(self, archive_path: Optional[str] = None) -> int:
        """Return PROCESSING images (left by a crash) back to PENDING."""
        sql = ("UPDATE images SET processing_status='pending', updated_at=? "
               "WHERE processing_status='processing'")
        params: list[Any] = [_now()]
        if archive_path:
            sql += " AND original_archive=?"
            params.append(archive_path)
        cur = self.db.execute(sql, params)
        self.db.commit()
        return cur.rowcount

    def claim_image(self, image_id: int) -> bool:
        """Atomically mark one PENDING image as PROCESSING.

        Returns False when the row is no longer PENDING (already claimed by
        another run), so callers can skip it safely.
        """
        with self.db.conn:  # transaction
            cur = self.db.execute(
                "UPDATE images SET processing_status='processing', updated_at=? "
                "WHERE image_id=? AND processing_status='pending'",
                (_now(), image_id),
            )
            return cur.rowcount == 1

    def counts(self, archive_path: Optional[str] = None) -> dict[str, int]:
        where = ""
        params: list[Any] = []
        if archive_path:
            where = "WHERE original_archive=?"
            params.append(archive_path)
        rows = self.db.query(
            f"SELECT processing_status, COUNT(*) AS n FROM images {where} GROUP BY processing_status",
            params,
        )
        result = {
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "total": 0,
        }
        for row in rows:
            result[row["processing_status"]] = int(row["n"])
            result["total"] += int(row["n"])
        return result

    def list_for_review(
        self,
        limit: int = 200,
        offset: int = 0,
        plate_normalized: Optional[str] = None,
    ) -> list[ImageRecord]:
        sql = "SELECT * FROM images"
        where = []
        params: list[Any] = []
        if plate_normalized:
            where.append("plate_text_normalized=?")
            params.append(plate_normalized)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY image_id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.db.query(sql, params)
        return [self._to_record(r) for r in rows]


# --------------------------------------------------------------------------- #
# Vehicle (group) repository
# --------------------------------------------------------------------------- #
class VehicleRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _next_vehicle_id(self) -> str:
        row = self.db.query_one("SELECT MAX(vehicle_id) AS m FROM vehicles")
        n = 0
        if row and row["m"]:
            try:
                n = int(str(row["m"]).rsplit("_", 1)[-1])
            except ValueError:
                n = 0
        return f"Vehicle_{n + 1:06d}"

    def get_or_create_for_plate(
        self,
        plate_normalized: str,
        source: GroupSource = GroupSource.PLATE_EXACT,
    ) -> str:
        """Return an existing vehicle id for this plate, else create one.

        This is what the (future) plate-grouping step uses: same normalized plate
        maps to the same candidate vehicle group.
        """
        row = self.db.query_one(
            "SELECT vehicle_id FROM vehicles WHERE plate_normalized=? COLLATE NOCASE",
            (plate_normalized,),
        )
        if row:
            return row["vehicle_id"]
        ts = _now()
        vid = self._next_vehicle_id()
        self.db.execute(
            """
            INSERT INTO vehicles (vehicle_id, plate_normalized, verification, source,
                                   label_priority, created_at, updated_at)
            VALUES (?,?, 'automatic_only', ?, 'automatic_candidate', ?, ?)
            """,
            (vid, plate_normalized, str(source), ts, ts),
        )
        self.db.commit()
        return vid

    def add_member(
        self,
        vehicle_id: str,
        image_id: int,
        label_source: str = "plate_exact",
        confidence: Optional[float] = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO vehicle_members (vehicle_id, image_id, label_source, confidence, created_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT (vehicle_id, image_id) DO UPDATE SET
                label_source = excluded.label_source,
                confidence = COALESCE(excluded.confidence, confidence)
            """,
            (vehicle_id, image_id, label_source, confidence, _now()),
        )
        self.db.commit()

    def remove_member(self, vehicle_id: str, image_id: int) -> None:
        self.db.execute(
            "DELETE FROM vehicle_members WHERE vehicle_id=? AND image_id=?",
            (vehicle_id, image_id),
        )
        self.db.commit()

    def members_of(self, vehicle_id: str) -> list[dict]:
        rows = self.db.query(
            """
            SELECT m.image_id, m.label_source, m.confidence,
                   i.original_filename, i.source_path, i.camera_id, i.date,
                   i.plate_text_normalized, i.processing_status
            FROM vehicle_members m
            JOIN images i ON i.image_id = m.image_id
            WHERE m.vehicle_id=?
            ORDER BY i.date, i.image_id
            """,
            (vehicle_id,),
        )
        return [dict(r) for r in rows]

    def vehicle_for_image(self, image_id: int) -> Optional[str]:
        row = self.db.query_one(
            "SELECT vehicle_id FROM vehicle_members WHERE image_id=?",
            (image_id,),
        )
        return row["vehicle_id"] if row else None

    def list_vehicles(self, limit: int = 500, offset: int = 0) -> list[dict]:
        rows = self.db.query(
            """
            SELECT v.vehicle_id, v.plate_normalized, v.verification, v.source,
                   COUNT(m.image_id) AS image_count
            FROM vehicles v
            LEFT JOIN vehicle_members m ON m.vehicle_id = v.vehicle_id
            GROUP BY v.vehicle_id
            ORDER BY v.vehicle_id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(r) for r in rows]

    def get(self, vehicle_id: str) -> Optional[dict]:
        row = self.db.query_one(
            "SELECT * FROM vehicles WHERE vehicle_id=?", (vehicle_id,)
        )
        return dict(row) if row else None

    def set_verification(
        self, vehicle_id: str, verification: GroupVerification
    ) -> None:
        self.db.execute(
            "UPDATE vehicles SET verification=?, label_priority=?, updated_at=? "
            "WHERE vehicle_id=?",
            (str(verification), _VERIFICATION_PRIORITY[verification], _now(), vehicle_id),
        )
        self.db.commit()

    def set_note(self, vehicle_id: str, note: str) -> None:
        self.db.execute(
            "UPDATE vehicles SET note=?, updated_at=? WHERE vehicle_id=?",
            (note, _now(), vehicle_id),
        )
        self.db.commit()

    def merge(self, source_id: str, target_id: str) -> str:
        """Merge ``source_id`` into ``target_id``; keep target. Returns target id.

        Members are moved (existing target members win on conflict); the source
        vehicle row is deleted. Only relationships change, never the images.
        """
        if source_id == target_id:
            return target_id
        src = self.get(source_id)
        tgt = self.get(target_id)
        if not src or not tgt:
            raise ValueError(f"merge: unknown vehicle ({source_id} / {target_id})")
        with self.db.conn:
            self.db.execute(
                """
                INSERT INTO vehicle_members (vehicle_id, image_id, label_source, confidence, created_at)
                SELECT ?, image_id, label_source, confidence, created_at FROM vehicle_members
                WHERE vehicle_id=?
                ON CONFLICT (vehicle_id, image_id) DO NOTHING
                """,
                (target_id, source_id),
            )
            self.db.execute("DELETE FROM vehicle_members WHERE vehicle_id=?", (source_id,))
            self.db.execute(
                "DELETE FROM vehicles WHERE vehicle_id=?", (source_id,)
            )
            self.db.execute(
                "UPDATE vehicles SET label_priority=?, updated_at=? WHERE vehicle_id=?",
                (_best_label_priority(src.get("label_priority"), tgt.get("label_priority")),
                 _now(), target_id),
            )
            self.db.commit()
        return target_id

    def split(self, vehicle_id: str, image_ids: Iterable[int]) -> str:
        """Move the given images out of ``vehicle_id`` into a new group.

        Returns the new group id. Images not in ``image_ids`` stay put. Source
        images are never modified, only membership.
        """
        ids = [int(i) for i in image_ids]
        if not ids:
            raise ValueError("split: no image_ids provided")
        ts = _now()
        new_id = self._next_vehicle_id()
        # Copy plate of the original as the starting point (weak signal).
        orig = self.get(vehicle_id)
        plate = orig["plate_normalized"] if orig else None
        with self.db.conn:
            self.db.execute(
                """
                INSERT INTO vehicles (vehicle_id, plate_normalized, verification, source,
                                       label_priority, created_at, updated_at)
                VALUES (?,?,'automatic_only','manual','automatic_candidate',?,?)
                """,
                (new_id, plate, ts, ts),
            )
            ph = ",".join("?" for _ in ids)
            self.db.execute(
                f"""
                INSERT INTO vehicle_members (vehicle_id, image_id, label_source, confidence, created_at)
                SELECT ?, image_id, 'manual', confidence, ? FROM vehicle_members
                WHERE vehicle_id=? AND image_id IN ({ph})
                ON CONFLICT (vehicle_id, image_id) DO NOTHING
                """,
                (new_id, ts, vehicle_id, *ids),
            )
            self.db.execute(
                f"DELETE FROM vehicle_members WHERE vehicle_id=? AND image_id IN ({ph})",
                (vehicle_id, *ids),
            )
            self.db.commit()
        return new_id


# --------------------------------------------------------------------------- #
# Processing-job repository (resume bookkeeping)
# --------------------------------------------------------------------------- #
class JobRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        job_type: str,
        archive_path: Optional[str] = None,
        total: int = 0,
    ) -> int:
        ts = _now()
        cur = self.db.execute(
            """
            INSERT INTO processing_jobs (job_type, status, total, processed, skipped,
                                         failed, pending, archive_path, created_at, updated_at)
            VALUES (?, 'pending', ?, 0, 0, 0, ?, ?, ?, ?)
            """,
            (job_type, total, total, archive_path, ts, ts),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def get(self, job_id: int) -> Optional[dict]:
        row = self.db.query_one(
            "SELECT * FROM processing_jobs WHERE job_id=?", (job_id,)
        )
        return dict(row) if row else None

    def set_state(self, job_id: int, state: JobState) -> None:
        self.db.execute(
            "UPDATE processing_jobs SET status=?, updated_at=? WHERE job_id=?",
            (str(state), _now(), job_id),
        )
        self.db.commit()

    def update_counters(
        self, job_id: int, processed: int, skipped: int, failed: int, pending: int
    ) -> None:
        self.db.execute(
            """
            UPDATE processing_jobs
            SET processed=?, skipped=?, failed=?, pending=?, updated_at=? WHERE job_id=?
            """,
            (processed, skipped, failed, pending, _now(), job_id),
        )
        self.db.commit()

    def list_jobs(self, limit: int = 200) -> list[dict]:
        rows = self.db.query(
            'SELECT * FROM processing_jobs ORDER BY job_id DESC LIMIT ?', (limit,)
        )
        return [dict(r) for r in rows]

    def last_job(self, job_type: Optional[str] = None) -> Optional[dict]:
        if job_type:
            row = self.db.query_one(
                "SELECT * FROM processing_jobs WHERE job_type=? ORDER BY job_id DESC LIMIT 1",
                (job_type,),
            )
        else:
            row = self.db.query_one(
                "SELECT * FROM processing_jobs ORDER BY job_id DESC LIMIT 1"
            )
        return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Plate / detection / ocr / review / duplicate / export repositories (light)
# --------------------------------------------------------------------------- #
class PlateRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        image_id: int,
        *,
        raw: Optional[str] = None,
        normalized: Optional[str] = None,
        confidence: Optional[float] = None,
        quality: OcrQuality = OcrQuality.UNKNOWN,
        bbox: Optional[dict] = None,
    ) -> int:
        cur = self.db.execute(
            """
            INSERT INTO plates (image_id, plate_text_raw, plate_text_normalized,
                                confidence, quality, bbox, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (image_id, raw, normalized, confidence, str(quality), _dumps(bbox), _now()),
        )
        self.db.commit()
        return int(cur.lastrowid)


class DetectionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        image_id: int,
        *,
        kind: str,
        class_name: Optional[str] = None,
        confidence: Optional[float] = None,
        bbox: Optional[dict] = None,
    ) -> int:
        cur = self.db.execute(
            """
            INSERT INTO detections (image_id, kind, class_name, confidence, bbox, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (image_id, kind, class_name, confidence, _dumps(bbox), _now()),
        )
        self.db.commit()
        return int(cur.lastrowid)


class OcrRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        image_id: int,
        *,
        raw: Optional[str] = None,
        normalized: Optional[str] = None,
        confidence: Optional[float] = None,
        engine: Optional[str] = None,
    ) -> int:
        cur = self.db.execute(
            """
            INSERT INTO ocr_results (image_id, text_raw, text_normalized, confidence, engine, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (image_id, raw, normalized, confidence, engine, _now()),
        )
        self.db.commit()
        return int(cur.lastrowid)


class ReviewRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        image_id: int,
        status: ReviewStatus,
        *,
        vehicle_id: Optional[str] = None,
        reviewer: Optional[str] = None,
        note: Optional[str] = None,
    ) -> int:
        ts = _now()
        existing = self.db.query_one(
            "SELECT review_id, vehicle_id, reviewer, note FROM reviews WHERE image_id=?", (image_id,)
        )
        if existing:
            self.db.execute(
                """
                UPDATE reviews SET review_status=?, vehicle_id=?, reviewer=?, note=?, reviewed_at=?
                WHERE review_id=?
                """,
                (
                    str(status),
                    vehicle_id or existing["vehicle_id"],
                    reviewer or existing["reviewer"],
                    note or existing["note"],
                    ts,
                    existing["review_id"],
                ),
            )
            review_id = int(existing["review_id"])
        else:
            cur = self.db.execute(
                """
                INSERT INTO reviews (image_id, vehicle_id, review_status, reviewer, note, reviewed_at)
                VALUES (?,?,?,?,?,?)
                """,
                (image_id, vehicle_id, str(status), reviewer, note, ts),
            )
            review_id = int(cur.lastrowid)
        self.db.execute(
            "UPDATE images SET review_status=?, updated_at=? WHERE image_id=?",
            (str(status), ts, image_id),
        )
        self.db.commit()
        return review_id


class DuplicateRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        image_id: int,
        duplicate_of: int,
        dup_type: str,
        score: Optional[float] = None,
    ) -> int:
        cur = self.db.execute(
            """
            INSERT INTO duplicates (image_id, duplicate_of_id, dup_type, score, created_at)
            VALUES (?,?,?,?,?)
            """,
            (image_id, duplicate_of, dup_type, score, _now()),
        )
        self.db.commit()
        return int(cur.lastrowid)


class ExportRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        output_dir: str,
        split_config: Optional[dict] = None,
        image_count: int = 0,
        vehicle_count: int = 0,
        created_by: Optional[str] = None,
    ) -> int:
        cur = self.db.execute(
            """
            INSERT INTO dataset_exports (exported_at, output_dir, split_config,
                                         image_count, vehicle_count, created_by)
            VALUES (?,?,?,?,?,?)
            """,
            (_now(), output_dir, _dumps(split_config), image_count, vehicle_count, created_by),
        )
        self.db.commit()
        return int(cur.lastrowid)