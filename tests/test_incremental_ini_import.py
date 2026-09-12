from __future__ import annotations

import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from vehicle_dataset_manager.core.enums import GroupVerification
from vehicle_dataset_manager.database.repositories import ImageRepository, VehicleRepository
from vehicle_dataset_manager.services.import_service import ImportMode, ImportService
from vehicle_dataset_manager.services.ini_parser import INI_PARSER_VERSION, parse_ini_bytes
from vehicle_dataset_manager.services.metadata import FilenameMetadataParser
from vehicle_dataset_manager.services.metadata_resolver import MetadataResolver, validate_ini_against_filename


INI_TEXT = """證號=TEST-CERT-0001
主機=RS015
地點=合成測試路段
速限=030km/h
影像序號=5790
日期=2026/09/10
時間=01:55:11
車速=059km/h
操作者姓名=測試人員
方向=車尾
方向=1
類型=超速
金額=59
車種=2
違規=13
車號=TST-6212
broken line
"""
IMAGE_NAME = "20260910_015511_139_RS015_5790_A.JPG"


def _jpeg() -> bytes:
    image = np.full((32, 48, 3), 120, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def _archive(path: Path, *, include_image=True, include_ini=True) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        if include_image:
            archive.writestr("cam/" + IMAGE_NAME, _jpeg())
        if include_ini:
            archive.writestr("cam/" + Path(IMAGE_NAME).stem + ".INI", INI_TEXT.encode("utf-8"))
        archive.writestr("cam/orphan.ini", "車號=AAA-1234".encode("utf-8"))
    return path


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (INI_TEXT.encode("utf-8"), "utf-8"),
        (INI_TEXT.encode("utf-8-sig"), "utf-8-sig"),
        (INI_TEXT.encode("cp950"), "cp950"),
        (INI_TEXT.encode("big5"), "cp950"),
    ],
)
def test_ini_encodings_duplicate_keys_and_mapping(payload, expected):
    parsed = parse_ini_bytes(payload)
    assert parsed.status == "ok"
    assert parsed.encoding_used == expected
    assert parsed.raw_values["方向"] == ["車尾", "1"]
    assert parsed.direction_text == "車尾"
    assert parsed.direction_code == "1"
    assert parsed.camera_id == "RS015"
    assert parsed.location == "合成測試路段"
    assert parsed.speed_limit == "030km/h"
    assert parsed.image_sequence == "5790"
    assert parsed.date == "2026-09-10" and parsed.time == "01:55:11"
    assert parsed.vehicle_speed == "059km/h"
    assert parsed.vehicle_type_code == "2" and parsed.violation_code == "13"
    assert parsed.plate_text_raw == "TST-6212"
    assert parsed.plate_text_normalized == "TST6212"
    assert any("duplicate key" in warning for warning in parsed.warnings)
    assert any("malformed line" in warning for warning in parsed.warnings)


def test_missing_plate_and_filename_qa():
    parsed = parse_ini_bytes(INI_TEXT.replace("車號=TST-6212\n", "").encode("utf-8"))
    assert parsed.plate_text_raw is None
    assert "INI_PLATE_MISSING" in parsed.warnings
    filename = FilenameMetadataParser().parse(IMAGE_NAME)
    assert filename["camera_id"] == "RS015" and filename["sequence"] == "5790"
    assert validate_ini_against_filename(parse_ini_bytes(INI_TEXT.encode()), filename) == []
    mismatch = dict(filename, date="2026-09-11", camera_id="RS016", sequence="5791")
    assert set(validate_ini_against_filename(parse_ini_bytes(INI_TEXT.encode()), mismatch)) == {
        "INI_DATE_MISMATCH", "INI_CAMERA_MISMATCH", "INI_FILENAME_MISMATCH"
    }


def test_plate_resolution_manual_ini_and_ocr_priority():
    ini_wins = MetadataResolver.resolve_plate(ini_plate="TST-6212", ocr_plate="TST-621Z", ocr_confidence=0.9)
    assert ini_wins.normalized == "TST6212" and ini_wins.source == "ini"
    assert ini_wins.validation_status == "OCR_MISMATCH"
    match = MetadataResolver.resolve_plate(ini_plate="TST-6212", ocr_plate="TST6212")
    assert match.validation_status == "MATCH"
    manual = MetadataResolver.resolve_plate(manual_plate="ABC-1234", ini_plate="TST-6212")
    assert manual.normalized == "ABC1234" and manual.source == "manual"
    assert manual.conflict and manual.conflict_type == "MANUAL_INI_PLATE_MISMATCH"


def test_fresh_import_pairs_uppercase_ini_and_reports_unmatched(workspace, db, tmp_path):
    result = ImportService(db, workspace).import_archive(_archive(tmp_path / "2026.zip"))
    assert result.images_found == 1 and result.images_new == 1
    assert result.ini_found == 2 and result.ini_matched == 1 and result.unmatched_ini == 1
    assert result.ini_with_plate == 1 and result.ini_parse_error == 0
    row = dict(db.query_one("SELECT * FROM images"))
    assert row["ini_present"] == 1 and row["ini_parse_status"] == "ok"
    assert row["ini_encoding"] == "utf-8" and row["ini_plate_text"] == "TST-6212"
    assert row["plate_text_normalized"] == "TST6212" and row["plate_source"] == "ini"
    assert row["camera_id"] == "RS015" and row["image_sequence"] == "5790"


def test_rescan_existing_merges_ini_without_duplicate_or_ai_and_keeps_manual(
    workspace, db, tmp_path
):
    archive = _archive(tmp_path / "2026-0909.zip")
    extracted = workspace.extracted_dir / archive.stem / "cam"
    extracted.mkdir(parents=True)
    image_path = extracted / IMAGE_NAME
    image_path.write_bytes(_jpeg())
    images = ImageRepository(db)
    image_id = images.upsert_from_scan(
        original_filename=IMAGE_NAME, source_path=str(image_path.resolve()),
        original_archive=archive.name, archive_year=2026,
    )
    images.mark_completed(
        image_id, plate_text_raw="ABC-1234", plate_text_normalized="ABC1234",
        manual_plate_text="ABC-1234", plate_source="manual",
    )
    vehicles = VehicleRepository(db)
    vehicle_id = vehicles.get_or_create_for_plate("ABC1234")
    vehicles.add_member(vehicle_id, image_id, label_source="manual")
    vehicles.set_verification(vehicle_id, GroupVerification.VERIFIED)

    result = ImportService(db, workspace).import_archive(
        archive, mode=ImportMode.RESCAN_EXISTING
    )
    assert result.images_found == 1 and result.images_new == 0 and result.images_existing == 1
    assert result.ini_new == 1 and result.metadata_updated == 1 and result.conflicts == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM images")["n"] == 1
    row = dict(db.query_one("SELECT * FROM images WHERE image_id=?", (image_id,)))
    assert row["processing_status"] == "completed"
    assert row["ini_plate_text"] == "TST-6212"
    assert row["manual_plate_text"] == "ABC-1234"
    assert row["plate_text_normalized"] == "ABC1234" and row["plate_source"] == "manual"
    assert row["conflict_type"] == "MANUAL_INI_PLATE_MISMATCH"
    assert vehicles.get(vehicle_id)["verification"] == "verified"
    assert db.query_one("SELECT COUNT(*) AS n FROM detections")["n"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM ocr_results")["n"] == 0

    repeated = ImportService(db, workspace).import_archive(
        archive, mode=ImportMode.RESCAN_EXISTING
    )
    assert repeated.archive_unchanged is True
    assert repeated.images_new == 0 and repeated.metadata_updated == 0
    assert repeated.ini_existing == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM images")["n"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM image_sources")["n"] == 1
    assert db.query_one(
        "SELECT status FROM processing_jobs WHERE job_id=?", (result.job_id,)
    )["status"] == "completed"


def test_same_bytes_parser_change_still_rescans(workspace, db, tmp_path):
    archive = _archive(tmp_path / "same.zip")
    service = ImportService(db, workspace)
    first = service.import_archive(archive)
    db.execute("UPDATE images SET ini_parser_version=?", (INI_PARSER_VERSION - 1,))
    db.execute("UPDATE archives SET parser_version=?", (INI_PARSER_VERSION - 1,))
    db.commit()
    second = service.import_archive(archive, mode=ImportMode.RESCAN_EXISTING)
    assert first.images_new == 1
    assert second.archive_unchanged and second.parser_changed
    assert second.metadata_updated == 1
    assert db.query_one("SELECT ini_parser_version FROM images")["ini_parser_version"] == INI_PARSER_VERSION


def test_renamed_duplicate_archive_reuses_image_asset(workspace, db, tmp_path):
    first = _archive(tmp_path / "first.zip")
    second = tmp_path / "backup.zip"
    second.write_bytes(first.read_bytes())
    service = ImportService(db, workspace)
    service.import_archive(first)
    result = service.import_archive(second)
    assert result.images_new == 0 and result.images_existing == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM images")["n"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM archives")["n"] == 2
    assert db.query_one("SELECT COUNT(*) AS n FROM image_sources")["n"] == 2

def test_changed_archive_same_member_keeps_old_asset(workspace, db, tmp_path):
    archive = _archive(tmp_path / "changing.zip")
    service = ImportService(db, workspace)
    first = service.import_archive(archive)
    original = dict(db.query_one("SELECT * FROM images"))
    old_bytes = Path(original["source_path"]).read_bytes()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("cam/" + IMAGE_NAME, _jpeg() + b"different-content")
    second = service.import_archive(archive)
    assert second.images_new == 1 and second.ini_found == 0
    assert first.extracted_dir != second.extracted_dir
    assert Path(original["source_path"]).read_bytes() == old_bytes
    assert db.query_one("SELECT COUNT(*) n FROM images")["n"] == 2


def test_force_reparse_is_idempotent_and_never_runs_ai(workspace, db, tmp_path):
    from vehicle_dataset_manager.pipeline.engine import ProcessingEngine
    service = ImportService(db, workspace)
    archive = _archive(tmp_path / "force.zip")
    service.import_archive(archive)
    before = dict(db.query_one("SELECT * FROM images"))
    result = service.import_archive(archive, mode=ImportMode.FORCE_METADATA)
    assert result.metadata_updated == 0
    assert dict(db.query_one("SELECT * FROM images")) == before
    assert result.ini_with_plate == 1
    assert ProcessingEngine(db, []).run(result.job_id).processed == 0
    assert db.query_one("SELECT processing_status FROM images")[0] == "pending"


def test_pair_failure_rolls_back_and_retry_succeeds(workspace, db, tmp_path, monkeypatch):
    service = ImportService(db, workspace)
    archive = _archive(tmp_path / "failure.zip")
    original = service._record_image_source
    monkeypatch.setattr(service, "_record_image_source", lambda *args: (_ for _ in ()).throw(OSError("simulated")))
    result = service.import_archive(archive)
    assert result.errors == 1
    assert db.query_one("SELECT COUNT(*) n FROM images")["n"] == 0
    assert db.query_one("SELECT parser_version FROM archives")[0] is None
    assert db.query_one("SELECT status FROM import_jobs")[0] == "failed"
    monkeypatch.setattr(service, "_record_image_source", original)
    assert service.import_archive(archive).images_new == 1


def test_cancel_during_merge_retains_checkpoint_and_resumes(workspace, db, tmp_path):
    service = ImportService(db, workspace)
    archive = _archive(tmp_path / "cancel.zip")
    # Add another distinct image so cancellation happens after the first commit.
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("second.jpg", _jpeg() + b"second")
    result = service.import_archive(
        archive, should_cancel=lambda: bool(db.query_one("SELECT 1 FROM images"))
    )
    assert result.cancelled
    assert db.query_one("SELECT COUNT(*) FROM images")[0] == 1
    assert db.query_one("SELECT status FROM import_jobs ORDER BY import_job_id DESC")[0] == "cancelled"
    assert service.import_archive(archive).images_new == 1


def test_conflicting_sources_preserve_effective_plate_and_both_raw_sources(workspace, db, tmp_path):
    service = ImportService(db, workspace)
    first = _archive(tmp_path / "source1.zip")
    service.import_archive(first)
    before = dict(db.query_one("SELECT * FROM images"))
    second = tmp_path / "source2.zip"
    with zipfile.ZipFile(second, "w") as output:
        output.writestr("cam/" + IMAGE_NAME, _jpeg())
        output.writestr("cam/" + Path(IMAGE_NAME).stem + ".ini", "車號=ZZZ-9999".encode())
    result = service.import_archive(second)
    after = dict(db.query_one("SELECT * FROM images"))
    assert result.conflicts == 1
    assert after["plate_text_normalized"] == before["plate_text_normalized"]
    assert after["conflict_type"] == "INI_SOURCE_PLATE_MISMATCH"
    assert db.query_one("SELECT COUNT(*) n FROM ini_sources")["n"] == 2


def test_manual_split_and_merge_are_not_undone_by_rescan(workspace, db, tmp_path):
    service = ImportService(db, workspace)
    archive = _archive(tmp_path / "manual.zip")
    service.import_archive(archive)
    vehicles = VehicleRepository(db)
    image_id = db.query_one("SELECT image_id FROM images")[0]
    original = vehicles.vehicle_for_image(image_id)
    split = vehicles.split(original, [image_id])
    service.import_archive(archive, mode=ImportMode.FORCE_METADATA)
    assert vehicles.vehicle_for_image(image_id) == split
    vehicles.merge(split, original)
    service.import_archive(archive, mode=ImportMode.FORCE_METADATA)
    assert vehicles.vehicle_for_image(image_id) == original


def test_invalid_dates_and_conflicting_duplicate_plate_do_not_become_labels():
    parsed = parse_ini_bytes("日期=2026/02/30\n時間=29:00:00\n車號=AAA-1111\n車號=BBB-2222".encode())
    assert parsed.date is None and parsed.time is None
    assert not parsed.has_plate
    assert "INI_PLATE_AMBIGUOUS" in parsed.warnings
    assert len(parsed.raw_lines) == 4


def test_conflict_queue_includes_ungrouped_images(db):
    from vehicle_dataset_manager.review_model import ReviewModel
    from vehicle_dataset_manager.database.repositories import ReviewRepository
    images = ImageRepository(db)
    image_id = images.upsert_from_scan(original_filename="synthetic.jpg", source_path="synthetic.jpg")
    images.set_result_fields(image_id, metadata_conflict=1, conflict_type="INI_PARSE_ERROR")
    model = ReviewModel(VehicleRepository(db), images, ReviewRepository(db))
    assert model.list_groups("metadata_conflicts")[0].image_count == 1
    assert model.group_images("__metadata__")[0].image_id == image_id


def test_recompressed_sevenz_reuses_same_image(workspace, db, tmp_path):
    import py7zr
    service = ImportService(db, workspace)
    service.import_archive(_archive(tmp_path / "first.zip"))
    image = tmp_path / IMAGE_NAME
    image.write_bytes(_jpeg())
    archive = tmp_path / "second.7z"
    with py7zr.SevenZipFile(archive, "w") as output:
        output.write(image, arcname=IMAGE_NAME)
    result = service.import_archive(archive)
    assert result.images_new == 0 and result.images_existing == 1


def test_failed_migration_rolls_back_all_ddl(db, monkeypatch):
    from vehicle_dataset_manager.database import schema
    import sqlite3
    monkeypatch.setattr(schema, "_MIGRATIONS", schema._MIGRATIONS + [[
        "ALTER TABLE images ADD COLUMN rollback_probe TEXT",
        "SELECT * FROM table_that_does_not_exist",
    ]])
    with pytest.raises(sqlite3.Error):
        db.migrate()
    assert "rollback_probe" not in {row["name"] for row in db.query("PRAGMA table_info(images)")}
    assert db.query_one("SELECT MAX(version) FROM schema_version")[0] == 5
