"""Tests for device (CPU/CUDA) resolution, reporting, and settings."""
from __future__ import annotations

import pytest

from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.detection.device import (
    cuda_available,
    gpu_report,
    resolve_device,
)


def test_default_device_is_cpu():
    # CPU is the default regardless of hardware.
    assert resolve_device(False) == "cpu"


def test_cuda_only_when_requested_and_available():
    result = resolve_device(True)
    assert result in ("cpu", "cuda")
    if cuda_available():
        assert result == "cuda"
    else:
        assert result == "cpu"


def test_gpu_report_shape():
    report = gpu_report(False)
    assert isinstance(report, str)
    assert "CUDA available:" in report
    assert "Device:" in report
    assert "GPU:" in report


def test_gpu_report_cuda_flag():
    cpu = gpu_report(False)
    assert "Device: cpu" in cpu


def test_settings_defaults():
    s = AppSettings()
    assert s.device.use_cuda is False
    assert s.models.vehicle_detector == "yolo"
    assert s.models.vehicle_model == "yolov8s.pt"
    assert s.processing.save_crops is True


def test_settings_roundtrip(tmp_path):
    s = AppSettings()
    s.device.use_cuda = True
    s.models.vehicle_model = "yolov8m.pt"
    s.models.vehicle_conf = 0.5
    path = tmp_path / "settings.json"
    s.save(path)

    loaded = AppSettings.load(path)
    assert loaded.device.use_cuda is True
    assert loaded.models.vehicle_model == "yolov8m.pt"
    assert loaded.models.vehicle_conf == pytest.approx(0.5)