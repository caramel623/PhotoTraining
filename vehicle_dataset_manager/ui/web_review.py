"""Opt-in LAN review. HTTP threads never touch Qt widgets or the database."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import secrets
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue, Empty
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QTimer
from vehicle_dataset_manager.core.enums import ReviewStatus
from vehicle_dataset_manager.review_model import ReviewModel
from vehicle_dataset_manager.ui.web_review_html import HTML


class _BoundedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class ReviewAPI:
    """Called only on the GUI thread, including all transactions."""

    def __init__(self, ctx, busy):
        self.ctx = ctx
        self.busy = busy
        self.model = ReviewModel(ctx.vehicles, ctx.images, ctx.reviews)

    def handle(self, payload):
        if self.busy():
            raise ValueError("目前正在處理、匯入或清除資料，請稍後重新整理。")
        action = payload.get("action")
        page = payload.get("page", 0)
        if type(page) is not int or not 0 <= page <= 100000:
            raise ValueError("頁碼不正確")
        if action == "groups":
            groups = self.model.list_groups()
            return {"groups": [asdict(g) for g in groups[page * 100:(page + 1) * 100]],
                    "total": len(groups)}
        vehicle = payload.get("vehicle")
        if not isinstance(vehicle, str) or not self.ctx.vehicles.get(vehicle):
            raise ValueError("群組不存在，請重新整理")
        with self.ctx.db.transaction():
            images = self.model.group_images(vehicle)
            revision = hashlib.sha256(json.dumps(
                [asdict(i) for i in images], sort_keys=True).encode()).hexdigest()
            if action == "images":
                rows = []
                for image in images[page * 100:(page + 1) * 100]:
                    row = asdict(image)
                    row.pop("display_path")  # Do not disclose filesystem paths.
                    rows.append(row)
                return {"images": rows, "total": len(images), "revision": revision}
            if payload.get("revision") != revision:
                raise ValueError("群組已被其他操作修改，請重新整理後再試。")
            if action == "confirm":
                self.model.confirm_group(vehicle)
                return {"ok": True}
            image_id = payload.get("image")
            image = next((i for i in images if i.image_id == image_id), None)
            if image is None:
                raise ValueError("照片已不在此群組")
            if action == "photo":
                path = Path(image.display_path or "").resolve()
                if not path.is_relative_to(self.ctx.workspace.root.resolve()):
                    raise ValueError("照片不在工作區內，無法透過區網提供")
                mime = mimetypes.guess_type(path.name)[0]
                if mime not in {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff"}:
                    raise ValueError("不支援的影像格式")
                if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError("照片不存在或超過 64 MB")
                return (mime, path.read_bytes())
            if action == "status":
                status = ReviewStatus(payload.get("status"))
                self.model.set_image_status(image_id, status, vehicle_id=vehicle)
                self.model.sync_verification(vehicle)
            elif action == "plate":
                plate = payload.get("plate")
                if not isinstance(plate, str) or len(plate) > 64:
                    raise ValueError("車牌格式不正確")
                target = self.model.edit_image_plate(image_id, plate, current_vehicle=vehicle)
                self.model.sync_verification(vehicle)
                self.model.sync_verification(target)
            else:
                raise ValueError("不支援的操作")
        return {"ok": True}


class WebReview(QObject):
    def __init__(self, ctx, busy, parent=None):
        super().__init__(parent)
        self.api = ReviewAPI(ctx, busy)
        self.queue = Queue(maxsize=16)
        self.server = None
        self.thread = None
        self.token = ""
        self._last_token = ""
        self._session = None
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._drain)

    def start(self, port, host="0.0.0.0"):
        if self.server:
            raise ValueError("區網覆核已啟動")
        owner = self
        session = object()
        token = f"{secrets.randbelow(1000000):06d}"
        while token == self._last_token:
            token = f"{secrets.randbelow(1000000):06d}"
        auth_lock = threading.Lock()
        failures = 0
        blocked_until = 0.0

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, *_args):
                pass  # Never log credentials or photo identifiers.

            def reply(self, code, mime, body):
                self.send_response(code)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                if code == 429:
                    self.send_header("Retry-After", "60")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy",
                    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "img-src blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path != "/":
                    self.reply(404, "text/plain", b"Not found")
                    return
                self.reply(200, "text/html; charset=utf-8", HTML.encode("utf-8"))

            def do_POST(self):
                nonlocal failures, blocked_until
                if self.path != "/api":
                    self.reply(404, "text/plain", b"Not found")
                    return
                supplied = self.headers.get("X-Review-Token", "")
                # Consume a bounded body before an early authentication reply;
                # closing Windows sockets with unread data can reset the reply.
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= 8192:
                        raise ValueError("Request too large")
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError("Incomplete request")
                except (ValueError, OSError):
                    self.reply(400, "text/plain", b"Invalid request")
                    return
                with auth_lock:
                    now = time.monotonic()
                    blocked = now < blocked_until
                    if not blocked and blocked_until:
                        failures, blocked_until = 0, 0.0
                    valid = secrets.compare_digest(supplied.encode(), token.encode())
                    if not blocked and not valid:
                        failures += 1
                        if failures >= 5:
                            blocked_until = now + 60
                if blocked:
                    self.reply(429, "text/plain; charset=utf-8", "驗證碼錯誤次數過多，請等待 60 秒或在桌面停止後重新啟動。".encode())
                    return
                if not valid:
                    self.reply(401, "text/plain; charset=utf-8", "存取碼不正確".encode())
                    return
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    self.reply(403, "text/plain", b"Cross-origin request refused")
                    return
                try:
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid request")
                    result = Queue(maxsize=1)
                    cancelled = threading.Event()
                    owner.queue.put_nowait((payload, result, cancelled, session))
                    try:
                        code, mime, body = result.get(timeout=15)
                    except Empty:
                        cancelled.set()
                        code, mime, body = 503, "text/plain; charset=utf-8", "程式忙碌，請重新整理確認結果。".encode()
                    self.reply(code, mime, body)
                except (ValueError, OSError):
                    self.reply(400, "text/plain", b"Invalid request")
                except Exception:
                    self.reply(503, "text/plain", b"Service busy")

        self.server = _BoundedServer((host, port), Handler)
        self.server.daemon_threads = True
        self.token = token
        self._last_token = token
        self._session = session
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        self.timer.start()
        return self.server.server_port

    def _drain(self):
        for _ in range(4):
            try:
                payload, result, cancelled, session = self.queue.get_nowait()
            except Empty:
                break
            if cancelled.is_set() or session is not self._session:
                result.put((503, "text/plain", b"Session expired"))
                continue
            try:
                value = self.api.handle(payload)
                if isinstance(value, tuple):
                    mime, body = value
                else:
                    mime, body = "application/json; charset=utf-8", json.dumps(value, ensure_ascii=False).encode()
                result.put((200, mime, body))
            except (ValueError, OSError) as exc:
                result.put((409, "text/plain; charset=utf-8", str(exc).encode()))
            except Exception:
                result.put((500, "text/plain; charset=utf-8", "操作失敗，請查看桌面程式。".encode()))

    def stop(self):
        self._session = None
        self.timer.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None
        while True:
            try:
                _, result, cancelled, _ = self.queue.get_nowait()
                cancelled.set()
                result.put((503, "text/plain", b"Service stopped"))
            except Empty:
                break
        self.token = ""
