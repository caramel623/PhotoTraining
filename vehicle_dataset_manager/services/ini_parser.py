"""Traceable flat key=value parser for speed-camera INI sidecars."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Optional

from vehicle_dataset_manager.services.plate import normalize_plate

INI_PARSER_VERSION = 2
INI_EXTENSIONS = {".ini"}
_DATE = re.compile(r"(20\d{2})[/.-]?([01]\d)[/.-]?([0-3]\d)")
_TIME = re.compile(r"([0-2]?\d):([0-5]\d)(?::([0-5]\d))?")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class ParsedIniMetadata:
    status: str = "ok"
    encoding_used: Optional[str] = None
    raw_values: dict[str, list[str]] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    certificate_id: Optional[str] = None
    camera_id: Optional[str] = None
    location: Optional[str] = None
    speed_limit: Optional[str] = None
    image_sequence: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    vehicle_speed: Optional[str] = None
    operator_name: Optional[str] = None
    direction_text: Optional[str] = None
    direction_code: Optional[str] = None
    violation_type: Optional[str] = None
    amount: Optional[str] = None
    vehicle_type_code: Optional[str] = None
    violation_code: Optional[str] = None
    plate_text_raw: Optional[str] = None
    plate_text_normalized: Optional[str] = None

    @property
    def has_plate(self) -> bool:
        return self.status == "ok" and "INI_PLATE_AMBIGUOUS" not in self.warnings and valid_ini_plate(self.plate_text_raw)

    def raw_json(self) -> str:
        return json.dumps({"values": self.raw_values, "lines": self.raw_lines, "warnings": self.warnings}, ensure_ascii=False, separators=(",", ":"))

    def pipeline_metadata(self) -> dict:
        return {
            "year": int(self.date[:4]) if self.date else None,
            "camera_id": self.camera_id, "location": self.location,
            "speed": _numeric(self.vehicle_speed), "speed_limit": _numeric(self.speed_limit),
            "date": self.date, "time": self.time,
            "datetime": f"{self.date} {self.time}" if self.date and self.time else None,
            "direction": self.direction_text or self.direction_code,
            "device_serial": self.certificate_id,
            "sequence": int(self.image_sequence) if self.image_sequence and self.image_sequence.isdigit() else self.image_sequence,
            "ini_plate_text": self.plate_text_raw,
            "ini_plate_normalized": self.plate_text_normalized if self.has_plate else None,
            "ini_status": self.status, "ini_encoding": self.encoding_used,
        }


def parse_ini_file(path: Path | str) -> ParsedIniMetadata:
    try:
        return parse_ini_bytes(Path(path).read_bytes())
    except OSError as exc:
        return ParsedIniMetadata(status="error", warnings=[str(exc)])


def parse_ini_bytes(data: bytes) -> ParsedIniMetadata:
    text, encoding = _decode(bytes(data))
    if text is None:
        return ParsedIniMetadata(status="error", warnings=["INI_PARSE_ERROR: unsupported encoding"])
    values: dict[str, list[str]] = {}
    warnings: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        if "=" not in line:
            warnings.append(f"malformed line {number}: {raw}")
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            warnings.append(f"malformed line {number}: empty key")
            continue
        values.setdefault(key, []).append(value)
        if len(values[key]) == 2:
            warnings.append(f"duplicate key: {key}")
    result = ParsedIniMetadata(encoding_used=encoding, raw_values=values, warnings=warnings)
    result.raw_lines = text.splitlines()
    if not values:
        result.status = "error"
        result.warnings.append("INI_PARSE_ERROR: no key-value data")
    result.certificate_id = _first(values, "證號")
    result.camera_id = _first(values, "主機")
    result.location = _first(values, "地點")
    result.speed_limit = _first(values, "速限")
    result.image_sequence = _first(values, "影像序號")
    result.date = _normalize_date(_first(values, "日期"))
    result.time = _normalize_time(_first(values, "時間"))
    result.vehicle_speed = _first(values, "車速")
    result.operator_name = _first(values, "操作者姓名")
    directions = [value for value in values.get("方向", []) if value]
    result.direction_text = next((value for value in directions if not value.isdigit()), None)
    result.direction_code = next((value for value in directions if value.isdigit()), None)
    result.violation_type = _first(values, "類型")
    result.amount = _first(values, "金額")
    result.vehicle_type_code = _first(values, "車種")
    result.violation_code = _first(values, "違規")
    result.plate_text_raw = _first(values, "車號")
    result.plate_text_normalized = normalize_plate(result.plate_text_raw)
    if len({normalize_plate(v) for v in values.get("車號", []) if v}) > 1:
        result.warnings.append("INI_PLATE_AMBIGUOUS")
    for key, normalized in (("日期", result.date), ("時間", result.time)):
        if _first(values, key) and not normalized:
            result.warnings.append(f"INI_INVALID_FIELD: {key}")
    if result.plate_text_raw and not result.has_plate:
        result.warnings.append("INI_PLATE_INVALID")
    if not result.plate_text_raw:
        result.warnings.append("INI_PLATE_MISSING")
    return result


def ini_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes) -> tuple[Optional[str], Optional[str]]:
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8", "cp950", "big5"):
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return None, None


def _first(values: dict[str, list[str]], key: str) -> Optional[str]:
    return next((value for value in values.get(key, []) if value != ""), None)


def _normalize_date(value: Optional[str]) -> Optional[str]:
    match = _DATE.search(value or "")
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups())).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _normalize_time(value: Optional[str]) -> Optional[str]:
    match = _TIME.search(value or "")
    return f"{int(match.group(1)):02d}:{match.group(2)}:{match.group(3) or '00'}" if match and int(match.group(1)) < 24 else None


def valid_ini_plate(value: Optional[str]) -> bool:
    """INI labels are never passed through OCR character substitutions."""
    plate = normalize_plate(value)
    return bool(plate and re.fullmatch(r"[A-Z0-9]{4,8}", plate) and any(c.isalpha() for c in plate) and any(c.isdigit() for c in plate))


def _numeric(value: Optional[str]) -> Optional[float]:
    match = _NUMBER.search(value or "")
    return float(match.group(0)) if match else None
