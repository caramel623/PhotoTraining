"""JSON-lines protocol shared by the PaddleOCR client and sidecar server.

The sidecar runs in a separate virtualenv (``.venv-ocr``, Python 3.13)
because PaddlePaddle wheels do not support the main interpreter (3.14).
The two processes talk over stdin/stdout:

* **stdout carries only protocol messages** (one JSON object per line).
* All human-facing output (paddle logs, warnings, tracebacks) must go to
  **stderr** so the stream stays parseable.

This module stays dependency-light (stdlib + numpy) so it can be imported
from both virtualenvs; it never imports paddle, torch, or Qt.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

PROTOCOL_VERSION = 1

#: Official PaddleOCR model-name presets (verified 2026-09-09, paddlex 3.7.2).
MODEL_PRESETS: Dict[str, Tuple[str, str]] = {
    "mobile": ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"),
    "server": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
}
DEFAULT_PRESET = "mobile"


def preset_models(preset: str = DEFAULT_PRESET) -> Tuple[str, str]:
    """Resolve a preset key to ``(det_model_name, rec_model_name)``.

    Unknown keys fall back to the default preset instead of raising, so a
    stale settings value never breaks the sidecar handshake.
    """
    return MODEL_PRESETS.get((preset or "").lower(), MODEL_PRESETS[DEFAULT_PRESET])


def encode_message(msg: Dict[str, Any]) -> bytes:
    """Serialize one protocol message as a UTF-8 JSON line (with newline)."""
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def decode_message(line: bytes | str) -> Optional[Dict[str, Any]]:
    """Parse one protocol line; return None for empty or non-JSON lines."""
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            return None
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


# -- image transfer ---------------------------------------------------------


def pack_image(bgr: np.ndarray) -> Tuple[str, int, int, int]:
    """Pack a uint8 BGR image as ``(base64_data, h, w, ch)`` (lossless)."""
    arr = np.ascontiguousarray(bgr)
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    if arr.ndim != 3:
        raise ValueError(f"expected 2-D or 3-D image, got {arr.ndim}-D")
    h, w, ch = int(arr.shape[0]), int(arr.shape[1]), int(arr.shape[2])
    data = base64.b64encode(arr.tobytes()).decode("ascii")
    return data, h, w, ch


def unpack_image(data_b64: str, h: int, w: int, ch: int) -> np.ndarray:
    """Inverse of :func:`pack_image`; raises ValueError on size mismatch."""
    raw = base64.b64decode(data_b64)
    expected = h * w * ch
    if len(raw) != expected:
        raise ValueError(
            f"image size mismatch: got {len(raw)} bytes, expected {expected}"
        )
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, ch).copy()


# -- request builders (client side) ------------------------------------------


def init_request(
    req_id: int,
    det_model: str,
    rec_model: str,
    enable_mkldnn: bool = False,
) -> bytes:
    return encode_message(
        {
            "op": "init",
            "id": req_id,
            "det_model": det_model,
            "rec_model": rec_model,
            "enable_mkldnn": bool(enable_mkldnn),
        }
    )


def ocr_request(req_id: int, bgr: np.ndarray) -> bytes:
    data, h, w, ch = pack_image(bgr)
    return encode_message({"op": "ocr", "id": req_id, "data": data, "h": h, "w": w, "ch": ch})


def ping_request(req_id: int) -> bytes:
    return encode_message({"op": "ping", "id": req_id})


def shutdown_request(req_id: int) -> bytes:
    return encode_message({"op": "shutdown", "id": req_id})


# -- response parsing (client side) ------------------------------------------


@dataclass
class OcrBox:
    """One recognized text region (axis-aligned box in image pixel coords)."""

    text: str
    score: float
    box: Tuple[int, int, int, int]  # x1, y1, x2, y2


def parse_results(msg: Dict[str, Any]) -> List[OcrBox]:
    """Extract per-text boxes from a ``result`` message, skipping bad items."""
    out: List[OcrBox] = []
    for item in msg.get("results") or []:
        try:
            text = str(item.get("text") or "")
            score = float(item.get("score") or 0.0)
            b = item.get("box")
            if not text or b is None or len(b) != 4:
                continue
            box = (int(round(b[0])), int(round(b[1])), int(round(b[2])), int(round(b[3])))
            out.append(OcrBox(text=text, score=score, box=box))
        except (TypeError, ValueError):
            continue
    return out
