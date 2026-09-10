"""Offline tests for the PaddleOCR sidecar client (fake server, no paddle)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from vehicle_dataset_manager.ocr.paddle_client import (
    PaddleOcr,
    PaddleOcrError,
    PaddleOcrProcess,
    PaddlePlateDetector,
)
from vehicle_dataset_manager.services.plate import is_plausible_plate

ROOT = Path(__file__).resolve().parents[1]
FAKE_SERVER = ROOT / "scripts" / "fake_paddle_server.py"


@pytest.fixture
def proc():
    p = PaddleOcrProcess(
        ocr_python=Path(sys.executable),
        server_cmd=[str(FAKE_SERVER)],
        cache_dir=ROOT / "runs" / "2025" / "cache" / "paddlex_test",
    )
    p.start()
    yield p
    p.close()


def make_img():
    return np.full((32, 64, 3), 120, dtype=np.uint8)


def test_ocr_returns_fake_boxes(proc):
    boxes = proc.ocr(make_img())
    assert [b.text for b in boxes] == ["FKE-1234", "RS015", "8"]
    assert boxes[0].box == (10, 20, 120, 40)
    assert boxes[0].score == pytest.approx(0.91)


def test_ping(proc):
    assert proc.ping() is True


def test_close_idempotent(proc):
    proc.close()
    proc.close()
    assert proc.running is False


def test_dead_sidecar_raises_fatal(proc):
    proc._proc.kill()
    proc._proc.wait()
    with pytest.raises(PaddleOcrError) as exc:
        proc.ocr(make_img())
    assert exc.value.fatal is True


def test_restart_recovers(proc):
    proc._proc.kill()
    proc._proc.wait()
    proc.restart()
    boxes = proc.ocr(make_img())
    assert boxes[0].text == "FKE-1234"


def test_timeout_on_hung_request(proc):
    with pytest.raises(PaddleOcrError):
        proc.send_raw({"op": "sleep", "id": 7, "seconds": 10}, timeout=0.5)


def test_stdout_noise_is_skipped(proc):
    resp = proc.send_raw({"op": "noise", "id": 8})
    assert resp["op"] == "pong"


def test_paddle_ocr_picks_best_plausible(proc):
    ocr = PaddleOcr(proc)
    result = ocr.recognize(make_img())
    assert result is not None
    assert result.text == "FKE-1234"  # RS015 and "8" are not plausible plates
    assert result.confidence == pytest.approx(0.91)
    assert result.bbox == (10, 20, 120, 40)


def test_paddle_ocr_retry_after_crash(proc):
    ocr = PaddleOcr(proc)
    proc._proc.kill()
    proc._proc.wait()
    result = ocr.recognize(make_img())
    assert result is not None and result.text == "FKE-1234"


def test_plate_detector_filters_plausible(proc):
    det = PaddlePlateDetector(proc)
    dets = det.detect(make_img())
    assert len(dets) == 1
    assert dets[0].class_name == "plate"
    assert dets[0].confidence == pytest.approx(0.91)
    assert dets[0].bbox == (10, 20, 120, 40)
    assert is_plausible_plate("FKE1234")
