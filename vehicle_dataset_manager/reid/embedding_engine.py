"""Pluggable ONNX Runtime embedding engine for plate-masked vehicle crops."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from vehicle_dataset_manager.reid.base import BaseReID


@dataclass(frozen=True)
class PreprocessConfig:
    width: int = 256
    height: int = 256
    mean: tuple[float, float, float] = (0.485, 0.456, 0.406)
    std: tuple[float, float, float] = (0.229, 0.224, 0.225)
    bgr_to_rgb: bool = True

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("input dimensions must be positive")
        if any(value <= 0 for value in self.std):
            raise ValueError("normalization std values must be positive")


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    """Return a finite float32 unit vector."""
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    if value.size == 0 or not np.isfinite(value).all():
        raise ValueError("embedding must be finite and non-empty")
    norm = float(np.linalg.norm(value))
    if norm <= 1e-12:
        raise ValueError("embedding norm must be greater than zero")
    return value / norm


class OnnxReIDEngine(BaseReID):
    """Run a single-input Re-ID ONNX model with explicit preprocessing.

    A session may be injected for tests or alternate ONNX Runtime setups.
    Production construction stays lazy: the model is loaded at first inference.
    """

    name = "onnx-reid"

    def __init__(
        self,
        model_path: str | Path,
        *,
        use_cuda: bool = False,
        preprocess: PreprocessConfig | None = None,
        session: Any | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.use_cuda = bool(use_cuda)
        self.preprocess_config = preprocess or PreprocessConfig()
        self._session = session
        self._input_name: str | None = None
        self._output_name: str | None = None

    @property
    def model_id(self) -> str:
        if not self.model_path.is_file():
            return self.model_path.name
        digest_builder = hashlib.sha256()
        with self.model_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()[:16]
        return f"{self.model_path.name}:{digest}"

    @property
    def providers(self) -> tuple[str, ...]:
        session = self._ensure_session()
        getter = getattr(session, "get_providers", None)
        return tuple(getter()) if getter else ()

    def warmup(self) -> None:
        self._ensure_session()

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Re-ID input must be a BGR HxWx3 image")
        if image.size == 0:
            raise ValueError("Re-ID input image is empty")
        cfg = self.preprocess_config
        interpolation = (
            cv2.INTER_AREA
            if image.shape[1] > cfg.width or image.shape[0] > cfg.height
            else cv2.INTER_LINEAR
        )
        value = cv2.resize(image, (cfg.width, cfg.height), interpolation=interpolation)
        if cfg.bgr_to_rgb:
            value = cv2.cvtColor(value, cv2.COLOR_BGR2RGB)
        value = value.astype(np.float32) / 255.0
        mean = np.asarray(cfg.mean, dtype=np.float32).reshape(1, 1, 3)
        std = np.asarray(cfg.std, dtype=np.float32).reshape(1, 1, 3)
        value = (value - mean) / std
        return np.ascontiguousarray(value.transpose(2, 0, 1)[None, ...])

    def extract_embedding(self, image: np.ndarray) -> np.ndarray:
        session = self._ensure_session()
        inputs = self.preprocess(image)
        outputs = session.run([self._output_name], {self._input_name: inputs})
        if not outputs:
            raise RuntimeError("Re-ID model returned no outputs")
        value = np.asarray(outputs[0])
        if value.ndim == 0:
            raise RuntimeError("Re-ID model returned a scalar")
        if value.ndim >= 2:
            if value.shape[0] != 1:
                raise RuntimeError("Re-ID model must return one embedding per image")
            value = value[0]
        return l2_normalize(value)

    def _ensure_session(self):
        if self._session is None:
            if not self.model_path.is_file():
                raise FileNotFoundError(f"Re-ID ONNX model not found: {self.model_path}")
            import onnxruntime as ort

            available = set(ort.get_available_providers())
            providers: list[str] = []
            if self.use_cuda and "CUDAExecutionProvider" in available:
                providers.append("CUDAExecutionProvider")
            providers.append("CPUExecutionProvider")
            self._session = ort.InferenceSession(
                str(self.model_path), providers=providers
            )
        inputs = self._session.get_inputs()
        outputs = self._session.get_outputs()
        if len(inputs) != 1 or not outputs:
            raise RuntimeError("Re-ID ONNX model must have one input and at least one output")
        self._input_name = inputs[0].name
        self._output_name = outputs[0].name
        return self._session
