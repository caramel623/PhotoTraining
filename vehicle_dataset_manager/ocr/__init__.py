"""Pluggable OCR engines."""
from vehicle_dataset_manager.ocr.base import BaseOcr, OcrResult, StubOcr
from vehicle_dataset_manager.ocr.paddle_client import (
    PaddleOcr,
    PaddleOcrError,
    PaddleOcrProcess,
    PaddlePlateDetector,
    build_paddle_engines,
    default_ocr_python,
)

__all__ = [
    "BaseOcr",
    "OcrResult",
    "StubOcr",
    "PaddleOcr",
    "PaddleOcrError",
    "PaddleOcrProcess",
    "PaddlePlateDetector",
    "build_paddle_engines",
    "default_ocr_python",
]
