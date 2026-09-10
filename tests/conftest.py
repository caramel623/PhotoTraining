"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

# Keep Ultralytics' config/state in a writable temp dir so tests never touch
# %APPDATA% and work in restricted/sandbox environments.
os.environ.setdefault(
    "YOLO_CONFIG_DIR", os.path.join(tempfile.gettempdir(), "vdm_yolo_test")
)

# Ensure the package is importable when running pytest from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vehicle_dataset_manager.core.workspace import Workspace  # noqa: E402
from vehicle_dataset_manager.database.connection import Database  # noqa: E402


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path / "ws").ensure()


@pytest.fixture
def db(workspace):
    conn = Database(workspace.database_path)
    yield conn
    conn.close()


def make_image_array(w=300, h=200, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.random((h, w, 3)) * 255).astype(np.uint8)


@pytest.fixture
def image_factory():
    return make_image_array


@pytest.fixture
def sample_zip(tmp_path, image_factory):
    """Create a ZIP with 4 JPEGs under a year/camera subfolder; return its path."""
    import zipfile

    year_dir = tmp_path / "2024"
    year_dir.mkdir()
    for i in range(4):
        cv2.imwrite(str(year_dir / f"CamA_{i:03d}.jpg"), image_factory(300, 200, seed=i))
    zp = tmp_path / "2024.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for p in year_dir.iterdir():
            zf.write(p, "2024/" + p.name)
    return zp