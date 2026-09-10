"""Core archive operations with progress callbacks and cancellation.

Cancellation is cooperative: every loop checks ``should_cancel()`` and aborts
early, returning a partial :class:`ExtractionResult` with ``cancelled=True``.
Already-extracted files remain on disk, so re-running an extraction is safe and
idempotent (existing files are overwritten, not duplicated).
"""
from __future__ import annotations

import logging
import os
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from vehicle_dataset_manager.core.enums import ArchiveType

log = logging.getLogger("vdm.archive")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
ARCHIVE_EXTS = {".zip", ".7z", ".7zip"}

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


# --------------------------------------------------------------------------- #
# Magic-byte / name detection
# --------------------------------------------------------------------------- #
def detect_archive_type(path: Path | str) -> ArchiveType:
    """Detect archive type by magic bytes, falling back to file extension."""
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        head = b""
    if head[:4] == b"PK\x03\x04":
        return ArchiveType.ZIP
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return ArchiveType.SEVEN_Z
    # Fallback to extension when magic bytes are inconclusive.
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return ArchiveType.ZIP
    if suffix in (".7z", ".7zip"):
        return ArchiveType.SEVEN_Z
    return ArchiveType.UNKNOWN


def find_sevenz_cli() -> Optional[Path]:
    """Locate a 7-Zip CLI executable if one is installed. Returns None if not."""
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
        Path.home() / "7-Zip" / "7z.exe",
    ]
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    # Finally, try the PATH.
    for name in ("7z.exe", "7z"):
        found = _which(name)
        if found:
            return Path(found)
    return None


def _which(name: str) -> Optional[str]:
    for dir_ in os.environ.get("PATH", "").split(os.pathsep):
        if not dir_:
            continue
        candidate = Path(dir_) / name
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Result / entry dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class ArchiveEntry:
    name: str
    path_in_archive: str
    is_dir: bool
    size: int
    is_image: bool = False

    @property
    def ext(self) -> str:
        return Path(self.path_in_archive).suffix.lower()


@dataclass
class VerifyResult:
    ok: bool
    message: str = ""
    file_count: int = 0
    bad_file_count: int = 0


@dataclass
class ExtractionResult:
    dest_dir: Path
    extracted: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    backend: str = ""
    entries: list[Path] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
class ArchiveManager:
    """Unified interface over ZIP and 7z archives."""

    def __init__(self, sevenz_cli: Optional[Path | str] = None) -> None:
        self.sevenz_cli = Path(sevenz_cli) if sevenz_cli else find_sevenz_cli()

    # -- listing -----------------------------------------------------------
    def list_archive(
        self,
        path: Path | str,
        on_progress: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCheck] = None,
    ) -> list[ArchiveEntry]:
        path = Path(path)
        atype = detect_archive_type(path)
        if atype == ArchiveType.ZIP:
            return self._list_zip(path, on_progress, should_cancel)
        if atype == ArchiveType.SEVEN_Z:
            return self._list_7z(path, on_progress, should_cancel)
        raise ValueError(f"Not a supported archive: {path}")

    def _list_zip(self, path, on_progress, should_cancel) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            total = len(infos)
            for i, info in enumerate(infos):
                if should_cancel and should_cancel():
                    break
                is_dir = info.is_dir()
                ext = Path(info.filename).suffix.lower()
                entries.append(
                    ArchiveEntry(
                        name=info.filename.rsplit("/", 1)[-1],
                        path_in_archive=info.filename.replace("\\", "/"),
                        is_dir=is_dir,
                        size=0 if is_dir else int(info.file_size),
                        is_image=(not is_dir and ext in IMAGE_EXTS),
                    )
                )
                if on_progress:
                    on_progress(i + 1, total, info.filename)
        return entries

    def _list_7z(self, path, on_progress, should_cancel) -> list[ArchiveEntry]:
        try:
            import py7zr
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("py7zr is required to read 7z archives") from exc
        entries: list[ArchiveEntry] = []
        with py7zr.SevenZipFile(str(path)) as zf:
            names = zf.getnames()
            total = len(names)
            for i, name in enumerate(names):
                if should_cancel and should_cancel():
                    break
                is_dir = name.endswith("/")
                ext = Path(name).suffix.lower()
                entries.append(
                    ArchiveEntry(
                        name=name.rstrip("/").rsplit("/", 1)[-1],
                        path_in_archive=name.replace("\\", "/"),
                        is_dir=is_dir,
                        size=0,
                        is_image=(not is_dir and ext in IMAGE_EXTS),
                    )
                )
                if on_progress:
                    on_progress(i + 1, total, name)
        return entries

    # -- extraction --------------------------------------------------------
    def extract_archive(
        self,
        path: Path | str,
        dest_dir: Path | str,
        on_progress: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCheck] = None,
        backend: str = "auto",
    ) -> ExtractionResult:
        """Extract to ``dest_dir``. ``backend``: auto | cli | py7zr."""
        path = Path(path)
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        atype = detect_archive_type(path)
        if atype == ArchiveType.ZIP:
            return self._extract_zip(path, dest, on_progress, should_cancel)
        if atype == ArchiveType.SEVEN_Z:
            if backend == "cli" and self.sevenz_cli:
                result = self._extract_7z_cli(path, dest, on_progress, should_cancel)
                if result is not None:
                    return result
            return self._extract_7z_py7zr(path, dest, on_progress, should_cancel)
        raise ValueError(f"Not a supported archive: {path}")

    def extract_archives_recursively(
        self,
        archive_path: Path | str,
        dest: Path | str,
        *,
        max_depth: int = 6,
        on_progress: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCheck] = None,
    ) -> ExtractionResult:
        """Extract ``archive_path`` into ``dest``, then recursively extract any
        nested archives found in the tree (e.g. a 7z of daily photo zips)."""
        top = self.extract_archive(archive_path, dest, on_progress=on_progress,
                                   should_cancel=should_cancel)
        self._extract_nested(dest, max_depth=max_depth, on_progress=on_progress,
                             should_cancel=should_cancel, result=top)
        return top

    def _extract_nested(
        self,
        root: Path,
        *,
        max_depth: int,
        on_progress: Optional[ProgressCallback],
        should_cancel: Optional[CancelCheck],
        result: ExtractionResult,
    ) -> None:
        depth = 0
        while depth < max_depth:
            if should_cancel and should_cancel():
                result.cancelled = True
                return
            found = sorted(
                p for p in root.rglob("*")
                if p.is_file() and p.suffix.lower() in ARCHIVE_EXTS
            )
            if not found:
                return
            for archive in found:
                if should_cancel and should_cancel():
                    result.cancelled = True
                    return
                target = archive.parent / archive.stem
                target.mkdir(parents=True, exist_ok=True)
                sub = self.extract_archive(archive, target, on_progress=on_progress,
                                           should_cancel=should_cancel)
                result.extracted += sub.extracted
                result.failed += sub.failed
                if sub.cancelled:
                    result.cancelled = True
                    return
                # Remove the intermediate archive so the next pass does not
                # re-extract it (it is re-derivable from the parent archive).
                try:
                    archive.unlink()
                except OSError:
                    pass
            depth += 1

    def _extract_zip(self, path, dest, on_progress, should_cancel) -> ExtractionResult:
        result = ExtractionResult(dest_dir=dest, backend="zipfile")
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist()]
            total = len(infos)
            for i, info in enumerate(infos):
                if should_cancel and should_cancel():
                    result.cancelled = True
                    break
                target = self._safe_dest(dest, info.filename)
                try:
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        result.skipped += 1
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(target, "wb") as out:
                            while True:
                                chunk = src.read(1024 * 1024)
                                if not chunk:
                                    break
                                out.write(chunk)
                        result.extracted += 1
                        result.entries.append(target)
                except (OSError, zipfile.BadZipFile) as exc:
                    result.failed += 1
                    log.warning("zip extract failed for %s: %s", info.filename, exc)
                if on_progress:
                    on_progress(i + 1, total, info.filename)
        return result

    def _extract_7z_cli(self, path, dest, on_progress, should_cancel) -> Optional[ExtractionResult]:
        """Extract via the 7-Zip CLI. Returns None if the CLI is unusable."""
        assert self.sevenz_cli is not None
        result = ExtractionResult(dest_dir=dest, backend="7z-cli")
        cmd = [str(self.sevenz_cli), "x", f"-o{dest}", "-y", "-bso0", "-bsp1", str(path)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            log.warning("7z CLI failed to start (%s); falling back to py7zr", exc)
            return None
        try:
            for _line in proc.stdout:  # noqa: B007 - stream to detect completion
                if should_cancel and should_cancel():
                    proc.terminate()
                    result.cancelled = True
                    break
        except Exception:  # noqa: BLE001
            pass
        proc.wait()
        if proc.returncode not in (0, None) and proc.returncode != 0:
            # 7z returns non-zero on warnings; only treat >=2 as failure here.
            if proc.returncode >= 2:
                log.warning("7z CLI returned %s; falling back to py7zr", proc.returncode)
                return None
        # Count files actually produced under dest.
        for p in dest.rglob("*"):
            if p.is_file():
                result.extracted += 1
                result.entries.append(p)
        if on_progress:
            on_progress(result.extracted, result.extracted, str(path.name))
        return result

    def _extract_7z_py7zr(self, path, dest, on_progress, should_cancel) -> ExtractionResult:
        try:
            import py7zr
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("py7zr is required to read 7z archives") from exc
        result = ExtractionResult(dest_dir=dest, backend="py7zr")
        if should_cancel and should_cancel():
            result.cancelled = True
            return result
        with py7zr.SevenZipFile(str(path)) as zf:
            names = zf.getnames()
            total = len(names)
            if on_progress:
                on_progress(0, total, str(path.name))
            # Single bulk extract is the reliable, documented py7zr usage.
            zf.extract(path=dest)
        for p in dest.rglob("*"):
            if p.is_file():
                result.extracted += 1
                result.entries.append(p)
            else:
                result.skipped += 1
        if on_progress:
            on_progress(total, total, str(path.name))
        return result

    # -- verification ------------------------------------------------------
    def verify_archive(self, path: Path | str) -> VerifyResult:
        path = Path(path)
        atype = detect_archive_type(path)
        if atype == ArchiveType.ZIP:
            try:
                with zipfile.ZipFile(path) as zf:
                    bad = zf.testzip()
                    count = len(zf.infolist())
                if bad is None:
                    return VerifyResult(True, "ZIP integrity OK", count, 0)
                return VerifyResult(False, f"Bad file in ZIP: {bad}", count, 1)
            except zipfile.BadZipFile as exc:
                return VerifyResult(False, f"Bad ZIP: {exc}", 0, 0)
        if atype == ArchiveType.SEVEN_Z:
            try:
                import py7zr

                with py7zr.SevenZipFile(str(path)) as zf:
                    names = zf.getnames()
                return VerifyResult(True, "7z readable", len(names), 0)
            except Exception as exc:  # noqa: BLE001
                return VerifyResult(False, f"7z read error: {exc}", 0, 0)
        return VerifyResult(False, f"Unknown archive type: {path}")

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _safe_dest(dest: Path, name: str) -> Path:
        """Resolve a member path inside ``dest``, guarding against traversal."""
        normalized = name.replace("\\", "/").lstrip("/")
        parts = [p for p in normalized.split("/") if p not in ("", ".")]
        if ".." in parts:
            raise ValueError(f"Unsafe archive path: {name}")
        return dest.joinpath(*parts) if parts else dest
