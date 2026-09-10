"""OCR abstraction.

Concrete engines (e.g. PaddleOCR) are injected at runtime. The :class:`StubOcr`
returns no result so Phase 1 can run without a model.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class OcrResult:
    text: str
    confidence: float
    bbox: Optional[tuple] = None


class BaseOcr(ABC):
    name: str = "base"

    @abstractmethod
    def recognize(self, image: np.ndarray) -> Optional[OcrResult]:
        """Return the best text read from ``image`` (a cropped plate), or None."""
        raise NotImplementedError

    def warmup(self) -> None:
        return None


class StubOcr(BaseOcr):
    name = "stub-ocr"

    def recognize(self, image: np.ndarray) -> Optional[OcrResult]:
        return None