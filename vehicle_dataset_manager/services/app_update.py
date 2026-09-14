"""Public GitHub update discovery and strictly validated portable release staging."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPO = "caramel623/PhotoTraining"
API = f"https://api.github.com/repos/{REPO}"
REPO_URL = f"https://github.com/{REPO}"
EXE = "VehicleDatasetManager.exe"
MANIFEST = "update-manifest.json"
MAX_ZIP = 2 * 1024**3
MAX_EXPANDED = 6 * 1024**3


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value or "")
    if not match:
        raise ValueError("僅接受正式版三段版本號")
    return tuple(map(int, match.groups()))


def api_json(path):
    request = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "VehicleDatasetManager-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(4 * 1024**2 + 1)
    if len(data) > 4 * 1024**2:
        raise ValueError("GitHub 回應過大")
    return json.loads(data)


def check_updates(current_version, current_commit=None, fetch=api_json):
    result = {"current_version": current_version, "current_commit": current_commit,
              "commit": None, "release": None, "asset": None, "errors": [],
              "new_release": False, "commit_status": "unknown"}
    try:
        commit = fetch("/commits/main")
        sha = commit["sha"]
        if not re.fullmatch("[0-9a-f]{40}", sha):
            raise ValueError("commit 格式不正確")
        result["commit"] = sha
        if current_commit == sha:
            result["commit_status"] = "identical"
        elif current_commit and re.fullmatch("[0-9a-f]{40}", current_commit):
            try:
                result["commit_status"] = fetch(f"/compare/{current_commit}...{sha}")["status"]
            except Exception:
                result["commit_status"] = "unknown"
    except Exception as exc:
        result["errors"].append(f"Commit 查詢失敗：{exc}")
    try:
        release = fetch("/releases/latest")
        tag = release["tag_name"]
        remote = version_tuple(tag)
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("非正式 Release")
        result["release"] = tag
        result["new_release"] = remote > version_tuple(current_version)
        expected = f"VehicleDatasetManager-{tag}-windows-x64.zip"
        matches = [a for a in release.get("assets", []) if a.get("name") == expected]
        if len(matches) == 1:
            asset = matches[0]
            if (re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or "")
                    and 0 < asset.get("size", 0) <= MAX_ZIP
                    and asset.get("browser_download_url") ==
                    f"{REPO_URL}/releases/download/{tag}/{expected}"):
                result["asset"] = asset
    except Exception as exc:
        result["errors"].append(f"Release 查詢失敗：{exc}")
    return result


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_member(name):
    if "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("套件含不安全路徑")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in {"", ".", ".."} for p in name.split("/")):
        raise ValueError("套件含不安全路徑")
    for part in path.parts:
        if part.endswith((" ", ".")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise ValueError("套件含 Windows 保留路徑")
    return path


def validate_payload(root, expected_version):
    manifest_path = root / MANIFEST
    if not manifest_path.is_file() or manifest_path.stat().st_size > 4 * 1024**2:
        raise ValueError("此套件不支援安全自動更新（缺少更新清單）")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != 1 or manifest.get("version") != expected_version.lstrip("v"):
        raise ValueError("更新清單版本不符")
    files = manifest.get("files")
    if not isinstance(files, dict) or EXE not in files:
        raise ValueError("更新清單不完整")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != set(files) | {MANIFEST}:
        raise ValueError("套件檔案與更新清單不符")
    for name, digest in files.items():
        safe_member(name)
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("更新檔案路徑越界")
        if not isinstance(digest, str) or sha256(path) != digest:
            raise ValueError("更新檔案校驗失敗")
    if not (root / "_internal").is_dir():
        raise ValueError("只支援資料夾型 EXE 套件")
    return manifest


def extract_release(archive, destination, expected_version):
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if sum(i.file_size for i in entries) > MAX_EXPANDED or len(entries) > 20000:
            raise ValueError("套件解壓大小超過限制")
        seen = set()
        for entry in entries:
            name = entry.filename.rstrip("/")
            path = safe_member(name)
            if path.parts[0] != "VehicleDatasetManager":
                raise ValueError("套件根目錄不正確")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("套件不得含符號連結")
            if name.casefold() in seen:
                raise ValueError("套件含重複路徑")
            seen.add(name.casefold())
            if entry.flag_bits & 1:
                raise ValueError("套件不得加密")
        package.extractall(destination)
    root = destination / "VehicleDatasetManager"
    validate_payload(root, expected_version)
    return root


def stage_release(info, cache, progress=lambda message: None):
    if not info.get("new_release") or not info.get("asset"):
        raise ValueError("沒有可安全自動安裝的新版本")
    asset = info["asset"]
    # Re-check strict source even if the caller stored or edited update metadata.
    tag = info["release"]
    if version_tuple(tag) <= version_tuple(info["current_version"]):
        raise ValueError("拒絕降級或重複安裝")
    expected = f"VehicleDatasetManager-{tag}-windows-x64.zip"
    url = f"{REPO_URL}/releases/download/{tag}/{expected}"
    if asset.get("browser_download_url") != url:
        raise ValueError("下載來源不正確")
    digest = asset.get("digest", "")
    size = asset.get("size", 0)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not 0 < size <= MAX_ZIP:
        raise ValueError("下載校驗資訊不完整")
    Path(cache).mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="update-", dir=cache))
    archive = folder / "release.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "VehicleDatasetManager-Updater"})
    progress("正在下載 Release 套件…")
    with urllib.request.urlopen(request, timeout=30) as response, archive.open("xb") as output:
        if not response.geturl().startswith("https://"):
            raise ValueError("拒絕不安全下載連線")
        total = 0
        while chunk := response.read(1024**2):
            total += len(chunk)
            if total > size:
                raise ValueError("下載大小超過預期")
            output.write(chunk)
    if total != size or sha256(archive) != digest[7:]:
        raise ValueError("下載檔案校驗失敗，未修改程式")
    progress("正在驗證並解壓程式檔案…")
    return extract_release(archive, folder / "staged", tag)
