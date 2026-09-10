import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from vehicle_dataset_manager.core.workspace import Workspace  # noqa: E402
from vehicle_dataset_manager.database.connection import Database  # noqa: E402
from vehicle_dataset_manager.archive.manager import ArchiveManager  # noqa: E402
from vehicle_dataset_manager.services.import_service import ImportService  # noqa: E402
from vehicle_dataset_manager.database.repositories import (  # noqa: E402
    DetectionRepository, ImageRepository, JobRepository, OcrRepository,
    PlateRepository, VehicleRepository,
)
from vehicle_dataset_manager.core.config import AppSettings  # noqa: E402
from vehicle_dataset_manager.detection.base import StubPlateDetector, StubVehicleDetector  # noqa: E402
from vehicle_dataset_manager.detection.yolo_detector import build_vehicle_detector  # noqa: E402
from vehicle_dataset_manager.ocr.base import StubOcr  # noqa: E402
from vehicle_dataset_manager.ocr.paddle_client import build_paddle_engines  # noqa: E402
from vehicle_dataset_manager.ocr.paddle_protocol import preset_models  # noqa: E402
from vehicle_dataset_manager.pipeline.engine import ProcessingEngine  # noqa: E402
from vehicle_dataset_manager.pipeline.stages import build_default_stages  # noqa: E402

WS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / "2025"
ARCHIVE = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "2025.7z"
CMD = sys.argv[3] if len(sys.argv) > 3 else "all"
MAX = int(sys.argv[4]) if len(sys.argv) > 4 else 0

ws = Workspace(WS).ensure()
db = Database(ws.database_path)
images = ImageRepository(db)


PADDLE_PROCESS = None


def build_engine():
    global PADDLE_PROCESS
    import os

    vehicles = VehicleRepository(db)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ws.root))
    settings = AppSettings()
    settings.device.use_cuda = os.environ.get("USE_CUDA", "0") == "1"
    settings.models.vehicle_model = os.environ.get("VEHICLE_MODEL", "yolov8s.pt")
    settings.models.vehicle_conf = float(os.environ.get("VEHICLE_CONF", "0.3"))
    det = build_vehicle_detector(settings, models_dir=ws.models_dir)
    print('  detector=' + type(det).__name__ + ' device=' + getattr(det, 'device', '?') + ' model=' + getattr(det, 'model_path', '?'), flush=True)

    ocr_engine = os.environ.get("OCR_ENGINE", "none").lower()
    if ocr_engine == "paddle":
        preset = os.environ.get("OCR_PRESET", "mobile")
        det_model, rec_model = preset_models(preset)
        plate_det, ocr, PADDLE_PROCESS = build_paddle_engines(
            cache_dir=ws.cache_dir / "paddlex",
            det_model=det_model,
            rec_model=rec_model,
        )
        print('  paddle ocr: preset=' + preset + ' cache=' + str(ws.cache_dir / "paddlex") + ' python=' + str(PADDLE_PROCESS.ocr_python), flush=True)
    else:
        plate_det, ocr, PADDLE_PROCESS = StubPlateDetector(), StubOcr(), None
        print('  paddle ocr: disabled (OCR_ENGINE=' + ocr_engine + ')', flush=True)

    stages = build_default_stages(
        det, plate_det, ocr, vehicles,
        crops_dir=ws.crops_dir / "vehicle", save_crops=True,
    )
    return ProcessingEngine(
        db, stages,
        image_repo=images, vehicle_repo=vehicles,
        job_repo=JobRepository(db), plate_repo=PlateRepository(db),
        detection_repo=DetectionRepository(db), ocr_repo=OcrRepository(db),
    )


def do_import():
    svc = ImportService(db, ws, ArchiveManager())
    t0 = time.time()
    last = {"t": t0}

    def on_progress(done, total, name):
        now = time.time()
        if now - last["t"] >= 5:
            print(f"  extract {done}/{total}  {name[:48]}  (+{now - t0:.0f}s)", flush=True)
            last["t"] = now

    res = svc.import_archive(ARCHIVE, on_progress=on_progress)
    c = images.counts(ARCHIVE.name)
    print(f"IMPORT done in {time.time() - t0:.0f}s  verified={res.verified} backend={res.message} year={res.archive_year}")
    print(f"  images_found={res.images_found}  images_new={res.images_new}  job_id={res.job_id}")
    print(f"  counts={c}")
    ext = ws.extracted_dir / ARCHIVE.stem
    dayzips = [p for p in ext.rglob("*.zip")] if ext.exists() else []
    print(f"  nested day-zips left on disk={len(dayzips)}  extracted_root={ext}")


def do_process():
    row = db.query_one(
        "SELECT job_id FROM processing_jobs WHERE archive_path=? ORDER BY job_id DESC LIMIT 1",
        (ARCHIVE.name,),
    )
    if row is None:
        print("NO JOB FOUND for", ARCHIVE.name)
        return
    job_id = int(row["job_id"])
    engine = build_engine()
    t0 = time.time()
    last = {"t": t0}

    def on_progress(done, tot, name):
        now = time.time()
        if now - last["t"] >= 5 or done == tot:
            print(f"  process {done}/{tot}  ({now - t0:.0f}s)", flush=True)
            last["t"] = now

    def should_cancel():
        if MAX <= 0:
            return False
        c = images.counts(ARCHIVE.name)
        return (c["completed"] + c["skipped"] + c["failed"]) >= MAX

    res = engine.run(job_id, on_progress=on_progress, should_cancel=should_cancel)
    c = images.counts(ARCHIVE.name)
    print(f"PROCESS done in {time.time() - t0:.0f}s  processed={res.processed} failed={res.failed} pending_left={res.pending_left} state={res.final_state}")
    print(f"  counts={c}")


def do_summary():
    print("SUMMARY")
    r = db.query_one("SELECT COUNT(*) n FROM images")
    print(f"  total_rows={r['n']}")
    for label in ("completed", "failed", "pending", "processing", "skipped"):
        rr = db.query_one(f"SELECT COUNT(*) n FROM images WHERE processing_status=?", (label,))
        print(f"  {label}={rr['n']}")
    print("  ---- camera distribution ----")
    for rr in db.query("SELECT camera_id, COUNT(*) n FROM images GROUP BY camera_id ORDER BY n DESC LIMIT 12"):
        print(f"    {rr['camera_id']}: {rr['n']}")
    r = db.query_one("SELECT MIN(speed) mn, MAX(speed) mx, ROUND(AVG(speed),1) av, COUNT(speed) n FROM images")
    print(f"  speed: min={r['mn']} max={r['mx']} avg={r['av']} n_with={r['n']}")
    r = db.query_one("SELECT COUNT(*) n FROM images WHERE speed IS NOT NULL AND speed_limit IS NOT NULL AND speed > speed_limit")
    print(f"  speed_violations={r['n']}")
    print("  ---- sample completed rows ----")
    for rr in db.query("SELECT original_filename, camera_id, speed, speed_limit, direction, location FROM images WHERE processing_status='completed' LIMIT 5"):
        print(f"    {rr['original_filename']} cam={rr['camera_id']} v={rr['speed']} lim={rr['speed_limit']} dir={rr['direction']} loc={rr['location']}")


if __name__ == "__main__":
    if CMD in ("import", "all"):
        do_import()
    if CMD in ("process", "all"):
        do_process()
    if CMD in ("summary", "all"):
        do_summary()
    if PADDLE_PROCESS is not None:
        PADDLE_PROCESS.close()
    db.close()