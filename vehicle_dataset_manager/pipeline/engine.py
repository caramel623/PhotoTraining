"""Resumable, cancellable batch-processing engine.

The engine pulls one PENDING image at a time (``claim_next_pending``), runs the
stage list on it, and persists the outcome. Because the claim step only returns
PENDING rows, re-running after a crash resumes exactly where it stopped without
re-processing finished images.

It is deliberately free of Qt so it can be unit-tested headlessly. The GUI
drives it from a QThread/QRunnable (see :mod:`workers`).
"""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from vehicle_dataset_manager.core.enums import JobState, ProcessingStatus
from vehicle_dataset_manager.database.repositories import (
    DetectionRepository,
    ImageRepository,
    JobRepository,
    OcrRepository,
    PlateRepository,
    VehicleRepository,
)
from vehicle_dataset_manager.database.connection import Database
from vehicle_dataset_manager.pipeline.stages import PipelineContext, Stage, StageError

log = logging.getLogger("vdm.processing")
err = logging.getLogger("vdm.error")

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


@dataclass
class EngineResult:
    job_id: int
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    pending_left: int = 0
    final_state: JobState = JobState.COMPLETED


class ProcessingEngine:
    def __init__(
        self,
        db: Database,
        stages: List[Stage],
        *,
        image_repo: Optional[ImageRepository] = None,
        vehicle_repo: Optional[VehicleRepository] = None,
        job_repo: Optional[JobRepository] = None,
        plate_repo: Optional[PlateRepository] = None,
        detection_repo: Optional[DetectionRepository] = None,
        ocr_repo: Optional[OcrRepository] = None,
    ) -> None:
        self.db = db
        self.stages = stages
        self.images = image_repo or ImageRepository(db)
        self.vehicles = vehicle_repo or VehicleRepository(db)
        self.jobs = job_repo or JobRepository(db)
        self.plates = plate_repo or PlateRepository(db)
        self.detections = detection_repo or DetectionRepository(db)
        self.ocrs = ocr_repo or OcrRepository(db)

    # -- public API ------------------------------------------------------
    def run(
        self,
        job_id: int,
        on_progress: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCheck] = None,
    ) -> EngineResult:
        job = self.jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown job_id {job_id}")
        self.jobs.set_state(job_id, JobState.RUNNING)
        archive_path = job.get("archive_path")
        result = EngineResult(job_id=job_id)

        while True:
            if should_cancel and should_cancel():
                self.jobs.set_state(job_id, JobState.CANCELLED)
                result.final_state = JobState.CANCELLED
                break
            record = self.images.claim_next_pending(archive_path)
            if record is None:
                self.jobs.set_state(job_id, JobState.COMPLETED)
                result.final_state = JobState.COMPLETED
                break
            self._process_one(record.image_id, result)
            counts = self.images.counts(archive_path)
            self.jobs.update_counters(
                job_id,
                processed=counts["completed"] + counts["skipped"],
                skipped=counts["skipped"],
                failed=counts["failed"],
                pending=counts["pending"] + counts["processing"],
            )
            if on_progress:
                on_progress(counts["total"] - counts["pending"] - counts["processing"],
                            counts["total"], record.original_filename)

        counts = self.images.counts(archive_path)
        result.pending_left = counts["pending"] + counts["processing"]
        return result

    def run_sample(
        self,
        job_id: int,
        count: int,
        seed: Optional[int] = None,
        on_progress: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCheck] = None,
    ) -> EngineResult:
        """Process a random subset of PENDING images (sample run).

        Used to validate the pipeline on a large archive without running the
        whole batch. The sample is drawn with ``ORDER BY RANDOM(seed)`` so a
        fixed seed is reproducible. Completed/failed rows are never touched;
        images stuck in PROCESSING (crash) are re-queued first. The job ends
        CANCELLED because the remaining PENDING batch is still unprocessed.
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown job_id {job_id}")
        archive_path = job.get("archive_path")
        self.images.reset_stuck_processing(archive_path)
        self.jobs.set_state(job_id, JobState.RUNNING)
        result = EngineResult(job_id=job_id)

        sql = "SELECT image_id FROM images WHERE processing_status='pending'"
        params: list = []
        if archive_path:
            sql += " AND original_archive=?"
            params.append(archive_path)
        if seed is not None:
            # deterministic pseudo-random order (LCG hash); SQLite RANDOM()
            # does not accept a seed argument
            sql += (" ORDER BY ((? * 374761393 + image_id * 668265263) "
                    "% 4294967291) LIMIT ?")
            params.append(seed)
        else:
            sql += " ORDER BY RANDOM() LIMIT ?"
        params.append(count)
        sampled = self.db.query(sql, params)
        log.info("sample: %d of %d pending images (seed=%s)",
                 len(sampled), count, seed)

        for index, row in enumerate(sampled, start=1):
            if should_cancel and should_cancel():
                break
            image_id = int(row["image_id"])
            if not self.images.claim_image(image_id):
                continue
            record = self.images.get(image_id)
            name = record.original_filename if record else ""
            self._process_one(image_id, result)
            counts = self.images.counts(archive_path)
            self.jobs.update_counters(
                job_id,
                processed=counts["completed"] + counts["skipped"],
                skipped=counts["skipped"],
                failed=counts["failed"],
                pending=counts["pending"] + counts["processing"],
            )
            if on_progress:
                on_progress(index, len(sampled), name)
        self.jobs.set_state(job_id, JobState.CANCELLED)
        counts = self.images.counts(archive_path)
        result.pending_left = counts["pending"] + counts["processing"]
        result.final_state = JobState.CANCELLED
        return result

    # -- internals -------------------------------------------------------
    def _process_one(self, image_id: int, result: EngineResult) -> None:
        record = self.images.get(image_id)
        if record is None:  # pragma: no cover - defensive
            return
        ctx = PipelineContext(
            image_id=image_id,
            path=Path(record.source_path) if record.source_path else Path(""),
            original_filename=record.original_filename,
        )
        try:
            for stage in self.stages:
                stage.run(ctx)
            self._persist(ctx)
            result.processed += 1
            log.info("processed image %s (%s)", image_id, record.original_filename)
        except StageError as exc:
            self.images.mark_failed(image_id, str(exc))
            result.failed += 1
            log.warning("image %s failed: %s", image_id, exc)
            err.error("image %s FAILED: %s", image_id, exc)
        except Exception:  # noqa: BLE001 - never let one image kill the batch
            msg = traceback.format_exc(limit=3)
            self.images.mark_failed(image_id, msg)
            result.failed += 1
            log.error("image %s unexpected error:\n%s", image_id, msg)
            err.error("image %s unexpected error:\n%s", image_id, msg)

    def _persist(self, ctx: PipelineContext) -> None:
        fields = {
            "width": ctx.width,
            "height": ctx.height,
            "sha256": ctx.sha256,
            "quality_flags": ctx.quality_flags,
        }
        meta = ctx.meta or {}
        if meta.get("camera_id"):
            fields["camera_id"] = meta["camera_id"]
        if meta.get("date"):
            fields["date"] = meta["date"]
        if meta.get("time"):
            fields["time"] = meta["time"]
        if meta.get("datetime"):
            fields["captured_datetime"] = meta["datetime"]
        if meta.get("speed") is not None:
            fields["speed"] = meta["speed"]
        if meta.get("speed_limit") is not None:
            fields["speed_limit"] = meta["speed_limit"]
        if meta.get("direction"):
            fields["direction"] = meta["direction"]
        if meta.get("location"):
            fields["location"] = meta["location"]
        if meta.get("device_serial"):
            fields["device_serial"] = meta["device_serial"]
        if meta.get("year") is not None:
            # do not overwrite archive-derived year
            existing = self.images.get(ctx.image_id)
            if existing is None or existing.archive_year is None:
                fields["archive_year"] = meta["year"]

        if ctx.vehicle_dets:
            fields["vehicle_bbox"] = [d.bbox_dict for d in ctx.vehicle_dets]
        if ctx.vehicle_crop_path:
            fields["vehicle_crop_path"] = ctx.vehicle_crop_path
        if ctx.vehicle_crop_bbox:
            fields["vehicle_crop_bbox"] = ctx.vehicle_crop_bbox
        if ctx.plate_bbox:
            fields["plate_bbox"] = ctx.plate_bbox
        if ctx.plate_raw is not None:
            fields["plate_text_raw"] = ctx.plate_raw
        if ctx.plate_norm is not None:
            fields["plate_text_normalized"] = ctx.plate_norm
        if ctx.plate_confidence is not None:
            fields["plate_confidence"] = ctx.plate_confidence

        self.images.mark_completed(ctx.image_id, **fields)

        # child rows
        for det in ctx.vehicle_dets:
            self.detections.add(ctx.image_id, kind="vehicle",
                                class_name=det.class_name, confidence=det.confidence,
                                bbox=det.bbox_dict)
        for det in ctx.plate_dets:
            self.detections.add(ctx.image_id, kind="plate",
                                class_name="plate", confidence=det.confidence,
                                bbox=det.bbox_dict)
        if ctx.plate_norm is not None or ctx.plate_raw is not None:
            self.plates.add(
                ctx.image_id, raw=ctx.plate_raw, normalized=ctx.plate_norm,
                confidence=ctx.plate_confidence, quality=ctx.ocr_quality,
                bbox=ctx.plate_bbox,
            )
            self.ocrs.add(
                ctx.image_id, raw=ctx.plate_raw, normalized=ctx.plate_norm,
                confidence=ctx.plate_confidence, engine="pipeline",
            )
