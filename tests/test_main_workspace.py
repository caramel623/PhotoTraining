from pathlib import Path

from vehicle_dataset_manager.core.config import AppSettings
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
