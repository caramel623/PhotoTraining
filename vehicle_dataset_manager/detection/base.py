"""Detector abstraction.

A detector receives a BGR image (``numpy.ndarray``) and returns a list of
:class:`Detection`. The concrete engine (e.g. Ultralytics YOLO) is injected at
runtime; until then a :class:`StubVehicleDetector` lets the pipeline run
end-to-end and persist status without any model download.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional

import numpy as np


@dataclass
class Detection:
    class_name: str
    confidence: float
    bbox: tuple  # (x1, y1, x2, y2) in pixel coords, top-left/bottom-right

    @property
    def bbox_dict(self) -> dict:
        x1, y1, x2, y2 = self.bbox
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


class BaseDetector(ABC):
    """Common interface for vehicle and plate detectors."""

    name: str = "base"

    @abstractmethod
    def detect(self, image: np.ndarray) -> List[Detection]:
        """Return detections for a single BGR image."""
        raise NotImplementedError

    def warmup(self) -> None:  # optional hook
        return None


class StubVehicleDetector(BaseDetector):
    """No-op detector: returns no vehicles. Keeps the pipeline runnable."""

    name = "stub-vehicle"

    def detect(self, image: np.ndarray) -> List[Detection]:
        return []


class StubPlateDetector(BaseDetector):
    """No-op plate detector: returns no plates."""

    name = "stub-plate"

    def detect(self, image: np.ndarray) -> List[Detection]:
        return []