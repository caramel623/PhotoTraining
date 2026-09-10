"""Offline tests for the PaddleOCR sidecar protocol (no paddle required)."""
from __future__ import annotations

import numpy as np
import pytest

from vehicle_dataset_manager.ocr.paddle_protocol import (
    DEFAULT_PRESET,
    MODEL_PRESETS,
    OcrBox,
    decode_message,
    encode_message,
    init_request,
    ocr_request,
    parse_results,
    ping_request,
    preset_models,
    shutdown_request,
    unpack_image,
    pack_image,
)


def test_roundtrip():
    msg = {"op": "result", "id": 7, "results": [{"text": "BFY-1765", "score": 0.91}]}
    decoded = decode_message(encode_message(msg))
    assert decoded == msg


def test_decode_ignores_noise():
    assert decode_message(b"") is None
    assert decode_message(b"\n") is None
    assert decode_message(b"   \n") is None
    assert decode_message(b"Creating model: ('PP-OCRv5_mobile_det', None, None)\n") is None
    assert decode_message(b"not json at all\n") is None
    assert decode_message(b"[1, 2, 3]\n") is None
    assert decode_message(b"\xff\xfe garbage") is None


def test_init_request_shape():
    msg = decode_message(init_request(1, "PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec", False))
    assert msg["op"] == "init"
    assert msg["id"] == 1
    assert msg["det_model"] == "PP-OCRv5_mobile_det"
    assert msg["rec_model"] == "PP-OCRv5_mobile_rec"
    assert msg["enable_mkldnn"] is False


def test_ping_and_shutdown_shape():
    assert decode_message(ping_request(5)) == {"op": "ping", "id": 5}
    assert decode_message(shutdown_request(6)) == {"op": "shutdown", "id": 6}


def test_pack_unpack_roundtrip():
    rng = np.random.default_rng(0)
    img = (rng.random((768, 1152, 3)) * 255).astype(np.uint8)
    data, h, w, ch = pack_image(img)
    back = unpack_image(data, h, w, ch)
    assert back.shape == img.shape
    assert np.array_equal(back, img)


def test_pack_gray_becomes_3d():
    img = np.zeros((10, 20), dtype=np.uint8)
    data, h, w, ch = pack_image(img)
    assert (h, w, ch) == (10, 20, 1)
    back = unpack_image(data, h, w, ch)
    assert back.shape == (10, 20, 1)


def test_unpack_size_mismatch_raises():
    data, h, w, ch = pack_image(np.zeros((4, 4, 3), dtype=np.uint8))
    with pytest.raises(ValueError):
        unpack_image(data, h, w, ch + 1)


def test_ocr_request_carries_image():
    img = np.full((8, 12, 3), 7, dtype=np.uint8)
    msg = decode_message(ocr_request(3, img))
    assert msg["op"] == "ocr"
    assert msg["id"] == 3
    assert (msg["h"], msg["w"], msg["ch"]) == (8, 12, 3)
    np.testing.assert_array_equal(unpack_image(msg["data"], msg["h"], msg["w"], msg["ch"]), img)


def test_preset_models():
    assert preset_models("mobile") == MODEL_PRESETS["mobile"]
    assert preset_models("server") == MODEL_PRESETS["server"]
    assert preset_models("SERVER") == MODEL_PRESETS["server"]
    assert preset_models("bogus") == MODEL_PRESETS[DEFAULT_PRESET]
    assert preset_models("") == MODEL_PRESETS[DEFAULT_PRESET]


def test_parse_results():
    msg = {
        "op": "result",
        "id": 1,
        "results": [
            {"text": "BFY-1765", "score": 0.91, "box": [10.4, 20.6, 110.2, 40.9]},
            {"text": "RS015", "score": 0.88, "box": [5, 5, 50, 20]},
            {"text": "", "score": 0.5, "box": [1, 2, 3, 4]},
            {"text": "NOBOX", "score": 0.5},
            {"text": "NOFLOAT", "score": "x", "box": [1, 2, 3, 4]},
        ],
    }
    boxes = parse_results(msg)
    assert isinstance(boxes[0], OcrBox)
    assert boxes[0].text == "BFY-1765"
    assert boxes[0].score == pytest.approx(0.91)
    assert boxes[0].box == (10, 21, 110, 41)
    assert [b.text for b in boxes] == ["BFY-1765", "RS015"]
    assert parse_results({}) == []
