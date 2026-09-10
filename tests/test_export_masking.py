import json

import numpy as np
import pytest

from vehicle_dataset_manager.exporting.masking import (
    infer_vehicle_crop_bbox,
    mask_plate,
    parse_bbox,
    relative_plate_bbox,
)


def test_parse_bbox_accepts_dict_sequence_and_json():
    expected = (10, 20, 50, 40)
    assert parse_bbox({"x1": 10, "y1": 20, "x2": 50, "y2": 40}) == expected
    assert parse_bbox([10, 20, 50, 40]) == expected
    assert parse_bbox(json.dumps({"x1": 10, "y1": 20, "x2": 50, "y2": 40})) == expected
    assert parse_bbox({"x1": 10}) is None


def test_infer_crop_bbox_prefers_new_persisted_value():
    direct = {"x1": 6, "y1": 7, "x2": 106, "y2": 87}
    assert infer_vehicle_crop_bbox(
        direct,
        [],
        crop_width=100,
        crop_height=80,
        source_width=300,
        source_height=200,
    ) == (6, 7, 106, 87)


def test_infer_legacy_crop_bbox_matches_actual_crop_dimensions():
    boxes = json.dumps(
        [
            {"x1": 50, "y1": 40, "x2": 150, "y2": 120},
            {"x1": 10, "y1": 10, "x2": 40, "y2": 30},
        ]
    )
    assert infer_vehicle_crop_bbox(
        None,
        boxes,
        crop_width=108,
        crop_height=88,
        source_width=300,
        source_height=200,
    ) == (46, 36, 154, 124)


def test_infer_legacy_crop_bbox_rejects_mismatch():
    assert infer_vehicle_crop_bbox(
        None,
        [{"x1": 10, "y1": 10, "x2": 30, "y2": 30}],
        crop_width=300,
        crop_height=200,
        source_width=500,
        source_height=400,
    ) is None


def test_relative_plate_bbox_translates_and_clips():
    assert relative_plate_bbox(
        {"x1": 90, "y1": 70, "x2": 130, "y2": 90},
        (46, 36, 154, 124),
        crop_width=108,
        crop_height=88,
    ) == (44, 34, 84, 54)


@pytest.mark.parametrize("method", ["solid_color", "blur", "inpaint"])
def test_mask_plate_returns_new_same_size_image(method):
    image = np.random.default_rng(42).integers(
        0, 256, size=(80, 120, 3), dtype=np.uint8
    )
    original = image.copy()
    masked, bbox = mask_plate(image, (40, 30, 80, 50), method=method, margin=4)
    assert masked.shape == image.shape
    assert bbox == (36, 26, 84, 54)
    assert np.array_equal(image, original)
    assert not np.array_equal(masked[26:54, 36:84], original[26:54, 36:84])


def test_mask_plate_rejects_unknown_method():
    with pytest.raises(ValueError, match="unknown plate mask"):
        mask_plate(np.zeros((20, 20, 3), dtype=np.uint8), (2, 2, 10, 10), method="cloud")
