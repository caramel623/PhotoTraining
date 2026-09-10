import glob, random, os, time
from ultralytics import YOLO
VEH = {"car":2,"motorcycle":3,"bus":5,"truck":7}
files = sorted(glob.glob(r"runs/2025/extracted/2025/*/*/*.jpg"))
print("total frames:", len(files))
random.seed(7)
sample = random.sample(files, min(20, len(files)))
for name in ["yolov8n.pt","yolov8s.pt"]:
    t0=time.time()
    m = YOLO(name)
    hit=0; clscount={}
    for f in sample:
        r = m.predict(f, conf=0.3, verbose=False)[0].boxes
        cls = r.cls.cpu().numpy().astype(int) if len(r.cls) else []
        for c in cls:
            if int(c) in VEH.values():
                hit+=1; break
        for c in cls:
            nm=m.names[int(c)]
            if int(c) in VEH.values(): clscount[nm]=clscount.get(nm,0)+1
    dt=time.time()-t0
    print(f"[{name}] frames={len(sample)} load+infer={dt:.1f}s  veh_hit={hit}/{len(sample)}  classes={clscount}")