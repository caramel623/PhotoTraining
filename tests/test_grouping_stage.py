"""GroupingStage quality gating and OcrStage NO_PLATE flagging (unit)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from vehicle_dataset_manager.core.enums import OcrQuality
from vehicle_dataset_manager.database.repositories import ImageRepository, VehicleRepository
from vehicle_dataset_manager.detection.base import Detection, StubPlateDetector
from vehicle_dataset_manager.ocr.base import StubOcr
from vehicle_dataset_manager.pipeline.stages import (
    GroupingStage,
    OcrStage,
    PipelineContext,
)


def _ctx(db, with_bgr=False):
    image_id = ImageRepository(db).upsert_from_scan(
        original_filename="x.jpg", source_path="/x/x.jpg"
    )
    ctx = PipelineContext(image_id=image_id, path=Path("x.jpg"), original_filename="x.jpg")
    if with_bgr:
        ctx.bgr = np.zeros((12, 20, 3), dtype=np.uint8)
    return ctx


def _stage(db, min_quality):
    veh = VehicleRepository(db)
    return GroupingStage(
        veh.get_or_create_for_plate, veh.add_member, min_quality=min_quality
    )


def test_high_quality_groups_by_default(db):
    st = _stage(db, OcrQuality.MEDIUM)
    ctx = _ctx(db)
    ctx.plate_norm = "AAA111"
    ctx.ocr_quality = OcrQuality.HIGH
    ctx.plate_confidence = 0.9
    st.run(ctx)
    assert ctx.vehicle_group_id is not None


def test_medium_skipped_when_min_quality_high(db):
    st = _stage(db, OcrQuality.HIGH)
    ctx = _ctx(db)
    ctx.plate_norm = "AAA111"
    ctx.ocr_quality = OcrQuality.MEDIUM
    st.run(ctx)
    assert ctx.vehicle_group_id is None


def test_low_never_groups(db):
    st = _stage(db, OcrQuality.MEDIUM)
    ctx = _ctx(db)
    ctx.plate_norm = "AAA111"
    ctx.ocr_quality = OcrQuality.LOW
    st.run(ctx)
    assert ctx.vehicle_group_id is None


def test_ocr_empty_marks_no_plate(db):
    st = OcrStage(StubPlateDetector(), StubOcr())
    ctx = _ctx(db, with_bgr=True)
    ctx.plate_dets = [Detection(class_name="plate", confidence=0.9, bbox=(0, 0, 10, 10))]
    st.run(ctx)
    assert "NO_PLATE" in ctx.quality_flags
    assert ctx.ocr_quality is OcrQuality.UNKNOWN
