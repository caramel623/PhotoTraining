import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vehicle_dataset_manager.core.workspace import Workspace
from vehicle_dataset_manager.database.connection import Database

WS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), "runs", "2025")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 12
ws = Workspace(WS).ensure()
db = Database(ws.database_path)
rows = db.query("SELECT image_id, original_filename FROM images WHERE processing_status='completed'")
random.seed(11)
pick = random.sample(rows, min(N, len(rows)))
for r in pick:
    db.execute(
        "UPDATE images SET processing_status='pending', vehicle_bbox=NULL, vehicle_crop_path=NULL, "
        "quality_flags='[]', plate_bbox=NULL, plate_text_raw=NULL, plate_text_normalized=NULL, "
        "plate_confidence=NULL, error=NULL WHERE image_id=?",
        (r["image_id"],),
    )
db.commit()
print(f"reset {len(pick)} images to pending (archive 2025.7z):")
for r in pick:
    print("   ", r["original_filename"])
db.close()