"""Deterministic positive, negative, and triplet generation."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from itertools import combinations


def build_pairs(
    records: list[dict],
    *,
    max_positive_per_vehicle: int = 20,
    negative_ratio: int = 2,
    seed: int = 42,
) -> tuple[list[dict], list[dict]]:
    """Return pair rows and anchor/positive/negative triplets.

    Positives prioritize cross-camera, then cross-year/date examples.
    Negatives prioritize manually separated identical plates, then same-camera
    candidates; deterministic hashes break ties without global randomness.
    """
    by_group_split: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_split: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        split = str(row["split"])
        by_group_split[(str(row["vehicle_id"]), split)].append(row)
        by_split[split].append(row)

    positives: list[dict] = []
    positive_records: list[tuple[dict, dict]] = []
    for (_vehicle_id, split), members in sorted(by_group_split.items()):
        ranked = sorted(
            combinations(members, 2),
            key=lambda pair: (_positive_rank(*pair), _tie(pair[0], pair[1], seed)),
        )
        for left, right in ranked[: max(0, max_positive_per_vehicle)]:
            positive_records.append((left, right))
            positives.append(_pair_row(left, right, 1, _positive_type(left, right), split))

    negative_budget = max(
        len(records), len(positive_records) * max(0, int(negative_ratio))
    )
    negatives: list[dict] = []
    seen: set[tuple[int, int]] = set()
    ordered_anchors = sorted(records, key=lambda row: int(row["image_id"]))
    anchor_index = 0
    while ordered_anchors and len(negatives) < negative_budget:
        anchor = ordered_anchors[anchor_index % len(ordered_anchors)]
        candidates = [
            row
            for row in by_split[str(anchor["split"])]
            if row["vehicle_id"] != anchor["vehicle_id"]
        ]
        candidates.sort(
            key=lambda row: (_negative_rank(anchor, row), _tie(anchor, row, seed))
        )
        chosen = None
        for candidate in candidates:
            key = tuple(sorted((int(anchor["image_id"]), int(candidate["image_id"]))))
            if key not in seen:
                seen.add(key)
                chosen = candidate
                break
        if chosen is not None:
            negatives.append(
                _pair_row(
                    anchor,
                    chosen,
                    0,
                    _negative_type(anchor, chosen),
                    str(anchor["split"]),
                )
            )
        anchor_index += 1
        if anchor_index >= len(ordered_anchors) and chosen is None:
            break
        if anchor_index > max(1, len(ordered_anchors) * negative_budget * 2):
            break

    triplets: list[dict] = []
    for anchor, positive in positive_records:
        candidates = [
            row
            for row in by_split[str(anchor["split"])]
            if row["vehicle_id"] != anchor["vehicle_id"]
        ]
        if not candidates:
            continue
        negative = min(
            candidates,
            key=lambda row: (_negative_rank(anchor, row), _tie(anchor, row, seed)),
        )
        triplets.append(
            {
                "anchor": anchor["reid_crop"],
                "positive": positive["reid_crop"],
                "negative": negative["reid_crop"],
                "split": anchor["split"],
                "negative_type": _negative_type(anchor, negative),
            }
        )
    return positives + negatives, triplets


def _pair_row(left: dict, right: dict, label: int, pair_type: str, split: str) -> dict:
    return {
        "image_a": left["reid_crop"],
        "image_b": right["reid_crop"],
        "label": label,
        "pair_type": pair_type,
        "split": split,
    }


def _year(row: dict) -> str:
    return str(row.get("archive_year") or str(row.get("date") or "")[:4])


def _positive_rank(left: dict, right: dict) -> tuple[int, int, int]:
    same_camera = left.get("camera_id") == right.get("camera_id")
    same_year = _year(left) == _year(right)
    same_date = left.get("date") == right.get("date")
    return (int(same_camera), int(same_year), int(same_date))


def _positive_type(left: dict, right: dict) -> str:
    if left.get("camera_id") != right.get("camera_id"):
        return "cross_camera"
    if _year(left) != _year(right):
        return "cross_year"
    if left.get("date") != right.get("date"):
        return "cross_date"
    return "same_vehicle"


def _negative_rank(left: dict, right: dict) -> tuple[int, int]:
    same_plate = (
        left.get("plate_normalized")
        and left.get("plate_normalized") == right.get("plate_normalized")
    )
    same_camera = left.get("camera_id") == right.get("camera_id")
    return (0 if same_plate else 1, 0 if same_camera else 1)


def _negative_type(left: dict, right: dict) -> str:
    if (
        left.get("plate_normalized")
        and left.get("plate_normalized") == right.get("plate_normalized")
    ):
        return "same_plate_manual_split"
    if left.get("camera_id") == right.get("camera_id"):
        return "same_camera"
    return "different_vehicle"


def _tie(left: dict, right: dict, seed: int) -> str:
    payload = f"{seed}:{left['image_id']}:{right['image_id']}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
