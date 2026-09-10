"""Application entry point.

Boots the workspace, settings, logging, database, and the main window.
Run with ``python -m vehicle_dataset_manager`` or ``python run.py``.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from vehicle_dataset_manager import __version__
from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.core.logging import setup_logging
from vehicle_dataset_manager.core.workspace import Workspace
from vehicle_dataset_manager.database.connection import Database
from vehicle_dataset_manager.workers.base import JobRunner


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vehicle-dataset-manager",
        description="Windows local vehicle image dataset builder for Vehicle Re-ID.",
    )
    parser.add_argument("--workspace", type=str, default=None,
                        help="Workspace directory (default: Documents/VehicleDatasetManager)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # Resolve workspace + settings (settings may point at a different workspace).
    initial_root = Path(args.workspace).expanduser() if args.workspace else None
    pre_settings = AppSettings.load((initial_root or _default_root()) / "settings.json")
    workspace = pre_settings.resolve_workspace()
    settings = AppSettings.load(workspace.settings_path)

    loggers = setup_logging(workspace.logs_dir)
    app_log = loggers["app"]
    app_log.info("starting Vehicle Dataset Manager v%s (workspace=%s)", __version__, workspace.root)

    import os

    os.environ.setdefault("YOLO_CONFIG_DIR", str(workspace.root))
    from vehicle_dataset_manager.detection.device import gpu_report

    app_log.info("device info:\n%s", gpu_report(settings.device.use_cuda))

    # Qt must be constructed before the QThreadPool-based JobRunner.
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Vehicle Dataset Manager")
    app.setOrganizationName("CPTR")

    db = Database(workspace.database_path)
    ctx = AppContext(
        workspace=workspace,
        settings=settings,
        db=db,
        archive_manager=None,  # auto-detect 7-Zip CLI
        import_service=None,  # filled below after ctx exists
    )
    # ImportService needs the db + workspace; wire it now that ctx is built.
    from vehicle_dataset_manager.archive.manager import ArchiveManager
    from vehicle_dataset_manager.services.import_service import ImportService

    ctx.archive_manager = ArchiveManager()
    ctx.import_service = ImportService(db, workspace, ctx.archive_manager)

    runner = JobRunner(max_workers=settings.processing.worker_count)

    from vehicle_dataset_manager.ui.main_window import MainWindow

    window = MainWindow(ctx, runner)
    window.show()

    try:
        return app.exec()
    finally:
        app_log.info("shutdown")
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def _default_root() -> Path:
    from vehicle_dataset_manager.core.workspace import default_workspace_root

    return default_workspace_root()


if __name__ == "__main__":
    raise SystemExit(main())