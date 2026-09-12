from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from vehicle_dataset_manager.services.import_service import ImportMode
from vehicle_dataset_manager.ui.import_page import ImportPage


def _application():
    return QApplication.instance() or QApplication([])


def test_import_page_exposes_rescan_modes_and_ini_summary_columns():
    _application()
    page = ImportPage(SimpleNamespace(), SimpleNamespace(is_running=False))
    modes = {page.import_mode.itemData(i) for i in range(page.import_mode.count())}
    assert modes == {
        ImportMode.NORMAL.value,
        ImportMode.RESCAN_EXISTING.value,
        ImportMode.FORCE_METADATA.value,
    }
    assert page.table.columnCount() == 13
    headers = {
        page.table.horizontalHeaderItem(i).text() for i in range(page.table.columnCount())
    }
    assert {"既有", "INI 配對", "INI 錯誤", "Metadata 更新", "衝突"} <= headers
    page.close()
