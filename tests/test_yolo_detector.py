"""Tests for the YOLO vehicle detector and the vehicle-crop stage.

The YOLO tests skip cleanly when ultralytics or a model file / real frame is
not present, so the suite stays green on a bare checkout.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

BASE = Path(__file__).resolve().parents[1]


def _model_path():
    for c in (BASE / "runs/2025/models/yolov8s.pt", BASE / "yolov8s.pt"):
        if c.exists():
            return c
    return None


def _known_frame():
    c = BASE / "runs/2025/extracted/2025/02/20250226/20250226_135204_297_RS015_2361_A.jpg"
    if c.exists():
        return c
    g = sorted((BASE / "runs/2025/extracted/2025").glob("*/*/*.jpg"))
    return g[0] if g else None


def test_factory_none_is_stub():
    from vehicle_dataset_manager.core.config import AppSettings
    from vehicle_dataset_manager.detection import build_vehicle_detector
    from vehicle_dataset_manager.detection.base import StubVehicleDetector

    s = AppSettings()
    s.models.vehicle_detector = "none"
    det = build_vehicle_detector(s)
    assert isinstance(det, StubVehicleDetector)


def test_factory_yolo_returns_yolo_detector():
    pytest.importorskip("ultralytics")
    from vehicle_dataset_manager.core.config import AppSettings
    from vehicle_dataset_manager.detection import YoloVehicleDetector, build_vehicle_detector

    s = AppSettings()  # defaults: yolo / yolov8s / cpu
    det = build_vehicle_detector(s, models_dir=None)
    assert isinstance(det, YoloVehicleDetector)
    assert det.device in ("cpu", "cuda")
    assert set(det.vehicle_class_ids) == {2, 3, 5, 7}


def test_detect_empty_image():
    pytest.importorskip("ultralytics")
    from vehicle_dataset_manager.detection import YoloVehicleDetector

    det = YoloVehicleDetector(device="cpu", conf=0.3)
    assert det.detect(np.zeros((10, 10, 3), dtype=np.uint8)) == []


@pytest.mark.integration
def test_detect_real_frame():
    pytest.importorskip("ultralytics")
    model = _model_path()
    frame = _known_frame()
    if model is None or frame is None:
        pytest.skip("no model file or real frame available")
    import cv2

    from vehicle_dataset_manager.detection import YoloVehicleDetector

    det = YoloVehicleDetector(model_path=str(model), device="cpu", conf=0.3)
    img = cv2.imread(str(frame))
    assert img is not None
    h, w = img.shape[:2]
    dets = det.detect(img)
    # The known frame contains a motorcycle; expect at least one vehicle.
    assert len(dets) >= 1
    for d in dets:
        x1, y1, x2, y2 = d.bbox
        assert 0 <= x1 < x2 <= w
        assert 0 <= y1 < y2 <= h
        assert d.class_name in ("car", "motorcycle", "bus", "truck")
        assert 0.0 < d.confidence <= 1.0


def test_vehicle_crop_stage_writes_new_file(tmp_path):
    import cv2

    from vehicle_dataset_manager.detection.base import Detection
    from vehicle_dataset_manager.pipeline.stages import PipelineContext, VehicleCropStage

    img = np.zeros((200, 300, 3), dtype=np.uint8)
    ctx = PipelineContext(
        image_id=1, path=tmp_path / "x.jpg", original_filename="x.jpg",
        bgr=img, width=300, height=200,
    )
    ctx.vehicle_dets = [
        Detection(class_name="car", confidence=0.9, bbox=(50, 40, 150, 120))
    ]
    crops = tmp_path / "crops"
    VehicleCropStage(crops).run(ctx)
    assert ctx.vehicle_crop_path is not None
    out_path = Path(ctx.vehicle_crop_path)
    assert out_path.exists()
    # bbox (50,40,150,120) + margin 4 -> x 46..154, y 36..124 -> 108x88
    out = cv2.imread(str(out_path))
    assert out is not None
    assert out.shape == (88, 108, 3)
    # original is untouched
    assert (img == 0).all()