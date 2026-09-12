"""Compatibility for pip's disk-backed resources in PyInstaller onedir builds."""
from __future__ import annotations

import sys
from pathlib import Path


def prepare_bundled_pip() -> None:
    """Register distlib's public filesystem finder before scripts is imported.

    --collect-all pip places launcher resources beside distlib.__file__.
    PyInstaller's loader is not in distlib's built-in finder registry.
    """
    if not getattr(sys, "frozen", False):
        return
    from pip._vendor import distlib
    from pip._vendor.distlib import resources

    base = Path(distlib.__file__).resolve().parent
    for name in ("t64.exe", "w64.exe"):
        path = base / name
        if not path.is_file():
            raise RuntimeError(f"封裝缺少 pip 啟動器資源：{name}；請重新下載完整程式資料夾。")
    resources.register_finder(distlib.__loader__, resources.ResourceFinder)
    # Validate that the registered finder can actually read the disk resource.
    launcher = resources.finder(distlib.__name__).find("t64.exe")
    if launcher is None or not launcher.bytes.startswith(b"MZ"):
        raise RuntimeError("封裝內的 pip 啟動器資源無效。")
