"""One-shot data wrap-up for the Phase 3 workspace (idempotent).

Fixes two data-state issues left by the Phase 3 handoff:
1. vehicles.label_priority was never written (code bug fixed separately) ->
   backfill 'automatic_candidate' for rows still NULL.
2. 9061 images were marked completed by the Phase-1 STUB run (NO_VEHICLE +
   NO_PLATE on every image, no detection rows). They never went through the
   real YOLO + PaddleOCR pipeline -> reset to 'pending' so the next
   `run_real.py ... process` picks them up. Real-run rows are untouched.
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ws = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / "2025"
db_path = ws / "database" / "vehicle_dataset.db"
con = sqlite3.connect(db_path)
cur = con.cursor()
ts = datetime.now(timezone.utc).isoformat()

# 1) backfill label_priority
n1 = cur.execute(
    "UPDATE vehicles SET label_priority='automatic_candidate', updated_at=? "
    "WHERE label_priority IS NULL",
    (ts,),
).rowcount
print(f"label_priority backfilled: {n1}")

# 2) requeue stub-run images (exact stub signature: both flags, no detections)
n2 = cur.execute(
    "UPDATE images SET processing_status='pending', updated_at=? "
    "WHERE processing_status='completed' AND quality_flags=?",
    (ts, '["NO_VEHICLE", "NO_PLATE"]'),
).rowcount
print(f"images requeued to pending: {n2}")
con.commit()

print("--- counts after ---")
for row in cur.execute("SELECT processing_status, COUNT(*) FROM images GROUP BY 1"):
    print(row)
for row in cur.execute("SELECT label_priority, COUNT(*) FROM vehicles GROUP BY 1"):
    print(row)
con.close()
