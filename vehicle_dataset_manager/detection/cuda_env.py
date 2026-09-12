"""CUDA environment detection & dependency install (CPU / CUDA).

When the user enables "Use CUDA" in Settings, this module lets the app:

1. *Detect* the compute environment: is PyTorch present, is it a CPU or CUDA
   build, is an NVIDIA GPU / driver visible (``nvidia-smi``), and is CUDA
   actually usable right now.
2. *Download / install* the matching CUDA build of PyTorch + torchvision.
   Source runs use the active interpreter; frozen builds use bundled pip and
   a versioned runtime beside the EXE. Output is streamed to the UI.

The module is **Qt-free** and importable without the ML stack: torch is
imported lazily and NVIDIA probing is performed through ``nvidia-smi``.
That keeps it fully unit-testable headlessly on machines without CUDA (e.g. the
AMD dev box) and safe to call on any machine.
"""
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from vehicle_dataset_manager.core.portable_runtime import (
    cuda_runtime_root,
    is_frozen_app,
)

from vehicle_dataset_manager.detection.device import (
    _torch,
    cuda_available,
    gpu_name,
)

#: Default PyTorch CUDA wheel index. Verified against the local torch version
#: (torch 2.14.0 -> ``cu126`` is the latest on the official index). Override
#: from Settings (``device.cuda_wheel_index``) if a different CUDA build is
#: required.
DEFAULT_CUDA_WHEEL_INDEX = "cu126"

#: Default timeout (seconds) for probing ``nvidia-smi``.
_NVIDIA_TIMEOUT = 15


class CudaStatus(str, Enum):
    """Diagnosed state of the CUDA environment."""

    READY = "ready"
    TORCH_CPU_BUILD = "torch_cpu_build"
    NO_TORCH = "no_torch"
    NO_GPU = "no_gpu"
    CUDA_UNAVAILABLE = "cuda_unavailable"


@dataclass
class NvidiaInfo:
    """Result of probing ``nvidia-smi``."""

    found: bool = False
    gpu_name: str = ""
    driver_version: str = ""


@dataclass
class CudaEnvironmentReport:
    """Snapshot of the CUDA environment + a recommended action."""

    torch_installed: bool = False
    torch_version: str = ""
    torch_cuda_version: Optional[str] = None  # bundled CUDA ("12.6"); None => CPU build
    torch_is_cpu_build: bool = True
    cuda_available: bool = False
    nvidia: NvidiaInfo = field(default_factory=NvidiaInfo)
    gpu_name: str = ""
    status: CudaStatus = CudaStatus.NO_GPU
    message: str = ""
    pip_command: str = ""


@dataclass
class InstallResult:
    """Outcome of a ``pip`` install run."""

    success: bool
    returncode: int
    output_tail: str = ""


def _clean_cuda_index(cuda_wheel_index: Optional[str]) -> str:
    idx = (cuda_wheel_index or "").strip()
    if not idx:
        return DEFAULT_CUDA_WHEEL_INDEX
    return idx


def build_pip_argv(
    cuda_wheel_index: Optional[str] = None,
    python: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    """Return the argv that installs the CUDA build of torch + torchvision.

    Uses the current interpreter (``sys.executable``) by default so the wheel
    lands in the active venv. ``--index-url`` points at the official PyTorch
    CUDA index (e.g. ``https://download.pytorch.org/whl/cu126``).
    """
    idx = _clean_cuda_index(cuda_wheel_index)
    interpreter = python or sys.executable
    argv = [
        interpreter,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "torch",
        "torchvision",
        "--index-url",
        "https://download.pytorch.org/whl/" + idx,
    ]
    if extra_args:
        argv = argv + list(extra_args)
    return argv


def _shell_quote(tok: str) -> str:
    """Quote a single argv token for a Windows shell (only when it has spaces)."""
    if tok and (" " in tok or "\t" in tok):
        return "'" + tok + "'"
    return tok


def build_pip_command(
    cuda_wheel_index: Optional[str] = None,
    python: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> str:
    """Human/clipboard-friendly form of :func:`build_pip_argv`."""
    if python is None and is_frozen_app():
        return "使用本頁的下載/安裝按鈕；套件會安裝到程式資料夾的 cuda-runtime。"
    return " ".join(_shell_quote(tok) for tok in build_pip_argv(cuda_wheel_index, python, extra_args))


def _portable_pip_args(cuda_wheel_index: Optional[str], target: Path) -> List[str]:
    idx = _clean_cuda_index(cuda_wheel_index)
    return [
        "install",
        "--upgrade",
        "--no-deps",
        "--target",
        str(target),
        "torch",
        "torchvision",
        "--index-url",
        "https://download.pytorch.org/whl/" + idx,
    ]


class _PipOutput(io.TextIOBase):
    def __init__(self, line_cb: Optional[Callable[[str], None]]) -> None:
        self.line_cb = line_cb
        self.lines: List[str] = []
        self.buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        text = str(value)
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._emit(line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        if self.buffer:
            self._emit(self.buffer.rstrip("\r"))
            self.buffer = ""

    def _emit(self, line: str) -> None:
        if not line:
            return
        self.lines.append(line)
        if len(self.lines) > 200:
            self.lines.pop(0)
        if self.line_cb is not None:
            self.line_cb(line)


def _run_pip_in_process(
    args: List[str],
    line_cb: Optional[Callable[[str], None]] = None,
) -> InstallResult:
    output = _PipOutput(line_cb)
    try:
        from vehicle_dataset_manager.services.frozen_pip import prepare_bundled_pip
        prepare_bundled_pip()
        from pip._internal.cli.main import main as pip_main
    except Exception as exc:
        message = "內建 pip 無法載入：" + str(exc)
        if line_cb is not None:
            line_cb(message)
        return InstallResult(False, 127, message)
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = int(pip_main(args) or 0)
    except Exception as exc:
        output.write("安裝器發生例外：" + str(exc))
        code = 1
    finally:
        output.flush()
    return InstallResult(code == 0, code, "\n".join(output.lines[-40:]))


def install_portable_cuda_torch(
    cuda_wheel_index: Optional[str] = None,
    line_cb: Optional[Callable[[str], None]] = None,
) -> InstallResult:
    """Install a versioned CUDA runtime beside a frozen onedir app."""
    runtime_root = cuda_runtime_root()
    versions_dir = runtime_root / "versions"
    target = versions_dir / ("cuda-" + uuid.uuid4().hex)
    target.mkdir(parents=True, exist_ok=False)
    result = _run_pip_in_process(
        _portable_pip_args(cuda_wheel_index, target),
        line_cb=line_cb,
    )
    if result.success and (target / "torch" / "__init__.py").is_file():
        runtime_root.mkdir(parents=True, exist_ok=True)
        marker_tmp = runtime_root / "active.txt.tmp"
        marker_tmp.write_text(target.name, encoding="utf-8")
        marker_tmp.replace(runtime_root / "active.txt")
        if line_cb is not None:
            line_cb("CUDA runtime 已安裝；重新啟動程式後生效。")
        return result
    shutil.rmtree(target, ignore_errors=True)
    if result.success:
        message = "pip 回報成功，但安裝內容缺少 torch；未切換 runtime。"
        if line_cb is not None:
            line_cb(message)
        return InstallResult(False, 1, message)
    return result


def probe_nvidia_smi(timeout: float = _NVIDIA_TIMEOUT) -> NvidiaInfo:
    """Query ``nvidia-smi`` for the GPU name + driver version.

    Returns ``NvidiaInfo(found=False)`` when the driver / binary is absent
    (e.g. AMD machines) or the call fails -- never raises.
    """
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,driver_version",
        "--format=csv,noheader",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return NvidiaInfo(found=False)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return NvidiaInfo(found=False)
    first = proc.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    name = parts[0] if len(parts) > 0 else ""
    driver = parts[1] if len(parts) > 1 else ""
    return NvidiaInfo(found=True, gpu_name=name, driver_version=driver)


def detect_environment(
    cuda_wheel_index: Optional[str] = None,
    nvidia_timeout: float = _NVIDIA_TIMEOUT,
) -> CudaEnvironmentReport:
    """Detect the CUDA environment and build a recommended action.

    No side effects beyond the lazy torch import + an ``nvidia-smi`` probe.
    Safe to call repeatedly and headlessly.
    """
    report = CudaEnvironmentReport()
    nvidia = probe_nvidia_smi(timeout=nvidia_timeout)
    report.nvidia = nvidia

    torch = _torch()
    if torch is None:
        report.torch_installed = False
        report.torch_is_cpu_build = True
        report.status = CudaStatus.NO_TORCH
        report.pip_command = build_pip_command(cuda_wheel_index)
        report.message = "未偵測到 PyTorch。若要啟用 CUDA，請先安裝 CUDA 版 PyTorch（含 torchvision）。"
        return report

    report.torch_installed = True
    report.torch_version = str(getattr(torch, "__version__", ""))
    report.torch_cuda_version = getattr(getattr(torch, "version", None), "cuda", None)
    report.torch_is_cpu_build = report.torch_cuda_version is None
    avail = cuda_available()
    report.cuda_available = avail
    report.gpu_name = nvidia.gpu_name or (gpu_name() if avail else "")

    if avail:
        report.status = CudaStatus.READY
        report.message = "CUDA 可用（" + (report.gpu_name or "NVIDIA GPU") + "）。可啟用 Use CUDA。"
        report.pip_command = ""
        return report

    if nvidia.found:
        if report.torch_is_cpu_build:
            report.status = CudaStatus.TORCH_CPU_BUILD
            report.pip_command = build_pip_command(cuda_wheel_index)
            report.message = (
                "偵測到 NVIDIA GPU（" + nvidia.gpu_name + "、驅動 " + nvidia.driver_version + "），"
                "但目前安裝的是 CPU 版 PyTorch（" + report.torch_version + "）。"
                "請安裝 CUDA 版 PyTorch 以啟用加速。"
            )
        else:
            report.status = CudaStatus.CUDA_UNAVAILABLE
            report.pip_command = build_pip_command(cuda_wheel_index)
            report.message = (
                "NVIDIA 驅動（" + nvidia.driver_version + "）已安裝、PyTorch 亦為 CUDA 版"
                "（CUDA " + str(report.torch_cuda_version) + "），但 CUDA 仍不可用。"
                "可能是驅動版本與 PyTorch 附帶的 CUDA 不相容：請更新 NVIDIA 驅動，"
                "或在設定中改用相符的 CUDA 版本（cuda_wheel_index）。"
            )
    else:
        report.status = CudaStatus.NO_GPU
        report.pip_command = build_pip_command(cuda_wheel_index)
        report.message = (
            "未偵測到 NVIDIA GPU／驅動（找不到 nvidia-smi）。"
            "請先安裝 NVIDIA 驅動；若機器確為 NVIDIA，可先下載 CUDA 版 PyTorch。"
        )
    return report


def run_command(
    argv: List[str],
    line_cb: Optional[Callable[[str], None]] = None,
    timeout: Optional[float] = None,
) -> InstallResult:
    """Run ``argv`` in a subprocess, streaming each output line to ``line_cb``.

    Returns an :class:`InstallResult`. Output is UTF-8 with replacement for any
    non-text bytes; the last ~40 lines are kept in ``output_tail``.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail: List[str] = []
    try:
        for raw in proc.stdout:  # type: ignore[union-attr]
            line = raw.rstrip("\n")
            tail.append(line)
            if len(tail) > 200:
                tail.pop(0)
            if line_cb is not None:
                try:
                    line_cb(line)
                except Exception:  # noqa: BLE001 - never let a UI cb kill the install
                    pass
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait()
    return InstallResult(success=(code == 0), returncode=code, output_tail="\n".join(tail[-40:]))


def install_cuda_torch(
    cuda_wheel_index: Optional[str] = None,
    python: Optional[str] = None,
    line_cb: Optional[Callable[[str], None]] = None,
    extra_args: Optional[List[str]] = None,
    timeout: Optional[float] = None,
) -> InstallResult:
    """Install the CUDA build of torch + torchvision via ``pip``.

    Thin wrapper over :func:`run_command` + :func:`build_pip_argv`.
    """
    if python is None and is_frozen_app():
        return install_portable_cuda_torch(cuda_wheel_index, line_cb=line_cb)
    argv = build_pip_argv(cuda_wheel_index, python, extra_args)
    return run_command(argv, line_cb=line_cb, timeout=timeout)
