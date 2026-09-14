"""Generate a reproducible update file inventory after an onedir build."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from vehicle_dataset_manager import __version__
from vehicle_dataset_manager.services.app_update import MANIFEST, sha256

def write_manifest(directory):
    directory = Path(directory).resolve()
    if not (directory / "VehicleDatasetManager.exe").is_file():
        raise ValueError("Not an onedir app")
    data = {
        "protocol": 1, "version": __version__,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
        "files": {p.relative_to(directory).as_posix(): sha256(p)
                  for p in sorted(directory.rglob("*"))
                  if p.is_file() and p.name != MANIFEST},
    }
    (directory / MANIFEST).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data

if __name__ == "__main__":
    print("Update manifest:", len(write_manifest(sys.argv[1])["files"]), "files")

