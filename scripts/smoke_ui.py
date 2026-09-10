import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("YOLO_CONFIG_DIR", os.path.join(os.getcwd(), "runs", "2025"))

from PySide6.QtWidgets import QApplication
from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.core.workspace import Workspace
from vehicle_dataset_manager.database.connection import Database
from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.archive.manager import ArchiveManager
from vehicle_dataset_manager.services.import_service import ImportService
from vehicle_dataset_manager.workers.base import JobRunner
from vehicle_dataset_manager.detection.device import gpu_report

ws = Workspace(os.path.join(os.getcwd(), "runs", "2025")).ensure()
settings = AppSettings.load(ws.settings_path)
db = Database(ws.database_path)
ctx = AppContext(workspace=ws, settings=settings, db=db,
                 archive_manager=None, import_service=None)
ctx.archive_manager = ArchiveManager()
ctx.import_service = ImportService(db, ws, ctx.archive_manager)
print("vehicle_detector:", type(ctx.vehicle_detector).__name__,
      "device:", getattr(ctx.vehicle_detector, "device", "?"))
print("plate_detector:", type(ctx.plate_detector).__name__)
print("settings: use_cuda=%s model=%s conf=%s save_crops=%s" % (
    settings.device.use_cuda, settings.models.vehicle_model,
    settings.models.vehicle_conf, settings.processing.save_crops))
print("--- gpu report (README 28) ---")
print(gpu_report(settings.device.use_cuda))

app = QApplication([])
from vehicle_dataset_manager.ui.main_window import MainWindow
runner = JobRunner(max_workers=1)
w = MainWindow(ctx, runner)
w.show()
app.processEvents()
sp = w.settings_page
print("settings_page: use_cuda=%s vehicle_model=%s device_info=%r" % (
    sp.use_cuda.isChecked(), sp.model_vehicle_model.currentText(),
    sp.device_info.text()))
print("project_page gpu_label:")
print(w.project_page.gpu_label.text())
w.close()
print("UI SMOKE OK")