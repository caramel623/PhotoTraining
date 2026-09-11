"""ONNX-based Taiwan license plate region detector (CPU).

Ported from Campus-Violation-Helper (caramel623/Campus-Violation-Helper)
``modules/plate_detector.py``. Implements :class:`BaseDetector` for the
plate slot. Uses the ``models/taiwan_plate_detector.onnx`` weights
(TLPRR v7, CC BY 4.0 — see ``models/MODEL_LICENSE.txt``).

Requires: onnxruntime (pip install onnxruntime)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Union

import numpy as np
from vehicle_dataset_manager.core.portable_runtime import application_dir

from vehicle_dataset_manager.detection.base import BaseDetector, Detection

log = logging.getLogger("vdm.detection.onnx")

#: Default model path beside the source tree or packaged EXE.
DEFAULT_MODEL_PATH = application_dir() / "models" / "taiwan_plate_detector.onnx"
EXPECTED_MODEL_SHA256 = "E40B1ABEC9818430D9AA1EA522A65DEE79AE98A502F1B548CFC831B70FD16BE4"

#: Input image size (letterboxed) expected by the ONNX model.
INPUT_SIZE = 640


class OnnxPlateDetector(BaseDetector):
    """Plate region detector backed by an ONNX YOLOv8 model (CPU)."""

    name = "onnx-plate"

    def __init__(
        self,
        model_path: Union[str, Path] = DEFAULT_MODEL_PATH,
        *,
        confidence: float = 0.25,
        iou_threshold: float = 0.45,
        padding_ratio: float = 0.12,
    ) -> None:
        self.model_path = Path(model_path)
        self.conf = float(confidence)
        self.iou_threshold = float(iou_threshold)
        self.padding_ratio = float(padding_ratio)
        self._session = None

    # ------------------------------------------------------------------ #
    #  BaseDetector API
    # ------------------------------------------------------------------ #

    def warmup(self) -> None:
        self._get_session()

    def detect(self, image: np.ndarray) -> List[Detection]:
        """Return plate-region detections for a BGR image.

        ``image`` must be ``H x W x 3`` BGR ``uint8``.
        """
        import cv2  # local import so module load stays light

        session = self._get_session()
        tensor, scale, pad = _letterbox_tensor(image)
        output = session.run(None, {session.get_inputs()[0].name: tensor})[0]
        return decode_yolov8_output(
            output,
            image_size=(image.shape[1], image.shape[0]),
            scale=scale,
            pad=pad,
            confidence=self.conf,
            iou_threshold=self.iou_threshold,
            padding_ratio=self.padding_ratio,
        )

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _get_session(self):
        if self._session is None:
            if not self.model_path.is_file():
                raise FileNotFoundError(f"plate detector model not found: {self.model_path}")
            import onnxruntime as ort  # heavy import, lazy

            self._session = ort.InferenceSession(
                str(self.model_path), providers=["CPUExecutionProvider"]
            )
            log.info("loaded plate model: %s", self.model_path.name)
        return self._session


# ---------------------------------------------------------------------- #
#  Letterbox helper (identical to Campus-Violation-Helper)
# ---------------------------------------------------------------------- #

def _letterbox_tensor(
    image: np.ndarray, size: int = INPUT_SIZE
) -> tuple[np.ndarray, float, tuple[float, float]]:
    import cv2

    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)

    pad_x = (size - resized_width) / 2
    pad_y = (size - resized_height) / 2
    left = round(pad_x - 0.1)
    right = round(pad_x + 0.1)
    top = round(pad_y - 0.1)
    bottom = round(pad_y + 0.1)

    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    tensor = padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(tensor), scale, (float(left), float(top))


# ---------------------------------------------------------------------- #
#  YOLOv8 output decoder (identical to Campus-Violation-Helper)
# ---------------------------------------------------------------------- #

def decode_yolov8_output(
    output: np.ndarray,
    *,
    image_size: tuple[int, int],
    scale: float,
    pad: tuple[float, float],
    confidence: float,
    iou_threshold: float,
    padding_ratio: float = 0.0,
) -> List[Detection]:
    import cv2

    predictions = np.squeeze(output).T
    if predictions.ndim != 2 or predictions.shape[1] < 5:
        raise ValueError(f"incompatible model output shape: {output.shape}")

    scores = predictions[:, 4:].max(axis=1)
    mask = scores >= confidence
    predictions = predictions[mask]
    scores = scores[mask]
    if len(predictions) == 0:
        return []

    xywh = predictions[:, :4]
    x1 = xywh[:, 0] - xywh[:, 2] / 2
    y1 = xywh[:, 1] - xywh[:, 3] / 2
    boxes = np.column_stack((x1, y1, xywh[:, 2], xywh[:, 3]))

    indices = cv2.dnn.NMSBoxes(
        boxes.tolist(), scores.tolist(), confidence, iou_threshold
    )
    if len(indices) == 0:
        return []

    image_width, image_height = image_size
    detections: List[Detection] = []
    for idx in np.asarray(indices).reshape(-1):
        cx, cy, w, h = xywh[idx]
        left = (cx - w / 2 - pad[0]) / scale
        top = (cy - h / 2 - pad[1]) / scale
        right = (cx + w / 2 - pad[0]) / scale
        bottom = (cy + h / 2 - pad[1]) / scale

        extra_x = (right - left) * padding_ratio
        extra_y = (bottom - top) * padding_ratio

        box = (
            max(0, round(left - extra_x)),
            max(0, round(top - extra_y)),
            min(image_width, round(right + extra_x)),
            min(image_height, round(bottom + extra_y)),
        )
        if box[2] - box[0] >= 8 and box[3] - box[1] >= 8:
            detections.append(
                Detection(
                    class_name="plate",
                    confidence=float(scores[idx]),
                    bbox=box,
                )
            )

    detections.sort(key=lambda d: d.confidence, reverse=True)
    return detections
