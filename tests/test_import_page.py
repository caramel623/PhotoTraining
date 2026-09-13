from __future__ import annotations

import os
import pytest
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
        ImportMode.REPAIR_PHOTOS.value,
        ImportMode.REPLACE_ORIGINALS.value,
    }
    assert page.table.columnCount() == 13
    headers = {
        page.table.horizontalHeaderItem(i).text() for i in range(page.table.columnCount())
    }
    assert {"既有", "INI 配對", "INI 錯誤", "Metadata 更新", "衝突"} <= headers
    page.close()


@pytest.mark.parametrize("choice,expected", [("yes", [2]), ("no", []), ("cancel", None)])
def test_original_replacement_confirmation_choices(monkeypatch, choice, expected):
    import vehicle_dataset_manager.ui.import_page as module
    from PySide6.QtWidgets import QMessageBox
    app = _application()
    called = []
    class Runner:
        def start(self, fn, **kwargs):
            called.append(fn(None, None))
    page = ImportPage(SimpleNamespace(), Runner())
    page._original_service = SimpleNamespace(apply=lambda plan, approved, *args: approved)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: {"yes": QMessageBox.Yes, "no": QMessageBox.No, "cancel": QMessageBox.Cancel}[choice])
    plan = {"cancelled": False, "items": [{"confirm": True, "state": {"image": {"image_id": 2, "original_filename": "test.png"}}, "width": 32, "height": 32}]}
    page._originals_prepared(plan)
    assert called == ([] if expected is None else [expected])
    page.close()


def test_unprocessed_originals_do_not_prompt(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app = _application()
    called = []
    class Runner:
        def start(self, fn, **kwargs):
            called.append(fn(None, None))
    page = ImportPage(SimpleNamespace(), Runner())
    page._original_service = SimpleNamespace(apply=lambda plan, approved, *args: approved)
    def unexpected(self):
        raise AssertionError("unprocessed photos must not prompt")
    monkeypatch.setattr(QMessageBox, "exec", unexpected)
    page._originals_prepared({"cancelled": False, "items": [{"confirm": False}]})
    assert called == [[]]
    page.close()
