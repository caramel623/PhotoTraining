"""Pluggable pipeline stages.

Each stage reads/writes a :class:`PipelineContext`. Stages perform computation
only; the engine persists results to SQLite. A stage that cannot process an
image raises :class:`StageError`, which the engine records as FAILED for that
image and then moves on (a single bad image never aborts the batch).

Default order:
    LoadImage -> Metadata -> Detect -> Ocr -> Grouping
With the stub detectors/OCR, Detect and Ocr are no-ops, so Phase 1 simply
loads, hashes, and parses metadata while remaining fully resumable.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import cv2
import numpy as np

from vehicle_dataset_manager.core.enums import GroupSource, OcrQuality
from vehicle_dataset_manager.detection.base import BaseDetector, Detection
from vehicle_dataset_manager.ocr.base import BaseOcr
from vehicle_dataset_manager.ocr.paddle_client import PaddleOcrError
from vehicle_dataset_manager.services.metadata import FilenameMetadataParser, IniSidecarParser
from vehicle_dataset_manager.services.photo_health import read_photo
from vehicle_dataset_manager.services.plate import (
    is_plausible_plate,
    normalize_plate,
)

log = logging.getLogger("vdm.pipeline")


class StageError(Exception):
    """Raised by a stage to mark the current image as failed."""


@dataclass
class PipelineContext:
    image_id: int
    path: Path
    original_filename: str
    bgr: Optional[np.ndarray] = None
    width: Optional[int] = None
    height: Optional[int] = None
    sha256: Optional[str] = None
    meta: dict = field(default_factory=dict)
    vehicle_dets: List[Detection] = field(default_factory=list)
    plate_dets: List[Detection] = field(default_factory=list)
    plate_bbox: Optional[dict] = None
    plate_raw: Optional[str] = None
    plate_norm: Optional[str] = None
    plate_confidence: Optional[float] = None
    ocr_quality: OcrQuality = OcrQuality.UNKNOWN
    vehicle_group_id: Optional[str] = None
    vehicle_crop_path: Optional[str] = None
    vehicle_crop_bbox: Optional[dict] = None
    primary_vehicle: Optional[Detection] = None
    quality_flags: List[str] = field(default_factory=list)


class Stage(ABC):
    name: str = "stage"

    @abstractmethod
    def run(self, ctx: PipelineContext) -> None:
        raise NotImplementedError


class LoadImageStage(Stage):
    name = "load"

    def run(self, ctx: PipelineContext) -> None:
        try:
            img, digest = read_photo(ctx.path)
        except (OSError, ValueError, cv2.error) as exc:
            raise StageError(f"image read failed ({exc}): {ctx.path}") from exc
        ctx.bgr = img
        h, w = img.shape[:2]
        ctx.height, ctx.width = int(h), int(w)
        ctx.sha256 = digest
        if ctx.width < 64 or ctx.height < 64:
            ctx.quality_flags.append("LOW_RESOLUTION")


class MetadataStage(Stage):
    name = "metadata"

    def __init__(self, parser: FilenameMetadataParser | None = None,
                 ini_parser=None) -> None:
        self.parser = parser or FilenameMetadataParser()
        self.ini_parser = ini_parser

    def run(self, ctx: PipelineContext) -> None:
        ctx.meta = self.parser.parse(ctx.original_filename)
        # Merge a sibling .ini sidecar when present; sidecar values win (richer).
        if self.ini_parser is not None:
            ini_path = ctx.path.with_suffix(".ini")
            if ini_path.exists():
                try:
                    with open(ini_path, "rb") as fh:
                        ini_meta = self.ini_parser.parse_bytes(fh.read())
                except OSError:
                    ini_meta = {}
                for key, value in ini_meta.items():
                    if value not in (None, ""):
                        ctx.meta[key] = value
        # Fall back to the archive year if the filename has none.
        if ctx.meta.get("year") is None and ctx.meta.get("date"):
            try:
                ctx.meta["year"] = int(str(ctx.meta["date"])[:4])
            except (ValueError, TypeError):
                pass


class DetectStage(Stage):
    name = "detect"

    def __init__(self, vehicle_detector: BaseDetector, plate_detector: BaseDetector) -> None:
        self.vehicle_detector = vehicle_detector
        self.plate_detector = plate_detector

    def run(self, ctx: PipelineContext) -> None:
        if ctx.bgr is None:
            return
        ctx.vehicle_dets = list(self.vehicle_detector.detect(ctx.bgr))
        try:
            ctx.plate_dets = list(self.plate_detector.detect(ctx.bgr))
        except PaddleOcrError as exc:
            raise StageError(f"plate detection failed: {exc}") from exc
        if not ctx.vehicle_dets:
            ctx.quality_flags.append("NO_VEHICLE")
        elif len(ctx.vehicle_dets) > 1:
            ctx.quality_flags.append("MULTIPLE_VEHICLES")


class OcrStage(Stage):
    name = "ocr"

    def __init__(self, plate_detector: BaseDetector, ocr: BaseOcr) -> None:
        self.plate_detector = plate_detector
        self.ocr = ocr

    def run(self, ctx: PipelineContext) -> None:
        if ctx.bgr is None or not ctx.plate_dets:
            if ctx.bgr is not None and not ctx.plate_dets:
                ctx.quality_flags.append("NO_PLATE")
            ctx.ocr_quality = OcrQuality.UNKNOWN
            return
        best = max(ctx.plate_dets, key=lambda d: d.confidence)
        x1, y1, x2, y2 = best.bbox
        crop = _crop(ctx.bgr, x1, y1, x2, y2)
        ctx.plate_bbox = best.bbox_dict
        try:
            result = self.ocr.recognize(crop)
        except PaddleOcrError as exc:
            raise StageError(f"ocr failed: {exc}") from exc
        if result is None or not result.text:
            if "NO_PLATE" not in ctx.quality_flags:
                ctx.quality_flags.append("NO_PLATE")
            ctx.ocr_quality = OcrQuality.UNKNOWN
            return
        ctx.plate_raw = result.text
        ctx.plate_norm = normalize_plate(result.text)
        ctx.plate_confidence = float(result.confidence)
        ctx.ocr_quality = _grade_quality(result.confidence, best.confidence, ctx.plate_norm)


class GroupingStage(Stage):
    """Turn a normalized plate into a candidate vehicle group (weak supervision)."""

    name = "grouping"

    def __init__(self, get_or_create, add_member, min_quality: OcrQuality = OcrQuality.MEDIUM) -> None:
        self.get_or_create = get_or_create
        self.add_member = add_member
        self.min_quality = min_quality

    def run(self, ctx: PipelineContext) -> None:
        ini_plate = normalize_plate(ctx.meta.get("ini_plate_normalized"))
        effective_plate = ini_plate or ctx.plate_norm
        effective_quality = OcrQuality.HIGH if ini_plate else ctx.ocr_quality
        if not effective_plate or effective_quality is OcrQuality.UNKNOWN:
            return
        order = (OcrQuality.LOW, OcrQuality.MEDIUM, OcrQuality.HIGH)
        if order.index(effective_quality) < order.index(self.min_quality):
            return
        if ini_plate:
            try:
                vid = self.get_or_create(effective_plate, source=GroupSource.PLATE_INI_EXACT)
            except TypeError:
                vid = self.get_or_create(effective_plate)
            label_source, confidence = "plate_ini_exact", 1.0
        else:
            vid = self.get_or_create(effective_plate)
            label_source, confidence = "plate_exact", ctx.plate_confidence
        self.add_member(vid, ctx.image_id, label_source, confidence)
        ctx.vehicle_group_id = vid


class VehicleCropStage(Stage):
    """Write the top vehicle crop(s) to disk as NEW files.

    Originals are never modified: each crop is saved into ``crops_dir`` under a
    derived name (``<stem>_v0.jpg`` ...). The highest-confidence vehicle is the
    "primary"; its path is stored on the context for the engine to persist.
    """

    name = "vehicle_crop"

    def __init__(self, crops_dir, max_crops: int = 1, quality: int = 92,
                 margin: int = 4) -> None:
        self.crops_dir = Path(crops_dir) if crops_dir else None
        self.max_crops = max(1, int(max_crops))
        self.quality = int(quality)
        self.margin = int(margin)

    def run(self, ctx: PipelineContext) -> None:
        if ctx.bgr is None or not ctx.vehicle_dets or self.crops_dir is None:
            return
        h, w = ctx.bgr.shape[:2]
        dets = sorted(ctx.vehicle_dets, key=lambda d: d.confidence,
                      reverse=True)[: self.max_crops]
        self.crops_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(ctx.original_filename).stem
        saved: List[str] = []
        for i, det in enumerate(dets):
            x1, y1, x2, y2 = det.bbox
            x1 = max(0, int(round(x1)) - self.margin)
            y1 = max(0, int(round(y1)) - self.margin)
            x2 = min(w, int(round(x2)) + self.margin)
            y2 = min(h, int(round(y2)) + self.margin)
            if x2 <= x1 or y2 <= y1:
                continue
            crop = ctx.bgr[y1:y2, x1:x2]
            out = self.crops_dir / f"{ctx.image_id}_{stem}_v{i}.jpg"
            if cv2.imwrite(str(out), crop, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]):
                saved.append(str(out))
                if i == 0:
                    ctx.primary_vehicle = det
                    ctx.vehicle_crop_bbox = {
                        "x1": x1, "y1": y1, "x2": x2, "y2": y2
                    }
        if saved:
            ctx.vehicle_crop_path = saved[0]


def _crop(img: np.ndarray, x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
    h, w = img.shape[:2]
    x1 = max(0, int(round(x1))); y1 = max(0, int(round(y1)))
    x2 = min(w, int(round(x2))); y2 = min(h, int(round(y2)))
    if x2 <= x1 or y2 <= y1:
        return img
    return img[y1:y2, x1:x2]


def _grade_quality(ocr_conf: float, det_conf: float, plate_norm: str) -> OcrQuality:
    if not plate_norm or not is_plausible_plate(plate_norm):
        return OcrQuality.LOW
    if ocr_conf >= 0.8 and det_conf >= 0.6:
        return OcrQuality.HIGH
    if ocr_conf >= 0.5:
        return OcrQuality.MEDIUM
    return OcrQuality.LOW


def build_default_stages(
    vehicle_detector: BaseDetector,
    plate_detector: BaseDetector,
    ocr: BaseOcr,
    vehicle_repo,
    crops_dir=None,
    save_crops: bool = True,
) -> List[Stage]:
    """Compose the standard stage list with the given (possibly stub) engines."""
    stages: List[Stage] = [
        LoadImageStage(),
        MetadataStage(ini_parser=IniSidecarParser()),
        DetectStage(vehicle_detector, plate_detector),
    ]
    if save_crops and crops_dir is not None:
        stages.append(VehicleCropStage(crops_dir))
    stages.append(OcrStage(plate_detector, ocr))
    stages.append(
        GroupingStage(
            get_or_create=vehicle_repo.get_or_create_for_plate,
            add_member=vehicle_repo.add_member,
        )
    )
    return stages
