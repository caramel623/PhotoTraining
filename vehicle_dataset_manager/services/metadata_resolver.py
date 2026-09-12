"""Central priority and QA rules for filename, INI, OCR and manual metadata."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from vehicle_dataset_manager.services.ini_parser import ParsedIniMetadata, valid_ini_plate
from vehicle_dataset_manager.services.plate import normalize_plate


@dataclass(frozen=True)
class PlateResolution:
    raw: Optional[str]
    normalized: Optional[str]
    source: str
    validation_status: str
    confidence: Optional[float]
    trust_level: Optional[str]
    conflict: bool = False
    conflict_type: Optional[str] = None


class MetadataResolver:
    """Resolve effective values without mutating any original source value."""

    @staticmethod
    def resolve_plate(
        *,
        manual_plate: Optional[str] = None,
        ini_plate: Optional[str] = None,
        ocr_plate: Optional[str] = None,
        ocr_confidence: Optional[float] = None,
    ) -> PlateResolution:
        manual = normalize_plate(manual_plate)
        ini = normalize_plate(ini_plate) if valid_ini_plate(ini_plate) else None
        ocr = normalize_plate(ocr_plate)
        if manual:
            conflict = bool(ini and ini != manual)
            return PlateResolution(
                manual_plate, manual, "manual",
                "MANUAL_INI_MISMATCH" if conflict else ("MATCH" if ini and ini == manual else "NOT_CHECKED"),
                1.0, "HIGH", conflict,
                "MANUAL_INI_PLATE_MISMATCH" if conflict else None,
            )
        if ini:
            validation = "MATCH" if ocr and ocr == ini else "OCR_MISMATCH" if ocr else "NOT_CHECKED"
            return PlateResolution(ini_plate, ini, "ini", validation, 1.0, "HIGH")
        if ocr:
            confidence = float(ocr_confidence) if ocr_confidence is not None else None
            trust = "HIGH" if confidence is not None and confidence >= 0.8 else "MEDIUM" if confidence is not None and confidence >= 0.5 else "LOW"
            return PlateResolution(ocr_plate, ocr, "ocr", "NOT_CHECKED", confidence, trust)
        return PlateResolution(None, None, "unknown", "NOT_CHECKED", None, None)

    @classmethod
    def ini_database_fields(
        cls,
        parsed: ParsedIniMetadata,
        *,
        ini_path: str,
        member_path: str,
        ini_hash: str,
        parser_version: int,
        filename_metadata: Optional[dict] = None,
        existing: Optional[dict] = None,
    ) -> dict:
        existing = existing or {}
        qa = validate_ini_against_filename(parsed, filename_metadata or {})
        resolution = cls.resolve_plate(
            manual_plate=existing.get("manual_plate_text"),
            ini_plate=parsed.plate_text_raw if parsed.has_plate else None,
            ocr_plate=existing.get("ocr_plate_text") or (
                existing.get("plate_text_raw") if existing.get("plate_source") == "ocr" else None
            ),
            ocr_confidence=existing.get("plate_confidence"),
        )
        warnings = list(parsed.warnings) + qa
        try:
            existing_flags = json.loads(existing.get("quality_flags") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            existing_flags = []
        fields = {
            "ini_present": 1, "ini_path": ini_path,
            "ini_archive_member": member_path, "ini_parse_status": parsed.status,
            "ini_encoding": parsed.encoding_used, "ini_raw_metadata": json.dumps(
                {"values": parsed.raw_values, "lines": parsed.raw_lines, "warnings": warnings},
                ensure_ascii=False, separators=(",", ":"),
            ),
            "ini_plate_text": parsed.plate_text_raw, "ini_sha256": ini_hash,
            "ini_parser_version": parser_version, "certificate_id": parsed.certificate_id,
            "device_serial": parsed.certificate_id, "camera_id": parsed.camera_id,
            "location": parsed.location, "date": parsed.date, "time": parsed.time,
            "captured_datetime": f"{parsed.date} {parsed.time}" if parsed.date and parsed.time else None,
            "image_sequence": parsed.image_sequence, "speed_limit": _numeric(parsed.speed_limit),
            "vehicle_speed": _numeric(parsed.vehicle_speed), "speed": _numeric(parsed.vehicle_speed),
            "operator_name": parsed.operator_name, "direction_text": parsed.direction_text,
            "direction_code": parsed.direction_code,
            "direction": parsed.direction_text or parsed.direction_code,
            "violation_type": parsed.violation_type, "amount": parsed.amount,
            "vehicle_type_code": parsed.vehicle_type_code, "violation_code": parsed.violation_code,
            "plate_text_raw": resolution.raw, "plate_text_normalized": resolution.normalized,
            "plate_source": resolution.source, "plate_validation_status": resolution.validation_status,
            "label_confidence": resolution.confidence, "label_trust_level": resolution.trust_level,
            "metadata_conflict": int(resolution.conflict), "conflict_type": resolution.conflict_type,
            "quality_flags": sorted(set(existing_flags + warnings)),
        }
        for key in (
            "certificate_id", "device_serial", "camera_id", "location", "date",
            "time", "captured_datetime", "image_sequence", "speed_limit",
            "vehicle_speed", "speed", "operator_name", "direction_text",
            "direction_code", "direction", "violation_type", "amount",
            "vehicle_type_code", "violation_code",
        ):
            if fields.get(key) is None:
                fields.pop(key, None)
        return fields


def validate_ini_against_filename(parsed: ParsedIniMetadata, filename: dict) -> list[str]:
    checks = (
        ("date", parsed.date, filename.get("date"), "INI_DATE_MISMATCH"),
        ("time", parsed.time, filename.get("time"), "INI_TIME_MISMATCH"),
        ("camera", parsed.camera_id, filename.get("camera_id"), "INI_CAMERA_MISMATCH"),
        ("sequence", parsed.image_sequence, filename.get("sequence"), "INI_FILENAME_MISMATCH"),
    )
    return [code for _name, ini_value, file_value, code in checks if ini_value and file_value and str(ini_value).casefold() != str(file_value).casefold()]


def _numeric(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None
