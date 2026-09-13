import json
import zipfile
from pathlib import Path

import cv2
import numpy as np

from vehicle_dataset_manager.services.import_service import ImportService
from vehicle_dataset_manager.services.original_replacement import OriginalReplacementService
from vehicle_dataset_manager.database.repositories import VehicleRepository, JobRepository
from vehicle_dataset_manager.pipeline.engine import ProcessingEngine
from vehicle_dataset_manager.pipeline.stages import LoadImageStage, Stage


def png(size, value):
    _, data = cv2.imencode(".png", np.full((size, size, 3), value, np.uint8))
    return data.tobytes()


def setup_originals(tmp_path, db, workspace):
    archive = tmp_path / "processed.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("照片1.png", png(16, 10))
        zf.writestr("照片2.png", png(16, 20))
    ImportService(db, workspace).import_archive(archive)
    folder = tmp_path / "原圖"
    folder.mkdir()
    (folder / "照片1.png").write_bytes(png(32, 10))
    (folder / "照片2.png").write_bytes(png(32, 20))
    return folder, OriginalReplacementService(db, workspace, ImportService(db, workspace).archive_manager)


def test_unprocessed_auto_processed_requires_consent(tmp_path, db, workspace):
    folder, service = setup_originals(tmp_path, db, workspace)
    db.execute("UPDATE images SET processing_status='completed',vehicle_bbox='[]' WHERE image_id=2")
    db.commit()
    old_paths = [r[0] for r in db.query("SELECT source_path FROM images ORDER BY image_id")]
    plan = service.prepare([folder])
    assert [i["confirm"] for i in plan["items"]] == [False, True]
    result = service.apply(plan)
    assert result["replaced"] == 1 and result["skipped"] == 1
    assert db.query_one("SELECT width FROM images WHERE image_id=1")[0] == 32
    assert db.query_one("SELECT source_path FROM images WHERE image_id=2")[0] == old_paths[1]
    assert all(Path(p).exists() for p in old_paths)


def test_confirmed_replacement_keeps_manual_and_groups_and_rebuilds(tmp_path, db, workspace):
    folder, service = setup_originals(tmp_path, db, workspace)
    db.execute("UPDATE images SET processing_status='completed',manual_plate_text='TST1234',review_status='confirmed',"
               "vehicle_bbox='[]',vehicle_crop_path='old-crop.jpg',plate_bbox='{}',ocr_plate_text='OLD',ocr_plate_normalized='OLD' WHERE image_id=1")
    vehicles = VehicleRepository(db)
    group = vehicles.get_or_create_for_plate("TST1234")
    vehicles.add_member(group, 1, label_source="manual", confidence=1)
    db.execute("INSERT INTO detections(image_id,kind,created_at) VALUES(1,'vehicle','test')")
    db.commit()
    membership = [tuple(r) for r in db.query("SELECT * FROM vehicle_members")]
    result = service.apply(service.prepare([folder]), approved_ids=[1])
    assert result["replaced"] == 2
    row = dict(db.query_one("SELECT * FROM images WHERE image_id=1"))
    assert row["manual_plate_text"] == "TST1234" and row["review_status"] == "confirmed"
    assert row["plate_text_normalized"] == "TST1234" and row["plate_source"] == "manual"
    assert row["processing_status"] == "pending"
    assert row["vehicle_crop_path"] is None and row["plate_bbox"] is None and row["ocr_plate_text"] is None
    assert not db.query("SELECT * FROM detections")
    assert [tuple(r) for r in db.query("SELECT * FROM vehicle_members")] == membership
    assert json.loads((Path(result["root"]) / "before-1.json").read_text(encoding="utf-8"))["image"]["ocr_plate_text"] == "OLD"
    # Existing automatic groups are preserved on rebuild too.
    class GroupingProbe(Stage):
        name = "grouping"
        def run(self, ctx):
            assert ctx.image_id != 1
    job = JobRepository(db).create("process", None)
    engine = ProcessingEngine(db, [LoadImageStage(), GroupingProbe()])
    assert engine.run(job).failed == 0
    assert [tuple(r) for r in db.query("SELECT * FROM vehicle_members")] == membership


def test_duplicate_unmatched_and_bad_originals_are_not_replaced(tmp_path, db, workspace):
    folder, service = setup_originals(tmp_path, db, workspace)
    (folder / "nested").mkdir()
    (folder / "nested" / "照片1.png").write_bytes(png(64, 30))
    (folder / "照片2.png").write_bytes(b"invalid")
    (folder / "unknown.png").write_bytes(png(32, 5))
    before = [tuple(r) for r in db.query("SELECT * FROM images")]
    plan = service.prepare([folder])
    assert len(plan["issues"]) == 3 and not plan["items"]
    assert service.apply(plan)["replaced"] == 0
    assert [tuple(r) for r in db.query("SELECT * FROM images")] == before


def test_changed_metadata_after_preview_is_not_overwritten(tmp_path, db, workspace):
    folder, service = setup_originals(tmp_path, db, workspace)
    plan = service.prepare([folder])
    db.execute("UPDATE images SET manual_plate_text='NEW1234' WHERE image_id=1")
    db.commit()
    result = service.apply(plan, approved_ids=[1, 2])
    assert result["replaced"] == 1 and len(result["errors"]) == 1
    assert db.query_one("SELECT manual_plate_text FROM images WHERE image_id=1")[0] == "NEW1234"


def test_zip_originals_cancel_and_repeat(tmp_path, db, workspace):
    folder, service = setup_originals(tmp_path, db, workspace)
    archive = tmp_path / "originals.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in folder.iterdir():
            zf.write(path, path.name)
    plan = service.prepare([archive])
    assert service.apply(plan, should_cancel=lambda: True)["cancelled"]
    assert service.apply(plan)["replaced"] == 2
    assert service.apply(service.prepare([archive]))["replaced"] == 0


def test_database_duplicate_filename_is_conflict(tmp_path, db, workspace):
    folder, service = setup_originals(tmp_path, db, workspace)
    db.execute("UPDATE images SET original_filename='照片1.png' WHERE image_id=2")
    db.commit()
    plan = service.prepare([folder])
    assert not plan["items"] and len(plan["issues"]) == 2
