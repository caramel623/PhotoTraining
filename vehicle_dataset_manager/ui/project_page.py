"""Project page: workspace / database / environment overview."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from vehicle_dataset_manager.app_context import AppContext


class ProjectPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.ws_label = QLabel()
        self.db_label = QLabel()
        self.gpu_label = QLabel()
        self.sevenz_label = QLabel()
        form.addRow("Workspace", self.ws_label)
        form.addRow("Database", self.db_label)
        form.addRow("GPU / CUDA", self.gpu_label)
        form.addRow("7-Zip CLI", self.sevenz_label)
        root.addLayout(form)
        self.info = QPlainTextEdit(); self.info.setReadOnly(True)
        root.addWidget(self.info, 1)
        self.btn_refresh = QPushButton("Refresh")
        root.addWidget(self.btn_refresh, 0, Qt.AlignmentFlag.AlignLeft)
        self.btn_refresh.clicked.connect(self.refresh)

    def refresh(self) -> None:
        self.ws_label.setText(str(self.ctx.workspace.root))
        self.db_label.setText(str(self.ctx.workspace.database_path))
        self.gpu_label.setText(_gpu_info(self.ctx.settings.device.use_cuda))
        self.sevenz_label.setText(str(self.ctx.archive_manager.sevenz_cli) if self.ctx.archive_manager.sevenz_cli else "not found (using py7zr for 7z)")
        counts = self.ctx.images.counts()
        total_vehicles = len(self.ctx.vehicles.list_vehicles(limit=100000))
        self.info.setPlainText(
            f"Images: total={counts['total']}  pending={counts['pending']}  "
            f"completed={counts['completed']}  failed={counts['failed']}  skipped={counts['skipped']}\n"
            f"Vehicle groups: {total_vehicles}\n\n"
            "Local-only: no telemetry, no cloud upload. Originals are never modified.\n"
            "Plate OCR is used for labelling/grouping only; Re-ID learns appearance."
        )


def _gpu_info(use_cuda: bool = False) -> str:
    if not use_cuda:
        return "CPU mode (CUDA disabled)"
    from vehicle_dataset_manager.detection.device import gpu_report

    return gpu_report(True)
