"""Read-only original-photo checks; Unicode-safe decoding shared by the pipeline."""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


def read_photo(path: Path):
    data = path.read_bytes()
    if not data:
        raise ValueError("empty image")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("failed to decode image")
    return image, hashlib.sha256(data).hexdigest()


def check_photo(path: str | None, expected_hash: str | None = None) -> str:
    if not path:
        return "missing"
    try:
        _, digest = read_photo(Path(path))
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    except (ValueError, cv2.error):
        return "corrupt"
    if expected_hash and digest != expected_hash:
        return "changed"
    return "ok" if expected_hash else "unverified"


@dataclass
class PhotoScanResult:
    total: int = 0
    checked: int = 0
    counts: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    cancelled: bool = False


class PhotoHealthService:
    def __init__(self, db):
        self.db = db

    def scan(self, on_progress=None, should_cancel=None):
        # Include missing files by starting from the database, not rglob.
        rows = self.db.query("SELECT image_id,source_path,sha256,original_archive FROM images ORDER BY image_id")
        result = PhotoScanResult(total=len(rows))
        counts = Counter()
        for row in rows:
            if should_cancel and should_cancel():
                result.cancelled = True
                break
            status = check_photo(row["source_path"], row["sha256"])
            counts[status] += 1
            result.checked += 1
            if status != "ok":
                result.issues.append(dict(row) | {"status": status})
            if on_progress and (result.checked % 25 == 0 or result.checked == result.total):
                on_progress(result.checked, result.total, "檢查工作區照片")
        result.counts = dict(counts)
        return result
