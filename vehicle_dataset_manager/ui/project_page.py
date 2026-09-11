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
