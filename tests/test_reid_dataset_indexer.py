from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from vehicle_dataset_manager.reid import DatasetIndexBuilder, EmbeddingIndex


class _Engine:
    model_id = "synthetic-reid:v1"

    def extract_embedding(self, image):
        mean = float(image.mean()) / 255.0
        return np.array([mean + 0.1, 1.0 - mean + 0.1], dtype=np.float32)


def _dataset(tmp_path, count=3):
    root = tmp_path / "Dataset"
    crops = root / "reid_crops"
    metadata = root / "metadata"
    crops.mkdir(parents=True)
    metadata.mkdir()
    rows = []
    for index in range(1, count + 1):
        relative = f"reid_crops/{index:08d}.jpg"
        assert cv2.imwrite(
            str(root / relative),
            np.full((20, 30, 3), index * 50, dtype=np.uint8),
        )
        rows.append(
            {
                "image_id": index,
                "vehicle_id": f"Vehicle_{index:06d}",
                "camera_id": "RS015",
                "archive_year": 2025,
                "split": "train",
                "reid_crop": relative,
                "label_priority": "human_verified",
                "source_updated_at": "2026-09-11T00:00:00+00:00",
                "plate_normalized": "MUST-NOT-BE-INDEXED",
            }
        )
    (metadata / "manifest.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return root


def test_builder_cancels_resumes_and_excludes_plate_metadata(tmp_path):
    root = _dataset(tmp_path)
    calls = 0

    def cancel_after_one():
        nonlocal calls
        calls += 1
        return calls > 1

    builder = DatasetIndexBuilder(_Engine(), checkpoint_every=1)
    first = builder.build(root, should_cancel=cancel_after_one)
    assert first.cancelled is True
    assert first.indexed == 1
    resumed = builder.build(root)
    assert resumed.cancelled is False
    assert resumed.reused == 1
    assert resumed.indexed == 2

    index = EmbeddingIndex.load(root / resumed.index_path)
    assert len(index) == 3
    result = index.search(np.array([0.5, 0.5]), limit=3)
    assert all("plate_normalized" not in item.metadata for item in result)
    state = json.loads(
        (root / "features" / "index_state.json").read_text(encoding="utf-8")
    )
    assert state["complete"] is True
    assert state["model_id"] == _Engine.model_id


def test_builder_rejects_unsafe_manifest_path(tmp_path):
    root = _dataset(tmp_path, count=1)
    manifest = root / "metadata" / "manifest.jsonl"
    row = json.loads(manifest.read_text(encoding="utf-8"))
    row["reid_crop"] = "../private.jpg"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        DatasetIndexBuilder(_Engine()).build(root)


def test_builder_rejects_resume_after_dataset_metadata_changes(tmp_path):
    root = _dataset(tmp_path, count=1)
    builder = DatasetIndexBuilder(_Engine())
    builder.build(root)
    manifest = root / "metadata" / "manifest.jsonl"
    row = json.loads(manifest.read_text(encoding="utf-8"))
    row["source_updated_at"] = "2026-09-12T00:00:00+00:00"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest changed"):
        builder.build(root)


def test_builder_rejects_index_from_different_model(tmp_path):
    root = _dataset(tmp_path, count=1)
    DatasetIndexBuilder(_Engine()).build(root)

    class OtherEngine(_Engine):
        model_id = "other:v1"

    with pytest.raises(ValueError, match="different model"):
        DatasetIndexBuilder(OtherEngine()).build(root)
