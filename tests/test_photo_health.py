import hashlib
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from vehicle_dataset_manager.services.photo_health import check_photo, PhotoHealthService
from vehicle_dataset_manager.services.import_service import ImportService, ImportMode
from vehicle_dataset_manager.pipeline.stages import LoadImageStage, PipelineContext


def archive_fixture(tmp_path, db, workspace):
    path = tmp_path / "原始照片.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(3):
            _, data = cv2.imencode(".png", np.full((16, 16, 3), i * 50, np.uint8))
            zf.writestr(f"中文資料夾/照片{i}.png", data.tobytes())
    service = ImportService(db, workspace)
    service.import_archive(path)
    rows = [dict(r) for r in db.query("SELECT * FROM images ORDER BY image_id")]
    return path, service, rows


def test_scan_and_repair_preserves_all_fields(tmp_path, db, workspace):
    archive, service, rows = archive_fixture(tmp_path, db, workspace)
    Path(rows[0]["source_path"]).unlink()
    Path(rows[1]["source_path"]).write_bytes(b"broken")
    db.execute("UPDATE images SET manual_plate_text='TEST1234',review_status='confirmed' WHERE image_id=?", (rows[0]["image_id"],))
    db.commit()
    before = [dict(r) for r in db.query("SELECT * FROM images ORDER BY image_id")]
    scan = PhotoHealthService(db).scan()
    assert scan.counts == {"missing": 1, "corrupt": 1, "ok": 1}
    result = service.import_archive(archive, mode=ImportMode.REPAIR_PHOTOS)
    assert result.photos_repaired == 2 and result.images_new == 0
    after = [dict(r) for r in db.query("SELECT * FROM images ORDER BY image_id")]
    for old, new in zip(before, after):
        assert {k: v for k, v in old.items() if k != "source_path"} == {k: v for k, v in new.items() if k != "source_path"}
        assert check_photo(new["source_path"], new["sha256"]) == "ok"
    assert before[2]["source_path"] == after[2]["source_path"]
    assert Path(before[1]["source_path"]).read_bytes() == b"broken"
    assert service.import_archive(archive, mode=ImportMode.REPAIR_PHOTOS).photos_repaired == 0
    assert PhotoHealthService(db).scan().counts == {"ok": 3}


def test_changed_photo_and_unknown_hash(tmp_path, db, workspace):
    archive, service, rows = archive_fixture(tmp_path, db, workspace)
    Path(rows[0]["source_path"]).write_bytes(Path(rows[1]["source_path"]).read_bytes())
    db.execute("UPDATE images SET sha256=NULL WHERE image_id=?", (rows[2]["image_id"],))
    db.commit()
    assert PhotoHealthService(db).scan().counts == {"changed": 1, "ok": 1, "unverified": 1}
    assert service.import_archive(archive, mode=ImportMode.REPAIR_PHOTOS).photos_repaired == 1
    assert db.query_one("SELECT sha256 FROM images WHERE image_id=?", (rows[2]["image_id"],))[0] is None


def test_cancel_and_wrong_archive_do_not_replace(tmp_path, db, workspace):
    archive, service, rows = archive_fixture(tmp_path, db, workspace)
    before = [dict(r) for r in db.query("SELECT * FROM images")]
    result = service.import_archive(archive, mode=ImportMode.REPAIR_PHOTOS, should_cancel=lambda: True)
    assert result.cancelled and result.photos_repaired == 0
    assert [dict(r) for r in db.query("SELECT * FROM images")] == before
    assert PhotoHealthService(db).scan(should_cancel=lambda: True).checked == 0
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("different.txt", "different archive with same name")
    with pytest.raises(ValueError, match="SHA256"):
        service.import_archive(archive, mode=ImportMode.REPAIR_PHOTOS)
    assert [dict(r) for r in db.query("SELECT * FROM images")] == before


def test_unicode_read_matches_pipeline(tmp_path):
    photo = tmp_path / "測試照片.png"
    _, encoded = cv2.imencode(".png", np.zeros((16, 16, 3), np.uint8))
    photo.write_bytes(encoded.tobytes())
    context = PipelineContext(1, photo, photo.name)
    LoadImageStage().run(context)
    assert context.width == 16 and context.sha256 == hashlib.sha256(encoded.tobytes()).hexdigest()
    assert check_photo(str(photo), context.sha256) == "ok"


def test_invalid_archive_photo_not_activated(tmp_path, db, workspace):
    archive = tmp_path / "invalid.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("bad.jpg", b"not an image")
    service = ImportService(db, workspace)
    service.import_archive(archive)
    before = [dict(r) for r in db.query("SELECT * FROM images")]
    result = service.import_archive(archive, mode=ImportMode.REPAIR_PHOTOS)
    assert result.errors == 1 and result.photos_repaired == 0
    assert [dict(r) for r in db.query("SELECT * FROM images")] == before


def test_cancel_after_checkpoint_then_resume(tmp_path, db, workspace):
    archive, service, rows = archive_fixture(tmp_path, db, workspace)
    for row in rows:
        Path(row["source_path"]).unlink()
    cancelled = [False]
    def progress(done, total, message):
        if message == "替換已驗證的照片路徑":
            cancelled[0] = True
    first = service.import_archive(archive, mode=ImportMode.REPAIR_PHOTOS,
                                   on_progress=progress, should_cancel=lambda: cancelled[0])
    assert first.cancelled and first.photos_repaired == 1
    second = service.import_archive(archive, mode=ImportMode.REPAIR_PHOTOS)
    assert second.photos_repaired == 2 and not second.cancelled
    assert PhotoHealthService(db).scan().counts == {"ok": 3}


def test_nested_7z_and_renamed_original(tmp_path, db, workspace):
    import py7zr
    inner = tmp_path / "inside.zip"
    _, data = cv2.imencode(".png", np.zeros((16, 16, 3), np.uint8))
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("照片.png", data.tobytes())
    outer = tmp_path / "original.7z"
    with py7zr.SevenZipFile(outer, "w") as zf:
        zf.write(inner, "inside.zip")
    service = ImportService(db, workspace)
    service.import_archive(outer)
    row = db.query_one("SELECT * FROM images")
    Path(row["source_path"]).unlink()
    renamed = tmp_path / "renamed.7z"
    renamed.write_bytes(outer.read_bytes())
    assert service.import_archive(renamed, mode=ImportMode.REPAIR_PHOTOS).photos_repaired == 1
    assert PhotoHealthService(db).scan().counts == {"ok": 1}
