"""Import page: select ZIP/7Z (or a folder of them), register images as PENDING."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.archive.manager import detect_archive_type
from vehicle_dataset_manager.core.enums import ArchiveType
from vehicle_dataset_manager.services.import_service import detect_year

log = logging.getLogger("vdm.app")

_ARCHIVE_FILTER = "壓縮檔 (*.zip *.7z *.7zip);;所有檔案 (*)"


class ImportPage(QWidget):
    refresh_requested = Signal()

    def __init__(self, ctx: AppContext, runner) -> None:
        super().__init__()
        self.ctx = ctx
        self.runner = runner
        self._selected: list[Path] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_files = QPushButton("加入壓縮檔…")
        self.btn_folder = QPushButton("加入資料夾…")
        self.btn_remove = QPushButton("移除")
        self.btn_clear = QPushButton("清除")
        for b in (self.btn_files, self.btn_folder, self.btn_remove, self.btn_clear):
            top.addWidget(b)
        top.addStretch(1)
        root.addLayout(top)

        mid = QHBoxLayout()
        self.archive_list = QListWidget()
        self.archive_list.setSelectionMode(QListWidget.ExtendedSelection)
        mid.addWidget(self.archive_list, 3)
        right = QVBoxLayout()
        right.addWidget(QLabel("年份（留空則從檔名自動判斷）"))
        self.year_spin = QSpinBox()
        self.year_spin.setRange(1990, 2100)
        self.year_spin.setValue(0)
        self.year_spin.setSpecialValueText("自動")
        self.year_spin.setValue(0)
        right.addWidget(self.year_spin)
        self.btn_import = QPushButton("匯入並登錄")
        self.btn_import.setMinimumHeight(40)
        right.addWidget(self.btn_import)
        right.addStretch(1)
        mid.addLayout(right, 1)
        root.addLayout(mid, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["壓縮檔", "類型", "年份", "找到影像", "新增", "工作編號"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        self.btn_files.clicked.connect(self._add_files)
        self.btn_folder.clicked.connect(self._add_folder)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_import.clicked.connect(self._import)

    # -- selection -------------------------------------------------------
    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "選擇壓縮檔", str(Path.home()), _ARCHIVE_FILTER)
        for f in files:
            self._add(Path(f))

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "選擇含有壓縮檔的資料夾", str(Path.home()))
        if not folder:
            return
        folder = Path(folder)
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.suffix.lower() in (".zip", ".7z", ".7zip"):
                self._add(p)

    def _add(self, path: Path) -> None:
        if any(p.resolve() == path.resolve() for p in self._selected):
            return
        atype = detect_archive_type(path)
        if atype not in (ArchiveType.ZIP, ArchiveType.SEVEN_Z):
            QMessageBox.warning(self, "不支援的格式", f"這不是 ZIP／7Z 壓縮檔：\n{path}")
            return
        year = detect_year(path.name) or "自動"
        item = QListWidgetItem(f"{path.name}   [{atype.value}]   年份={year}")
        item.setData(0, str(path))
        self.archive_list.addItem(item)
        self._selected.append(path)

    def _remove_selected(self) -> None:
        for item in self.archive_list.selectedItems():
            self.archive_list.takeItem(self.archive_list.row(item))
            self._selected.remove(Path(item.data(0)))

    def _clear(self) -> None:
        self.archive_list.clear()
        self._selected.clear()
        self.table.setRowCount(0)

    def _year(self):
        return self.year_spin.value() if self.year_spin.value() != 0 else None

    # -- import ----------------------------------------------------------
    def _import(self) -> None:
        if not self._selected:
            QMessageBox.information(self, "匯入", "請先加入至少一個壓縮檔。")
            return
        if self.runner.is_running:
            QMessageBox.information(self, "匯入", "目前已有工作正在執行。")
            return
        year = self._year()
        paths = list(self._selected)

        def do_import(progress_cb, should_cancel):
            svc = self.ctx.import_service
            results = []
            for p in paths:
                if should_cancel():
                    break
                results.append(svc.import_archive(p, archive_year=year, should_cancel=should_cancel))
            return results

        self.btn_import.setEnabled(False)
        self.progress.setValue(0)
        sig = self.runner.start(do_import)
        sig.progress.connect(self.progress_cb)
        sig.finished.connect(self._import_done)
        sig.error.connect(self._import_error)

    def progress_cb(self, done, total, current) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.progress.setFormat(f"{done}/{total}  {current}")

    def _import_done(self, results) -> None:
        self.btn_import.setEnabled(True)
        self.progress.setRange(0, 1)
        if not results:
            self.progress.setValue(0)
            self.refresh_requested.emit()
            return
        for r in results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(r.archive))
            self.table.setItem(row, 1, QTableWidgetItem(str(detect_archive_type(Path(r.archive)).value)))
            self.table.setItem(row, 2, QTableWidgetItem(str(r.archive_year)))
            self.table.setItem(row, 3, QTableWidgetItem(str(r.images_found)))
            self.table.setItem(row, 4, QTableWidgetItem(str(r.images_new)))
            self.table.setItem(row, 5, QTableWidgetItem(str(r.job_id)))
        self._clear()
        self.refresh_requested.emit()
        QMessageBox.information(self, "匯入完成", f"已登錄 {len(results)} 個壓縮檔。請到「影像處理」頁開始執行。")

    def _import_error(self, message) -> None:
        self.btn_import.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        QMessageBox.critical(self, "匯入錯誤", message)
