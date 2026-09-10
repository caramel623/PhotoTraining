from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from vehicle_dataset_manager.core.enums import GroupVerification
from vehicle_dataset_manager.database.repositories import (
    ExportRepository,
    ImageRepository,
    VehicleRepository,
)
from vehicle_dataset_manager.exporting import DatasetExporter, ExportOptions, SplitSpec


def _write_scene(path: Path, seed: int) -> np.ndarray:
    image = np.random.default_rng(seed).integers(
        0, 256, size=(200, 300, 3), dtype=np.uint8
    )
    assert cv2.imwrite(str(path), image)
    return image


def _seed_image(db, tmp_path, *, vehicle_id, index, camera, year, plate_bbox=True):
    images = ImageRepository(db)
    vehicles = VehicleRepository(db)
    source = tmp_path / f"source_{index}.jpg"
    full = _write_scene(source, index)
    crop_rect = {"x1": 40, "y1": 30, "x2": 200, "y2": 150}
    crop_path = tmp_path / f"crop_{index}.jpg"
    assert cv2.imwrite(str(crop_path), full[30:150, 40:200])
    image_id = images.upsert_from_scan(
        original_filename=source.name,
        source_path=str(source),
        camera_id=camera,
        archive_year=year,
    )
    fields = {
        "date": f"{year}-01-{index:02d}",
        "width": 300,
        "height": 200,
        "vehicle_bbox": [crop_rect],
        "vehicle_crop_bbox": crop_rect,
        "vehicle_crop_path": str(crop_path),
        "plate_text_normalized": "BFY1765",
    }
    if plate_bbox:
        fields["plate_bbox"] = {"x1": 100, "y1": 90, "x2": 150, "y2": 110}
    images.mark_completed(image_id, **fields)
    vehicles.add_member(vehicle_id, image_id, label_source="manual")
    return image_id, source


def _verified_group(db, plate):
    vehicles = VehicleRepository(db)
    vehicle_id = vehicles.get_or_create_for_plate(plate)
    vehicles.set_verification(vehicle_id, GroupVerification.VERIFIED)
    return vehicle_id


def _options(**changes):
    values = {"split": SplitSpec(train_ratio=1, val_ratio=0, test_ratio=0)}
    values.update(changes)
    return ExportOptions(**values)


def test_dataset_exporter_writes_complete_portable_tree(db, tmp_path):
    group_a = _verified_group(db, "AAA111")
    group_b = _verified_group(db, "BBB222")
    original_bytes = {}
    for vehicle_id, index, camera, year in (
        (group_a, 1, "CAM-A", 2024),
        (group_a, 2, "CAM-B", 2025),
        (group_b, 3, "CAM-A", 2025),
    ):
        image_id, source = _seed_image(
            db,
            tmp_path,
            vehicle_id=vehicle_id,
            index=index,
            camera=camera,
            year=year,
        )
        original_bytes[image_id] = source.read_bytes()

    output = tmp_path / "Dataset"
    repository = ExportRepository(db)
    result = DatasetExporter(repository).export(
        output, _options(), created_by="pytest"
    )
    assert result.exported == 3
    assert result.skipped == 0
    assert result.split_counts == {"train": 3}
    assert result.pair_count >= 1
    assert result.triplet_count == 1

    expected = (
        "images",
        "vehicle_crops",
        "plate_crops",
        "reid_crops",
        "metadata/images.csv",
        "metadata/vehicles.csv",
        "metadata/labels.csv",
        "metadata/manifest.jsonl",
        "metadata/pairs.csv",
        "metadata/triplets.csv",
        "splits/train.csv",
        "splits/val.csv",
        "splits/test.csv",
        "export_config.json",
        "export_state.json",
    )
    assert all((output / relative).exists() for relative in expected)

    manifest = output / "metadata" / "manifest.jsonl"
    records = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 3
    assert str(tmp_path) not in json.dumps(records)
    assert all(record["plate_mask_bbox"] for record in records)
    assert all(record["vehicle_id"].startswith("Vehicle_") for record in records)

    reid = cv2.imread(str(output / records[0]["reid_crop"]))
    assert reid is not None
    assert reid[58:82, 58:112].mean() < 8
    plate = cv2.imread(str(output / records[0]["plate_crop"]))
    assert plate.shape[:2] == (20, 50)
    for image_id, before in original_bytes.items():
        record = next(row for row in records if row["image_id"] == image_id)
        assert (output / record["original_image"]).read_bytes() == before
    assert repository.list_exports()[0]["image_count"] == 3


def test_defaults_to_verified_and_skips_missing_plate(db, tmp_path):
    verified = _verified_group(db, "AAA111")
    automatic = VehicleRepository(db).get_or_create_for_plate("BBB222")
    _seed_image(
        db,
        tmp_path,
        vehicle_id=verified,
        index=1,
        camera="A",
        year=2025,
        plate_bbox=False,
    )
    _seed_image(
        db,
        tmp_path,
        vehicle_id=automatic,
        index=2,
        camera="B",
        year=2025,
    )
    result = DatasetExporter(ExportRepository(db)).export(
        tmp_path / "safe", _options()
    )
    assert result.total == 1
    assert result.exported == 0
    assert result.skip_reasons == {"missing_or_outside_plate_bbox": 1}


def test_export_resumes_and_rejects_different_settings(db, tmp_path):
    vehicle_id = _verified_group(db, "AAA111")
    for index in (1, 2):
        _seed_image(
            db,
            tmp_path,
            vehicle_id=vehicle_id,
            index=index,
            camera="A",
            year=2025,
        )
    output = tmp_path / "resume"
    calls = 0

    def cancel_after_one():
        nonlocal calls
        calls += 1
        return calls > 1

    exporter = DatasetExporter(ExportRepository(db))
    first = exporter.export(output, _options(), should_cancel=cancel_after_one)
    assert first.cancelled is True
    assert first.exported == 1
    partial_record = json.loads(
        (output / "metadata" / "manifest.jsonl").read_text(encoding="utf-8")
    )
    first_reid = output / partial_record["reid_crop"]
    first_reid_mtime = first_reid.stat().st_mtime_ns

    resumed = exporter.export(output, _options())
    assert resumed.cancelled is False
    assert resumed.exported == 2
    assert first_reid.stat().st_mtime_ns == first_reid_mtime
    state = json.loads((output / "export_state.json").read_text(encoding="utf-8"))
    assert state["complete"] is True
    with pytest.raises(ValueError, match="different settings"):
        exporter.export(output, _options(mask_method="blur"))
