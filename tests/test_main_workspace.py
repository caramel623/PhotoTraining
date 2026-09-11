from pathlib import Path

from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.core.workspace import default_workspace_root
from vehicle_dataset_manager.main import resolve_startup_workspace


def test_explicit_workspace_wins_over_saved_workspace(tmp_path):
    explicit = tmp_path / "explicit"
    other = tmp_path / "other"
    explicit.mkdir()
    settings = AppSettings()
    settings.paths.workspace = str(other)
    settings.processing.worker_count = 3
    settings.save(explicit / "settings.json")

    workspace, loaded = resolve_startup_workspace(str(explicit))

    assert workspace.root == explicit.resolve()
    assert loaded.processing.worker_count == 3
    assert not other.exists()
    assert workspace.database_dir.is_dir()


def test_packaged_default_workspace_is_executable_directory(monkeypatch, tmp_path):
    executable = tmp_path / "VehicleDatasetManager.exe"
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(executable))

    assert default_workspace_root() == tmp_path.resolve()


def test_unconfigured_startup_uses_application_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "vehicle_dataset_manager.main._default_root",
        lambda: tmp_path,
    )

    workspace, loaded = resolve_startup_workspace(None)

    assert workspace.root == tmp_path.resolve()
    assert loaded.paths.workspace is None
    assert workspace.database_dir.is_dir()
