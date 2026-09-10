"""Client side of the PaddleOCR sidecar (main process, Python 3.14 venv).

``PaddleOcrProcess`` spawns and drives the sidecar
(``vehicle_dataset_manager.ocr.paddle_server``) as a subprocess in the
dedicated ``.venv-ocr`` environment and speaks the JSON-lines protocol.
``PaddleOcr`` and ``PaddlePlateDetector`` wrap a *shared* process so one
sidecar serves both the plate-detection slot and the OCR slot.

Guarantees (per project priorities: stable, no data loss, resumable):
* the client never imports paddle/torch; a missing or broken sidecar raises
  :class:`PaddleOcrError`, which the pipeline stage turns into a per-image
  FAILED (retryable), never a whole-batch crash;
* a dead sidecar is restarted automatically and the failed request retried
  once before giving up;
* ``close()`` is idempotent and safe to call from any thread.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from vehicle_dataset_manager.detection.base import BaseDetector, Detection
from vehicle_dataset_manager.ocr.base import BaseOcr, OcrResult
from vehicle_dataset_manager.ocr.paddle_protocol import (
    DEFAULT_PRESET,
    OcrBox,
    decode_message,
    encode_message,
    init_request,
    ocr_request,
    parse_results,
    ping_request,
    preset_models,
    shutdown_request,
)
from vehicle_dataset_manager.services.plate import is_plausible_plate

log = logging.getLogger("vdm.ocr.paddle")

REPO_ROOT = Path(__file__).resolve().parents[2]


class PaddleOcrError(RuntimeError):
    """Sidecar communication or engine failure."""

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


def default_ocr_python() -> Path:
    """Locate the .venv-ocr interpreter (override with VDM_OCR_PYTHON)."""
    env = os.environ.get("VDM_OCR_PYTHON")
    if env:
        return Path(env)
    if os.name == "nt":
        return REPO_ROOT / ".venv-ocr" / "Scripts" / "python.exe"
    return REPO_ROOT / ".venv-ocr" / "bin" / "python"


class PaddleOcrProcess:
    """Owns the sidecar subprocess and the request/response channel."""

    def __init__(
        self,
        ocr_python: Optional[Path] = None,
        server_cmd: Optional[List[str]] = None,
        cache_dir: Optional[Path] = None,
        det_model: Optional[str] = None,
        rec_model: Optional[str] = None,
        enable_mkldnn: bool = False,
        init_timeout: float = 600.0,
        request_timeout: float = 120.0,
        stderr_line: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.ocr_python = Path(ocr_python) if ocr_python else default_ocr_python()
        # ``server_cmd`` (without the python exe) lets tests inject a fake
        # server; production uses ``-m vehicle_dataset_manager.ocr.paddle_server``.
        self.server_cmd = list(server_cmd) if server_cmd else [
            "-m", "vehicle_dataset_manager.ocr.paddle_server"
        ]
        # PADDLE_PDX_CACHE_HOME is the cache root itself (~/.paddlex equivalent).
        self.cache_dir = Path(cache_dir) if cache_dir else REPO_ROOT / "runs" / "home" / ".paddlex"
        self.det_model, self.rec_model = preset_models()
        if det_model:
            self.det_model = det_model
        if rec_model:
            self.rec_model = rec_model
        self.enable_mkldnn = bool(enable_mkldnn)
        self.init_timeout = float(init_timeout)
        self.request_timeout = float(request_timeout)
        self.stderr_line = stderr_line

        self._proc: Optional[subprocess.Popen] = None
        self._resp_q: "queue.Queue" = queue.Queue()
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._started = False
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self._started and self.running:
            return
        self._started = True
        self._closed = False
        self._start_process()
        self._send_recv(init_request(1, self.det_model, self.rec_model, self.enable_mkldnn),
                        timeout=self.init_timeout, expect="ready")
        log.info("sidecar ready: %s + %s (pid=%s)", self.det_model, self.rec_model,
                 self._proc.pid if self._proc else "?")

    def _start_process(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PADDLE_PDX_CACHE_HOME"] = str(self.cache_dir)
        # Keep every tool that resolves "~" writable and local.
        env["HOME"] = str(self.cache_dir.parent)
        env["USERPROFILE"] = str(self.cache_dir.parent)
        kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
        )
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_NEW_WINDOW", 0x08000000)
        proc = subprocess.Popen([str(self.ocr_python)] + self.server_cmd, **kwargs)
        self._proc = proc
        # Reader threads are bound to *this* process + *this* queue so a stale
        # EOF from a previous (dead) generation can never poison a restart.
        q = self._resp_q
        threading.Thread(target=self._read_stdout, args=(proc, q), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()

    def close(self) -> None:
        if self._closed and not self.running:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                with self._send_lock:
                    proc.stdin.write(encode_message({"op": "shutdown", "id": -1}))
                    proc.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._proc = None

    def restart(self) -> None:
        """Tear down and rebuild the sidecar (used after a communication failure)."""
        log.warning("restarting paddle sidecar")
        self.close()
        self._resp_q = queue.Queue()
        self.start()

    # -- channel -----------------------------------------------------------

    def _read_stdout(self, proc: "subprocess.Popen", q: "queue.Queue") -> None:
        for raw in proc.stdout:
            msg = decode_message(raw)
            if msg is None:
                if raw.strip():
                    log.warning("sidecar stdout noise: %r", raw[:160])
                continue
            q.put(msg)
        q.put(None)  # EOF

    def _read_stderr(self, proc: "subprocess.Popen") -> None:
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if self.stderr_line is not None:
                try:
                    self.stderr_line(line)
                except Exception:  # noqa: BLE001
                    pass
            else:
                log.debug("sidecar: %s", line)

    def _send_recv(
        self,
        msg: bytes,
        timeout: Optional[float] = None,
        expect: Optional[str] = None,
    ) -> dict:
        if not self.running:
            raise PaddleOcrError("sidecar not running", fatal=True)
        with self._send_lock:
            self._proc.stdin.write(msg)  # noqa: B025 (checked via running)
            self._proc.stdin.flush()
        deadline = time.monotonic() + (timeout or self.request_timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PaddleOcrError(
                    f"timed out waiting for sidecar response ({timeout or self.request_timeout}s)"
                )
            try:
                resp = self._resp_q.get(timeout=remaining)
            except queue.Empty:
                raise PaddleOcrError(
                    "timed out waiting for sidecar response", fatal=True
                ) from None
            if resp is None:
                code = self._proc.poll() if self._proc else None
                raise PaddleOcrError(f"sidecar exited (code={code})", fatal=True)
            if expect is not None and resp.get("op") != expect:
                if resp.get("op") == "error":
                    raise PaddleOcrError(
                        str(resp.get("message", "unknown error")),
                        fatal=bool(resp.get("fatal")),
                    )
                log.warning("unexpected response op %r (wanted %r)", resp.get("op"), expect)
                continue
            return resp

    # -- public requests ----------------------------------------------------

    def ocr(self, bgr: np.ndarray) -> List[OcrBox]:
        if not self._started:
            self.start()
        req_id = self._next_id
        self._next_id += 1
        resp = self._send_recv(ocr_request(req_id, bgr), expect="result")
        if resp.get("id") != req_id:
            log.warning("response id mismatch: sent %s got %s", req_id, resp.get("id"))
        return parse_results(resp)

    def send_raw(self, msg: dict, timeout: Optional[float] = None) -> dict:
        """Low-level request escape hatch (tests, future UI probes)."""
        if not self._started:
            self.start()
        return self._send_recv(encode_message(msg), timeout=timeout)

    def ping(self) -> bool:
        if not self._started:
            self.start()
        req_id = self._next_id
        self._next_id += 1
        self._send_recv(ping_request(req_id), timeout=5.0, expect="pong")
        return True


class PaddleOcr(BaseOcr):
    """``BaseOcr`` backed by the sidecar: returns the best *plausible* plate."""

    name = "paddle-ocr"

    def __init__(self, process: PaddleOcrProcess) -> None:
        self.process = process

    def warmup(self) -> None:
        self.process.start()

    def recognize(self, image: np.ndarray) -> Optional[OcrResult]:
        boxes = self._ocr_with_retry(image)
        plausible = [b for b in boxes if is_plausible_plate(b.text)]
        if not plausible:
            return None
        best = max(plausible, key=lambda b: b.score)
        return OcrResult(text=best.text, confidence=best.score, bbox=best.box)

    def _ocr_with_retry(self, image: np.ndarray) -> List[OcrBox]:
        try:
            return self.process.ocr(image)
        except PaddleOcrError as first:
            log.warning("ocr failed (%s); restarting sidecar and retrying once", first)
            self.process.restart()
            try:
                return self.process.ocr(image)
            except PaddleOcrError as second:
                raise PaddleOcrError(f"sidecar retry failed: {second}") from second


class PaddlePlateDetector(BaseDetector):
    """Plate detector via full-image OCR (no separate plate model in v1)."""

    name = "paddle-plate"

    def __init__(self, process: PaddleOcrProcess) -> None:
        self.process = process

    def warmup(self) -> None:
        self.process.start()

    def detect(self, image: np.ndarray) -> List[Detection]:
        boxes = self._ocr_with_retry(image)
        out: List[Detection] = []
        for b in boxes:
            if is_plausible_plate(b.text):
                out.append(
                    Detection(
                        class_name="plate",
                        confidence=b.score,
                        bbox=b.box,
                    )
                )
        return out

    def _ocr_with_retry(self, image: np.ndarray) -> List[OcrBox]:
        try:
            return self.process.ocr(image)
        except PaddleOcrError as first:
            log.warning("plate detect failed (%s); restarting sidecar and retrying once", first)
            self.process.restart()
            try:
                return self.process.ocr(image)
            except PaddleOcrError as second:
                raise PaddleOcrError(f"sidecar retry failed: {second}") from second


def build_paddle_engines(
    ocr_python: Optional[Path] = None,
    server_cmd: Optional[List[str]] = None,
    cache_dir: Optional[Path] = None,
    det_model: Optional[str] = None,
    rec_model: Optional[str] = None,
    enable_mkldnn: bool = False,
    init_timeout: float = 600.0,
    request_timeout: float = 120.0,
    stderr_line: Optional[Callable[[str], None]] = None,
) -> "tuple[PaddlePlateDetector, PaddleOcr, PaddleOcrProcess]":
    """Create the shared sidecar process plus both engine wrappers.

    The caller owns the returned process and should ``close()`` it on exit.
    """
    process = PaddleOcrProcess(
        ocr_python=ocr_python,
        server_cmd=server_cmd,
        cache_dir=cache_dir,
        det_model=det_model,
        rec_model=rec_model,
        enable_mkldnn=enable_mkldnn,
        init_timeout=init_timeout,
        request_timeout=request_timeout,
        stderr_line=stderr_line,
    )
    return PaddlePlateDetector(process), PaddleOcr(process), process
