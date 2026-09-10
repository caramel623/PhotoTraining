from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.database.repositories import ExportRepository
from vehicle_dataset_manager.ui.export_page import ExportPage, _parse_ints


class _Runner:
    is_running = False

    def stop(self):
        return None


def _app():
    return QApplication.instance() or QApplication([])


def _page(db, workspace):
    settings = AppSettings()
    ctx = SimpleNamespace(
        workspace=workspace,
        settings=settings,
        exports=ExportRepository(db),
    )
    return ExportPage(ctx, _Runner())


def test_export_page_defaults_to_safe_local_options(db, workspace):
    _app()
    page = _page(db, workspace)
    options = page._options()
    assert options.label_priorities == ("human_verified",)
    assert options.mask_method == "solid_color"
    assert options.require_plate_bbox is True
    assert options.copy_originals is True
    assert str(workspace.exports_dir) in page.output_path.text()
    page._preview()
    assert "Eligible: 0 / 0" in page.status_label.text()
    page.close()


def test_export_page_parses_time_and_camera_configuration(db, workspace):
    _app()
    page = _page(db, workspace)
    page.split_strategy.setCurrentIndex(page.split_strategy.findData("time"))
    page.val_years.setText("2024, 2025")
    page.test_years.setText("2026")
    options = page._options()
    assert options.split.strategy == "time"
    assert options.split.val_years == (2024, 2025)
    assert options.split.test_years == (2026,)
    assert page.val_years.isEnabled()
    assert not page.val_cameras.isEnabled()

    page.split_strategy.setCurrentIndex(page.split_strategy.findData("camera"))
    page.test_cameras.setText("RS015, RS017")
    options = page._options()
    assert options.split.test_cameras == ("RS015", "RS017")
    assert page.test_cameras.isEnabled()
    assert not page.test_years.isEnabled()
    page.close()


def test_parse_ints_rejects_invalid_year():
    with pytest.raises(ValueError, match="invalid year"):
        _parse_ints("2025, twenty-six")
