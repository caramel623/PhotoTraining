"""Shared application context (services + repositories).

Holds the non-Qt services and repositories so GUI pages can share one
database connection and a consistent set of engines. Qt objects (threads,
widgets) are created in ``main`` / the pages themselves.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from vehicle_dataset_manager.archive.manager import ArchiveManager
from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.core.workspace import Workspace
from vehicle_dataset_manager.database.connection import Database
from vehicle_dataset_manager.database.repositories import (
    DetectionRepository,
    ExportRepository,
    ImageRepository,
    JobRepository,
    OcrRepository,
    PlateRepository,
    ReviewRepository,
    VehicleRepository,
)
from vehicle_dataset_manager.detection.base import StubPlateDetector, StubVehicleDetector
from vehicle_dataset_manager.detection.onnx_plate_detector import (
    DEFAULT_MODEL_PATH as DEFAULT_PLATE_MODEL_PATH,
    OnnxPlateDetector,
)
from vehicle_dataset_manager.detection.yolo_detector import build_vehicle_detector
from vehicle_dataset_manager.ocr.base import StubOcr
from vehicle_dataset_manager.ocr.paddle_client import (
    PaddleOcrProcess,
    build_paddle_engines,
)
from vehicle_dataset_manager.ocr.paddle_protocol import preset_models
from vehicle_dataset_manager.pipeline.engine import ProcessingEngine
from vehicle_dataset_manager.pipeline.stages import build_default_stages
from vehicle_dataset_manager.services.import_service import ImportService

log = logging.getLogger("vdm.app")


@dataclass
class AppContext:
    workspace: Workspace
    settings: AppSettings
    db: Database
    archive_manager: ArchiveManager
    import_service: ImportService

    images: ImageRepository = field(init=False)
    vehicles: VehicleRepository = field(init=False)
    jobs: JobRepository = field(init=False)
    plates: PlateRepository = field(init=False)
    detections: DetectionRepository = field(init=False)
    ocrs: OcrRepository = field(init=False)
    reviews: ReviewRepository = field(init=False)
    exports: ExportRepository = field(init=False)

    vehicle_detector: object = StubVehicleDetector()
    plate_detector: object = StubPlateDetector()
    ocr: object = StubOcr()
    #: Shared PaddleOCR sidecar process (None when the stub OCR is in use).
    paddle_process: Optional[PaddleOcrProcess] = None

    def __post_init__(self) -> None:
        self.images = ImageRepository(self.db)
        self.vehicles = VehicleRepository(self.db)
        self.jobs = JobRepository(self.db)
        self.plates = PlateRepository(self.db)
        self.detections = DetectionRepository(self.db)
        self.ocrs = OcrRepository(self.db)
        self.reviews = ReviewRepository(self.db)
        self.exports = ExportRepository(self.db)
        self.build_detectors()

    def build_detectors(self) -> None:
        """(Re)build pluggable engines from the current settings.

        Cheap to call: the YOLO model is loaded lazily on first inference.
        Ultralytics' config dir is pinned to the workspace so the install
        stays local and portable (no state in %APPDATA%).
        """
        import os

        os.environ.setdefault("YOLO_CONFIG_DIR", str(self.workspace.root))
        if self.settings is None:
            # Headless/test contexts without settings keep the no-op stubs.
            self.vehicle_detector = StubVehicleDetector()
            self.plate_detector = StubPlateDetector()
            return
        self.vehicle_detector = build_vehicle_detector(
            self.settings, models_dir=self.workspace.models_dir
        )
        self._build_ocr()
        self._build_plate()

    def _build_ocr(self) -> None:
        """Select the OCR/plate engines from settings.

        The paddle engines share ONE sidecar process. Any construction
        problem (missing .venv-ocr, bad path) falls back to the no-op stubs
        so the batch always runs; per-image OCR failures are handled by the
        pipeline (FAILED + retryable).
        """
        self.paddle_process = None
        slot = (self.settings.models.ocr or "none").lower()
        plate_slot = (self.settings.models.plate_detector or "none").lower()
        if slot != "paddle" and plate_slot != "paddle":
            log.debug("ocr slot=%r, plate slot=%r -> stub engines", slot, plate_slot)
            return
        ocr_cfg = self.settings.ocr
        cache_dir = Path(ocr_cfg.cache_dir) if ocr_cfg.cache_dir else (
            self.workspace.cache_dir / "paddlex"
        )
        det_model, rec_model = preset_models(ocr_cfg.model_preset)
        try:
            ocr_python = Path(ocr_cfg.ocr_python) if ocr_cfg.ocr_python else None
            plate_det, ocr, process = build_paddle_engines(
                ocr_python=ocr_python,
                cache_dir=cache_dir,
                det_model=det_model,
                rec_model=rec_model,
                enable_mkldnn=ocr_cfg.enable_mkldnn,
                init_timeout=ocr_cfg.init_timeout,
                request_timeout=ocr_cfg.request_timeout,
            )
            self.plate_detector = plate_det
            self.ocr = ocr if slot == "paddle" else StubOcr()
            self.paddle_process = process
            log.info(
                "paddle OCR enabled: preset=%s cache=%s python=%s",
                ocr_cfg.model_preset, cache_dir, process.ocr_python,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to build paddle OCR engines (%s); using stubs", exc)
            self.plate_detector = StubPlateDetector()
            self.ocr = StubOcr()
            self.paddle_process = None

    def _build_plate(self) -> None:
        """Select the ONNX plate detector when the plate slot is ``onnx``.

        Runs after :meth:`_build_ocr` so an explicit ``onnx`` choice wins
        over the PaddleOCR-derived plate engine. On construction problems
        (missing model file, onnxruntime not installed) the previously
        built engine (paddle or stub) is kept.
        """
        if self.settings is None:
            return
        slot = (self.settings.models.plate_detector or "none").lower()
        if slot != "onnx":
            return
        try:
            self.plate_detector = OnnxPlateDetector()
            log.info("plate detector: onnx (model=%s)", DEFAULT_PLATE_MODEL_PATH)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to build onnx plate detector (%s); keeping previous engine", exc)

    def close(self) -> None:
        """Release non-DB resources (the PaddleOCR sidecar). Idempotent."""
        if self.paddle_process is not None:
            try:
                self.paddle_process.close()
            except Exception:  # noqa: BLE001
                log.warning("error closing paddle sidecar", exc_info=True)
            self.paddle_process = None

    def build_engine(self) -> ProcessingEngine:
        stages = build_default_stages(
            self.vehicle_detector, self.plate_detector, self.ocr, self.vehicles,
            crops_dir=self.workspace.crops_dir / "vehicle",
            save_crops=self.settings.processing.save_crops,
        )
        return ProcessingEngine(
            self.db,
            stages,
            image_repo=self.images,
            vehicle_repo=self.vehicles,
            job_repo=self.jobs,
            plate_repo=self.plates,
            detection_repo=self.detections,
            ocr_repo=self.ocrs,
        )

    def jobs_all(self) -> list[dict]:
        return self.jobs.list_jobs()
    def save_settings(self) -> None:
        self.settings.save(self.workspace.settings_path)