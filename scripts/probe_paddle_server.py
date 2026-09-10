r"""G1 smoke test: spawn the PaddleOCR sidecar and drive the protocol end-to-end.

Usage (from the repo root):

    .venv-ocr\Scripts\python.exe scripts\probe_paddle_server.py
    # or the main venv python; the server subprocess always uses .venv-ocr

Exit code 0 = protocol + engine OK. Prints per-image OCR boxes.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import cv2  # noqa: E402
from vehicle_dataset_manager.ocr import paddle_protocol as proto  # noqa: E402

OCR_PY = ROOT / ".venv-ocr" / "Scripts" / "python.exe"
CACHE_HOME = ROOT / "runs" / "home"  # writable HOME; models already cached in .paddlex
CROPS_DIR = ROOT / "runs" / "2025" / "crops" / "vehicle"


def pump_stderr(proc) -> None:
    for line in proc.stderr:
        sys.stderr.buffer.write(line)
        sys.stderr.buffer.flush()


def main() -> int:
    if not OCR_PY.exists():
        print("MISSING .venv-ocr python at", OCR_PY)
        return 2
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["HOME"] = str(CACHE_HOME)
    env["USERPROFILE"] = str(CACHE_HOME)
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        [str(OCR_PY), "-m", "vehicle_dataset_manager.ocr.paddle_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=env,
    )
    threading.Thread(target=pump_stderr, args=(proc,), daemon=True).start()

    def send(msg: bytes) -> None:
        proc.stdin.write(msg)
        proc.stdin.flush()

    def recv():
        return proto.decode_message(proc.stdout.readline())

    imgs = sorted(CROPS_DIR.glob("*.jpg"))[:8]
    print(f"crops: {len(imgs)}  server={OCR_PY.name}")

    det_model, rec_model = proto.preset_models("mobile")
    t0 = time.time()
    send(proto.init_request(1, det_model, rec_model, enable_mkldnn=False))
    ready = recv()
    if not ready or ready.get("op") != "ready":
        print("INIT FAILED:", ready)
        proc.kill()
        return 1
    print(
        f"ready: paddleocr={ready['version']} det={ready['det_model']} "
        f"rec={ready['rec_model']} ({time.time() - t0:.1f}s)"
    )

    failures = 0
    for i, path in enumerate(imgs):
        img = cv2.imread(str(path))
        if img is None:
            print(f"{path.name}: unreadable, skipped")
            failures += 1
            continue
        t0 = time.time()
        send(proto.ocr_request(2 + i, img))
        resp = recv()
        if not resp or resp.get("op") != "result":
            print(f"{path.name}: BAD RESPONSE {resp}")
            failures += 1
            continue
        boxes = proto.parse_results(resp)
        best = max(boxes, key=lambda b: b.score) if boxes else None
        box_str = str(best.box) if best else "-"
        text_str = best.text if best else "-"
        score_str = f"{best.score:.3f}" if best else "-"
        print(
            f"{path.name[:44]:44} n={len(boxes)} "
            f"best={text_str} ({score_str} @ {box_str}) "
            f"ocr={resp.get('elapsed_ms')}ms wire={int((time.time() - t0) * 1000)}ms"
        )

    send(proto.ping_request(98))
    pong = recv()
    print("ping ->", pong)

    send(proto.shutdown_request(99))
    bye = recv()
    proc.stdin.close()
    rc = proc.wait(timeout=30)
    print("shutdown ->", bye, " exit_code=", rc)

    if rc != 0 or failures:
        return 1
    print("PROBE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
