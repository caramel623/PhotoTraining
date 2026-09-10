"""Vehicle Groups page: lists groups; merge/split arrive in Phase 4."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vehicle_dataset_manager.app_context import AppContext


class VehicleGroupPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        root = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Vehicle ID", "Plate (normalized)", "Verification", "Images"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.table, 1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_merge = QPushButton("Merge Selected")
        self.btn_split = QPushButton("Split…")
        self.btn_merge.setEnabled(False)
        self.btn_split.setEnabled(False)
        root.addWidget(self.btn_refresh, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.btn_merge, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.btn_split, 0, Qt.AlignmentFlag.AlignLeft)
        self.btn_refresh.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        rows = self.ctx.vehicles.list_vehicles(limit=100000)
        self.table.setRowCount(len(rows))
        for i, v in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(v["vehicle_id"]))
            self.table.setItem(i, 1, QTableWidgetItem(v["plate_normalized"] or ""))
            self.table.setItem(i, 2, QTableWidgetItem(v["verification"]))
            self.table.setItem(i, 3, QTableWidgetItem(str(v["image_count"])))