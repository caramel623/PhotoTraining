"""Logs page: live log viewer (in-process broadcast) + file reload."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.core.logging import APP_NAME, ERROR_NAME, PROCESSING_NAME


class LogsPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.source = QComboBox()
        top.addWidget(self.source)
        self.source.addItems(["全部（即時）", "應用程式日誌", "處理日誌", "錯誤日誌"])
        self.source.currentIndexChanged.connect(self._reload_file)
        self.btn_reload = QPushButton("重新載入檔案")
        self.btn_clear = QPushButton("清除畫面")
        top.addWidget(self.btn_reload)
        top.addWidget(self.btn_clear)
        top.addStretch(1)
        root.addLayout(top)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        root.addWidget(self.view, 1)

        self.btn_reload.clicked.connect(self._reload_file)
        self.btn_clear.clicked.connect(self.view.clear)

        # Subscribe to the in-process broadcaster for live updates.
        app_logger = logging.getLogger(APP_NAME)
        for handler in app_logger.handlers:
            if hasattr(handler, "subscribe"):
                handler.subscribe(self._on_log)
                break

    def _on_log(self, levelno: int, name: str, message: str) -> None:
        if self.source.currentIndex() != 0:
            return
        level = logging.getLevelName(levelno)
        self.view.appendPlainText(f"[{level}] {message}")
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def _reload_file(self, *_args) -> None:
        idx = self.source.currentIndex()
        if idx == 0:
            return
        path = {
            1: self.ctx.workspace.log_app_path,
            2: self.ctx.workspace.log_processing_path,
            3: self.ctx.workspace.log_error_path,
        }.get(idx)
        if path and path.exists():
            self.view.setPlainText(path.read_text(encoding="utf-8", errors="replace"))
            self.view.moveCursor(QTextCursor.End)
