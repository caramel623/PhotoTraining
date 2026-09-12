"""Import page: select ZIP/7Z (or a folder of them), register images as PENDING."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QDialog,
    QPlainTextEdit,
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
from vehicle_dataset_manager.services.import_service import ImportMode, detect_year

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
        self.btn_history = QPushButton("檢視最近匯入／重掃紀錄")
        root.addWidget(self.btn_history)
        self.btn_history.clicked.connect(self._show_history)
        self.btn_cancel = QPushButton("取消目前工作")
        root.addWidget(self.btn_cancel)
        self.btn_cancel.clicked.connect(lambda: self.runner.stop())

        mid = QHBoxLayout()
        self.archive_list = QListWidget()
        self.archive_list.setSelectionMode(QListWidget.ExtendedSelection)
        mid.addWidget(self.archive_list, 3)
        right = QVBoxLayout()
        right.addWidget(QLabel("年份（留空則從檔名自動判斷）"))
        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 2100)
        self.year_spin.setValue(0)
        self.year_spin.setSpecialValueText("自動")
        self.year_spin.setValue(0)
        right.addWidget(self.year_spin)
        right.addWidget(QLabel("匯入模式"))
        self.import_mode = QComboBox()
        self.import_mode.addItem("一般匯入（必要時更新 metadata）", ImportMode.NORMAL.value)
        self.import_mode.addItem("重新掃描既有資料（不新增影像）", ImportMode.RESCAN_EXISTING.value)
        self.import_mode.addItem("強制重解析 INI／metadata", ImportMode.FORCE_METADATA.value)
        right.addWidget(self.import_mode)
        mode_help = QLabel("重掃請先選取要處理的壓縮檔；不會重跑偵測、OCR 或 Re-ID。強制模式也可新增影像。")
        mode_help.setWordWrap(True)
        right.addWidget(mode_help)
        self.btn_import = QPushButton("開始匯入／重新掃描")
        self.btn_import.setMinimumHeight(40)
        right.addWidget(self.btn_import)
        right.addStretch(1)
        mid.addLayout(right, 1)
        root.addLayout(mid, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.table = QTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels(
            [
                "壓縮檔", "模式", "影像", "既有", "新增", "INI 配對",
                "INI 缺少", "INI 錯誤", "INI 車牌", "未配對 INI",
                "Metadata 更新", "衝突", "工作編號",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        root.addWidget(self.table, 1)

        self.btn_files.clicked.connect(self._add_files)
        self.btn_folder.clicked.connect(self._add_folder)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_import.clicked.connect(self._import)

    # -- selection -------------------------------------------------------
    def _show_history(self):
        rows = self.ctx.db.query(
            "SELECT j.*,a.archive_filename FROM import_jobs j JOIN archives a ON a.archive_id=j.archive_id "
            "ORDER BY import_job_id DESC LIMIT 100"
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("最近 100 筆匯入／重掃紀錄")
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        labels = (
            ("archive_filename", "壓縮檔"), ("mode", "模式"), ("status", "狀態"),
            ("started_at", "開始 UTC"), ("finished_at", "結束 UTC"),
            ("images_found", "影像總數"), ("images_new", "新增影像"), ("images_existing", "既有影像"),
            ("ini_found", "INI 總數"), ("ini_new", "新增 INI"), ("ini_existing", "既有 INI"),
            ("ini_matched", "INI 配對"), ("ini_missing", "缺少 INI"), ("ini_parse_error", "解析錯誤"),
            ("ini_with_plate", "含車牌"), ("ini_without_plate", "無有效車牌"),
            ("unmatched_ini", "無影像 INI"), ("metadata_updated", "資料更新"),
            ("conflicts", "衝突"), ("errors", "錯誤"), ("message", "訊息"),
        )
        text.setPlainText("\n\n".join(
            "\n".join(f"{label}：{row[key]}" for key, label in labels) for row in rows
        ) or "目前沒有匯入紀錄。")
        layout.addWidget(text)
        dialog.resize(740, 560)
        dialog.exec()

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
        item.setData(Qt.UserRole, str(path))
        self.archive_list.addItem(item)
        self._selected.append(path)

    def _remove_selected(self) -> None:
        for item in self.archive_list.selectedItems():
            self.archive_list.takeItem(self.archive_list.row(item))
            self._selected.remove(Path(item.data(Qt.UserRole)))

    def _clear(self) -> None:
        self.archive_list.clear()
        self._selected.clear()
        self.table.setRowCount(0)

    def _clear_selection(self) -> None:
        self.archive_list.clear()
        self._selected.clear()

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
        mode = ImportMode(str(self.import_mode.currentData()))
        selected_paths = [Path(item.data(Qt.UserRole)) for item in self.archive_list.selectedItems()]
        if mode != ImportMode.NORMAL and not selected_paths:
            QMessageBox.information(self, "重新掃描", "請在清單選取要重新掃描的壓縮檔；可按 Ctrl 或 Shift 多選。")
            return
        paths = selected_paths if mode != ImportMode.NORMAL and selected_paths else list(self._selected)

        def do_import(progress_cb, should_cancel):
            svc = self.ctx.import_service
            results = []
            for p in paths:
                if should_cancel():
                    break
                results.append(
                    svc.import_archive(
                        p, archive_year=year, should_cancel=should_cancel, mode=mode, on_progress=progress_cb
                    )
                )
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
            self.table.item(row, 0).setToolTip(
                f"INI 總數：{r.ini_found}；新增：{r.ini_new}；既有：{r.ini_existing}；無車牌：{r.ini_without_plate}\n"
                f"取消：{r.cancelled}；錯誤：{r.errors}；{r.message}"
            )
            values = (
                r.mode, r.images_found, r.images_existing, r.images_new,
                r.ini_matched, r.ini_missing, r.ini_parse_error, r.ini_with_plate,
                r.unmatched_ini, r.metadata_updated, r.conflicts, r.job_id,
            )
            for column, value in enumerate(values, start=1):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self._clear_selection()
        self.refresh_requested.emit()
        QMessageBox.information(
            self, "匯入完成",
            f"已返回 {len(results)} 個壓縮檔結果；取消 {sum(r.cancelled for r in results)} 個，錯誤 {sum(r.errors for r in results)} 筆。\n"
            "影像欄為發現總數；僅掃既有模式不新增影像。詳細統計請將游標停在壓縮檔名稱。重掃不會重跑 AI。",
        )

    def _import_error(self, message) -> None:
        self.btn_import.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        QMessageBox.critical(self, "匯入錯誤", message)
