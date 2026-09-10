"""Workspace layout management.

A Workspace is a plain folder (default ``%USERPROFILE%\\Documents\\VehicleDatasetManager``)
that holds the database, extracted images, crops, logs, etc. Original archives
are never modified. All derived artefacts are new files.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKSPACE_NAME = "VehicleDatasetManager"

#: Sub-directories created (if missing) under the workspace root.
WORKSPACE_SUBDIRS = (
    "database",
    "cache",
    "archives",
    "extracted",
    "crops",
    "plates",
    "reid",
    "exports",
    "logs",
    "models",
    "thumbs",
)


def default_workspace_root() -> Path:
    """Return the default workspace root under the user's Documents folder."""
    return Path.home() / "Documents" / DEFAULT_WORKSPACE_NAME


@dataclass
class Workspace:
    """Strongly-typed view over a workspace directory tree."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()

    # -- generic ---------------------------------------------------------
    def path(self, name: str) -> Path:
        return self.root / name

    def ensure(self) -> "Workspace":
        """Create the full directory layout. Idempotent. Returns self."""
        for sub in WORKSPACE_SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        return self

    # -- named directories ------------------------------------------------
    @property
    def database_dir(self) -> Path:
        return self.path("database")

    @property
    def cache_dir(self) -> Path:
        return self.path("cache")

    @property
    def archives_dir(self) -> Path:
        return self.path("archives")

    @property
    def extracted_dir(self) -> Path:
        return self.path("extracted")

    @property
    def crops_dir(self) -> Path:
        return self.path("crops")

    @property
    def plates_dir(self) -> Path:
        return self.path("plates")

    @property
    def reid_dir(self) -> Path:
        return self.path("reid")

    @property
    def exports_dir(self) -> Path:
        return self.path("exports")

    @property
    def logs_dir(self) -> Path:
        return self.path("logs")

    @property
    def models_dir(self) -> Path:
        return self.path("models")

    @property
    def thumbs_dir(self) -> Path:
        return self.path("thumbs")

    # -- key file locations ----------------------------------------------
    @property
    def database_path(self) -> Path:
        return self.database_dir / "vehicle_dataset.db"

    @property
    def settings_path(self) -> Path:
        return self.root / "settings.json"

    @property
    def log_app_path(self) -> Path:
        return self.logs_dir / "app.log"

    @property
    def log_processing_path(self) -> Path:
        return self.logs_dir / "processing.log"

    @property
    def log_error_path(self) -> Path:
        return self.logs_dir / "error.log"