"""Activate optional packages installed beside the frozen application."""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_dir() -> Path:
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def cuda_runtime_root() -> Path:
    return application_dir() / "cuda-runtime"


def activate_portable_cuda_runtime(root: Path | None = None) -> Path | None:
    """Prepend the successfully installed CUDA package directory."""
    if root is None and not is_frozen_app():
        return None
    runtime_root = Path(root) if root is not None else cuda_runtime_root()
    marker = runtime_root / "active.txt"
    try:
        version_name = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not version_name or Path(version_name).name != version_name:
        return None
    versions = (runtime_root / "versions").resolve()
    target = (versions / version_name).resolve()
    try:
        target.relative_to(versions)
    except ValueError:
        return None
    if not (target / "torch" / "__init__.py").is_file():
        return None
    target_text = str(target)
    if target_text not in sys.path:
        sys.path.insert(0, target_text)
    return target
