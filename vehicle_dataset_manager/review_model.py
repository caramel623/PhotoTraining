"""Manual-review domain logic (Qt-free).

Assembles the data the Review GUI needs and wraps the per-image / per-group
review actions. Operates only on repositories, so it is fully testable without
Qt. Originals are never modified: actions touch ``images`` metadata and
``vehicle_members`` relationships only (README 14).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from vehicle_dataset_manager.core.enums import (
    GroupSource,
    GroupVerification,
    ReviewStatus,
)
from vehicle_dataset_manager.services.plate import normalize_plate


@dataclass
class GroupSummary:
    vehicle_id: str
    plate_normalized: Optional[str]
    verification: str
    source: str
    image_count: int
    cameras: list = field(default_factory=list)
    years: list = field(default_factory=list)


@dataclass
class GroupImage:
    image_id: int
    original_filename: str
    review_status: str
    plate_text_normalized: Optional[str]
    display_path: Optional[str]
    camera_id: Optional[str] = None
    date: Optional[str] = None
    confidence: Optional[float] = None


def _resolve_display_path(crop_path: Optional[str], source_path: Optional[str]) -> Optional[str]:
    """Prefer the vehicle crop (what the reviewer cares about); fall back to the
    full original. Returns the first existing file, else None."""
    for candidate in (crop_path, source_path):
        if candidate:
            p = Path(candidate)
            if p.is_file():
                return str(p)
    return None


class ReviewModel:
    def __init__(self, vehicles, images, reviews) -> None:
        self.vehicles = vehicles
        self.images = images
        self.reviews = reviews

    # -- read ------------------------------------------------------------ #
    def list_groups(self, verification: Optional[str] = None) -> list[GroupSummary]:
        rows = self.vehicles.list_vehicles(limit=100000)
        out: list[GroupSummary] = []
        for r in rows:
            if verification and r.get("verification") != verification:
                continue
            vid = r["vehicle_id"]
            members = self.vehicles.members_for_review(vid)
            cams = sorted({m["camera_id"] for m in members if m.get("camera_id")})
            years = sorted({int(m["date"][:4]) for m in members if m.get("date") and m["date"][:4].isdigit()})
            out.append(
                GroupSummary(
                    vehicle_id=vid,
                    plate_normalized=r.get("plate_normalized"),
                    verification=r.get("verification"),
                    source=r.get("source"),
                    image_count=r.get("image_count", 0),
                    cameras=cams,
                    years=years,
                )
            )
        return out

    def group_summary(self, vehicle_id: str) -> Optional[GroupSummary]:
        for g in self.list_groups():
            if g.vehicle_id == vehicle_id:
                return g
        return None

    def group_images(self, vehicle_id: str) -> list[GroupImage]:
        out: list[GroupImage] = []
        for m in self.vehicles.members_for_review(vehicle_id):
            out.append(
                GroupImage(
                    image_id=m["image_id"],
                    original_filename=m.get("original_filename") or "",
                    review_status=m.get("review_status") or ReviewStatus.UNREVIEWED.value,
                    plate_text_normalized=m.get("plate_text_normalized"),
                    display_path=_resolve_display_path(
                        m.get("vehicle_crop_path"), m.get("source_path")
                    ),
                    camera_id=m.get("camera_id"),
                    date=m.get("date"),
                    confidence=m.get("confidence"),
                )
            )
        return out

    # -- per-image review ------------------------------------------------ #
    def set_image_status(
        self,
        image_id: int,
        status: ReviewStatus,
        *,
        vehicle_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> int:
        return self.reviews.upsert(
            image_id, status, vehicle_id=vehicle_id, note=note
        )

    def edit_image_plate(
        self,
        image_id: int,
        new_plate: str,
        *,
        current_vehicle: str,
    ) -> str:
        """Re-tag an image's plate and re-link its membership to the group that
        matches the new plate (weak-supervision model). Returns the vehicle id
        the image now belongs to. Originals untouched."""
        plate = normalize_plate(new_plate)
        if not plate:
            raise ValueError("plate must contain at least one letter or digit")
        self.images.set_result_fields(
            image_id,
            plate_text_normalized=plate,
            plate_text_raw=new_plate.strip().upper(),
        )
        target = self.vehicles.get_or_create_for_plate(plate, source=GroupSource.MANUAL)
        if target != current_vehicle:
            self.vehicles.remove_member(current_vehicle, image_id)
            self.vehicles.add_member(target, image_id, label_source="manual")
        return target

    # -- group verification --------------------------------------------- #
    def suggest_verification(self, vehicle_id: str) -> GroupVerification:
        members = self.vehicles.members_for_review(vehicle_id)
        if not members:
            return GroupVerification.AUTOMATIC_ONLY
        statuses = [m.get("review_status") or ReviewStatus.UNREVIEWED.value for m in members]
        same = sum(1 for s in statuses if s == ReviewStatus.VERIFIED_SAME.value)
        unreviewed = sum(1 for s in statuses if s == ReviewStatus.UNREVIEWED.value)
        if same == 0 or unreviewed > 0:
            return GroupVerification.AUTOMATIC_ONLY
        conflicting = any(
            s in (ReviewStatus.VERIFIED_NOT_SAME.value, ReviewStatus.UNCERTAIN.value)
            for s in statuses
        )
        return (
            GroupVerification.PARTIALLY_VERIFIED
            if conflicting
            else GroupVerification.VERIFIED
        )

    def confirm_group(self, vehicle_id: str) -> GroupVerification:
        """Explicit human confirmation -> verified (highest label priority)."""
        self.vehicles.set_verification(vehicle_id, GroupVerification.VERIFIED)
        return GroupVerification.VERIFIED

    def sync_verification(self, vehicle_id: str) -> GroupVerification:
        """Persist the group state implied by its current per-image reviews."""
        verification = self.suggest_verification(vehicle_id)
        self.vehicles.set_verification(vehicle_id, verification)
        return verification

    # -- merge / split --------------------------------------------------- #
    def merge(self, source_id: str, target_id: str) -> str:
        return self.vehicles.merge(source_id, target_id)

    def split(self, vehicle_id: str, image_ids: Iterable[int]) -> str:
        return self.vehicles.split(vehicle_id, list(image_ids))
