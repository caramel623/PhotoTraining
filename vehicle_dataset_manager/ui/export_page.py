"""Dataset Export page (placeholder for Phase 5)."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ExportPage(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        title = QLabel("<h2>Dataset Export</h2>")
        note = QLabel(
            "Phase 5 feature.\n\n"
            "Will export: vehicle_crops / plate_crops / reid_crops (plate-masked),\n"
            "metadata CSVs, manifest.jsonl, and time/camera-based train/val/test splits,\n"
            "plus positive/negative training pairs.\n\n"
            "The output layout and split configuration are defined in the project README."
        )
        note.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(note)
        root.addStretch(1)