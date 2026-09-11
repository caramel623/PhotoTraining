"""Install the isolated Python 3.13 PaddleOCR runtime and selected models."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from vehicle_dataset_manager.core.portable_runtime import application_dir
from vehicle_dataset_manager.ocr.paddle_client import sidecar_source_root


@dataclass
class OcrInstallResult:
    success: bool
    returncode: int
    message: str
    python_path: str = ""


def _emit(line_cb: Optional[Callable[[str], None]], line: str) -> None:
    if line_cb is not None:
        try:
            line_cb(line)
        except Exception:
            pass


def _run(
    argv: list[str],
    line_cb: Optional[Callable[[str], None]] = None,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> tuple[int, str]:
    _emit(line_cb, "> " + " ".join(argv))
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(cwd) if cwd else None,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                if os.name == "nt" else 0
            ),
        )
    except OSError as exc:
        message = "無法啟動命令：" + str(exc)
        _emit(line_cb, message)
        return 127, message
    tail: list[str] = []
    for raw in proc.stdout or []:
        line = raw.rstrip("\r\n")
        if line:
            tail.append(line)
            if len(tail) > 80:
                tail.pop(0)
            _emit(line_cb, line)
    code = proc.wait()
    return code, "\n".join(tail[-40:])


def _probe_python(argv: list[str]) -> Optional[Path]:
    try:
        proc = subprocess.run(
            argv + ["-c", "import sys; print(sys.executable)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                if os.name == "nt" else 0
            ),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    path = Path(lines[-1])
    return path if path.is_file() else None


def find_python_313() -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA")
    candidates: list[list[str]] = []
    if local:
        candidates.append([str(Path(local) / "Programs" / "Python" / "Python313" / "python.exe")])
    candidates.extend([["py", "-3.13"], ["python3.13"]])
    for candidate in candidates:
        found = _probe_python(candidate)
        if found is not None:
            return found
    return None


def ensure_python_313(
    line_cb: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[Path], int, str]:
    found = find_python_313()
    if found is not None:
        _emit(line_cb, "Python 3.13：" + str(found))
        return found, 0, ""
    if os.name != "nt" or shutil.which("winget") is None:
        message = "找不到 Python 3.13，且無法使用 winget 自動安裝。"
        _emit(line_cb, message)
        return None, 127, message
    _emit(line_cb, "找不到 Python 3.13，開始透過 winget 安裝使用者版本。")
    code, tail = _run(
        [
            "winget", "install", "--id", "Python.Python.3.13", "-e",
            "--scope", "user", "--silent", "--source", "winget",
            "--accept-package-agreements", "--accept-source-agreements",
            "--disable-interactivity",
        ],
        line_cb=line_cb,
    )
    if code != 0:
        return None, code, tail or "winget 安裝 Python 3.13 失敗。"
    found = find_python_313()
    if found is None:
        message = "Python 3.13 安裝完成，但找不到 python.exe；請重新啟動程式後再試。"
        _emit(line_cb, message)
        return None, 2, message
    return found, 0, ""


def install_ocr_runtime(
    cache_dir: Path,
    preset: str = "mobile",
    line_cb: Optional[Callable[[str], None]] = None,
) -> OcrInstallResult:
    base_python, code, message = ensure_python_313(line_cb)
    if base_python is None:
        return OcrInstallResult(False, code, message)
    venv_dir = application_dir() / ".venv-ocr"
    venv_python = venv_dir / "Scripts" / "python.exe"
    if not venv_python.is_file():
        _emit(line_cb, "建立獨立 OCR 環境：" + str(venv_dir))
        code, tail = _run([str(base_python), "-m", "venv", str(venv_dir)], line_cb)
        if code != 0:
            return OcrInstallResult(False, code, tail or "建立 OCR 環境失敗。")
    commands = [
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        [
            str(venv_python), "-m", "pip", "install", "--upgrade",
            "paddlepaddle==3.3.1", "paddleocr==3.7.0", "paddlex==3.7.2",
        ],
    ]
    for command in commands:
        code, tail = _run(command, line_cb)
        if code != 0:
            return OcrInstallResult(False, code, tail or "安裝 OCR 套件失敗。", str(venv_python))
    source_root = sidecar_source_root()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source_root)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PADDLE_PDX_CACHE_HOME"] = str(cache_dir)
    _emit(line_cb, "下載並驗證 " + preset + " OCR 模型。首次下載可能需要較長時間。")
    code, tail = _run(
        [
            str(venv_python), "-m", "vehicle_dataset_manager.ocr.paddle_server",
            "--selftest", "--preset", preset,
        ],
        line_cb=line_cb,
        env=env,
        cwd=source_root,
    )
    if code != 0:
        return OcrInstallResult(False, code, tail or "OCR 模型下載或自測失敗。", str(venv_python))
    message = "PaddleOCR 依賴與 " + preset + " 模型已安裝完成。"
    _emit(line_cb, message)
    return OcrInstallResult(True, 0, message, str(venv_python))
