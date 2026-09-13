"""Idempotent archive import and parser-version-aware metadata rescanning."""
from __future__ import annotations

import json
import hashlib
import logging
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from vehicle_dataset_manager import __version__
from vehicle_dataset_manager.archive.manager import ArchiveManager, IMAGE_EXTS, detect_archive_type
from vehicle_dataset_manager.core.enums import ArchiveType, JobState, GroupSource
from vehicle_dataset_manager.core.workspace import Workspace
from vehicle_dataset_manager.database.connection import Database
from vehicle_dataset_manager.database.repositories import ImageRepository, JobRepository, VehicleRepository
from vehicle_dataset_manager.services.plate import normalize_plate
from vehicle_dataset_manager.services.ini_parser import INI_PARSER_VERSION, ini_sha256, parse_ini_bytes
from vehicle_dataset_manager.services.metadata import FilenameMetadataParser
from vehicle_dataset_manager.services.metadata_resolver import MetadataResolver
from vehicle_dataset_manager.services.photo_health import check_photo, read_photo

log = logging.getLogger("vdm.app")
_YEAR_RE = re.compile(r"(20\d{2})")
_ARCHIVE_EXTS = {".zip", ".7z", ".7zip"}


class ImportMode(str, Enum):
    NORMAL = "normal"
    RESCAN_EXISTING = "rescan_existing"
    FORCE_METADATA = "force_metadata"
    REPAIR_PHOTOS = "repair_photos"
    REPLACE_ORIGINALS = "replace_originals"


@dataclass
class ImportResult:
    archive: str
    archive_year: Optional[int]
    extracted_dir: Path
    images_found: int
    images_new: int
    job_id: int
    verified: bool
    message: str = ""
    mode: str = ImportMode.NORMAL.value
    images_existing: int = 0
    ini_found: int = 0
    ini_new: int = 0
    ini_existing: int = 0
    ini_matched: int = 0
    ini_missing: int = 0
    ini_parse_error: int = 0
    ini_with_plate: int = 0
    ini_without_plate: int = 0
    unmatched_ini: int = 0
    metadata_updated: int = 0
    conflicts: int = 0
    errors: int = 0
    archive_unchanged: bool = False
    cancelled: bool = False
    parser_changed: bool = False
    photos_repaired: int = 0


def detect_year(name: str, provided: Optional[int] = None) -> Optional[int]:
    if provided:
        return provided
    match = _YEAR_RE.search(name)
    return int(match.group(1)) if match else None


class ImportService:
    def __init__(self, db: Database, workspace: Workspace, archive_manager: Optional[ArchiveManager] = None) -> None:
        self.db = db
        self.workspace = workspace
        self.archive_manager = archive_manager or ArchiveManager()
        self.images = ImageRepository(db)
        self.jobs = JobRepository(db)
        self.filename_parser = FilenameMetadataParser()

    def import_archive(
        self, archive_path: Path | str, archive_year: Optional[int] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        mode: ImportMode | str = ImportMode.NORMAL,
    ) -> ImportResult:
        archive_path = Path(archive_path).resolve()
        mode = ImportMode(mode)
        if mode == ImportMode.REPLACE_ORIGINALS:
            raise ValueError("原圖替換需要先預覽並確認，請使用匯入頁的原圖替換模式。")
        atype = detect_archive_type(archive_path)
        if atype not in (ArchiveType.ZIP, ArchiveType.SEVEN_Z):
            raise ValueError(f"Unsupported archive type: {archive_path}")
        verify = self.archive_manager.verify_archive(archive_path)
        archive_hash = _sha256_file(archive_path)
        if not verify.ok:
            raise ValueError(f"Archive verification failed: {archive_path.name}")
        if mode == ImportMode.REPAIR_PHOTOS:
            return self._repair_photos(archive_path, archive_hash, archive_year, on_progress, should_cancel)
        archive_id, unchanged, parser_changed = self._register_archive(archive_path, archive_hash)
        audit_id = self._start_audit(archive_id, mode)
        year = detect_year(archive_path.name, archive_year)
        dest = self.workspace.extracted_dir / f"archive-{archive_hash}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            extraction = self.archive_manager.extract_archives_recursively(
                archive_path, dest, on_progress=on_progress, should_cancel=should_cancel
            )
            result = self._merge_extracted(
                archive_path, archive_id, year, dest, mode, verify.ok,
                extraction.backend, unchanged, parser_changed, should_cancel,
            )
            result.job_id = self._processing_job(result)
            result.cancelled = result.cancelled or extraction.cancelled
            result.errors += extraction.failed
            completed = not result.cancelled and not result.errors
            self._finish_audit(audit_id, result, "completed" if completed else "cancelled" if result.cancelled else "failed")
            if completed:
                self.db.execute(
                    "UPDATE archives SET parser_version=?, import_version=?, last_scanned_at=?, updated_at=? WHERE archive_id=?",
                    (INI_PARSER_VERSION, __version__, _now(), _now(), archive_id),
                )
            self.db.commit()
            return result
        except Exception as exc:
            self.db.execute(
                "UPDATE import_jobs SET status='failed', finished_at=?, message=? WHERE import_job_id=?",
                (_now(), str(exc), audit_id),
            )
            self.db.commit()
            raise

    def _repair_photos(self, archive_path, archive_hash, year, on_progress, should_cancel):
        known = self.db.query_one("SELECT archive_id FROM archives WHERE sha256=? ORDER BY archive_id LIMIT 1", (archive_hash,))
        if not known:
            raise ValueError("此壓縮檔沒有相同 SHA256 的匯入紀錄，無法確認為原始壓縮檔；請勿以同檔名猜測覆蓋。")
        audit_id = self._start_audit(int(known["archive_id"]), ImportMode.REPAIR_PHOTOS)
        # Fresh, retained snapshot: never extract over a live photo or delete a
        # damaged copy. Each successful DB checkpoint switches only its path.
        dest = Path(tempfile.mkdtemp(prefix="repair-", dir=self.workspace.extracted_dir))
        result = ImportResult(archive_path.name, year, dest, 0, 0, 0, True,
                              mode=ImportMode.REPAIR_PHOTOS.value)
        try:
            extraction = self.archive_manager.extract_archives_recursively(
                archive_path, dest, on_progress=on_progress, should_cancel=should_cancel)
            result.cancelled = extraction.cancelled or bool(should_cancel and should_cancel())
            result.errors = extraction.failed
            if not result.cancelled and not result.errors:
                candidates = {}
                files = sorted(p for p in dest.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
                result.images_found = len(files)
                for index, path in enumerate(files, 1):
                    if should_cancel and should_cancel():
                        result.cancelled = True
                        break
                    try:
                        _, digest = read_photo(path)
                        candidates.setdefault(digest, path)
                    except Exception:
                        result.errors += 1
                    if on_progress and (index % 25 == 0 or index == len(files)):
                        on_progress(index, len(files), "驗證壓縮檔內照片")
                if not result.cancelled:
                    rows = self.db.query("SELECT image_id,source_path,sha256 FROM images WHERE sha256 IS NOT NULL")
                    for index, row in enumerate(rows, 1):
                        if should_cancel and should_cancel():
                            result.cancelled = True
                            break
                        candidate = candidates.get(row["sha256"])
                        if candidate is None:
                            continue
                        result.images_existing += 1
                        if check_photo(row["source_path"], row["sha256"]) == "ok":
                            continue
                        with self.db.transaction():
                            self.db.execute("UPDATE images SET source_path=? WHERE image_id=? AND sha256=?",
                                            (str(candidate.resolve()), row["image_id"], row["sha256"]))
                        result.photos_repaired += 1
                        if on_progress:
                            on_progress(index, len(rows), "替換已驗證的照片路徑")
            result.message = (
                f"修復 {result.photos_repaired} 張；匹配既有影像 {result.images_existing} 張。"
                "僅更新照片路徑，保留標註與處理狀態；無 SHA256 或不匹配者不替換。"
                f"原副本保留；新快照：{dest}"
            )
            self._finish_audit(audit_id, result, "cancelled" if result.cancelled else "failed" if result.errors else "completed")
            return result
        except Exception as exc:
            result.errors += 1
            result.message = str(exc)
            self._finish_audit(audit_id, result, "failed")
            raise

    def _merge_extracted(
        self, archive_path: Path, archive_id: int, year: Optional[int], dest: Path,
        mode: ImportMode, verified: bool, backend: str, unchanged: bool,
        parser_changed: bool, should_cancel,
    ) -> ImportResult:
        files = [p for p in sorted(dest.rglob("*")) if p.is_file()]
        images = [p for p in files if p.suffix.lower() in IMAGE_EXTS]
        inis = [p for p in files if p.suffix.lower() == ".ini"]
        ini_by_key: dict[tuple[str, str], list[Path]] = {}
        for ini in inis:
            ini_by_key.setdefault(_pair_key(ini, dest), []).append(ini)
        paired = set()
        result = ImportResult(
            archive=archive_path.name, archive_year=year, extracted_dir=dest,
            images_found=len(images), images_new=0, job_id=0, verified=verified,
            mode=mode.value, ini_found=len(inis), archive_unchanged=unchanged,
            parser_changed=parser_changed,
        )
        # Backfill pre-INI assets by actual content, never by filename.
        for row in self.db.query("SELECT image_id,source_path FROM images WHERE sha256 IS NULL"):
            if should_cancel and should_cancel():
                result.cancelled = True
                break
            path = Path(row["source_path"] or "")
            if path.is_file():
                try:
                    with self.db.transaction():
                        self.images.set_result_fields(int(row["image_id"]), sha256=_sha256_file(path))
                except OSError:
                    log.warning("Cannot hash legacy asset %s", row["image_id"])
        for image in images:
            if result.cancelled or (should_cancel and should_cancel()):
                result.cancelled = True
                break
            matches = ini_by_key.get(_pair_key(image, dest), [])
            paired.update(matches)
            previous = asdict(result)
            try:
                with self.db.transaction():
                    self._merge_image(image, matches, archive_path, archive_id, year, dest, mode, result)
            except Exception:
                for key, value in previous.items():
                    setattr(result, key, value)
                result.errors += 1
                log.exception("Metadata merge failed for %s", image)
        if not result.cancelled:
            for ini in inis:
                if ini not in paired:
                    result.unmatched_ini += 1
                    try:
                        data = ini.read_bytes()
                        with self.db.transaction():
                            self._record_member(archive_id, ini.relative_to(dest).as_posix(), "ini", len(data), ini_sha256(data))
                    except OSError:
                        result.errors += 1
                    log.warning("INI_IMAGE_MISSING: %s", ini)
        flags = [backend]
        if unchanged:
            flags.append("FILE_UNCHANGED")
        if parser_changed:
            flags.append("PARSER_CHANGED")
        if mode == ImportMode.FORCE_METADATA:
            flags.append("FORCE_RESCAN")
        if result.cancelled:
            flags.append("CANCELLED")
        result.message = "; ".join(flags)
        return result

    def _merge_image(self, image, matches, archive_path, archive_id, year, dest, mode, result):
        image_hash = _sha256_file(image)
        existing = self.images.get_by_sha256(image_hash)
        if existing is None and mode == ImportMode.RESCAN_EXISTING:
            return
        if existing is None:
            image_id = self.images.upsert_from_scan(
                original_filename=image.name, source_path=str(image.resolve()),
                original_archive=archive_path.name, archive_year=year, sha256=image_hash,
            )
            result.images_new += 1
        else:
            image_id = existing.image_id
            result.images_existing += 1
        self._record_member(archive_id, image.relative_to(dest).as_posix(), "image", image.stat().st_size, image_hash)
        self._record_image_source(image_id, archive_id, image.relative_to(dest).as_posix())
        before = self.images.get_details(image_id) or {}
        if not matches:
            result.ini_missing += 1
            return
        result.ini_matched += 1
        for ini in matches:
            data = ini.read_bytes()
            self._record_member(archive_id, ini.relative_to(dest).as_posix(), "ini", len(data), ini_sha256(data))
        if len(matches) != 1:
            result.ini_parse_error += 1
            self.images.set_result_fields(image_id, metadata_conflict=1, conflict_type="INI_PAIR_AMBIGUOUS")
            result.conflicts += 1
            return
        ini = matches[0]
        data = ini.read_bytes()
        ini_hash = ini_sha256(data)
        ini_member = ini.relative_to(dest).as_posix()
        result.ini_existing += int(bool(before.get("ini_present")))
        result.ini_new += int(not before.get("ini_present"))
        parsed = parse_ini_bytes(data)
        result.ini_parse_error += int(parsed.status != "ok")
        result.ini_with_plate += int(parsed.has_plate)
        result.ini_without_plate += int(not parsed.has_plate)
        self.db.execute(
            "INSERT INTO ini_sources(image_id,archive_id,member_path,sha256,parser_version,parse_status,raw_metadata,plate_text) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(image_id,archive_id,member_path) DO UPDATE SET "
            "sha256=excluded.sha256,parser_version=excluded.parser_version,parse_status=excluded.parse_status,"
            "raw_metadata=excluded.raw_metadata,plate_text=excluded.plate_text",
            (image_id, archive_id, ini_member, ini_hash, INI_PARSER_VERSION, parsed.status, parsed.raw_json(), parsed.plate_text_raw if parsed.has_plate else None),
        )
        fields = MetadataResolver.ini_database_fields(
            parsed, ini_path=str(ini.resolve()), member_path=ini_member,
            ini_hash=ini_hash, parser_version=INI_PARSER_VERSION,
            filename_metadata=self.filename_parser.parse(image.name), existing=before,
        )
        source_plates = {
            normalize_plate(row["plate_text"]) for row in self.db.query(
                "SELECT plate_text FROM ini_sources WHERE image_id=? AND plate_text IS NOT NULL", (image_id,)
            )
        }
        if len(source_plates) > 1:
            fields = {"metadata_conflict": 1, "conflict_type": "INI_SOURCE_PLATE_MISMATCH"}
        elif parsed.status != "ok":
            fields = {key: value for key, value in fields.items() if key.startswith("ini_")}
            fields.update(metadata_conflict=1, conflict_type="INI_PARSE_ERROR")
        elif "INI_PLATE_AMBIGUOUS" in parsed.warnings:
            fields.update(metadata_conflict=1, conflict_type="INI_PLATE_AMBIGUOUS")
        if before.get("ini_sha256") == ini_hash and before.get("ini_parser_version") == INI_PARSER_VERSION:
            fields.pop("ini_path", None)
            fields.pop("ini_archive_member", None)
        changes = {}
        for key, value in fields.items():
            previous = before.get(key)
            if key == "quality_flags":
                try:
                    previous = json.loads(previous or "[]")
                except (ValueError, TypeError):
                    previous = []
            if previous != value:
                changes[key] = value
        if changes:
            self.images.set_result_fields(image_id, **changes)
            result.metadata_updated += 1
        result.conflicts += int(bool(fields.get("metadata_conflict", before.get("metadata_conflict"))))
        self._refresh_candidate(image_id, self.images.get_details(image_id) or {})

    def _refresh_candidate(self, image_id, current):
        if current.get("metadata_conflict") or current.get("manual_plate_text") or current.get("review_status") != "unreviewed":
            return
        protected = self.db.query_one(
            "SELECT 1 FROM vehicle_members m JOIN vehicles v ON v.vehicle_id=m.vehicle_id "
            "WHERE m.image_id=? AND (m.label_source='manual' OR v.source IN ('manual','mixed') OR v.verification<>'automatic_only')",
            (image_id,),
        )
        if protected:
            return
        if current.get("plate_source") != "ini":
            self.db.execute(
                "DELETE FROM vehicle_members WHERE image_id=? AND label_source='plate_ini_exact'", (image_id,)
            )
        if current.get("plate_source") == "ini" and current.get("plate_text_normalized"):
            vehicles = VehicleRepository(self.db)
            group = vehicles.get_or_create_for_plate(current["plate_text_normalized"], source=GroupSource.PLATE_INI_EXACT)
            vehicles.add_member(group, image_id, label_source=GroupSource.PLATE_INI_EXACT.value, confidence=1.0)


    def _register_archive(self, path: Path, digest: str) -> tuple[int, bool, bool]:
        ts = _now()
        stat = path.stat()
        key = str(path)
        row = self.db.query_one("SELECT * FROM archives WHERE archive_path=?", (key,))
        unchanged = bool(row and row["sha256"] == digest)
        parser_changed = not row or row["parser_version"] != INI_PARSER_VERSION
        duplicate = self.db.query_one(
            "SELECT archive_id FROM archives WHERE sha256=? AND archive_path<>? AND archive_id<? ORDER BY archive_id LIMIT 1",
            (digest, key, int(row["archive_id"]) if row else 9223372036854775807),
        )
        if row:
            archive_id = int(row["archive_id"])
            self.db.execute(
                "UPDATE archives SET archive_filename=?, file_size=?, mtime_ns=?, sha256=?, duplicate_of_archive_id=?, updated_at=? WHERE archive_id=?",
                (path.name, stat.st_size, stat.st_mtime_ns, digest, int(duplicate["archive_id"]) if duplicate else None, ts, archive_id),
            )
        else:
            cur = self.db.execute(
                "INSERT INTO archives (archive_path,archive_filename,file_size,mtime_ns,sha256,parser_version,duplicate_of_archive_id,created_at,updated_at) VALUES (?,?,?,?,?,NULL,?,?,?)",
                (key, path.name, stat.st_size, stat.st_mtime_ns, digest, int(duplicate["archive_id"]) if duplicate else None, ts, ts),
            )
            archive_id = int(cur.lastrowid)
        self.db.commit()
        return archive_id, unchanged, parser_changed

    def _record_member(self, archive_id: int, path: str, kind: str, size: int, digest: str) -> None:
        self.db.execute(
            "INSERT INTO archive_members (archive_id,member_path,member_type,size,sha256,last_seen_at) VALUES (?,?,?,?,?,?) ON CONFLICT(archive_id,member_path) DO UPDATE SET member_type=excluded.member_type,size=excluded.size,sha256=excluded.sha256,last_seen_at=excluded.last_seen_at",
            (archive_id, path, kind, size, digest, _now()),
        )

    def _record_image_source(self, image_id: int, archive_id: int, member_path: str) -> None:
        ts = _now()
        self.db.execute(
            "INSERT INTO image_sources (image_id,archive_id,member_path,first_seen_at,last_seen_at) VALUES (?,?,?,?,?) ON CONFLICT(image_id,archive_id,member_path) DO UPDATE SET last_seen_at=excluded.last_seen_at",
            (image_id, archive_id, member_path, ts, ts),
        )

    def _start_audit(self, archive_id: int, mode: ImportMode) -> int:
        cur = self.db.execute(
            "INSERT INTO import_jobs (archive_id,mode,status,started_at) VALUES (?,?, 'running', ?)",
            (archive_id, mode.value, _now()),
        )
        self.db.commit(); return int(cur.lastrowid)

    def _finish_audit(self, audit_id: int, result: ImportResult, status: str) -> None:
        data = asdict(result)
        columns = ("images_found","images_new","images_existing","ini_found","ini_new",
                   "ini_existing","ini_matched","ini_missing","ini_parse_error",
                   "ini_with_plate","ini_without_plate","unmatched_ini",
                   "metadata_updated","conflicts","errors")
        assignments = ",".join(f"{name}=?" for name in columns)
        self.db.execute(
            f"UPDATE import_jobs SET status=?, {assignments}, finished_at=?, message=? WHERE import_job_id=?",
            (status, *(int(data[name]) for name in columns), _now(), result.message, audit_id),
        )
        self.db.commit()

    def _processing_job(self, result: ImportResult) -> int:
        if result.mode != ImportMode.NORMAL.value:
            job_id = self.jobs.create("metadata_rescan", result.archive, total=0)
            self.jobs.set_state(job_id, JobState.COMPLETED)
            return job_id
        return self.jobs.create("process", result.archive, total=self.images.counts(result.archive)["pending"])

    def import_folder(self, folder: Path | str, on_progress=None, should_cancel=None, mode: ImportMode | str = ImportMode.NORMAL) -> List[ImportResult]:
        archives = sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in _ARCHIVE_EXTS)
        results: List[ImportResult] = []
        for archive in archives:
            if should_cancel and should_cancel():
                break
            try:
                results.append(self.import_archive(archive, on_progress=on_progress, should_cancel=should_cancel, mode=mode))
            except Exception as exc:
                log.warning("import failed for %s: %s", archive, exc)
        return results


def _pair_key(path: Path, root: Path) -> tuple[str, str]:
    relative = path.relative_to(root)
    return relative.parent.as_posix().casefold(), path.stem.casefold()


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
