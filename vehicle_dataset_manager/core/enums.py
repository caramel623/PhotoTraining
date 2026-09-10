"""Enumerations shared across the whole application.

Using ``str`` mixins makes the values JSON/SQLite friendly (they serialize to
plain strings) while still giving us enum semantics in Python.
"""
from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    """Base: ``str(value)`` yields the raw value, useful for DB storage."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class ArchiveType(_StrEnum):
    ZIP = "zip"
    SEVEN_Z = "7z"
    UNKNOWN = "unknown"


class ProcessingStatus(_StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReviewStatus(_StrEnum):
    UNREVIEWED = "unreviewed"
    VERIFIED_SAME = "verified_same_vehicle"
    VERIFIED_NOT_SAME = "verified_not_same_vehicle"
    UNCERTAIN = "uncertain"
    EXCLUDED = "excluded"


class GroupVerification(_StrEnum):
    AUTOMATIC_ONLY = "automatic_only"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"


class GroupSource(_StrEnum):
    PLATE_EXACT = "plate_exact"
    PLATE_FUZZY = "plate_fuzzy"
    MANUAL = "manual"
    REID = "reid"
    MIXED = "mixed"


class OcrQuality(_StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class VehicleType(_StrEnum):
    MOTORCYCLE = "motorcycle"
    CAR = "car"
    BUS = "bus"
    TRUCK = "truck"
    BICYCLE = "bicycle"
    SCOOTER = "scooter"
    UNKNOWN = "unknown"


class QualityFlag(_StrEnum):
    BAD_IMAGE = "BAD_IMAGE"
    LOW_RESOLUTION = "LOW_RESOLUTION"
    BLURRY = "BLURRY"
    TOO_SMALL = "TOO_SMALL"
    OCR_LOW_CONFIDENCE = "OCR_LOW_CONFIDENCE"
    NO_VEHICLE = "NO_VEHICLE"
    NO_PLATE = "NO_PLATE"
    MULTIPLE_VEHICLES = "MULTIPLE_VEHICLES"
    DUPLICATE = "DUPLICATE"
    NEAR_DUPLICATE = "NEAR_DUPLICATE"


class JobState(_StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Priority for resolving a vehicle-group training label, highest first.
LABEL_PRIORITY = ("human_verified", "high_confidence_plate_match", "automatic_candidate")