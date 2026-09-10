from __future__ import annotations

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.archive.manager import ArchiveManager
from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.database.connection import Database
from vehicle_dataset_manager.reid import OnnxReIDEngine, StubReID
from vehicle_dataset_manager.services.import_service import ImportService


def _context(workspace, settings):
    db = Database(workspace.database_path)
    archives = ArchiveManager()
    context = AppContext(
        workspace=workspace,
        settings=settings,
        db=db,
        archive_manager=archives,
        import_service=ImportService(db, archives, workspace),
    )
    return context


def test_reid_config_round_trip(workspace):
    settings = AppSettings()
    settings.models.reid = "onnx"
    settings.reid.model_path = "D:/models/vehicle_reid.onnx"
    settings.reid.input_width = 320
    settings.save(workspace.settings_path)
    restored = AppSettings.load(workspace.settings_path)
    assert restored.models.reid == "onnx"
    assert restored.reid.model_path == "D:/models/vehicle_reid.onnx"
    assert restored.reid.input_width == 320


def test_context_builds_onnx_lazily_and_falls_back_when_missing(workspace):
    model = workspace.models_dir / "unit-re3.onnx"
    model.write_bytes(b"model identity only; inference is lazy")
    settings = AppSettings()
    settings.models.vehicle_detector = "none"
    settings.models.plate_detector = "none"
    settings.models.ocr = "none"
    settings.models.reid = "onnx"
    settings.reid.model_path = str(model)
    context = _context(workspace, settings)
    try:
        assert isinstance(context.reid, OnnxReIDEngine)
        model.unlink()
        context.build_detectors()
        assert isinstance(context.reid, StubReID)
    finally:
        context.close()
        context.db.close()


def test_app_context_falls_back_when_model_missing(workspace):
    settings = AppSettings()
    settings.models.vehicle_detector = "none"
    settings.models.reid = "onnx"
    context = _context(workspace, settings)
    try:
        assert isinstance(context.reid, StubReID)
    finally:
        context.close()
        context.db.close()


def test_app_context_builds_lazy_onnx_engine(workspace):
    model = workspace.models_dir / "custom.onnx"
    model.write_bytes(b"placeholder-for-lazy-construction")
    settings = AppSettings()
    settings.models.vehicle_detector = "none"
    settings.models.reid = "onnx"
    settings.reid.model_path = str(model)
    settings.reid.input_width = 128
    context = _context(workspace, settings)
    try:
        assert isinstance(context.reid, OnnxReIDEngine)
        assert context.reid.preprocess_config.width == 128
    finally:
        context.close()
        context.db.close()
