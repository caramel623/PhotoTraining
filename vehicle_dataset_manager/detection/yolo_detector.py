"""Ultralytics YOLO vehicle detector (pluggable, CPU / CUDA).

Implements :class:`BaseDetector` for the vehicle slot. The model is loaded
lazily on first use, so constructing the detector (and importing this module)
never requires the ML stack to already be present. Only vehicle classes
(COCO: car / motorcycle / bus / truck) are returned; everything else is
filtered out. Detections are in pixel coordinates of the input image.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence, Union

import numpy as np

from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.detection.base import (
    BaseDetector,
    Detection,
    StubVehicleDetector,
)
from vehicle_dataset_manager.detection.device import resolve_device

log = logging.getLogger("vdm.detection")

#: COCO class ids treated as "vehicles" (car, motorcycle, bus, truck).
VEHICLE_CLASS_IDS = (2, 3, 5, 7)


class YoloVehicleDetector(BaseDetector):
    """Vehicle detector backed by an Ultralytics YOLO model (COCO classes)."""

    name = "yolo-vehicle"

    def __init__(
        self,
        model_path: Union[str, Path] = "yolov8s.pt",
        device: str = "cpu",
        conf: float = 0.3,
        imgsz: int = 640,
        vehicle_class_ids: Sequence[int] = VEHICLE_CLASS_IDS,
    ) -> None:
        self.model_path = str(model_path)
        self.device = device
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.vehicle_class_ids = tuple(int(c) for c in vehicle_class_ids)
        self._model = None

    # -- lazy model load -------------------------------------------------
    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO  # heavy; import only when needed

            log.info("loading YOLO vehicle model %s on %s", self.model_path, self.device)
            self._model = YOLO(self.model_path)
            if self.device == "cuda":
                try:
                    self._model.to("cuda")
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not move model to cuda (%s); using cpu", exc)
                    self.device = "cpu"
        return self._model

    def warmup(self) -> None:
        """Pre-load the model so the first frame is not slow. Never raises."""
        try:
            self._ensure_model()
        except Exception as exc:  # noqa: BLE001
            log.warning("yolo warmup failed: %s", exc)

    # -- interface -------------------------------------------------------
    def detect(self, image: np.ndarray) -> List[Detection]:
        if image is None or image.size == 0:
            return []
        model = self._ensure_model()
        results = model.predict(image, conf=self.conf, imgsz=self.imgsz, verbose=False)
        out: List[Detection] = []
        for r in results:
            boxes = r.boxes
            if boxes is None or len(boxes.cls) == 0:
                continue
            ids = boxes.cls.detach().cpu().numpy().astype(int)
            confs = boxes.conf.detach().cpu().numpy()
            xyxy = boxes.xyxy.detach().cpu().numpy()
            names = r.names or {}
            for cid, conf, box in zip(ids, confs, xyxy):
                if int(cid) not in self.vehicle_class_ids:
                    continue
                x1, y1, x2, y2 = (float(v) for v in box)
                out.append(
                    Detection(
                        class_name=str(names.get(int(cid), cid)),
                        confidence=float(conf),
                        bbox=(x1, y1, x2, y2),
                    )
                )
        return out


def build_vehicle_detector(
    settings: AppSettings, models_dir: Optional[Path] = None
) -> BaseDetector:
    """Construct the vehicle detector from settings.

    Falls back to :class:`StubVehicleDetector` (with a warning) if the slot is
    "none", unknown, or the ML stack / model cannot be created, so the batch
    never crashes on a missing model. A bare model name is resolved under
    ``models_dir`` (workspace) for portability; ultralytics downloads it there
    on first use if absent.
    """
    slot = (settings.models.vehicle_detector or "none").lower()
    if slot == "none":
        return StubVehicleDetector()
    if slot != "yolo":
        log.warning("unknown vehicle_detector slot %r; using stub", slot)
        return StubVehicleDetector()

    model_name = settings.models.vehicle_model or "yolov8s.pt"
    model_path = model_name
    # treat as a bare name when it has no directory separator
    bare = ("/" not in model_name) and ("\\" not in model_name)
    if models_dir is not None and bare and not Path(model_name).exists():
        model_path = str(Path(models_dir) / model_name)

    device = resolve_device(settings.device.use_cuda)
    try:
        return YoloVehicleDetector(
            model_path=model_path,
            device=device,
            conf=settings.models.vehicle_conf,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to build YOLO vehicle detector (%s); using stub", exc)
        return StubVehicleDetector()