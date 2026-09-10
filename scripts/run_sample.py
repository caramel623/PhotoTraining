"""Process a random sample of PENDING images (fast pipeline validation).

Usage:
    run_sample.py [workspace] [archive] [count] [seed]

Defaults: runs/2025, 2025.7z, 200 images, seed 42.
Set OCR_ENGINE=paddle for real PaddleOCR (otherwise stub OCR is used).

Unlike run_real.py (which walks images in image_id order), this draws a
random subset via ProcessingEngine.run_sample so a large archive can be
validated in minutes without touching the rest of the PENDING batch.
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

import run_real as rr  # noqa: E402  (reuses workspace, db and build_engine)

COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 200
SEED = int(sys.argv[4]) if len(sys.argv) > 4 else 42

row = rr.db.query_one(
    "SELECT job_id FROM processing_jobs WHERE archive_path=? ORDER BY job_id DESC LIMIT 1",
    (rr.ARCHIVE.name,),
)
if row is None:
    print("NO JOB FOUND for", rr.ARCHIVE.name)
    sys.exit(1)
job_id = int(row["job_id"])

c0 = rr.images.counts(rr.ARCHIVE.name)
print(f"SAMPLE run  archive={rr.ARCHIVE.name} count={COUNT} seed={SEED} job_id={job_id}")
print(f"  counts_before={c0}")

engine = rr.build_engine()
start_iso = datetime.now(timezone.utc).isoformat()
t0 = time.time()


def on_progress(done, tot, name):
    print(f"  sample {done}/{tot}  ({time.time() - t0:.0f}s)  {name[:52]}", flush=True)


res = engine.run_sample(job_id, COUNT, seed=SEED, on_progress=on_progress)
c1 = rr.images.counts(rr.ARCHIVE.name)
print(f"SAMPLE done in {time.time() - t0:.0f}s  processed={res.processed} failed={res.failed} pending_left={res.pending_left}")
print(f"  counts_after={c1}")

print("  ---- sample breakdown (rows updated this run) ----")
rows = rr.db.query(
    "SELECT processing_status, plate_text_normalized, plate_confidence, quality_flags "
    "FROM images WHERE updated_at >= ? ORDER BY image_id",
    (start_iso,),
)
done = [r for r in rows if r["processing_status"] == "completed"]
failed = [r for r in rows if r["processing_status"] == "failed"]
with_plate = [r for r in done if r["plate_text_normalized"]]
no_plate = [r for r in done if not r["plate_text_normalized"]]
print(f"  completed={len(done)}  with_plate={len(with_plate)}  no_plate={len(no_plate)}  failed={len(failed)}")
if failed:
    for r in failed[:5]:
        print(f"    FAILED {r['quality_flags']}")
top = {}
for r in with_plate:
    top[r["plate_text_normalized"]] = top.get(r["plate_text_normalized"], 0) + 1
print("  top plates:", sorted(top.items(), key=lambda kv: -kv[1])[:10])

if rr.PADDLE_PROCESS is not None:
    rr.PADDLE_PROCESS.close()
rr.db.close()