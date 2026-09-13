"""Two-phase, filename-unique original-photo replacement with row checkpoints."""
from __future__ import annotations

import json
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from vehicle_dataset_manager.archive.manager import IMAGE_EXTS
from vehicle_dataset_manager.services.photo_health import read_photo
from vehicle_dataset_manager.services.metadata_resolver import MetadataResolver


class OriginalReplacementService:
    def __init__(self, db, workspace, archive_manager):
        self.db, self.workspace, self.archives = db, workspace, archive_manager

    def _state(self, image_id):
        row = self.db.query_one("SELECT * FROM images WHERE image_id=?", (image_id,))
        if row is None:
            return None
        state = {"image": dict(row)}
        for table in ("detections", "plates", "ocr_results", "reviews", "vehicle_members"):
            state[table] = [dict(r) for r in self.db.query(f"SELECT * FROM {table} WHERE image_id=?", (image_id,))]
        state["groups"] = [dict(r) for r in self.db.query(
            "SELECT v.* FROM vehicles v JOIN vehicle_members m ON m.vehicle_id=v.vehicle_id WHERE m.image_id=?", (image_id,))]
        state["duplicates"] = [dict(r) for r in self.db.query(
            "SELECT * FROM duplicates WHERE image_id=? OR duplicate_of_id=?", (image_id, image_id))]
        return state

    @staticmethod
    def needs_confirmation(state):
        row = state["image"]
        return bool(row["processing_status"] != "pending" or row["review_status"] != "unreviewed"
                    or row.get("manual_plate_text") or row.get("vehicle_bbox")
                    or row.get("plate_bbox") or row.get("vehicle_crop_path")
                    or any(state[name] for name in ("detections", "plates", "ocr_results", "reviews"))
                    or any(m.get("label_source") == "manual" for m in state["vehicle_members"])
                    or any(g["source"] in ("manual", "mixed") or g["verification"] != "automatic_only" for g in state["groups"]))

    def prepare(self, sources, on_progress=None, should_cancel=None):
        root = Path(tempfile.mkdtemp(prefix="originals-", dir=self.workspace.extracted_dir))
        plan = {"root": str(root), "items": [], "issues": [], "cancelled": False}
        for index, source in enumerate(sources):
            source = Path(source).resolve()
            if should_cancel and should_cancel():
                plan["cancelled"] = True
                return plan
            dest = root / str(index)
            dest.mkdir()
            if source.is_dir():
                # Never recursively copy the live workspace into itself.
                if root.is_relative_to(source):
                    raise ValueError("原圖資料夾不能包含目前工作區；請選擇獨立的原圖來源資料夾。")
                for path in source.rglob("*"):
                    if should_cancel and should_cancel():
                        plan["cancelled"] = True
                        return plan
                    if path.is_file() and not path.is_symlink() and path.suffix.lower() in IMAGE_EXTS:
                        target = dest / path.relative_to(source)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
            else:
                if not self.archives.verify_archive(source).ok:
                    raise ValueError(f"壓縮檔驗證失敗：{source.name}")
                result = self.archives.extract_archives_recursively(source, dest, on_progress=on_progress, should_cancel=should_cancel)
                if result.failed:
                    raise ValueError(f"解壓失敗：{source.name}")
                if result.cancelled:
                    plan["cancelled"] = True
                    return plan
        incoming, existing = defaultdict(list), defaultdict(list)
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                incoming[path.name.casefold()].append(path)
        for row in self.db.query("SELECT image_id,original_filename FROM images"):
            existing[row["original_filename"].casefold()].append(row["image_id"])
        for index, (name, paths) in enumerate(incoming.items(), 1):
            if should_cancel and should_cancel():
                plan["cancelled"] = True
                break
            ids = existing.get(name, [])
            if len(ids) != 1 or len(paths) != 1:
                plan["issues"].append(f"{name}：無配對或同名衝突（來源 {len(paths)}，資料庫 {len(ids)}），未替換")
                continue
            try:
                image, digest = read_photo(paths[0])
            except Exception as exc:
                plan["issues"].append(f"{name}：無法讀取原圖：{exc}")
                continue
            state = self._state(ids[0])
            if state is None:
                continue
            if digest == state["image"].get("sha256"):
                plan["issues"].append(f"{name}：內容相同，未替換（遺失檔案請用修復模式）")
                continue
            plan["items"].append({"state": state, "path": str(paths[0]), "sha256": digest,
                                  "width": int(image.shape[1]), "height": int(image.shape[0]),
                                  "confirm": self.needs_confirmation(state)})
            if on_progress:
                on_progress(index, len(incoming), "配對原始影像")
        return plan

    def apply(self, plan, approved_ids=(), on_progress=None, should_cancel=None):
        result = {"replaced": 0, "skipped": 0, "errors": list(plan["issues"]), "cancelled": plan["cancelled"], "root": plan["root"]}
        if plan["cancelled"]:
            return result
        approved_ids = set(approved_ids)
        for index, item in enumerate(plan["items"], 1):
            if should_cancel and should_cancel():
                result["cancelled"] = True
                break
            old = item["state"]["image"]
            image_id = old["image_id"]
            if item["confirm"] and image_id not in approved_ids:
                result["skipped"] += 1
                continue
            try:
                _, digest = read_photo(Path(item["path"]))
                if digest != item["sha256"]:
                    raise ValueError("待替換副本內容已變動，請重新匯入")
                with self.db.transaction():
                    if self._state(image_id) != item["state"]:
                        raise ValueError("資料或標註已變動，請重新預覽確認")
                    backup = Path(plan["root"]) / f"before-{image_id}.json"
                    with backup.open("x", encoding="utf-8") as handle:
                        json.dump(item["state"], handle, ensure_ascii=False, indent=2)
                    for table in ("detections", "plates", "ocr_results"):
                        self.db.execute(f"DELETE FROM {table} WHERE image_id=?", (image_id,))
                    self.db.execute("DELETE FROM duplicates WHERE image_id=? OR duplicate_of_id=?", (image_id, image_id))
                    plate = MetadataResolver.resolve_plate(manual_plate=old.get("manual_plate_text"),
                        ini_plate=old.get("ini_plate_text") if old.get("plate_source") in ("ini", "manual") else None,
                        ocr_plate=None, ocr_confidence=None)
                    self.db.execute(
                        "UPDATE images SET source_path=?,sha256=?,width=?,height=?,processing_status='pending',"
                        "vehicle_bbox=NULL,plate_bbox=NULL,vehicle_crop_path=NULL,vehicle_crop_bbox=NULL,"
                        "perceptual_hash=NULL,ocr_plate_text=NULL,ocr_plate_normalized=NULL,plate_confidence=NULL,error=NULL,"
                        "plate_text_raw=?,plate_text_normalized=?,plate_source=?,plate_validation_status=?,"
                        "label_confidence=?,label_trust_level=?,quality_flags=? WHERE image_id=?",
                        (item["path"], digest, item["width"], item["height"], plate.raw, plate.normalized,
                         plate.source, plate.validation_status, plate.confidence, plate.trust_level,
                         json.dumps(["ORIGINAL_REPLACED"] + [v for v in json.loads(old.get("quality_flags") or "[]") if v.startswith("INI_")]), image_id))
                result["replaced"] += 1
            except Exception as exc:
                result["errors"].append(f"#{image_id} {old['original_filename']}：{exc}")
            if on_progress:
                on_progress(index, len(plan["items"]), "替換原始影像")
        with (Path(plan["root"]) / "replacement-report.json").open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        return result
