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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vehicle-dataset-manager",
        description="Windows 本機車輛 Re-ID 影像資料集建立工具。",
        add_help=False,
    )
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示這份說明後結束")
    parser.add_argument("--workspace", type=str, default=None,
                        help="工作區目錄（預設：程式所在資料夾）")
    parser.add_argument("--check-installer", metavar="NEW_DIRECTORY",
                        help="在全新目錄離線測試套件安裝器，不下載 CUDA 或讀取照片")
    parser.add_argument("--apply-update", help=argparse.SUPPRESS)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="顯示程式版本後結束",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.apply_update:
        from vehicle_dataset_manager.services.update_apply import helper_main
        return helper_main(args.apply_update)
    if args.check_installer:
        from vehicle_dataset_manager.services.installer_check import check_installer
        return check_installer(args.check_installer)

    workspace, settings = resolve_startup_workspace(args.workspace)
    from vehicle_dataset_manager.app_context import AppContext
    from vehicle_dataset_manager.core.logging import setup_logging
    from vehicle_dataset_manager.database.connection import Database
    from vehicle_dataset_manager.workers.base import JobRunner

    loggers = setup_logging(workspace.logs_dir)
    app_log = loggers["app"]
    app_log.info("starting Vehicle Dataset Manager v%s (workspace=%s)", __version__, workspace.root)

    import os

    os.environ.setdefault("YOLO_CONFIG_DIR", str(workspace.root))
    if settings.device.use_cuda:
        from vehicle_dataset_manager.detection.device import gpu_report

        app_log.info("device info:\n%s", gpu_report(True))
    else:
        app_log.info("device info: CPU mode (CUDA disabled)")

    # Qt must be constructed before the QThreadPool-based JobRunner.
    from PySide6.QtCore import QLibraryInfo, QTranslator
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    qt_translator = QTranslator(app)
    translations_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_translator.load("qtbase_zh_TW", translations_dir):
        app.installTranslator(qt_translator)
    app.setApplicationName("車輛資料集管理工具")
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


def resolve_startup_workspace(
    workspace_arg: str | None,
) -> tuple[object, object]:
    """Resolve startup paths; an explicit CLI workspace always wins."""
    from vehicle_dataset_manager.core.config import AppSettings
    from vehicle_dataset_manager.core.workspace import Workspace

    if workspace_arg:
        workspace = Workspace(Path(workspace_arg).expanduser()).ensure()
        return workspace, AppSettings.load(workspace.settings_path)
    default_root = _default_root()
    pre_settings = AppSettings.load(default_root / "settings.json")
    root = pre_settings.paths.workspace or str(default_root)
    workspace = Workspace(Path(root).expanduser()).ensure()
    return workspace, AppSettings.load(workspace.settings_path)


if __name__ == "__main__":
    raise SystemExit(main())
