from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENC = "utf-8"


def edit(rel, old, new):
    p = ROOT / rel
    t = p.read_text(encoding=ENC)
    if t.count(old) != 1:
        raise SystemExit("ANCHOR FAIL " + rel + " count=" + str(t.count(old)) + " OLD<<" + old[:90] + ">>")
    p.write_text(t.replace(old, new, 1), encoding=ENC)
    print("OK  ", rel)


def append(rel, chunk):
    p = ROOT / rel
    t = p.read_text(encoding=ENC)
    if not t.endswith("\n"):
        t += "\n"
    p.write_text(t + chunk, encoding=ENC)
    print("OK+ ", rel)


probe = "主機"
print("PROBE codepoints:", [hex(ord(c)) for c in probe])

# 1) schema: add v2 migration (whitespace-robust: split before _get_version)
sp = ROOT / "vehicle_dataset_manager/database/schema.py"
t = sp.read_text(encoding=ENC)
head, tail = t.split("def _get_version", 1)
base = head.rstrip()
assert base.endswith("]"), "schema tail unexpected: " + base[-20:]
base = base[:-1].rstrip()
assert base.endswith("]"), "schema inner close unexpected"
v2 = (",\n    [\n"
      "        \"ALTER TABLE images ADD COLUMN speed_limit REAL\",\n"
      "        \"ALTER TABLE images ADD COLUMN location TEXT\",\n"
      "        \"ALTER TABLE images ADD COLUMN device_serial TEXT\",\n"
      "    ],\n]")
sp.write_text(base + v2 + "\n\n" + "def _get_version" + tail, encoding=ENC)
print("OK  ", "schema.py (v2 migration)")

# 2) repositories: allow new columns
edit("vehicle_dataset_manager/database/repositories.py",
r'''            "vehicle_type", "speed", "direction", "sha256", "perceptual_hash",
            "width", "height", "error",
        }''',
r'''            "vehicle_type", "speed", "direction", "speed_limit", "location",
            "device_serial", "sha256", "perceptual_hash",
            "width", "height", "error",
        }''')

# 3) engine: persist new fields
edit("vehicle_dataset_manager/pipeline/engine.py",
r'''        if meta.get("speed") is not None:
            fields["speed"] = meta["speed"]
        if meta.get("direction"):
            fields["direction"] = meta["direction"]''',
r'''        if meta.get("speed") is not None:
            fields["speed"] = meta["speed"]
        if meta.get("speed_limit") is not None:
            fields["speed_limit"] = meta["speed_limit"]
        if meta.get("direction"):
            fields["direction"] = meta["direction"]
        if meta.get("location"):
            fields["location"] = meta["location"]
        if meta.get("device_serial"):
            fields["device_serial"] = meta["device_serial"]''')

# 4) stages: import IniSidecarParser
edit("vehicle_dataset_manager/pipeline/stages.py",
r'''from vehicle_dataset_manager.services.metadata import FilenameMetadataParser''',
r'''from vehicle_dataset_manager.services.metadata import FilenameMetadataParser, IniSidecarParser''')

# 5) stages: MetadataStage accepts ini_parser + merges sidecar
edit("vehicle_dataset_manager/pipeline/stages.py",
r'''    def __init__(self, parser: FilenameMetadataParser | None = None) -> None:
        self.parser = parser or FilenameMetadataParser()

    def run(self, ctx: PipelineContext) -> None:
        ctx.meta = self.parser.parse(ctx.original_filename)
        # Fall back to the archive year if the filename has none.''',
r'''    def __init__(self, parser: FilenameMetadataParser | None = None,
                 ini_parser=None) -> None:
        self.parser = parser or FilenameMetadataParser()
        self.ini_parser = ini_parser

    def run(self, ctx: PipelineContext) -> None:
        ctx.meta = self.parser.parse(ctx.original_filename)
        # Merge a sibling .ini sidecar when present; sidecar values win (richer).
        if self.ini_parser is not None:
            ini_path = ctx.path.with_suffix(".ini")
            if ini_path.exists():
                try:
                    with open(ini_path, "rb") as fh:
                        ini_meta = self.ini_parser.parse_bytes(fh.read())
                except OSError:
                    ini_meta = {}
                for key, value in ini_meta.items():
                    if value not in (None, ""):
                        ctx.meta[key] = value
        # Fall back to the archive year if the filename has none.''')

# 6) stages: build_default_stages wires the ini parser
edit("vehicle_dataset_manager/pipeline/stages.py",
r'''    return [
        LoadImageStage(),
        MetadataStage(),''',
r'''    return [
        LoadImageStage(),
        MetadataStage(ini_parser=IniSidecarParser()),''')

# 7) manager: ARCHIVE_EXTS constant
edit("vehicle_dataset_manager/archive/manager.py",
r'''IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}''',
r'''IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
ARCHIVE_EXTS = {".zip", ".7z", ".7zip"}''')

# 8) manager: recursive (nested) extraction
edit("vehicle_dataset_manager/archive/manager.py",
r'''    def _extract_zip(self, path, dest, on_progress, should_cancel) -> ExtractionResult:''',
r'''    def extract_archives_recursively(
        self,
        archive_path: Path | str,
        dest: Path | str,
        *,
        max_depth: int = 6,
        on_progress: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCheck] = None,
    ) -> ExtractionResult:
        """Extract ``archive_path`` into ``dest``, then recursively extract any
        nested archives found in the tree (e.g. a 7z of daily photo zips)."""
        top = self.extract_archive(archive_path, dest, on_progress=on_progress,
                                   should_cancel=should_cancel)
        self._extract_nested(dest, max_depth=max_depth, on_progress=on_progress,
                             should_cancel=should_cancel, result=top)
        return top

    def _extract_nested(
        self,
        root: Path,
        *,
        max_depth: int,
        on_progress: Optional[ProgressCallback],
        should_cancel: Optional[CancelCheck],
        result: ExtractionResult,
    ) -> None:
        depth = 0
        while depth < max_depth:
            if should_cancel and should_cancel():
                result.cancelled = True
                return
            found = sorted(
                p for p in root.rglob("*")
                if p.is_file() and p.suffix.lower() in ARCHIVE_EXTS
            )
            if not found:
                return
            for archive in found:
                if should_cancel and should_cancel():
                    result.cancelled = True
                    return
                target = archive.parent / archive.stem
                target.mkdir(parents=True, exist_ok=True)
                sub = self.extract_archive(archive, target, on_progress=on_progress,
                                           should_cancel=should_cancel)
                result.extracted += sub.extracted
                result.failed += sub.failed
                if sub.cancelled:
                    result.cancelled = True
                    return
            depth += 1

    def _extract_zip(self, path, dest, on_progress, should_cancel) -> ExtractionResult:''')

# 9) import_service: use recursive extraction
edit("vehicle_dataset_manager/services/import_service.py",
r'''        extract = self.archive_manager.extract_archive(
            archive_path, dest, on_progress=on_progress, should_cancel=should_cancel
        )''',
r'''        extract = self.archive_manager.extract_archives_recursively(
            archive_path, dest, on_progress=on_progress, should_cancel=should_cancel
        )''')

# 10) metadata: append IniSidecarParser
append("vehicle_dataset_manager/services/metadata.py", r'''

# --------------------------------------------------------------------------- #
# Speed-camera .ini sidecar parser (flat key=value, usually Big5/cp950)
# --------------------------------------------------------------------------- #
_INI_KEY_MAP = {
    "主機": "camera_id", "host": "camera_id", "camera": "camera_id",
    "車速": "speed", "speed": "speed",
    "速限": "speed_limit", "limit": "speed_limit",
    "地點": "location", "location": "location",
    "證號": "device_serial", "serial": "device_serial", "device": "device_serial",
    "方向": "direction", "direction": "direction",
    "日期": "date", "date": "date",
    "時間": "time", "time": "time",
    "影像序號": "sequence", "sequence": "sequence",
}
_INI_ENCODINGS = ("cp950", "big5", "gbk", "utf-8", "latin-1")
_INI_NUM = re.compile(r"(\d+(?:\.\d+)?)")
_INI_DATE = re.compile(r"(20\d{2})[/.-]?([01]\d)[/.-]?([0-3]\d)")
_INI_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


class IniSidecarParser:
    """Parse a speed-camera .ini sidecar (flat key=value, usually Big5)."""

    name = "ini_sidecar"

    def parse_bytes(self, data: bytes) -> Dict[str, Optional[object]]:
        result: Dict[str, Optional[object]] = {
            "year": None, "camera_id": None, "date": None, "time": None,
            "datetime": None, "speed": None, "speed_limit": None,
            "direction": None, "location": None, "device_serial": None,
            "sequence": None,
        }
        text = None
        for enc in _INI_ENCODINGS:
            try:
                text = bytes(data).decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            return result
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line or "=" not in line or line.startswith("["):
                continue
            key, _, value = line.partition("=")
            field = _INI_KEY_MAP.get(key.strip().lower())
            value = value.strip()
            if field is None or not value:
                continue
            if field in ("speed", "speed_limit"):
                m = _INI_NUM.search(value)
                if m:
                    result[field] = float(m.group(1))
            elif field == "sequence":
                m = _INI_NUM.search(value)
                result[field] = int(m.group(1)) if m else value
            elif field == "date":
                m = _INI_DATE.search(value)
                if m:
                    result["date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                    result["year"] = int(m.group(1))
            elif field == "time":
                m = _INI_TIME.search(value)
                if m:
                    result["time"] = f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3) or '00'}"
            else:
                result[field] = value
        if result["date"] and result["time"]:
            result["datetime"] = f"{result['date']} {result['time']}"
        return result
''')

print("ALL EDITS APPLIED")