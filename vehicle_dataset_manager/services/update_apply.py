"""Post-exit portable update helper. Never removes a workspace or user files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from vehicle_dataset_manager.services.app_update import EXE, MANIFEST, validate_payload


def apply_files(staged, target, version, copy=shutil.copytree):
    staged, target = Path(staged).resolve(), Path(target).resolve()
    if target == Path(target.anchor) or target == Path.home().resolve():
        raise ValueError("拒絕廣泛的安裝目錄")
    if target == staged or target.is_relative_to(staged) or staged.is_relative_to(target / "_internal"):
        raise ValueError("更新來源與目標重疊")
    if (target / ".git").exists() or not (target / EXE).is_file():
        raise ValueError("目標不是已安裝的可攜版程式")
    for name in (EXE, "_internal", MANIFEST, ".updates"):
        path = target / name
        if path.is_symlink() or os.path.isjunction(path) or (path.exists() and not path.resolve().is_relative_to(target)):
            raise ValueError("安裝目錄含連結，已取消更新")
    validate_payload(staged, version)
    required = sum(p.stat().st_size for p in staged.rglob("*") if p.is_file())
    if shutil.disk_usage(target).free < required + 256 * 1024**2:
        raise ValueError("安裝磁碟可用空間不足，未修改程式")
    # A nonstandard workspace inside the managed runtime must not be replaced.
    internal = target / "_internal"
    if internal.exists():
        for path in internal.rglob("*"):
            if path.is_symlink() or os.path.isjunction(path) or (path.is_file() and (
                path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".7z"}
                or path.name.lower() == "settings.json")):
                raise ValueError("_internal 含自訂資料或連結，請改用手動更新")
    backup = target / ".updates" / ("backup-" + uuid4().hex)
    backup.mkdir(parents=True, exist_ok=False)
    moved, attempted = [], []
    names = (EXE, "_internal", MANIFEST)
    try:
        for name in names:
            old = target / name
            if old.exists():
                old.rename(backup / name)
                moved.append(name)
        for name in names:
            attempted.append(name)
            source, dest = staged / name, target / name
            if source.is_dir():
                copy(source, dest)
            else:
                shutil.copy2(source, dest)
        # Verify installed program files, while leaving root-level models and
        # every other existing user file outside the managed set untouched.
        manifest = json.loads((staged / MANIFEST).read_text(encoding="utf-8"))
        from vehicle_dataset_manager.services.app_update import sha256
        for name, digest in manifest["files"].items():
            if name == EXE or name.startswith("_internal/"):
                if sha256(target / name) != digest:
                    raise ValueError("安裝後校驗失敗")
    except BaseException:
        failed = backup / "failed-new-files"
        failed.mkdir()
        for name in reversed(attempted):
            path = target / name
            if path.exists():
                path.rename(failed / name)
        for name in reversed(moved):
            (backup / name).rename(target / name)
        raise
    return backup


def helper_main(request_path):
    import ctypes
    from ctypes import wintypes

    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("自動替換只支援 Windows EXE")
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    staged = Path(sys.executable).resolve().parent
    target = Path(request["target"]).resolve()
    workspace = Path(request["workspace"]).resolve()
    validate_payload(staged, request["version"])
    if request_path.parent.parent != target / ".updates":
        raise ValueError("更新請求不在指定程式的隔離目錄")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, int(request["parent_pid"]))
    if not handle:
        raise RuntimeError("無法確認主程式仍在執行，未套用更新")
    try:
        (request_path.parent / "ready").write_text("ready", encoding="ascii")
        if kernel.WaitForSingleObject(handle, 120000) != 0:
            raise RuntimeError("等待主程式結束逾時，未套用更新")
    finally:
        kernel.CloseHandle(handle)
    result = {"success": False}
    try:
        backup = apply_files(staged, target, request["version"])
        result = {"success": True, "backup": str(backup)}
    except Exception as exc:
        result["error"] = str(exc)
    (request_path.parent / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Restore startup workspace; never start the app from the staging directory.
    subprocess.Popen([str(target / EXE), "--workspace", str(workspace)], cwd=str(target))
    return 0 if result["success"] else 1


def launch_helper(staged, target, workspace, version):
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise ValueError("原始碼模式僅提供版本檢查；自動更新需使用 EXE")
    target = Path(target).resolve()
    if target != Path(sys.executable).resolve().parent:
        raise ValueError("只能更新目前執行的 EXE")
    validate_payload(Path(staged), version)
    directory = target / ".updates" / ("request-" + uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    request = directory / "request.json"
    request.write_text(json.dumps({
        "target": str(target), "workspace": str(Path(workspace).resolve()),
        "version": version, "parent_pid": os.getpid(),
    }), encoding="utf-8")
    process = subprocess.Popen([str(Path(staged) / EXE), "--apply-update", str(request)],
                               cwd=str(staged), creationflags=subprocess.CREATE_NO_WINDOW)
    return directory, process
