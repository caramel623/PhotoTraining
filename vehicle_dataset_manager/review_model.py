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
from vehicle_dataset_manager.services.metadata_resolver import MetadataResolver


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
    ini_plate_text: Optional[str] = None
    ocr_plate_text: Optional[str] = None
    effective_plate_text: Optional[str] = None
    plate_source: str = "unknown"
    plate_validation_status: str = "unknown"
    ini_encoding: Optional[str] = None
    ini_parse_status: str = "missing"
    metadata_conflict: bool = False
    conflict_type: Optional[str] = None


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
        if verification == "metadata_conflicts":
            count = len(self._conflict_rows())
            return [GroupSummary("__metadata__", "INI／OCR 衝突佇列", "automatic_only", "metadata", count)]
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
        if vehicle_id == "__metadata__":
            return self.list_groups("metadata_conflicts")[0]
        for g in self.list_groups():
            if g.vehicle_id == vehicle_id:
                return g
        return None

    def group_images(self, vehicle_id: str) -> list[GroupImage]:
        out: list[GroupImage] = []
        rows = self._conflict_rows() if vehicle_id == "__metadata__" else self.vehicles.members_for_review(vehicle_id)
        for m in rows:
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
                    ini_plate_text=m.get("ini_plate_text"),
                    ocr_plate_text=m.get("ocr_plate_text"),
                    effective_plate_text=m.get("effective_plate_text"),
                    plate_source=m.get("plate_source") or "unknown",
                    plate_validation_status=m.get("plate_validation_status") or "unknown",
                    ini_encoding=m.get("ini_encoding"),
                    ini_parse_status=m.get("ini_parse_status") or "missing",
                    metadata_conflict=bool(m.get("metadata_conflict")),
                    conflict_type=m.get("conflict_type"),
                )
            )
        return out

    def _conflict_rows(self):
        return [dict(row) for row in self.images.db.query(
            "SELECT *, plate_text_raw AS effective_plate_text FROM images "
            "WHERE metadata_conflict=1 OR plate_validation_status='OCR_MISMATCH' ORDER BY image_id"
        )]

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
            image_id, status, vehicle_id=None if vehicle_id == "__metadata__" else vehicle_id, note=note
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
            manual_plate_text=new_plate.strip().upper(),
            plate_text_normalized=plate, plate_text_raw=new_plate.strip().upper(),
            plate_source="manual", label_confidence=1.0, label_trust_level="HIGH",
        )
        details = self.images.get_details(image_id) or {}
        resolved = MetadataResolver.resolve_plate(
            manual_plate=new_plate, ini_plate=details.get("ini_plate_text"),
            ocr_plate=details.get("ocr_plate_text"),
            ocr_confidence=details.get("plate_confidence"),
        )
        self.images.set_result_fields(
            image_id, plate_validation_status=resolved.validation_status,
            metadata_conflict=int(resolved.conflict), conflict_type=resolved.conflict_type,
        )
        target = self.vehicles.get_or_create_for_plate(plate, source=GroupSource.MANUAL)
        if current_vehicle == "__metadata__":
            current_vehicle = self.vehicles.vehicle_for_image(image_id)
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
        """Confirm every member atomically; individual reviews remain editable."""
        with self.images.db.transaction():
            for member in self.vehicles.members_for_review(vehicle_id):
                self.set_image_status(
                    member["image_id"], ReviewStatus.VERIFIED_SAME,
                    vehicle_id=vehicle_id,
                )
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
