"""Headless tests for the CUDA environment detect / install module.

These run on any machine (no GPU / no network needed) by monkeypatching the
lazy torch import and ``subprocess.run``/``Popen``.
"""
from __future__ import annotations

import sys

import pytest

from vehicle_dataset_manager.detection import cuda_env


class FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


# -- command building ----------------------------------------------------
def test_build_pip_argv_default():
    argv = cuda_env.build_pip_argv(cuda_wheel_index="cu126")
    assert argv[0]  # interpreter present
    assert "-m" in argv and "pip" in argv
    assert "torch" in argv and "torchvision" in argv
    assert "--index-url" in argv
    url = argv[argv.index("--index-url") + 1]
    assert url == "https://download.pytorch.org/whl/cu126"


def test_build_pip_argv_empty_falls_back_to_default():
    argv = cuda_env.build_pip_argv(cuda_wheel_index="")
    url = argv[argv.index("--index-url") + 1]
    assert url.endswith("/cu126")


def test_build_pip_command_contains_url():
    cmd = cuda_env.build_pip_command(cuda_wheel_index="cu128")
    assert "cu128" in cmd and "pip" in cmd and "torch" in cmd


def test_build_pip_command_frozen_uses_internal_installer(monkeypatch):
    monkeypatch.setattr(cuda_env, "is_frozen_app", lambda: True)
    cmd = cuda_env.build_pip_command(cuda_wheel_index="cu126")
    assert "下載/安裝" in cmd
    assert "-m pip" not in cmd


def test_build_pip_argv_extra_args():
    argv = cuda_env.build_pip_argv(cuda_wheel_index="cu126", extra_args=["--no-cache-dir"])
    assert "--no-cache-dir" in argv


# -- nvidia-smi probing --------------------------------------------------
def test_probe_nvidia_smi_found(monkeypatch):
    monkeypatch.setattr(
        cuda_env.subprocess, "run",
        lambda *a, **k: FakeProc(0, "NVIDIA RTX A5000, 551.23\n"),
    )
    info = cuda_env.probe_nvidia_smi()
    assert info.found is True
    assert info.gpu_name == "NVIDIA RTX A5000"
    assert info.driver_version == "551.23"


def test_probe_nvidia_smi_absent(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(cuda_env.subprocess, "run", boom)
    assert cuda_env.probe_nvidia_smi().found is False


def test_probe_nvidia_smi_nonzero_exit(monkeypatch):
    monkeypatch.setattr(cuda_env.subprocess, "run", lambda *a, **k: FakeProc(3, ""))
    assert cuda_env.probe_nvidia_smi().found is False


# -- detection matrix ----------------------------------------------------
class _Ver:
    def __init__(self, cuda):
        self.cuda = cuda


class _Torch:
    def __init__(self, ver, cuda):
        self.__version__ = ver
        self.version = _Ver(cuda)


def _patch_torch(monkeypatch, torch, avail, name):
    monkeypatch.setattr(cuda_env, "_torch", lambda: torch)
    monkeypatch.setattr(cuda_env, "cuda_available", lambda: avail)
    monkeypatch.setattr(cuda_env, "gpu_name", lambda: name)


def test_detect_ready(monkeypatch):
    _patch_torch(monkeypatch, _Torch("2.14.0+cu126", "12.6"), True, "NVIDIA RTX A5000")
    monkeypatch.setattr(
        cuda_env.subprocess, "run",
        lambda *a, **k: FakeProc(0, "NVIDIA RTX A5000, 551.23\n"),
    )
    rep = cuda_env.detect_environment()
    assert rep.status == cuda_env.CudaStatus.READY
    assert rep.pip_command == ""
    assert rep.cuda_available is True
    assert rep.torch_is_cpu_build is False


def test_detect_cpu_build_with_gpu(monkeypatch):
    _patch_torch(monkeypatch, _Torch("2.14.0+cpu", None), False, "")
    monkeypatch.setattr(
        cuda_env.subprocess, "run",
        lambda *a, **k: FakeProc(0, "NVIDIA RTX A5000, 551.23\n"),
    )
    rep = cuda_env.detect_environment()
    assert rep.status == cuda_env.CudaStatus.TORCH_CPU_BUILD
    assert "cu126" in rep.pip_command


def test_detect_no_torch(monkeypatch):
    monkeypatch.setattr(cuda_env, "_torch", lambda: None)
    monkeypatch.setattr(cuda_env.subprocess, "run", lambda *a, **k: FakeProc(3, ""))
    rep = cuda_env.detect_environment()
    assert rep.status == cuda_env.CudaStatus.NO_TORCH
    assert rep.pip_command != ""


def test_detect_no_gpu(monkeypatch):
    _patch_torch(monkeypatch, _Torch("2.14.0+cpu", None), False, "")
    monkeypatch.setattr(cuda_env.subprocess, "run", lambda *a, **k: FakeProc(3, ""))
    rep = cuda_env.detect_environment()
    assert rep.status == cuda_env.CudaStatus.NO_GPU


def test_detect_cuda_unavailable_driver_mismatch(monkeypatch):
    # torch is a CUDA build + driver present, but torch says cuda unavailable.
    _patch_torch(monkeypatch, _Torch("2.14.0+cu126", "12.6"), False, "")
    monkeypatch.setattr(
        cuda_env.subprocess, "run",
        lambda *a, **k: FakeProc(0, "NVIDIA RTX A5000, 551.23\n"),
    )
    rep = cuda_env.detect_environment()
    assert rep.status == cuda_env.CudaStatus.CUDA_UNAVAILABLE


# -- command runner (real subprocess, tiny script) -----------------------
def test_run_command_streams_lines(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("print('hello')\nprint('world')\n", encoding="utf-8")
    lines = []
    res = cuda_env.run_command([sys.executable, str(script)], line_cb=lines.append)
    assert res.success is True
    assert "hello" in lines and "world" in lines
    assert "world" in res.output_tail


def test_run_command_failure_returncode(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    res = cuda_env.run_command([sys.executable, str(script)])
    assert res.success is False
    assert res.returncode == 2


def test_install_cuda_torch_uses_pip_index(tmp_path):
    # Point at the real python, but override argv via a tiny echo of the argv.
    script = tmp_path / "s.py"
    script.write_text("import sys\nfor a in sys.argv[1:]:\n    print(a)\n", encoding="utf-8")
    captured = []
    # Run our real argv (python -m pip ...) but replace pip with a stub that prints argv:
    argv = cuda_env.build_pip_argv("cu126", python=sys.executable)
    # Swap the '-m pip ...' tail for a script that just echoes the index arg.
    argv2 = [sys.executable, str(script), "cu126-index"]
    res = cuda_env.run_command(argv2, line_cb=captured.append)
    assert res.success is True
    assert "cu126-index" in captured
    # And the real builder still references the index:
    assert any("cu126" in tok for tok in argv)


def test_frozen_install_uses_versioned_target_and_switches_after_success(
    monkeypatch, tmp_path
):
    runtime_root = tmp_path / "cuda-runtime"
    captured = {}

    def fake_pip(args, line_cb=None):
        captured["args"] = args
        target = __import__("pathlib").Path(args[args.index("--target") + 1])
        torch_dir = target / "torch"
        torch_dir.mkdir(parents=True)
        (torch_dir / "__init__.py").write_text("", encoding="utf-8")
        return cuda_env.InstallResult(True, 0, "ok")

    monkeypatch.setattr(cuda_env, "is_frozen_app", lambda: True)
    monkeypatch.setattr(cuda_env, "cuda_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(cuda_env, "_run_pip_in_process", fake_pip)
    result = cuda_env.install_cuda_torch("cu126")
    assert result.success
    assert "--no-deps" in captured["args"]
    assert captured["args"][-1].endswith("/cu126")
    active = (runtime_root / "active.txt").read_text(encoding="utf-8")
    assert (runtime_root / "versions" / active / "torch" / "__init__.py").is_file()


def test_frozen_install_failure_does_not_activate_partial_runtime(monkeypatch, tmp_path):
    runtime_root = tmp_path / "cuda-runtime"
    monkeypatch.setattr(cuda_env, "is_frozen_app", lambda: True)
    monkeypatch.setattr(cuda_env, "cuda_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(
        cuda_env,
        "_run_pip_in_process",
        lambda args, line_cb=None: cuda_env.InstallResult(False, 3, "failed"),
    )
    result = cuda_env.install_cuda_torch("cu126")
    assert not result.success
    assert not (runtime_root / "active.txt").exists()
    assert list((runtime_root / "versions").iterdir()) == []
