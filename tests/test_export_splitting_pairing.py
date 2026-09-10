from vehicle_dataset_manager.exporting.pairing import build_pairs
from vehicle_dataset_manager.exporting.splitting import SplitSpec, assign_splits


def _row(image_id, vehicle_id, *, year=2025, camera="A", plate=None):
    return {
        "image_id": image_id,
        "vehicle_id": vehicle_id,
        "archive_year": year,
        "date": f"{year}-01-01",
        "camera_id": camera,
        "plate_normalized": plate,
        "reid_crop": f"reid_crops/{image_id:08d}.jpg",
        "split": "train",
    }


def test_group_hash_split_is_deterministic_and_has_no_vehicle_leakage():
    rows = [
        _row(1, "Vehicle_1"),
        _row(2, "Vehicle_1"),
        _row(3, "Vehicle_2"),
        _row(4, "Vehicle_3"),
    ]
    spec = SplitSpec(seed=7)
    first = assign_splits(rows, spec)
    second = assign_splits(list(reversed(rows)), spec)
    assert first == second
    assert first[1] == first[2]
    assert set(first.values()) <= {"train", "val", "test"}


def test_time_split_moves_whole_vehicle_to_test_if_any_member_is_test_year():
    rows = [
        _row(1, "Vehicle_1", year=2025),
        _row(2, "Vehicle_1", year=2026),
        _row(3, "Vehicle_2", year=2025),
        _row(4, "Vehicle_3", year=2024),
    ]
    result = assign_splits(
        rows, SplitSpec(strategy="time", val_years=(2024,), test_years=(2026,))
    )
    assert result[1] == result[2] == "test"
    assert result[3] == "train"
    assert result[4] == "val"


def test_camera_split_moves_whole_vehicle_to_configured_camera_split():
    rows = [
        _row(1, "Vehicle_1", camera="A"),
        _row(2, "Vehicle_1", camera="C"),
        _row(3, "Vehicle_2", camera="B"),
    ]
    result = assign_splits(
        rows, SplitSpec(strategy="camera", test_cameras=("c",))
    )
    assert result[1] == result[2] == "test"
    assert result[3] == "train"


def test_pairs_prioritize_cross_camera_and_same_plate_negative():
    rows = [
        _row(1, "Vehicle_A", year=2024, camera="A", plate="SAME123"),
        _row(2, "Vehicle_A", year=2025, camera="B", plate="SAME123"),
        _row(3, "Vehicle_B", year=2025, camera="A", plate="SAME123"),
    ]
    pairs, triplets = build_pairs(
        rows, max_positive_per_vehicle=1, negative_ratio=1, seed=9
    )
    positive = [row for row in pairs if row["label"] == 1]
    negatives = [row for row in pairs if row["label"] == 0]
    assert len(positive) == 1
    assert positive[0]["pair_type"] == "cross_camera"
    assert negatives
    assert all(row["pair_type"] == "same_plate_manual_split" for row in negatives)
    assert len(triplets) == 1
    assert triplets[0]["negative_type"] == "same_plate_manual_split"


def test_pairs_never_cross_dataset_splits():
    rows = [
        _row(1, "Vehicle_A"),
        _row(2, "Vehicle_A"),
        _row(3, "Vehicle_B"),
        _row(4, "Vehicle_C"),
    ]
    rows[2]["split"] = "test"
    rows[3]["split"] = "test"
    pairs, _triplets = build_pairs(rows, seed=3)
    path_to_split = {row["reid_crop"]: row["split"] for row in rows}
    assert all(
        path_to_split[pair["image_a"]] == path_to_split[pair["image_b"]]
        for pair in pairs
    )
