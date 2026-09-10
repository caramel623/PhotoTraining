from __future__ import annotations

import zipfile

from vehicle_dataset_manager.archive.manager import (
    ArchiveManager,
    detect_archive_type,
)
from vehicle_dataset_manager.core.enums import ArchiveType


def test_detect_zip(tmp_path):
    zp = tmp_path / "a.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("x.txt", "hello")
    assert detect_archive_type(zp) == ArchiveType.ZIP


def test_detect_7z(tmp_path):
    import py7zr

    p7 = tmp_path / "a.7z"
    with py7zr.SevenZipFile(str(p7), "w") as zf:
        zf.writestr("data", "x.txt")
    assert detect_archive_type(p7) == ArchiveType.SEVEN_Z


def test_detect_unknown(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    assert detect_archive_type(f) == ArchiveType.UNKNOWN


def test_zip_list_and_extract(tmp_path, sample_zip):
    am = ArchiveManager()
    entries = am.list_archive(sample_zip)
    assert len(entries) == 4
    assert sum(e.is_image for e in entries) == 4
    out = tmp_path / "out"
    result = am.extract_archive(sample_zip, out)
    assert result.extracted == 4
    assert result.backend == "zipfile"
    # original archive untouched
    assert sample_zip.exists()


def test_7z_extract(tmp_path):
    import py7zr

    p7 = tmp_path / "b.7z"
    with py7zr.SevenZipFile(str(p7), "w") as zf:
        zf.writestr("one", "f1.txt")
        zf.writestr("two", "sub/f2.txt")
    am = ArchiveManager()
    assert am.verify_archive(p7).ok is True
    out = tmp_path / "o7"
    result = am.extract_archive(p7, out)
    assert result.extracted == 2
    assert result.backend == "py7zr"


def test_cancel_extraction(tmp_path, sample_zip):
    am = ArchiveManager()
    result = am.extract_archive(sample_zip, tmp_path / "oc", should_cancel=lambda: True)
    assert result.cancelled is True
    assert result.extracted == 0