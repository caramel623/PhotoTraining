"""Offline fake PaddleOCR sidecar for tests (no paddle required).

Speaks the same JSON-lines protocol as the real server:
  init -> ready | ocr -> fixed results | ping -> pong | shutdown -> bye
  "crash" op -> exit(3)      (simulates a dead sidecar)
  "sleep" op -> wait N seconds then pong   (simulates a hung request)
  "noise" op -> write a non-JSON line to stdout, then pong
"""
import sys
import time

from vehicle_dataset_manager.ocr.paddle_protocol import decode_message, encode_message

RESULTS = [
    {"text": "FKE-1234", "score": 0.91, "box": [10, 20, 120, 40]},
    {"text": "RS015", "score": 0.7, "box": [5, 5, 60, 25]},
    {"text": "8", "score": 0.4, "box": [80, 80, 90, 90]},
]


def _write(msg: dict) -> None:
    sys.stdout.buffer.write(encode_message(msg))
    sys.stdout.buffer.flush()


def main() -> int:
    for raw in sys.stdin.buffer:
        msg = decode_message(raw)
        if msg is None:
            continue
        op = msg.get("op")
        rid = msg.get("id")
        if op == "init":
            _write({
                "op": "ready", "id": rid, "engine": "fake", "version": "0.0",
                "det_model": msg.get("det_model", ""), "rec_model": msg.get("rec_model", ""),
                "elapsed_ms": 0,
            })
        elif op == "ocr":
            _write({"op": "result", "id": rid, "results": RESULTS, "elapsed_ms": 0})
        elif op == "ping":
            _write({"op": "pong", "id": rid})
        elif op == "shutdown":
            _write({"op": "bye", "id": rid})
            return 0
        elif op == "crash":
            return 3
        elif op == "sleep":
            time.sleep(float(msg.get("seconds", 2)))
            _write({"op": "pong", "id": rid})
        elif op == "noise":
            sys.stdout.buffer.write(b"Creating model: (fake noise line)\n")
            sys.stdout.buffer.flush()
            _write({"op": "pong", "id": rid})
        else:
            _write({"op": "error", "id": rid, "message": f"unknown op: {op!r}", "fatal": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
