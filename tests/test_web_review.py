import threading
import time
import urllib.request
import urllib.error
import json

import pytest

from tests.test_review_page import _app, _seed_page as _base_seed
from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.ui.web_review import ReviewAPI, WebReview


def _seed_page(db, workspace):
    ctx, group, ids = _base_seed(db, workspace)
    ctx.db = db
    return ctx, group, ids


def test_web_defaults_and_port_validation():
    assert AppSettings().review_web_port == 8080
    assert AppSettings(review_web_port=9090).review_web_port == 9090
    with pytest.raises(ValueError):
        AppSettings(review_web_port=65536)


def test_six_digit_code_rotates_on_restart(db, workspace, monkeypatch):
    import vehicle_dataset_manager.ui.web_review as module
    app = _app()
    values = iter([7, 7, 812])
    monkeypatch.setattr(module.secrets, "randbelow", lambda limit: next(values))
    ctx, _, _ = _seed_page(db, workspace)
    server = WebReview(ctx, lambda: False)
    try:
        server.start(0, "127.0.0.1")
        assert server.token == "000007"
        old_session = server._session
        server.stop()
        assert not server.token
        port = server.start(0, "127.0.0.1")
        assert server.token == "000812"
        assert server._session is not old_session
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api",
                data=b'{}', headers={"X-Review-Token": "000007"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=3)
        assert error.value.code == 401
    finally:
        server.stop()


def test_code_guessing_is_throttled(db, workspace):
    app = _app()
    ctx, _, _ = _seed_page(db, workspace)
    server = WebReview(ctx, lambda: False)
    try:
        port = server.start(0, "127.0.0.1")
        for attempt in range(6):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api",
                    data=b'{"action":"groups"}', headers={"X-Review-Token": "invalid"})
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req, timeout=3)
            assert error.value.code == (401 if attempt < 5 else 429)
            error.value.read()
            error.value.close()
    finally:
        server.stop()


def test_web_confirm_then_correct_and_reject_stale(db, workspace):
    ctx, group, ids = _seed_page(db, workspace)
    api = ReviewAPI(ctx, lambda: False)
    data = api.handle({"action": "images", "vehicle": group})
    assert "display_path" not in data["images"][0]
    api.handle({"action": "confirm", "vehicle": group, "revision": data["revision"]})
    assert all(ctx.images.get(i).review_status == "verified_same_vehicle" for i in ids)
    with pytest.raises(ValueError, match="重新整理"):
        api.handle({"action": "status", "vehicle": group, "image": ids[0],
                    "revision": data["revision"], "status": "uncertain"})
    fresh = api.handle({"action": "images", "vehicle": group})
    api.handle({"action": "status", "vehicle": group, "image": ids[0],
                "revision": fresh["revision"], "status": "uncertain"})
    assert ctx.images.get(ids[0]).review_status == "uncertain"
    assert ctx.vehicles.get(group)["verification"] == "partially_verified"


def test_web_confirm_preserves_different_vehicle(db, workspace):
    ctx, group, ids = _seed_page(db, workspace)
    api = ReviewAPI(ctx, lambda: False)
    data = api.handle({"action": "images", "vehicle": group})
    api.handle({"action": "status", "vehicle": group, "image": ids[0],
                "revision": data["revision"], "status": "verified_not_same_vehicle"})
    for _ in range(2):
        data = api.handle({"action": "images", "vehicle": group})
        api.handle({"action": "confirm", "vehicle": group, "revision": data["revision"]})
        assert ctx.images.get(ids[0]).review_status == "verified_not_same_vehicle"
        assert ctx.images.get(ids[1]).review_status == "verified_same_vehicle"
        assert ctx.vehicles.get(group)["verification"] == "partially_verified"


def test_web_busy_rejects_actions(db, workspace):
    ctx, _, _ = _seed_page(db, workspace)
    with pytest.raises(ValueError, match="稍後"):
        ReviewAPI(ctx, lambda: True).handle({"action": "groups"})


def test_web_photo_rejects_external_file(db, workspace, tmp_path):
    ctx, group, ids = _seed_page(db, workspace)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"private")
    ctx.images.set_result_fields(ids[0], vehicle_crop_path=str(outside))
    api = ReviewAPI(ctx, lambda: False)
    data = api.handle({"action": "images", "vehicle": group})
    with pytest.raises(ValueError, match="不在工作區"):
        api.handle({"action": "photo", "vehicle": group, "image": ids[0],
                    "revision": data["revision"]})


def test_http_auth_lifecycle_and_main_thread_dispatch(db, workspace):
    app = _app()
    ctx, group, ids = _seed_page(db, workspace)
    server = WebReview(ctx, lambda: False)
    port = server.start(0, "127.0.0.1")
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base, timeout=3) as response:
            assert "區網人工覆核" in response.read().decode()
        body = json.dumps({"action": "groups"}).encode()
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + "/api", data=body), timeout=3)
        assert error.value.code == 401
        results = []
        def request():
            try:
                req = urllib.request.Request(base + "/api", data=body,
                        headers={"X-Review-Token": server.token})
                with urllib.request.urlopen(req, timeout=5) as response:
                    results.append(json.loads(response.read()))
            except Exception as exc:
                results.append(exc)
        thread = threading.Thread(target=request)
        thread.start()
        deadline = time.monotonic() + 6
        while thread.is_alive() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        thread.join(timeout=1)
        assert results and isinstance(results[0], dict), results
        assert results[0]["groups"][0]["vehicle_id"] == group
    finally:
        server.stop()
    assert server.server is None and not server.token
