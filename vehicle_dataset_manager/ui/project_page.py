"""Project page: workspace / database / environment overview."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QFormLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget, QInputDialog, QMessageBox

from vehicle_dataset_manager.app_context import AppContext


class ProjectPage(QWidget):
    database_cleared = Signal()
    reset_started = Signal()
    reset_finished = Signal()

    def __init__(self, ctx: AppContext, runner=None) -> None:
        super().__init__()
        self.ctx = ctx
        self.runner = runner
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.ws_label = QLabel()
        self.db_label = QLabel()
        self.gpu_label = QLabel()
        self.sevenz_label = QLabel()
        form.addRow("工作區", self.ws_label)
        form.addRow("資料庫", self.db_label)
        form.addRow("GPU / CUDA", self.gpu_label)
        form.addRow("7-Zip 命令列工具", self.sevenz_label)
        root.addLayout(form)
        self.info = QPlainTextEdit(); self.info.setReadOnly(True)
        root.addWidget(self.info, 1)
        self.btn_refresh = QPushButton("重新整理")
        root.addWidget(self.btn_refresh, 0, Qt.AlignmentFlag.AlignLeft)
        self.btn_refresh.clicked.connect(self.refresh)
        warning = QLabel("清除資料庫會移除所有匯入、辨識、人工標註、群組與工作紀錄；不刪除照片、模型或設定。")
        warning.setWordWrap(True)
        root.addWidget(warning)
        self.btn_clear_database = QPushButton("備份並清除資料庫…")
        self.btn_clear_database.setEnabled(self.runner is not None)
        self.btn_clear_database.clicked.connect(self._clear_database)
        root.addWidget(self.btn_clear_database)

    def _clear_database(self):
        if self.runner is None or self.runner.is_running:
            QMessageBox.information(self, "清除資料庫", "請先完成或取消目前的工作，再清除資料庫。")
            return
        text, accepted = QInputDialog.getText(self, "確認清除資料庫",
            f"目標：{self.ctx.workspace.database_path}\n"
            "1. 自動備份到 database/backups，備份失敗就不清除。\n"
            "2. 清除全部影像紀錄、辨識結果、人工標註、群組及匯入／輸出工作紀錄。\n"
            "3. 保留照片、原始壓縮檔、模型、設定及已匯出的檔案。\n"
            "清除後可重新匯入；恢復舊標註需要還原備份。\n請完整輸入「清除資料庫」：")
        if not accepted or text != "清除資料庫":
            return
        if self.runner.is_running:
            QMessageBox.information(self, "清除資料庫", "目前已有工作正在執行，未清除。")
            return
        self.reset_started.emit()
        self.btn_clear_database.setEnabled(False)
        try:
            self.runner.start(lambda progress_cb, should_cancel: self.ctx.db.backup_and_clear(),
                              on_finished=self._clear_done, on_error=self._clear_error)
        except Exception as exc:
            self._clear_error(str(exc))

    @Slot(object)
    def _clear_done(self, backup):
        self.btn_clear_database.setEnabled(True)
        self.reset_finished.emit()
        self.database_cleared.emit()
        self.refresh()
        QMessageBox.information(self, "資料庫已清除",
            f"已清除資料庫紀錄，可重新匯入。照片、模型及設定未刪除。\n清除前備份：\n{backup}")

    @Slot(str)
    def _clear_error(self, message):
        self.btn_clear_database.setEnabled(True)
        self.reset_finished.emit()
        QMessageBox.critical(self, "清除未完成", f"未清除資料，或清除交易已回復；請勿刪除備份。\n{message}")

    def refresh(self) -> None:
        self.ws_label.setText(str(self.ctx.workspace.root))
        self.db_label.setText(str(self.ctx.workspace.database_path))
        self.gpu_label.setText(_gpu_info(self.ctx.settings.device.use_cuda))
        self.sevenz_label.setText(str(self.ctx.archive_manager.sevenz_cli) if self.ctx.archive_manager.sevenz_cli else "未找到（7z 將使用 py7zr）")
        counts = self.ctx.images.counts()
        total_vehicles = len(self.ctx.vehicles.list_vehicles(limit=100000))
        self.info.setPlainText(
            f"影像：總數={counts['total']}  待處理={counts['pending']}  "
            f"已完成={counts['completed']}  失敗={counts['failed']}  已略過={counts['skipped']}\n"
            f"車輛群組：{total_vehicles}\n\n"
            "僅限本機：不使用遙測、不上傳雲端，且不會修改原始檔。\n"
            "車牌 OCR 僅用於標記與分組；Re-ID 學習車輛外觀。"
        )


def _gpu_info(use_cuda: bool = False) -> str:
    if not use_cuda:
        return "CPU 模式（CUDA 已停用）"
    from vehicle_dataset_manager.detection.device import gpu_report

    return gpu_report(True)
