import zipfile

import cv2
import numpy as np

from vehicle_dataset_manager.archive.manager import ArchiveManager
from vehicle_dataset_manager.core.enums import JobState
from vehicle_dataset_manager.database.repositories import (
    DetectionRepository,
    ImageRepository,
    JobRepository,
    OcrRepository,
    PlateRepository,
    VehicleRepository,
)
from vehicle_dataset_manager.detection.base import StubPlateDetector, StubVehicleDetector
from vehicle_dataset_manager.ocr.base import StubOcr
from vehicle_dataset_manager.pipeline.engine import ProcessingEngine
from vehicle_dataset_manager.pipeline.stages import build_default_stages
from vehicle_dataset_manager.services.import_service import ImportService
from vehicle_dataset_manager.services.metadata import IniSidecarParser


def _tiny_jpeg() -> bytes:
    arr = np.full((16, 16, 3), 128, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", arr)
    assert ok
    return buf.tobytes()


INI_BYTES = (
    "證號=2412BAD00801\r\n"
    "主機=RS015\r\n"
    "地點=外環-棒球場\r\n"
    "速限=030km/h\r\n"
    "車速=052km/h\r\n"
    "影像序號=2520\r\n"
    "日期=2025/02/28\r\n"
    "時間=02:08:07\r\n"
    "方向=車尾\r\n"
).encode("cp950")


def _engine(db, vehicles):
    stages = build_default_stages(StubVehicleDetector(), StubPlateDetector(), StubOcr(), vehicles)
    return ProcessingEngine(
        db, stages,
        image_repo=ImageRepository(db), vehicle_repo=vehicles,
        job_repo=JobRepository(db), plate_repo=PlateRepository(db),
        detection_repo=DetectionRepository(db), ocr_repo=OcrRepository(db),
    )


def test_ini_parser_fields():
    m = IniSidecarParser().parse_bytes(INI_BYTES)
    assert m["camera_id"] == "RS015"
    assert m["speed"] == 52.0
    assert m["speed_limit"] == 30.0
    assert m["location"] == "外環-棒球場"
    assert m["device_serial"] == "2412BAD00801"
    assert m["direction"] == "車尾"
    assert m["date"] == "2025-02-28"
    assert m["time"] == "02:08:07"
    assert m["sequence"] == 2520
    assert m["datetime"] == "2025-02-28 02:08:07"


def test_nested_zip_of_zip(tmp_path):
    inner = tmp_path / "20250228.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("cam/shot.jpg", _tiny_jpeg())
        zf.writestr("cam/shot.ini", INI_BYTES)
    outer = tmp_path / "2025.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "02/20250228.zip")
    dest = tmp_path / "dest"
    ArchiveManager().extract_archives_recursively(outer, dest)
    assert list(dest.rglob("shot.jpg")), "missing shot.jpg: " + str([str(p) for p in dest.rglob("*")])


def test_import_nested_and_process(workspace, db, tmp_path):
    cam = tmp_path / "cam"
    cam.mkdir()
    for i in range(3):
        name = f"20250228_120000_000_RS015_{2520 + i}_A.jpg"
        cv2.imwrite(str(cam / name), np.full((16, 16, 3), 40 + i, dtype=np.uint8))
        (cam / (name[:-4] + ".ini")).write_bytes(INI_BYTES)
    inner = tmp_path / "20250228.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        for p in cam.iterdir():
            zf.write(p, "cam/" + p.name)
    outer = tmp_path / "2025.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "02/20250228.zip")

    res = ImportService(db, workspace).import_archive(outer)
    assert res.images_found == 3
    assert res.images_new == 3

    vehicles = VehicleRepository(db)
    engine = _engine(db, vehicles)
    run = engine.run(res.job_id)
    assert run.processed == 3

    row = db.query_one(
        "SELECT camera_id, speed, speed_limit, location, device_serial, direction "
        "FROM images WHERE processing_status='completed' LIMIT 1"
    )
    assert row["camera_id"] == "RS015"
    assert row["speed"] == 52.0
    assert row["speed_limit"] == 30.0
    assert row["location"] == "外環-棒球場"
    assert row["device_serial"] == "2412BAD00801"
    assert row["direction"] == "車尾"


def test_resume_no_reprocess(workspace, db, tmp_path):
    cam = tmp_path / "cam"
    cam.mkdir()
    for i in range(2):
        name = f"20250228_120000_000_RS015_{2520 + i}_A.jpg"
        cv2.imwrite(str(cam / name), np.full((16, 16, 3), 40 + i, dtype=np.uint8))
    inner = tmp_path / "20250228.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        for p in cam.iterdir():
            zf.write(p, "cam/" + p.name)
    outer = tmp_path / "2025.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "02/20250228.zip")

    res = ImportService(db, workspace).import_archive(outer)
    vehicles = VehicleRepository(db)
    engine = _engine(db, vehicles)
    first = engine.run(res.job_id)
    assert first.processed == 2
    second = engine.run(res.job_id)
    assert second.processed == 0
    assert second.final_state == JobState.COMPLETED