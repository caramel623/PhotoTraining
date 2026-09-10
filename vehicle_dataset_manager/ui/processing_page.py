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
        self.btn_process_all = QPushButton("Process All Pending")
        self.btn_resume = QPushButton("Resume Selected Job")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_retry = QPushButton("Retry Failed")
        self.btn_refresh = QPushButton("Refresh")
        for b in (self.btn_process_all, self.btn_resume, self.btn_cancel, self.btn_retry, self.btn_refresh):
            top.addWidget(b)
        top.addStretch(1)
        root.addLayout(top)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Job", "Type", "Status", "Total", "Processed", "Failed", "Pending", "Archive"]
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
            self.table.setItem(i, 1, QTableWidgetItem(str(job["job_type"])))
            self.table.setItem(i, 2, QTableWidgetItem(str(job["status"])))
            self.table.setItem(i, 3, QTableWidgetItem(str(job["total"])))
            self.table.setItem(i, 4, QTableWidgetItem(str(job["processed"])))
            self.table.setItem(i, 5, QTableWidgetItem(str(job["failed"])))
            self.table.setItem(i, 6, QTableWidgetItem(str(job["pending"])))
            self.table.setItem(i, 7, QTableWidgetItem(str(job["archive_path"] or "(all)")))
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
            QMessageBox.information(self, "Processing", "No pending images. Import archives first (or Retry Failed).")
            return
        job_id = self.ctx.jobs.create("process", None, total=pending)
        self._run_engine(job_id)

    def _resume(self) -> None:
        job_id = self._selected_job_id()
        if job_id is None:
            QMessageBox.information(self, "Processing", "Select a job to resume.")
            return
        self._run_engine(job_id)

    def _retry(self) -> None:
        n = self.ctx.images.retry_failed()
        self.refresh()
        QMessageBox.information(self, "Retry", f"{n} failed image(s) reset to pending. Press 'Process All Pending'.")

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
                self, "Processing",
                f"State: {result.final_state.value}\nProcessed: {result.processed}\nFailed: {result.failed}\nPending left: {result.pending_left}",
            )

    def _error(self, message) -> None:
        self.progress.setRange(0, 1)
        self._set_running_ui()
        self.refresh()
        self.refresh_requested.emit()
        QMessageBox.critical(self, "Processing error", message)
