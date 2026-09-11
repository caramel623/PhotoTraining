from __future__ import annotations

from pathlib import Path

from vehicle_dataset_manager.ocr import installer


def test_find_python_313_uses_first_working_candidate(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        installer, "_probe_python",
        lambda argv: python if "Python313" in argv[0] else None,
    )
    assert installer.find_python_313() == python


def test_ensure_python_reports_missing_winget(monkeypatch):
    monkeypatch.setattr(installer, "find_python_313", lambda: None)
    monkeypatch.setattr(installer.os, "name", "nt")
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    found, code, message = installer.ensure_python_313()
    assert found is None
    assert code == 127
    assert "winget" in message


def test_install_ocr_runtime_creates_venv_installs_packages_and_models(
    monkeypatch, tmp_path
):
    base_python = tmp_path / "python313.exe"
    base_python.write_bytes(b"")
    source_root = tmp_path / "sidecar_src"
    source_root.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    calls = []

    monkeypatch.setattr(
        installer, "ensure_python_313", lambda line_cb=None: (base_python, 0, "")
    )
    monkeypatch.setattr(installer, "application_dir", lambda: app_dir)
    monkeypatch.setattr(installer, "sidecar_source_root", lambda: source_root)

    def fake_run(argv, line_cb=None, env=None, cwd=None):
        calls.append((argv, env, cwd))
        if argv[1:3] == ["-m", "venv"]:
            venv_python = Path(argv[3]) / "Scripts" / "python.exe"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_bytes(b"")
        return 0, "ok"

    monkeypatch.setattr(installer, "_run", fake_run)
    result = installer.install_ocr_runtime(tmp_path / "cache", "server")

    assert result.success
    assert len(calls) == 4
    package_call = calls[2][0]
    assert "paddlepaddle==3.3.1" in package_call
    assert "paddleocr==3.7.0" in package_call
    model_call, model_env, model_cwd = calls[3]
    assert model_call[-2:] == ["--preset", "server"]
    assert model_env["PADDLE_PDX_CACHE_HOME"] == str(tmp_path / "cache")
    assert model_cwd == source_root


def test_install_ocr_runtime_does_not_claim_success_when_model_selftest_fails(
    monkeypatch, tmp_path
):
    app_dir = tmp_path / "app"
    venv_python = app_dir / ".venv-ocr" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_bytes(b"")
    source_root = tmp_path / "source"
    source_root.mkdir()
    monkeypatch.setattr(
        installer, "ensure_python_313", lambda line_cb=None: (tmp_path / "py.exe", 0, "")
    )
    monkeypatch.setattr(installer, "application_dir", lambda: app_dir)
    monkeypatch.setattr(installer, "sidecar_source_root", lambda: source_root)
    calls = {"count": 0}

    def fake_run(argv, line_cb=None, env=None, cwd=None):
        calls["count"] += 1
        return (9, "model failed") if "--selftest" in argv else (0, "ok")

    monkeypatch.setattr(installer, "_run", fake_run)
    result = installer.install_ocr_runtime(tmp_path / "cache", "mobile")
    assert not result.success
    assert result.returncode == 9
    assert "model failed" in result.message
