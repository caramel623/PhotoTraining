"""PaddleOCR sidecar server (run inside the dedicated ``.venv-ocr`` venv).

Started by the client (or by ``scripts/probe_paddle_server.py``) as::

    <.venv-ocr python> -m vehicle_dataset_manager.ocr.paddle_server

Protocol (see :mod:`vehicle_dataset_manager.ocr.paddle_protocol`):

    client -> {"op":"init","id":N,"det_model":...,"rec_model":...,"enable_mkldnn":false}
    server -> {"op":"ready","id":N,"engine":"paddleocr","version":...,...}
    client -> {"op":"ocr","id":N,"data":"<b64>","h":H,"w":W,"ch":3}
    server -> {"op":"result","id":N,"results":[{"text","score","box":[x1,y1,x2,y2]},...]}
    client -> {"op":"ping","id":N}            server -> {"op":"pong","id":N}
    client -> {"op":"shutdown","id":N}        server -> {"op":"bye","id":N}  (exit 0)

Stream discipline: **stdout carries only protocol JSON lines**. The server
captures the original stdout buffer for protocol writes and reroutes
``sys.stdout`` to stderr *before* importing paddle, so no library logging or
stray ``print`` can corrupt the JSON stream. Paddle/paddlex log to stderr
natively (verified 2026-09-09).

Model cache: controlled by the ``PADDLE_PDX_CACHE_HOME`` env var, which the
spawner points at a writable, portable location (e.g. ``<workspace>/cache/paddlex``).
This module never touches the database or the workspace itself.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from typing import Any, Dict, Optional

from vehicle_dataset_manager.ocr.paddle_protocol import (
    DEFAULT_PRESET,
    decode_message,
    encode_message,
    preset_models,
    unpack_image,
)


def _log(message: str) -> None:
    print(f"[paddle-server] {message}", file=sys.stderr, flush=True)


class Engine:
    """Lazily-built PaddleOCR wrapper (heavy imports happen in :meth:`init`)."""

    def __init__(self) -> None:
        self._eng = None
        self.det_model = ""
        self.rec_model = ""
        self.version = ""

    @property
    def ready(self) -> bool:
        return self._eng is not None

    def init(self, det_model: str, rec_model: str, enable_mkldnn: bool) -> None:
        if self._eng is not None:
            return
        import paddleocr  # heavy; imported only when first needed

        self.version = getattr(paddleocr, "__version__", "unknown")
        # Paddle 3.3.1 PIR/oneDNN workaround: with mkldnn on, some op throws
        # "ConvertPirAttribute2RuntimeAttribute not support
        # ArrayAttribute<DoubleAttribute>". paddlex maps enable_mkldnn=False
        # to run_mode="paddle".
        self._eng = paddleocr.PaddleOCR(
            text_detection_model_name=det_model,
            text_recognition_model_name=rec_model,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=bool(enable_mkldnn),
        )
        self.det_model = det_model
        self.rec_model = rec_model

    def ocr(self, image: "Any") -> list:
        import numpy as np

        if self._eng is None:
            raise RuntimeError("engine not initialized (send 'init' first)")
        results = list(self._eng.predict(image))
        out: list = []
        if not results:
            return out
        res = results[0]
        texts = list(res.get("rec_texts") or [])
        scores = list(res.get("rec_scores") or [])
        polys = res.get("rec_polys")
        polys = np.asarray(polys) if polys is not None else None
        if polys is not None and polys.ndim == 2:
            polys = polys.reshape(1, *polys.shape)
        for i, text in enumerate(texts):
            score = float(scores[i]) if i < len(scores) else 0.0
            box: Optional[list] = None
            if polys is not None and polys.size and i < polys.shape[0]:
                poly = polys[i].astype(float).reshape(-1, 2)
                box = [
                    int(round(poly[:, 0].min())),
                    int(round(poly[:, 1].min())),
                    int(round(poly[:, 0].max())),
                    int(round(poly[:, 1].max())),
                ]
            out.append({"text": str(text), "score": score, "box": box})
        return out


def _selftest(preset: str = DEFAULT_PRESET) -> int:
    """Build the default (mobile) engine and exit 0/1. For manual smoke runs
    and the future settings-page 'detect' button (G5)."""
    det_model, rec_model = preset_models(preset)
    _log(f"selftest: building engine det={det_model} rec={rec_model}")
    t0 = time.time()
    try:
        engine = Engine()
        engine.init(det_model, rec_model, enable_mkldnn=False)
    except Exception:
        _log("selftest FAILED:")
        traceback.print_exc(file=sys.stderr)
        return 1
    _log(
        f"selftest OK: paddleocr={engine.version} "
        f"det={det_model} rec={rec_model} ({time.time() - t0:.1f}s)"
    )
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="paddle_server")
    parser.add_argument("--selftest", action="store_true", help="build engine, print status, exit")
    parser.add_argument("--preset", default=DEFAULT_PRESET, choices=("mobile", "server"))
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest(args.preset)

    # Protocol writes go to the ORIGINAL stdout buffer (bytes, UTF-8);
    # everything else (paddle, warnings, prints) is rerouted to stderr.
    proto_out = sys.stdout.buffer
    sys.stdout = sys.stderr

    def send(msg: Dict[str, Any]) -> None:
        proto_out.write(encode_message(msg))
        proto_out.flush()

    engine = Engine()

    def handle(msg: Dict[str, Any]) -> bool:
        """Process one request. Returns True to stop the loop."""
        op = msg.get("op")
        req_id = msg.get("id")
        t0 = time.time()
        if op == "init":
            det_model = msg.get("det_model") or ""
            rec_model = msg.get("rec_model") or ""
            if not det_model or not rec_model:
                p_det, p_rec = preset_models(DEFAULT_PRESET)
                det_model, rec_model = det_model or p_det, rec_model or p_rec
            try:
                engine.init(det_model, rec_model, bool(msg.get("enable_mkldnn", False)))
            except Exception as exc:  # noqa: BLE001
                _log(f"init failed: {exc}")
                traceback.print_exc(file=sys.stderr)
                send({"op": "error", "id": req_id, "message": str(exc), "fatal": True})
                return False
            _log(f"engine ready: {engine.det_model} + {engine.rec_model}")
            send(
                {
                    "op": "ready",
                    "id": req_id,
                    "engine": "paddleocr",
                    "version": engine.version,
                    "det_model": engine.det_model,
                    "rec_model": engine.rec_model,
                    "elapsed_ms": int((time.time() - t0) * 1000),
                }
            )
        elif op == "ocr":
            try:
                image = unpack_image(
                    msg["data"], int(msg["h"]), int(msg["w"]), int(msg["ch"])
                )
            except Exception as exc:  # noqa: BLE001
                send({"op": "error", "id": req_id, "message": f"bad image payload: {exc}", "fatal": False})
                return False
            try:
                results = engine.ocr(image)
            except Exception as exc:  # noqa: BLE001
                _log(f"ocr failed: {exc}")
                traceback.print_exc(file=sys.stderr)
                send({"op": "error", "id": req_id, "message": str(exc), "fatal": False})
                return False
            send(
                {
                    "op": "result",
                    "id": req_id,
                    "results": results,
                    "elapsed_ms": int((time.time() - t0) * 1000),
                }
            )
        elif op == "ping":
            send({"op": "pong", "id": req_id})
        elif op == "shutdown":
            send({"op": "bye", "id": req_id})
            return True
        else:
            send({"op": "error", "id": req_id, "message": f"unknown op: {op!r}", "fatal": False})
        return False

    try:
        for raw in sys.stdin.buffer:
            msg = decode_message(raw)
            if msg is None:
                if raw.strip():
                    _log(f"ignoring non-protocol line: {raw[:120]!r}")
                continue
            try:
                if handle(msg):
                    break
            except Exception:  # noqa: BLE001 - one bad request must not kill the server
                _log("unhandled error in request handling:")
                traceback.print_exc(file=sys.stderr)
                send({"op": "error", "id": msg.get("id"), "message": "internal server error", "fatal": False})
    except KeyboardInterrupt:
        pass
    finally:
        _log("server exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
