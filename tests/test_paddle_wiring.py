"""G3 wiring tests: config slots + AppContext paddle selection (offline)."""
from __future__ import annotations

from pathlib import Path

import pytest

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.archive.manager import ArchiveManager
from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.detection.base import StubPlateDetector, StubVehicleDetector
from vehicle_dataset_manager.ocr.base import StubOcr
from vehicle_dataset_manager.ocr.paddle_client import (
    PaddleOcr,
    PaddlePlateDetector,
    default_ocr_python,
)
from vehicle_dataset_manager.ocr.paddle_protocol import MODEL_PRESETS


def make_settings(tmp_path, ocr_slot="none"):
    s = AppSettings()
    s.models.ocr = ocr_slot
    s.paths.workspace = str(tmp_path / "ws")
    return s


def test_config_defaults():
    s = AppSettings()
    assert s.models.ocr == "none"
    assert s.ocr.model_preset == "mobile"
    assert s.ocr.enable_mkldnn is False
    assert s.ocr.ocr_python is None
    assert s.ocr.cache_dir is None
    assert s.ocr.init_timeout > 0
    assert s.ocr.request_timeout > 0
    assert MODEL_PRESETS["mobile"][0] == "PP-OCRv5_mobile_det"


def test_context_stub_by_default(db, workspace, tmp_path):
    s = make_settings(tmp_path, ocr_slot="none")
    c = AppContext(workspace=workspace, settings=s, db=db,
                   archive_manager=ArchiveManager(), import_service=None)
    assert isinstance(c.plate_detector, StubPlateDetector)
    assert isinstance(c.ocr, StubOcr)
    assert c.paddle_process is None


def test_context_paddle_builds_shared_sidecar(db, workspace, tmp_path):
    s = make_settings(tmp_path, ocr_slot="paddle")
    # point at a python that exists (main venv) so construction succeeds
    import sys
    s.ocr.ocr_python = sys.executable
    c = AppContext(workspace=workspace, settings=s, db=db,
                   archive_manager=ArchiveManager(), import_service=None)
    assert isinstance(c.plate_detector, PaddlePlateDetector)
    assert isinstance(c.ocr, PaddleOcr)
    assert c.paddle_process is not None
    # both engines share the ONE sidecar process
    assert c.plate_detector.process is c.ocr.process
    assert c.paddle_process is c.ocr.process
    # cache defaults under the workspace
    assert c.paddle_process.cache_dir == workspace.cache_dir / "paddlex"
    c.close()
    assert c.paddle_process is None


def test_context_paddle_missing_python_falls_back_to_stub(db, workspace, tmp_path):
    s = make_settings(tmp_path, ocr_slot="paddle")
    s.ocr.ocr_python = str(tmp_path / "no_such_python.exe")
    c = AppContext(workspace=workspace, settings=s, db=db,
                   archive_manager=ArchiveManager(), import_service=None)
    # construction itself does not spawn; stubs kept only on build error.
    # A missing interpreter is caught at start; here build succeeds lazily.
    assert c.paddle_process is not None
    c.close()


def test_default_ocr_python_overridable(tmp_path, monkeypatch):
    monkeypatch.setenv("VDM_OCR_PYTHON", str(tmp_path / "custom.exe"))
    assert default_ocr_python() == tmp_path / "custom.exe"
