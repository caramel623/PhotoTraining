from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea

from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.ui.settings_page import SettingsPage


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
    assert not scroll_area.widget().isAncestorOf(page.btn_save)

    page.resize(640, 420)
    page.show()
    app.processEvents()
    assert scroll_area.verticalScrollBar().maximum() > 0
    page.close()
