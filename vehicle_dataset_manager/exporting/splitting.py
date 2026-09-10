"""Leakage-safe, vehicle-group-level dataset split assignment."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SplitSpec:
    strategy: str = "group_hash"
    train_ratio: float = 0.70
    val_ratio: float = 0.15
    test_ratio: float = 0.15
    seed: int = 42
    val_years: tuple[int, ...] = ()
    test_years: tuple[int, ...] = ()
    val_cameras: tuple[str, ...] = ()
    test_cameras: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.strategy not in {"group_hash", "time", "camera"}:
            raise ValueError(f"unknown split strategy: {self.strategy}")
        if min(self.train_ratio, self.val_ratio, self.test_ratio) < 0:
            raise ValueError("split ratios cannot be negative")
        if self.train_ratio + self.val_ratio + self.test_ratio <= 0:
            raise ValueError("at least one split ratio must be positive")


def assign_splits(records: Iterable[dict], spec: SplitSpec) -> dict[int, str]:
    """Assign every image while keeping each vehicle in exactly one split."""
    spec.validate()
    rows = list(records)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["vehicle_id"])].append(row)
    group_splits: dict[str, str] = {}
    if spec.strategy == "time" and (spec.test_years or spec.val_years):
        test = set(spec.test_years)
        val = set(spec.val_years)
        for vehicle_id, members in grouped.items():
            years = {_year(row) for row in members}
            years.discard(None)
            group_splits[vehicle_id] = (
                "test" if years & test else "val" if years & val else "train"
            )
    elif spec.strategy == "camera" and (spec.test_cameras or spec.val_cameras):
        test = {value.casefold() for value in spec.test_cameras}
        val = {value.casefold() for value in spec.val_cameras}
        for vehicle_id, members in grouped.items():
            cameras = {
                str(row.get("camera_id") or "").casefold() for row in members
            }
            group_splits[vehicle_id] = (
                "test" if cameras & test else "val" if cameras & val else "train"
            )
    else:
        for vehicle_id in grouped:
            group_splits[vehicle_id] = _hash_split(vehicle_id, spec)
    return {
        int(row["image_id"]): group_splits[str(row["vehicle_id"])] for row in rows
    }


def _year(row: dict):
    value = row.get("archive_year")
    if value is None and row.get("date"):
        value = str(row["date"])[:4]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hash_split(vehicle_id: str, spec: SplitSpec) -> str:
    total = spec.train_ratio + spec.val_ratio + spec.test_ratio
    train_edge = spec.train_ratio / total
    val_edge = train_edge + spec.val_ratio / total
    digest = hashlib.sha256(f"{spec.seed}:{vehicle_id}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") / float(2**64)
    if value < train_edge:
        return "train"
    if value < val_edge:
        return "val"
    return "test"
