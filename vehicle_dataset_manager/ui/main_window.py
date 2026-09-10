"""Main application window: tabbed navigation over all feature pages."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.ui.export_page import ExportPage
from vehicle_dataset_manager.ui.import_page import ImportPage
from vehicle_dataset_manager.ui.logs_page import LogsPage
from vehicle_dataset_manager.ui.processing_page import ProcessingPage
from vehicle_dataset_manager.ui.project_page import ProjectPage
from vehicle_dataset_manager.ui.review_page import ReviewPage
from vehicle_dataset_manager.ui.settings_page import SettingsPage
from vehicle_dataset_manager.ui.vehicle_group_page import VehicleGroupPage


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, runner) -> None:
        super().__init__()
        self.ctx = ctx
        self.runner = runner
        self.setWindowTitle("Vehicle Dataset Manager")
        self.resize(1280, 820)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.import_page = ImportPage(ctx, runner)
        self.processing_page = ProcessingPage(ctx, runner)
        self.review_page = ReviewPage(ctx)
        self.vehicle_group_page = VehicleGroupPage(ctx)
        self.export_page = ExportPage(ctx)
        self.settings_page = SettingsPage(ctx)
        self.logs_page = LogsPage(ctx)
        self.project_page = ProjectPage(ctx)

        self.tabs.addTab(self.import_page, "Import")
        self.tabs.addTab(self.processing_page, "Processing")
        self.tabs.addTab(self.review_page, "Review")
        self.tabs.addTab(self.vehicle_group_page, "Vehicle Groups")
        self.tabs.addTab(self.export_page, "Dataset Export")
        self.tabs.addTab(self.settings_page, "Settings")
        self.tabs.addTab(self.logs_page, "Logs")
        self.tabs.addTab(self.project_page, "Project")

        self.setCentralWidget(self.tabs)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._update_status()

        # Cross-page refresh: when import/processing finishes, update the status.
        self.import_page.refresh_requested.connect(self._update_status)
        self.processing_page.refresh_requested.connect(self._update_status)

    def _update_status(self, *_args) -> None:
        counts = self.ctx.images.counts()
        self.status.showMessage(
            f"Workspace: {self.ctx.workspace.root}   |   "
            f"Images: total={counts['total']}  pending={counts['pending']}  "
            f"completed={counts['completed']}  failed={counts['failed']}  "
            f"skipped={counts['skipped']}"
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        try:
            if self.runner.is_running:
                self.runner.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.ctx.db.close()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)