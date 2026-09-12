from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vehicle_dataset_manager.services.frozen_pip import prepare_bundled_pip


def test_unknown_frozen_loader_is_registered_before_resource_lookup(monkeypatch, tmp_path):
    from pip._vendor import distlib
    from pip._vendor.distlib import resources

    class FrozenLoader:
        pass

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(distlib, "__loader__", FrozenLoader())
    monkeypatch.setattr(distlib, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(resources, "_finder_cache", {})
    monkeypatch.setattr(resources, "_finder_registry", dict(resources._finder_registry))
    for name in ("t64.exe", "w64.exe"):
        (tmp_path / name).write_bytes(b"MZ-test-resource")
    with pytest.raises(distlib.DistlibException, match="Unable to locate finder"):
        resources.finder(distlib.__name__)
    prepare_bundled_pip()
    assert resources.finder(distlib.__name__).find("t64.exe").bytes == b"MZ-test-resource"
    prepare_bundled_pip()  # repeated install remains safe


def test_missing_launcher_has_actionable_error(monkeypatch, tmp_path):
    from pip._vendor import distlib
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(distlib, "__file__", str(tmp_path / "__init__.py"))
    with pytest.raises(RuntimeError, match="t64.exe"):
        prepare_bundled_pip()


def test_source_run_needs_no_frozen_compatibility(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    prepare_bundled_pip()


def test_real_offline_install_and_windows_launcher(tmp_path):
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / "offline-install"
    proc = subprocess.run(
        [sys.executable, str(root / "run.py"), "--check-installer", str(target)],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((target / "report.json").read_text(encoding="utf-8"))
    assert len(report["runs"]) == 2
    assert all(run["success"] and run["installed"] and run["launcher_created"] for run in report["runs"])
    # Reusing a directory must not overwrite existing output.
    retry = subprocess.run(
        [sys.executable, str(root / "run.py"), "--check-installer", str(target)],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert retry.returncode != 0
    assert (target / "report.json").is_file()
