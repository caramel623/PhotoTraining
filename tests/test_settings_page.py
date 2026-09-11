from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea

from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.detection import cuda_env
from vehicle_dataset_manager.detection import onnx_plate_detector
from vehicle_dataset_manager.detection.cuda_env import CudaStatus
from vehicle_dataset_manager.ocr import installer as ocr_installer
from vehicle_dataset_manager.ui.settings_page import SettingsPage, _install_all_dependencies


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_settings_content_scrolls_and_save_button_stays_fixed(workspace) -> None:
    app = _application()
    ctx = SimpleNamespace(
        settings=AppSettings(),
        workspace=workspace,
        save_settings=lambda: None,
        build_detectors=lambda: None,
    )
    page = SettingsPage(ctx)

    scroll_area = page.findChild(QScrollArea, "settingsScrollArea")
    assert scroll_area is page.scroll_area
    assert scroll_area.widgetResizable()
    assert scroll_area.widget() is not None
    assert scroll_area.widget().isAncestorOf(page.model_reid)
    assert scroll_area.widget().isAncestorOf(page.btn_install_all)
    assert not scroll_area.widget().isAncestorOf(page.btn_save)

    page.resize(640, 420)
    page.show()
    app.processEvents()
    assert scroll_area.verticalScrollBar().maximum() > 0
    page.close()


def test_install_all_dependencies_installs_ocr_and_skips_cuda_without_nvidia(
    monkeypatch, tmp_path
) -> None:
    model = tmp_path / "taiwan_plate_detector.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr(onnx_plate_detector, "DEFAULT_MODEL_PATH", model)
    monkeypatch.setattr(
        ocr_installer,
        "install_ocr_runtime",
        lambda cache_dir, preset, line_cb: ocr_installer.OcrInstallResult(
            True, 0, "OCR ready", "C:/app/.venv-ocr/Scripts/python.exe"
        ),
    )
    report = cuda_env.CudaEnvironmentReport(status=CudaStatus.NO_GPU)
    monkeypatch.setattr(cuda_env, "detect_environment", lambda index: report)
    monkeypatch.setattr(
        cuda_env,
        "install_cuda_torch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CUDA must be skipped")),
    )
    lines = []
    result = _install_all_dependencies(tmp_path, "mobile", "cu126", lines.append)
    assert result["success"]
    assert not result["cuda_installed"]
    assert "保留 CPU 模式" in lines[-1]


def test_install_all_dependencies_fails_when_plate_model_is_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        onnx_plate_detector,
        "DEFAULT_MODEL_PATH",
        tmp_path / "missing.onnx",
    )

    result = _install_all_dependencies(
        tmp_path / "cache", "mobile", "cu126", lambda _line: None
    )

    assert result["success"] is False
    assert result["returncode"] == 4
    assert "車牌 ONNX 模型" in result["message"]
