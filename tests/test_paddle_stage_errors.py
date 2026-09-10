"""Stage conversion tests: PaddleOcrError -> StageError (FAILED, retryable)."""
from __future__ import annotations

import numpy as np
import pytest

from vehicle_dataset_manager.detection.base import BaseDetector
from vehicle_dataset_manager.ocr.base import BaseOcr
from vehicle_dataset_manager.ocr.paddle_client import PaddleOcrError
from vehicle_dataset_manager.pipeline.stages import (
    DetectStage,
    OcrStage,
    PipelineContext,
    StageError,
)


class RaisingPlateDetector(BaseDetector):
    name = "raising-plate"

    def detect(self, image):
        raise PaddleOcrError("sidecar died", fatal=True)


class RailingOcr(BaseOcr):
    name = "raising-ocr"

    def recognize(self, image):
        raise PaddleOcrError("sidecar died", fatal=True)


class OkVehicleDetector(BaseDetector):
    name = "ok-vehicle"

    def detect(self, image):
        return []


class OkPlateDetector(BaseDetector):
    name = "ok-plate"

    def detect(self, image):
        from vehicle_dataset_manager.detection.base import Detection
        return [Detection(class_name="plate", confidence=0.9, bbox=(10, 20, 100, 40))]


def make_ctx():
    img = np.full((100, 200, 3), 5, dtype=np.uint8)
    return PipelineContext(image_id=1, path=None, original_filename="x.jpg", bgr=img)


def test_detect_stage_converts_paddle_error():
    stage = DetectStage(OkVehicleDetector(), RaisingPlateDetector())
    ctx = make_ctx()
    with pytest.raises(StageError):
        stage.run(ctx)


def test_ocr_stage_converts_paddle_error():
    stage = OcrStage(OkPlateDetector(), RailingOcr())
    ctx = make_ctx()
    from vehicle_dataset_manager.detection.base import Detection
    ctx.plate_dets = [Detection(class_name="plate", confidence=0.9, bbox=(10, 20, 100, 40))]
    with pytest.raises(StageError):
        stage.run(ctx)
