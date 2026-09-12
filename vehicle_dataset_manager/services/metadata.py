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

from vehicle_dataset_manager.services.ini_parser import parse_ini_bytes

_YEAR = re.compile(r"(20\d{2})")
# Matches 2024-05-15_12:30:45, 20240515123045, 20240515, 2024_05_15 1230, etc.
_DT = re.compile(
    r"(20\d{2})[-_]?([01]\d?)[-_]?([0-3]\d?)"
    r"(?:[-_ T]?([01]?\d|2[0-3]):?([0-5]\d)?(?::?([0-5]\d))?)?"
)
_CAMERA = re.compile(r"(?:cam(?:era)?|cctv|host|主机|攝影機|攝影)\s*[-_]?\s*([A-Z0-9]{1,4})", re.I)
_RS_CAMERA = re.compile(r"(?:^|_)(RS\d{3,})(?:_|$)", re.I)
_RS_SEQUENCE = re.compile(r"(?:^|_)RS\d{3,}_(\d+)(?:_|\.)", re.I)
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
            "sequence": None,
        }
        name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        ym = _YEAR.search(name)
        if ym:
            result["year"] = int(ym.group(1))

        cam = _CAMERA.search(name)
        if cam is None:
            cam = _RS_CAMERA.search(name)
        if cam:
            result["camera_id"] = cam.group(1).upper()
        sequence = _RS_SEQUENCE.search(name)
        if sequence:
            result["sequence"] = sequence.group(1)

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


class IniSidecarParser:
    """Compatibility adapter over the versioned, traceable INI parser."""

    name = "ini_sidecar"

    def parse_bytes(self, data: bytes) -> Dict[str, Optional[object]]:
        return parse_ini_bytes(data).pipeline_metadata()
