"""Metadata parsing from filenames (and, later, on-image overlays).

The interface :class:`MetadataParser` lets additional sources (e.g. an OCR
overlay parser) be added without touching the pipeline. The default
:class:`FilenameMetadataParser` extracts year / camera / date / time / speed
from filename patterns. It never raises on unexpected names; it simply returns
whatever it can find.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Dict, Optional

_YEAR = re.compile(r"(20\d{2})")
# Matches 2024-05-15_12:30:45, 20240515123045, 20240515, 2024_05_15 1230, etc.
_DT = re.compile(
    r"(20\d{2})[-_]?([01]\d?)[-_]?([0-3]\d?)"
    r"(?:[-_ T]?([01]?\d|2[0-3]):?([0-5]\d)?(?::?([0-5]\d))?)?"
)
_CAMERA = re.compile(r"(?:cam(?:era)?|cctv|host|主机|攝影機|攝影)\s*[-_]?\s*([A-Z0-9]{1,4})", re.I)
_SPEED = re.compile(r"(\d{1,3})\s*(?:km/h|kmh)", re.I)
_DIRECTION = re.compile(r"(?P<dir>east|west|north|south|北|南|東|西|inbound|outbound)", re.I)


class MetadataParser(ABC):
    """Interface for extracting camera metadata from a source."""

    name: str = "metadata"

    @abstractmethod
    def parse(self, filename: str) -> Dict[str, Optional[object]]:
        raise NotImplementedError


class FilenameMetadataParser(MetadataParser):
    """Best-effort extraction of metadata from a filename."""

    name = "filename"

    def parse(self, filename: str) -> Dict[str, Optional[object]]:
        result: Dict[str, Optional[object]] = {
            "year": None,
            "camera_id": None,
            "date": None,
            "time": None,
            "datetime": None,
            "speed": None,
            "direction": None,
        }
        name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        ym = _YEAR.search(name)
        if ym:
            result["year"] = int(ym.group(1))

        cam = _CAMERA.search(name)
        if cam:
            result["camera_id"] = cam.group(1).upper()

        dt = _DT.search(name)
        if dt:
            year, month, day = dt.group(1), dt.group(2), dt.group(3)
            hh, mm, ss = dt.group(4), dt.group(5), dt.group(6)
            if month and day:
                result["date"] = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            if hh is not None:
                result["time"] = f"{int(hh):02d}:{(mm or '00').zfill(2)}:{(ss or '00').zfill(2)}"
            if result["date"] and result["time"]:
                result["datetime"] = f"{result['date']} {result['time']}"
            if result["year"] is None and year:
                result["year"] = int(year)

        sp = _SPEED.search(name)
        if sp:
            result["speed"] = int(sp.group(1))

        dirm = _DIRECTION.search(name)
        if dirm:
            result["direction"] = dirm.group(1)

        return result


# --------------------------------------------------------------------------- #
# Speed-camera .ini sidecar parser (flat key=value, usually Big5/cp950)
# --------------------------------------------------------------------------- #
_INI_KEY_MAP = {
    "主機": "camera_id", "host": "camera_id", "camera": "camera_id",
    "車速": "speed", "speed": "speed",
    "速限": "speed_limit", "limit": "speed_limit",
    "地點": "location", "location": "location",
    "證號": "device_serial", "serial": "device_serial", "device": "device_serial",
    "方向": "direction", "direction": "direction",
    "日期": "date", "date": "date",
    "時間": "time", "time": "time",
    "影像序號": "sequence", "sequence": "sequence",
}
_INI_ENCODINGS = ("cp950", "big5", "gbk", "utf-8", "latin-1")
_INI_NUM = re.compile(r"(\d+(?:\.\d+)?)")
_INI_DATE = re.compile(r"(20\d{2})[/.-]?([01]\d)[/.-]?([0-3]\d)")
_INI_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


class IniSidecarParser:
    """Parse a speed-camera .ini sidecar (flat key=value, usually Big5)."""

    name = "ini_sidecar"

    def parse_bytes(self, data: bytes) -> Dict[str, Optional[object]]:
        result: Dict[str, Optional[object]] = {
            "year": None, "camera_id": None, "date": None, "time": None,
            "datetime": None, "speed": None, "speed_limit": None,
            "direction": None, "location": None, "device_serial": None,
            "sequence": None,
        }
        text = None
        for enc in _INI_ENCODINGS:
            try:
                text = bytes(data).decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            return result
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line or "=" not in line or line.startswith("["):
                continue
            key, _, value = line.partition("=")
            field = _INI_KEY_MAP.get(key.strip().lower())
            value = value.strip()
            if field is None or not value:
                continue
            if field in ("speed", "speed_limit"):
                m = _INI_NUM.search(value)
                if m:
                    result[field] = float(m.group(1))
            elif field == "sequence":
                m = _INI_NUM.search(value)
                result[field] = int(m.group(1)) if m else value
            elif field == "date":
                m = _INI_DATE.search(value)
                if m:
                    result["date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                    result["year"] = int(m.group(1))
            elif field == "time":
                m = _INI_TIME.search(value)
                if m:
                    result["time"] = f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3) or '00'}"
            else:
                result[field] = value
        if result["date"] and result["time"]:
            result["datetime"] = f"{result['date']} {result['time']}"
        return result
