import sys
from pathlib import Path
import py7zr

EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
path = sys.argv[1]
with py7zr.SevenZipFile(str(path)) as zf:
    info = zf.list()

e0 = info[0]
print("entry_type=", type(e0).__name__)
try:
    print("vars_keys=", sorted(vars(e0).keys()))
except Exception as ex:
    print("vars failed", ex)

def attr(e, name, default=None):
    try:
        return getattr(e, name, default)
    except Exception:
        return default

size_attr = None
for cand in ("uncompressed", "uncompressed_size"):
    v = attr(e0, cand)
    if isinstance(v, int):
        size_attr = cand
        break
print("size_attr=", size_attr, "sample=", attr(e0, size_attr) if size_attr else None)
print("filename_sample=", getattr(e0, "filename", None))
print("is_directory_sample=", getattr(e0, "is_directory", None))

def size_of(e):
    v = attr(e, size_attr) if size_attr else None
    try:
        return int(v) if v is not None else 0
    except Exception:
        return 0

def name_of(e):
    return getattr(e, "filename", "?")

def is_dir(e):
    return bool(getattr(e, "is_directory", False))

def is_img(e):
    if is_dir(e):
        return False
    return Path(name_of(e)).suffix.lower() in EXTS

total = len(info)
images = [e for e in info if is_img(e)]
uncomp = sum(size_of(e) for e in info)
img_uncomp = sum(size_of(e) for e in images)
print(f"total_entries={total}")
print(f"image_entries={len(images)}")
print(f"total_uncompressed_MB={uncomp/1024/1024:.1f}")
print(f"image_uncompressed_MB={img_uncomp/1024/1024:.1f}")
print("---- first 12 ----")
for e in info[:12]:
    print(("DIR" if is_dir(e) else "  "), name_of(e), size_of(e))
print("---- last 3 ----")
for e in info[-3:]:
    print(name_of(e), size_of(e))