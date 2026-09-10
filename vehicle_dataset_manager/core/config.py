"""Application settings (paths, models, processing, OCR, mask, export).

Stored as JSON at ``<workspace>/settings.json``. Uses pydantic for validation.
The ``workspace`` field is optional: when empty the default location is used.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from vehicle_dataset_manager.core.workspace import Workspace, default_workspace_root


class PathsConfig(BaseModel):
    workspace: Optional[str] = None
    archive_dir: Optional[str] = None
    output_dir: Optional[str] = None
    temp_dir: Optional[str] = None


class ModelsConfig(BaseModel):
    """Model slot ids. Actual engine objects are injected later (pluggable)."""

    #: vehicle slot: "none" (stub) or "yolo" (Ultralytics YOLO, COCO classes)
    vehicle_detector: str = "yolo"
    #: Ultralytics model name or path for the vehicle detector.
    vehicle_model: str = "yolov8s.pt"
    #: Confidence threshold for vehicle detections.
    vehicle_conf: float = Field(default=0.3, ge=0.0, le=1.0)
    #: Plate slot: "none" (stub) or "paddle" (PaddleOCR full-image OCR filter).
    plate_detector: str = "none"
    #: OCR slot: "none" (stub) or "paddle" (PaddleOCR sidecar in .venv-ocr).
    ocr: str = "none"
    reid: str = "none"


class DeviceConfig(BaseModel):
    """Compute-device selection.

    Default is CPU. CUDA is used only when ``use_cuda`` is enabled *and* a
    CUDA device is actually available at runtime (NVIDIA GPU + a CUDA build of
    PyTorch). On machines without CUDA (e.g. AMD GPUs) everything runs on CPU.
    """

    #: Prefer CUDA (GPU) when available. Default False => CPU.
    use_cuda: bool = False
    #: PyTorch CUDA wheel index (e.g. "cu126") used when downloading CUDA deps.
    cuda_wheel_index: str = "cu126"


class ProcessingConfig(BaseModel):
    batch_size: int = Field(default=1, ge=1)
    worker_count: int = Field(default=1, ge=1)
    confidence_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    #: Write the top vehicle crop per image into the workspace crops dir.
    save_crops: bool = True


class OcrConfig(BaseModel):
    plate_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    #: PaddleOCR model preset: "mobile" (fast) or "server" (accurate).
    model_preset: str = "mobile"
    #: .venv-ocr interpreter path; None = auto-detect (.venv-ocr/Scripts/python.exe).
    ocr_python: Optional[str] = None
    #: PaddleX model cache dir; None = <workspace>/cache/paddlex (portable, local).
    cache_dir: Optional[str] = None
    #: Paddle 3.3.1 PIR/oneDNN workaround: keep False.
    enable_mkldnn: bool = False
    #: Sidecar model load/download may take a while on first run.
    init_timeout: float = Field(default=600.0, gt=0)
    #: Per-request inference timeout.
    request_timeout: float = Field(default=120.0, gt=0)


class MaskConfig(BaseModel):
    #: solid_color | blur | inpaint  (Phase 1 uses solid_color)
    method: str = "solid_color"
    margin: int = Field(default=6, ge=0)


class ExportConfig(BaseModel):
    output_format: str = "csv"
    image_quality: int = Field(default=90, ge=1, le=100)


class AppSettings(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    device: DeviceConfig = Field(default_factory=DeviceConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    mask: MaskConfig = Field(default_factory=MaskConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    # -- persistence -----------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "AppSettings":
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls.model_validate(raw)
        except (json.JSONDecodeError, ValueError):
            # Corrupt settings must not crash the app; fall back to defaults.
            return cls()

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- derived ---------------------------------------------------------
    def resolve_workspace(self) -> Workspace:
        root = self.paths.workspace or str(default_workspace_root())
        return Workspace(root).ensure()