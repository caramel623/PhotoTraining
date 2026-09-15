import json
from pathlib import Path
import shutil
import zipfile

import pytest

from vehicle_dataset_manager.services.app_update import (
    EXE, MANIFEST, REPO_URL, check_updates, extract_release, sha256, validate_payload,
)
from vehicle_dataset_manager.services.update_apply import apply_files


def payload(root):
    root.mkdir()
    (root / EXE).write_bytes(b"new exe")
    (root / "_internal").mkdir()
    (root / "_internal" / "runtime.dll").write_bytes(b"new runtime")
    (root / MANIFEST).write_text(json.dumps({
        "protocol": 1, "version": "0.0.10",
        "files": {p.relative_to(root).as_posix(): sha256(p) for p in root.rglob("*") if p.is_file()},
    }))
    return root


def installed(root):
    root.mkdir()
    (root / EXE).write_bytes(b"old exe")
    (root / "_internal").mkdir()
    (root / "_internal" / "runtime.dll").write_bytes(b"old runtime")
    for name in ("photos.jpg", "settings.json", "vehicle.db"):
        (root / name).write_bytes(b"private")
    for name in ("database", "models", ".venv-ocr", "cuda-runtime"):
        (root / name).mkdir()
        (root / name / "keep").write_bytes(b"private")
    return root


def private_unchanged(root):
    for name in ("photos.jpg", "settings.json", "vehicle.db"):
        assert (root / name).read_bytes() == b"private"
    for name in ("database", "models", ".venv-ocr", "cuda-runtime"):
        assert (root / name / "keep").read_bytes() == b"private"


def test_success_preserves_private_files_and_backup(tmp_path):
    source = payload(tmp_path / "stage")
    target = installed(tmp_path / "installed")
    backup = apply_files(source, target, "v0.0.10")
    assert (target / EXE).read_bytes() == b"new exe"
    assert (backup / EXE).read_bytes() == b"old exe"
    private_unchanged(target)


def test_failed_copy_restores_old_program(tmp_path):
    source = payload(tmp_path / "stage")
    target = installed(tmp_path / "installed")
    def fail(source, destination):
        destination.mkdir()
        (destination / "partial").write_bytes(b"partial")
        raise OSError("simulated full disk")
    with pytest.raises(OSError):
        apply_files(source, target, "0.0.10", copy=fail)
    assert (target / EXE).read_bytes() == b"old exe"
    assert (target / "_internal" / "runtime.dll").read_bytes() == b"old runtime"
    private_unchanged(target)


def test_hash_failure_never_moves_installed_program(tmp_path):
    source = payload(tmp_path / "stage")
    target = installed(tmp_path / "installed")
    (source / EXE).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        apply_files(source, target, "0.0.10")
    assert (target / EXE).read_bytes() == b"old exe"
    assert not (target / ".updates").exists()


@pytest.mark.parametrize("name", [
    "../escape", "VehicleDatasetManager/../escape",
    "VehicleDatasetManager/C:/escape", "VehicleDatasetManager/CON",
    "VehicleDatasetManager/a.", "VehicleDatasetManager/a\\b",
])
def test_rejects_zip_traversal_and_windows_aliases(tmp_path, name):
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, b"bad")
    with pytest.raises(ValueError):
        extract_release(archive, tmp_path / "out", "0.0.10")


def test_valid_zip_and_duplicate_rejection(tmp_path):
    source = payload(tmp_path / "stage")
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for p in source.rglob("*"):
            if p.is_file():
                z.write(p, "VehicleDatasetManager/" + p.relative_to(source).as_posix())
    extracted = extract_release(archive, tmp_path / "out", "v0.0.10")
    assert validate_payload(extracted, "0.0.10")["protocol"] == 1
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("VehicleDatasetManager/VEHICLEDATASETMANAGER.EXE", b"alias")
    with pytest.raises(ValueError):
        extract_release(archive, tmp_path / "out2", "0.0.10")


def test_no_downgrade_and_commit_direction():
    def fetch(path):
        if path == "/commits/main":
            return {"sha": "b" * 40}
        if path.startswith("/compare"):
            return {"status": "behind"}
        return {"tag_name": "v0.0.7", "assets": []}
    result = check_updates("0.0.9", "a" * 40, fetch)
    assert not result["new_release"]
    assert result["commit_status"] == "behind"


def test_independent_commit_failure_and_strict_asset():
    def fetch(path):
        if path == "/commits/main":
            raise OSError("offline")
        name = "VehicleDatasetManager-v0.0.10-windows-x64.zip"
        return {"tag_name": "v0.0.10", "assets": [{
            "name": name, "size": 123, "digest": "sha256:" + "a" * 64,
            "browser_download_url": f"{REPO_URL}/releases/download/v0.0.10/{name}",
        }]}
    result = check_updates("0.0.9", fetch=fetch)
    assert result["new_release"] and result["asset"] and result["errors"]


def test_stage_reports_download_extract_and_validation(tmp_path, monkeypatch):
    import io
    from vehicle_dataset_manager.services import app_update
    source = payload(tmp_path / "source")
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for path in source.rglob("*"):
            if path.is_file():
                package.write(path, "VehicleDatasetManager/" + path.relative_to(source).as_posix())
    url = REPO_URL + "/releases/download/v0.0.10/VehicleDatasetManager-v0.0.10-windows-x64.zip"
    class Response(io.BytesIO):
        def geturl(self):
            return url
    monkeypatch.setattr(app_update.urllib.request, "urlopen",
                        lambda *a, **k: Response(archive.read_bytes()))
    messages = []
    result = app_update.stage_release({
        "new_release": True, "current_version": "0.0.9", "release": "v0.0.10",
        "asset": {"browser_download_url": url, "size": archive.stat().st_size,
                  "digest": "sha256:" + sha256(archive)},
    }, tmp_path / "cache", messages.append)
    assert (result / EXE).is_file()
    assert any("100%" in m and "MB/s" in m for m in messages)
    assert any("SHA-256" in m for m in messages)
    assert any("解壓縮：" in m for m in messages)
    assert any("校驗程式檔案：" in m for m in messages)
    assert "等待確認" in messages[-1]


def test_update_panel_progress_and_waiting_hint():
    import time
    from PySide6.QtWidgets import QApplication
    from vehicle_dataset_manager.ui.update_panel import UpdatePanel
    app = QApplication.instance() or QApplication([])
    panel = UpdatePanel(None, lambda: False)
    panel._begin_activity("正在下載")
    panel.last_activity -= 16
    panel._activity_tick()
    assert "沒有新進度" in panel.status.text()
    panel._progress("正在解壓縮：50 / 100 個檔案")
    assert "50 / 100" in panel.status.text()
    assert "沒有新進度" not in panel.status.text()
    panel._end_activity()
    assert not panel.activity_timer.isActive()
    received = []
    def work(progress):
        progress("背景工作進度")
        time.sleep(0.05)
        return "done"
    panel._start(work, received.append)
    deadline = time.monotonic() + 5
    while panel.is_working and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    panel.task.wait(5000)
    app.processEvents()
    assert received == ["done"]
    assert panel.phase == "背景工作進度"
    assert not panel.activity_timer.isActive()
    panel.close()
