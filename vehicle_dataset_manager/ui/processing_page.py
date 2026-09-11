"""Processing page: run / resume / cancel the batch engine; retry failures."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.ui.i18n import display_value

log = logging.getLogger("vdm.processing")


class ProcessingPage(QWidget):
    refresh_requested = Signal()

    def __init__(self, ctx: AppContext, runner) -> None:
        super().__init__()
        self.ctx = ctx
        self.runner = runner
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_process_all = QPushButton("處理全部待辦影像")
        self.btn_resume = QPushButton("繼續選取的工作")
        self.btn_cancel = QPushButton("取消")
        self.btn_retry = QPushButton("重試失敗項目")
        self.btn_refresh = QPushButton("重新整理")
        for b in (self.btn_process_all, self.btn_resume, self.btn_cancel, self.btn_retry, self.btn_refresh):
            top.addWidget(b)
        top.addStretch(1)
        root.addLayout(top)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["工作編號", "類型", "狀態", "總數", "已處理", "失敗", "待處理", "壓縮檔"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        root.addWidget(self.table, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        root.addWidget(self.progress)

        self.btn_process_all.clicked.connect(self._process_all)
        self.btn_resume.clicked.connect(self._resume)
        self.btn_cancel.clicked.connect(self.runner.stop)
        self.btn_retry.clicked.connect(self._retry)
        self.btn_refresh.clicked.connect(self.refresh)

    def refresh(self) -> None:
        rows = self.ctx.jobs_all()
        self.table.setRowCount(len(rows))
        for i, job in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(job["job_id"])))
            self.table.setItem(i, 1, QTableWidgetItem(display_value(job["job_type"])))
            self.table.setItem(i, 2, QTableWidgetItem(display_value(job["status"])))
            self.table.setItem(i, 3, QTableWidgetItem(str(job["total"])))
            self.table.setItem(i, 4, QTableWidgetItem(str(job["processed"])))
            self.table.setItem(i, 5, QTableWidgetItem(str(job["failed"])))
            self.table.setItem(i, 6, QTableWidgetItem(str(job["pending"])))
            self.table.setItem(i, 7, QTableWidgetItem(str(job["archive_path"] or "（全部）")))
        self._set_running_ui()

    def _selected_job_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return int(item.text()) if item else None

    def _set_running_ui(self) -> None:
        running = self.runner.is_running
        self.btn_process_all.setEnabled(not running)
        self.btn_resume.setEnabled(not running)
        self.btn_retry.setEnabled(not running)
        self.btn_cancel.setEnabled(running)

    def _run_engine(self, job_id: int) -> None:
        def do_run(progress_cb, should_cancel):
            engine = self.ctx.build_engine()
            return engine.run(job_id, on_progress=progress_cb, should_cancel=should_cancel)

        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._set_running_ui()
        sig = self.runner.start(do_run)
        sig.progress.connect(self._progress)
        sig.finished.connect(self._done)
        sig.error.connect(self._error)

    def _process_all(self) -> None:
        counts = self.ctx.images.counts()
        pending = counts["pending"] + counts["processing"]
        if pending == 0:
            QMessageBox.information(self, "影像處理", "沒有待處理影像。請先匯入壓縮檔，或重試失敗項目。")
            return
        job_id = self.ctx.jobs.create("process", None, total=pending)
        self._run_engine(job_id)

    def _resume(self) -> None:
        job_id = self._selected_job_id()
        if job_id is None:
            QMessageBox.information(self, "影像處理", "請選擇要繼續的工作。")
            return
        self._run_engine(job_id)

    def _retry(self) -> None:
        n = self.ctx.images.retry_failed()
        self.refresh()
        QMessageBox.information(self, "重試", f"已將 {n} 張失敗影像重設為待處理。請按「處理全部待辦影像」。")

    def _progress(self, done, total, current) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.progress.setFormat(f"{done}/{total}  {current}")

    def _done(self, result) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._set_running_ui()
        self.refresh()
        self.refresh_requested.emit()
        if result is not None:
            QMessageBox.information(
                self, "影像處理",
                f"狀態：{display_value(result.final_state.value)}\n已處理：{result.processed}\n失敗：{result.failed}\n剩餘待處理：{result.pending_left}",
            )

    def _error(self, message) -> None:
        self.progress.setRange(0, 1)
        self._set_running_ui()
        self.refresh()
        self.refresh_requested.emit()
        QMessageBox.critical(self, "處理錯誤", message)
