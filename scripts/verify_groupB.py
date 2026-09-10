import sys, logging, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)
import cv2
from vehicle_dataset_manager.core.config import AppSettings
from vehicle_dataset_manager.detection import build_vehicle_detector
import glob
s = AppSettings()
print("slot:", s.models.vehicle_detector, "model:", s.models.vehicle_model, "conf:", s.models.vehicle_conf, "use_cuda:", s.device.use_cuda)
det = build_vehicle_detector(s, models_dir=os.getcwd())
print("detector:", type(det).__name__, "device:", getattr(det, "device", "n/a"))
det.warmup()
files = sorted(glob.glob(r"runs/2025/extracted/2025/*/*/*.jpg"))
step = max(1, len(files)//8)
random_sample = [files[i] for i in range(0, min(len(files), step*8), step)]
hit=0
for f in random_sample:
    img = cv2.imread(f)
    dets = det.detect(img)
    hit += 1 if dets else 0
    top = sorted(dets, key=lambda d: d.confidence, reverse=True)
    print(f"{'VEH' if dets else '---'} {os.path.basename(f)} -> {[(d.class_name, round(d.confidence,2)) for d in top]}")
print(f"vehicle hit {hit}/{len(random_sample)}")