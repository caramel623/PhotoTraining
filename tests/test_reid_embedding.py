from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from vehicle_dataset_manager.reid import OnnxReIDEngine, PreprocessConfig, l2_normalize


class _Session:
    def __init__(self, output):
        self.output = output
        self.feed = None

    def get_inputs(self):
        return [SimpleNamespace(name="images")]

    def get_outputs(self):
        return [SimpleNamespace(name="embedding")]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, outputs, feed):
        self.feed = (outputs, feed)
        return [self.output]


def test_l2_normalize_and_rejects_invalid_values():
    result = l2_normalize(np.array([3.0, 4.0]))
    assert result.dtype == np.float32
    assert np.linalg.norm(result) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        l2_normalize(np.zeros(3))
    with pytest.raises(ValueError):
        l2_normalize(np.array([np.nan]))


def test_onnx_engine_preprocesses_bgr_and_normalizes_embedding(tmp_path):
    session = _Session(np.array([[3.0, 4.0]], dtype=np.float32))
    cfg = PreprocessConfig(
        width=2,
        height=1,
        mean=(0.0, 0.0, 0.0),
        std=(1.0, 1.0, 1.0),
    )
    engine = OnnxReIDEngine(tmp_path / "model.onnx", session=session, preprocess=cfg)
    image = np.array([[[0, 0, 255], [255, 0, 0]]], dtype=np.uint8)
    result = engine.extract_embedding(image)
    assert result.tolist() == pytest.approx([0.6, 0.8])
    outputs, feed = session.feed
    assert outputs == ["embedding"]
    tensor = feed["images"]
    assert tensor.shape == (1, 3, 1, 2)
    assert tensor[0, :, 0, 0].tolist() == pytest.approx([1.0, 0.0, 0.0])
    assert engine.providers == ("CPUExecutionProvider",)


def test_onnx_engine_rejects_bad_image_and_missing_model(tmp_path):
    engine = OnnxReIDEngine(tmp_path / "missing.onnx")
    with pytest.raises(ValueError, match="HxWx3"):
        engine.preprocess(np.zeros((10, 10), dtype=np.uint8))
    with pytest.raises(FileNotFoundError):
        engine.warmup()
