"""Main application window: tabbed navigation over all feature pages."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget, QMessageBox

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.ui.export_page import ExportPage
from vehicle_dataset_manager.ui.import_page import ImportPage
from vehicle_dataset_manager.ui.logs_page import LogsPage
from vehicle_dataset_manager.ui.processing_page import ProcessingPage
from vehicle_dataset_manager.ui.project_page import ProjectPage
from vehicle_dataset_manager.ui.review_page import ReviewPage
from vehicle_dataset_manager.ui.settings_page import SettingsPage
from vehicle_dataset_manager.ui.vehicle_group_page import VehicleGroupPage
from vehicle_dataset_manager.ui.web_review import WebReview
from vehicle_dataset_manager.ui.update_panel import UpdatePanel


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, runner) -> None:
        super().__init__()
        self.ctx = ctx
        self.runner = runner
        self.setWindowTitle("車輛資料集管理工具")
        self.resize(1280, 820)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.import_page = ImportPage(ctx, runner)
        self.processing_page = ProcessingPage(ctx, runner)
        self.review_page = ReviewPage(ctx)
        self.vehicle_group_page = VehicleGroupPage(ctx)
        self.export_page = ExportPage(ctx, runner)
        self.settings_page = SettingsPage(ctx)
        self.logs_page = LogsPage(ctx)
        self.project_page = ProjectPage(ctx, runner)
        self.web_review = WebReview(ctx, self._web_busy, self)
        self.settings_page.btn_web.clicked.connect(self._toggle_web)
        self.update_panel = UpdatePanel(ctx, self._update_busy, self.project_page)
        self.project_page.layout().addWidget(self.update_panel)
        self.update_panel.busy_changed.connect(lambda busy: self.tabs.setEnabled(not busy))
        self.update_panel.exit_ready.connect(self.close)

        self.tabs.addTab(self.import_page, "匯入")
        self.tabs.addTab(self.processing_page, "影像處理")
        self.tabs.addTab(self.review_page, "人工複核")
        self.tabs.addTab(self.vehicle_group_page, "車輛群組")
        self.tabs.addTab(self.export_page, "資料集匯出")
        self.tabs.addTab(self.settings_page, "設定")
        self.tabs.addTab(self.logs_page, "日誌")
        self.tabs.addTab(self.project_page, "專案資訊")
        self.tabs.currentChanged.connect(self._refresh_visible_page)

        self.setCentralWidget(self.tabs)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._update_status()

        # Cross-page refresh: when import/processing finishes, update the status.
        self.import_page.refresh_requested.connect(self._update_status)
        self.processing_page.refresh_requested.connect(self._update_status)
        self.processing_page.refresh_requested.connect(self.review_page.refresh_groups)
        self.project_page.reset_started.connect(lambda: self.tabs.setEnabled(False))
        self.project_page.reset_finished.connect(lambda: self.tabs.setEnabled(True))
        self.project_page.database_cleared.connect(self._database_cleared)

    def _web_busy(self):
        return self.runner.is_running or not self.tabs.isEnabled()

    def _update_busy(self):
        worker = self.settings_page._worker
        return self._web_busy() or bool(worker is not None and worker.isRunning())

    def _toggle_web(self):
        if self.web_review.server:
            self.web_review.stop()
            self.settings_page.btn_web.setText("啟動區網覆核")
            self.settings_page.web_status.setText("區網覆核已停止，原存取碼已失效。")
            return
        try:
            port = self.web_review.start(self.settings_page.web_port.value())
        except OSError as exc:
            QMessageBox.warning(self, "無法啟動區網覆核", f"連接埠可能已被占用：{exc}")
            return
        self.settings_page.btn_web.setText("停止區網覆核")
        self.settings_page.web_status.setText(
            f"本機：http://127.0.0.1:{port}/\n區網：http://本機區網IP:{port}/\n"
            f"驗證碼（6 位數字）：{self.web_review.token}\n停止後重新啟動會產生新碼。\n"
            "請只允許 Windows 防火牆私人網路存取。HTTP 不加密，僅限可信任區網。"
        )
        self.settings_page.web_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def _database_cleared(self):
        self.import_page._clear()
        self.processing_page.refresh()
        self.review_page.refresh_groups()
        self.vehicle_group_page.refresh()
        self._update_status()

    def _refresh_visible_page(self, index: int) -> None:
        page = self.tabs.widget(index)
        if page is self.review_page:
            self.review_page.refresh_groups()
        elif page is self.vehicle_group_page:
            self.vehicle_group_page.refresh()

    def _update_status(self, *_args) -> None:
        counts = self.ctx.images.counts()
        self.status.showMessage(
            f"工作區：{self.ctx.workspace.root}   |   "
            f"影像：總數={counts['total']}  待處理={counts['pending']}  "
            f"已完成={counts['completed']}  失敗={counts['failed']}  "
            f"已略過={counts['skipped']}"
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self.tabs.isEnabled():
            event.ignore()  # Do not close SQLite during backup/reset.
            return
        try:
            if self.runner.is_running:
                self.runner.stop()
        except Exception:  # noqa: BLE001
            pass
        self.web_review.stop()
        try:
            self.ctx.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.ctx.db.close()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
