import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vehicle_dataset_manager.core.workspace import Workspace
from vehicle_dataset_manager.database.connection import Database
ws = Workspace(r"runs\2025").ensure()
db = Database(ws.database_path)
rows = db.query("""
  SELECT original_filename, vehicle_bbox, vehicle_crop_path, quality_flags
  FROM images
  WHERE vehicle_crop_path IS NOT NULL OR vehicle_bbox IS NOT NULL
  ORDER BY original_filename
""")
print(f"rows with vehicle data: {len(rows)}")
for r in rows:
    crop = r["vehicle_crop_path"]
    exists = ""
    if crop:
        exists = "OK" if os.path.exists(crop) else "MISSING"
    print(f"  {r['original_filename']}")
    print(f"      bbox={r['vehicle_bbox']}")
    print(f"      crop=[{exists}] {crop}")
    print(f"      flags={r['quality_flags']}")
# detection child rows
n = db.query_one("SELECT COUNT(*) n FROM detections WHERE kind='vehicle'")
print(f"vehicle detection rows: {n['n']}")
db.close()