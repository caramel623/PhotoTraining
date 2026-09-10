"""Import archives: extract + scan images into the database as PENDING.

Import (registration) is deliberately separated from processing:
* :meth:`ImportService.import_archive` extracts the archive and registers every
  image it finds as a PENDING row (deduplicated by source_path+filename).
* The :class:`~vehicle_dataset_manager.pipeline.engine.ProcessingEngine` then
  processes those PENDING rows, and can be stopped/resumed freely.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from vehicle_dataset_manager.archive.manager import (
    ArchiveManager,
    IMAGE_EXTS,
    detect_archive_type,
)
from vehicle_dataset_manager.core.enums import ArchiveType, JobState
from vehicle_dataset_manager.core.workspace import Workspace
from vehicle_dataset_manager.database.connection import Database
from vehicle_dataset_manager.database.repositories import ImageRepository, JobRepository

log = logging.getLogger("vdm.app")

_YEAR_RE = re.compile(r"(20\d{2})")
_ARCHIVE_EXTS = {".zip", ".7z", ".7zip"}


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


def detect_year(name: str, provided: Optional[int] = None) -> Optional[int]:
    """Get the year from an explicit value, else from the filename, else None."""
    if provided:
        return provided
    m = _YEAR_RE.search(name)
    return int(m.group(1)) if m else None


class ImportService:
    def __init__(self, db: Database, workspace: Workspace, archive_manager: Optional[ArchiveManager] = None) -> None:
        self.db = db
        self.workspace = workspace
        self.archive_manager = archive_manager or ArchiveManager()
        self.images = ImageRepository(db)
        self.jobs = JobRepository(db)

    def import_archive(
        self,
        archive_path: Path | str,
        archive_year: Optional[int] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> ImportResult:
        archive_path = Path(archive_path)
        atype = detect_archive_type(archive_path)
        if atype not in (ArchiveType.ZIP, ArchiveType.SEVEN_Z):
            raise ValueError(f"Unsupported archive type: {archive_path}")

        verify = self.archive_manager.verify_archive(archive_path)
        year = detect_year(archive_path.name, archive_year)
        dest = self.workspace.extracted_dir / archive_path.stem
        dest.mkdir(parents=True, exist_ok=True)

        extract = self.archive_manager.extract_archives_recursively(
            archive_path, dest, on_progress=on_progress, should_cancel=should_cancel
        )

        # Scan extracted images and register as PENDING (deduplicated).
        found = 0
        new = 0
        for p in sorted(dest.rglob("*")):
            if should_cancel and should_cancel():
                break
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                found += 1
                before = self.images.get_by_source(str(p.resolve()), p.name)
                self.images.upsert_from_scan(
                    original_filename=p.name,
                    source_path=str(p.resolve()),
                    original_archive=archive_path.name,
                    archive_year=year,
                )
                if before is None:
                    new += 1

        total_pending = self.images.counts(archive_path.name)["pending"]
        job_id = self.jobs.create("process", archive_path.name, total=total_pending)
        return ImportResult(
            archive=archive_path.name,
            archive_year=year,
            extracted_dir=dest,
            images_found=found,
            images_new=new,
            job_id=job_id,
            verified=verify.ok,
            message=extract.backend,
        )

    def import_folder(
        self,
        folder: Path | str,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> List[ImportResult]:
        folder = Path(folder)
        results: List[ImportResult] = []
        archives = sorted(
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in _ARCHIVE_EXTS
        )
        for i, arc in enumerate(archives):
            if should_cancel and should_cancel():
                break
            try:
                results.append(self.import_archive(arc, on_progress=on_progress, should_cancel=should_cancel))
            except Exception as exc:  # noqa: BLE001 - one bad archive shouldn't stop the rest
                log.warning("import failed for %s: %s", arc, exc)
        return results