"""Plate-crop coordinate conversion and local masking helpers."""
from __future__ import annotations

import json
from typing import Any, Optional

import cv2
import numpy as np

BBox = tuple[int, int, int, int]


def parse_bbox(value: Any) -> Optional[BBox]:
    """Return a normalized `(x1, y1, x2, y2)` bbox or `None`."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(value, dict):
        try:
            coords = tuple(
                int(round(float(value[key]))) for key in ("x1", "y1", "x2", "y2")
            )
        except (KeyError, TypeError, ValueError):
            return None
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            coords = tuple(int(round(float(item))) for item in value)
        except (TypeError, ValueError):
            return None
    else:
        return None
    x1, y1, x2, y2 = coords
    return coords if x2 > x1 and y2 > y1 else None


def parse_bbox_list(value: Any) -> list[BBox]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(value, list):
        return []
    return [bbox for item in value if (bbox := parse_bbox(item)) is not None]


def clip_bbox(bbox: BBox, width: int, height: int) -> Optional[BBox]:
    x1, y1, x2, y2 = bbox
    clipped = (
        max(0, min(width, x1)),
        max(0, min(height, y1)),
        max(0, min(width, x2)),
        max(0, min(height, y2)),
    )
    return clipped if clipped[2] > clipped[0] and clipped[3] > clipped[1] else None


def infer_vehicle_crop_bbox(
    stored_crop_bbox: Any,
    stored_vehicle_bboxes: Any,
    *,
    crop_width: int,
    crop_height: int,
    source_width: Optional[int],
    source_height: Optional[int],
    legacy_margin: int = 4,
) -> Optional[BBox]:
    """Resolve the original-image rectangle used to create a vehicle crop.

    New rows carry `vehicle_crop_bbox` directly. Legacy rows only contain
    detector boxes; for those, compare every margin-expanded candidate with
    the actual crop dimensions. Ambiguous/mismatched data returns `None` so
    the exporter never masks a guessed location.
    """
    direct = parse_bbox(stored_crop_bbox)
    if direct is not None:
        return direct
    candidates = parse_bbox_list(stored_vehicle_bboxes)
    if not candidates:
        return None
    width_limit = int(source_width) if source_width else None
    height_limit = int(source_height) if source_height else None
    scored: list[tuple[int, BBox]] = []
    for x1, y1, x2, y2 in candidates:
        expanded = (
            x1 - legacy_margin,
            y1 - legacy_margin,
            x2 + legacy_margin,
            y2 + legacy_margin,
        )
        if width_limit and height_limit:
            clipped = clip_bbox(expanded, width_limit, height_limit)
            if clipped is None:
                continue
            expanded = clipped
        width_error = abs((expanded[2] - expanded[0]) - crop_width)
        height_error = abs((expanded[3] - expanded[1]) - crop_height)
        scored.append((width_error + height_error, expanded))
    if not scored:
        return None
    score, best = min(scored, key=lambda item: item[0])
    tolerance = max(6, int(round((crop_width + crop_height) * 0.04)))
    return best if score <= tolerance else None


def relative_plate_bbox(
    plate_bbox: Any,
    vehicle_crop_bbox: BBox,
    *,
    crop_width: int,
    crop_height: int,
) -> Optional[BBox]:
    plate = parse_bbox(plate_bbox)
    if plate is None:
        return None
    vx1, vy1, _vx2, _vy2 = vehicle_crop_bbox
    relative = (
        plate[0] - vx1,
        plate[1] - vy1,
        plate[2] - vx1,
        plate[3] - vy1,
    )
    return clip_bbox(relative, crop_width, crop_height)


def mask_plate(
    image: np.ndarray,
    bbox: BBox,
    *,
    method: str = "solid_color",
    margin: int = 6,
) -> tuple[np.ndarray, BBox]:
    """Return a new plate-masked image and the actual clipped mask bbox."""
    if image is None or image.size == 0:
        raise ValueError("mask_plate: empty image")
    height, width = image.shape[:2]
    x1, y1, x2, y2 = bbox
    expanded = clip_bbox(
        (x1 - margin, y1 - margin, x2 + margin, y2 + margin), width, height
    )
    if expanded is None:
        raise ValueError("mask_plate: bbox is outside the image")
    x1, y1, x2, y2 = expanded
    output = image.copy()
    method = method.lower().strip()
    if method == "solid_color":
        output[y1:y2, x1:x2] = 0
    elif method == "blur":
        roi = output[y1:y2, x1:x2]
        minimum = min(roi.shape[:2])
        kernel = min(31, minimum if minimum % 2 else minimum - 1)
        if kernel < 3:
            output[y1:y2, x1:x2] = 0
        else:
            output[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (kernel, kernel), 0)
    elif method == "inpaint":
        mask = np.zeros((height, width), dtype=np.uint8)
        mask[y1:y2, x1:x2] = 255
        output = cv2.inpaint(output, mask, 3, cv2.INPAINT_TELEA)
    else:
        raise ValueError(f"unknown plate mask method: {method}")
    return output, expanded
