"""Import page: select ZIP/7Z (or a folder of them), register images as PENDING."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Signal, Qt, Slot
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
from vehicle_dataset_manager.services.photo_health import PhotoHealthService
from vehicle_dataset_manager.services.original_replacement import OriginalReplacementService

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
        self.btn_original_folder = QPushButton("加入原圖資料夾…")
        self.btn_remove = QPushButton("移除")
        self.btn_clear = QPushButton("清除")
        for b in (self.btn_files, self.btn_folder, self.btn_original_folder, self.btn_remove, self.btn_clear):
            top.addWidget(b)
        top.addStretch(1)
        root.addLayout(top)
        self.btn_history = QPushButton("檢視最近匯入／重掃紀錄")
        root.addWidget(self.btn_history)
        self.btn_history.clicked.connect(self._show_history)
        self.btn_scan_photos = QPushButton("掃描工作區照片完整性")
        root.addWidget(self.btn_scan_photos)
        self.btn_scan_photos.clicked.connect(self._scan_photos)
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
        self.import_mode.addItem("從相同原始壓縮檔修復照片", ImportMode.REPAIR_PHOTOS.value)
        self.import_mode.addItem("以未裁切原圖替換（同檔名配對）", ImportMode.REPLACE_ORIGINALS.value)
        right.addWidget(self.import_mode)
        mode_help = QLabel("重掃／修復請選取清單中的壓縮檔。照片修復只替換缺失、無法讀取或內容變動的照片路徑，保留標註與舊副本；不重跑 AI。")
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
        self.btn_original_folder.clicked.connect(self._add_original_folder)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_import.clicked.connect(self._import)

    # -- selection -------------------------------------------------------
    def _scan_photos(self):
        if self.runner.is_running:
            QMessageBox.information(self, "照片掃描", "目前已有工作正在執行。")
            return
        self.btn_scan_photos.setEnabled(False)
        self.progress.setRange(0, 0)
        def scan(progress_cb, should_cancel):
            return PhotoHealthService(self.ctx.db).scan(progress_cb, should_cancel)
        self.runner.start(scan, on_progress=self.progress_cb,
                          on_finished=self._scan_done, on_error=self._import_error)

    @Slot(object)
    def _scan_done(self, result):
        self.btn_scan_photos.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if result is None:
            return
        labels = {"ok": "正常", "missing": "遺失", "unreadable": "無法讀取／權限",
                  "corrupt": "空白或無法解碼", "changed": "內容與原雜湊不同",
                  "unverified": "可讀但無歷史雜湊（不能自動修復）"}
        dialog = QDialog(self)
        dialog.setWindowTitle("工作區照片掃描結果（只讀）")
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        summary = f"已檢查 {result.checked}/{result.total}；取消：{result.cancelled}\n"
        summary += "\n".join(f"{label}：{result.counts.get(key, 0)}" for key, label in labels.items())
        summary += "\n\n範圍：資料庫登錄的原圖，不含裁切圖與未匯入檔案。\n選擇『從相同原始壓縮檔修復照片』補回；無雜湊者不以檔名猜測。\n以下最多顯示 1000 筆：\n"
        text.setPlainText(summary + "\n".join(
            f"#{r['image_id']} [{labels[r['status']]}] {r['original_archive']} | {r['source_path']}"
            for r in result.issues[:1000]))
        layout.addWidget(text)
        dialog.resize(900, 600)
        dialog.exec()

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

    def _add_original_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "選擇未裁切原圖資料夾（包含子資料夾）", str(Path.home()))
        if not folder:
            return
        path = Path(folder)
        if path not in self._selected:
            item = QListWidgetItem(f"{path.name} [原圖資料夾]")
            item.setData(Qt.UserRole, str(path))
            self.archive_list.addItem(item)
            self._selected.append(path)
            item.setSelected(True)
        self.import_mode.setCurrentIndex(self.import_mode.findData(ImportMode.REPLACE_ORIGINALS.value))

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
        if mode == ImportMode.REPLACE_ORIGINALS:
            self._prepare_originals(paths)
            return
        if any(p.is_dir() for p in paths):
            QMessageBox.information(self, "匯入", "原圖資料夾只能用於『以未裁切原圖替換』模式。")
            return
        if mode == ImportMode.REPAIR_PHOTOS:
            answer = QMessageBox.question(self, "確認修復照片",
                "將驗證原始壓縮檔與照片 SHA256，建立新解壓快照，僅替換失效照片的資料庫路徑。\n"
                "保留原副本、人工標註、群組與處理狀態，不新增影像、不重跑 AI。需要額外解壓空間。\n是否繼續？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return

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
        self.runner.start(do_import, on_progress=self.progress_cb,
                          on_finished=self._import_done, on_error=self._import_error)

    def _prepare_originals(self, paths):
        self._original_service = OriginalReplacementService(self.ctx.db, self.ctx.workspace, self.ctx.archive_manager)
        self.btn_import.setEnabled(False)
        self.progress.setRange(0, 0)
        self.runner.start(lambda progress, cancel: self._original_service.prepare(paths, progress, cancel),
                          on_progress=self.progress_cb, on_finished=self._originals_prepared, on_error=self._import_error)

    @Slot(object)
    def _originals_prepared(self, plan):
        if plan is None or plan["cancelled"]:
            self.btn_import.setEnabled(True)
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            QMessageBox.information(self, "原圖替換", "預覽已取消，尚未替換任何照片。")
            return
        protected = [item for item in plan["items"] if item["confirm"]]
        approved = []
        if protected:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("確認替換已處理影像")
            dialog.setText(f"未處理影像 {len(plan['items']) - len(protected)} 張會直接替換。\n"
                f"另有 {len(protected)} 張已處理或有人工標註，是否一併替換？\n"
                "保留人工車牌、群組、複核及舊照片；舊偵測／裁切／OCR 結果失效，排回待處理。\n"
                "是：一併替換；否：只替換未處理；取消：全部不替換。")
            dialog.setDetailedText("\n".join(
                f"#{i['state']['image']['image_id']} {i['state']['image']['original_filename']} "
                f"{i['state']['image'].get('width') or '?'}×{i['state']['image'].get('height') or '?'} → {i['width']}×{i['height']}"
                for i in protected))
            dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            dialog.setDefaultButton(QMessageBox.No)
            choice = dialog.exec()
            if choice == QMessageBox.Cancel:
                self.btn_import.setEnabled(True)
                self.progress.setRange(0, 1)
                self.progress.setValue(0)
                return
            if choice == QMessageBox.Yes:
                approved = [i["state"]["image"]["image_id"] for i in protected]
        self.runner.start(lambda progress, cancel: self._original_service.apply(plan, approved, progress, cancel),
                          on_progress=self.progress_cb, on_finished=self._originals_done, on_error=self._import_error)

    @Slot(object)
    def _originals_done(self, result):
        self.btn_import.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.refresh_requested.emit()
        dialog = QMessageBox(self)
        dialog.setWindowTitle("原圖替換結果")
        dialog.setText(f"已替換 {result['replaced']} 張；未同意替換 {result['skipped']} 張；取消：{result['cancelled']}。\n"
            f"未配對／衝突／其他提示：{len(result['errors'])} 筆，請展開詳細資料。\n"
            "已替換影像排回待處理，請到『影像處理』重建偵測及裁切。\n"
            f"舊副本未刪除；替換紀錄：{result['root']}\\replacement-report.json")
        dialog.setDetailedText("\n".join(result["errors"]) or "無衝突或錯誤。")
        dialog.exec()
        self._clear_selection()

    @Slot(int, int, str)
    def progress_cb(self, done, total, current) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.progress.setFormat(f"{done}/{total}  {current}")

    @Slot(object)
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
            f"修復照片：{sum(r.photos_repaired for r in results)} 張（無歷史雜湊或不匹配者不替換）。\n"
            "影像欄為發現總數；僅掃既有模式不新增影像。詳細統計請將游標停在壓縮檔名稱。重掃不會重跑 AI。",
        )

    @Slot(str)
    def _import_error(self, message) -> None:
        self.btn_scan_photos.setEnabled(True)
        self.btn_import.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        QMessageBox.critical(self, "匯入錯誤", message)
