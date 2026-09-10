"""Review page (placeholder for Phase 4: manual verification GUI).

Phase 1 ships a working shell so navigation is complete; the full grid +
keyboard-shortcut review workflow is added in Phase 4.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ReviewPage(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        title = QLabel("<h2>Manual Review</h2>")
        note = QLabel(
            "Phase 4 feature.\n\n"
            "Will provide: vehicle-group list on the left, image thumbnail grid on the "
            "right, and per-image labels (same / not-same / uncertain / excluded) with "
            "keyboard shortcuts (Enter=confirm, N=not same, U=uncertain, E=edit, M=merge, "
            "S=split).\n\n"
            "Grouping data is already recorded in the database by the pipeline."
        )
        note.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(note)
        root.addStretch(1)