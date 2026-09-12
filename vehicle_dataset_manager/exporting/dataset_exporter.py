"""Resumable, fully local Vehicle Re-ID dataset exporter."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from vehicle_dataset_manager.exporting.masking import (
    infer_vehicle_crop_bbox,
    mask_plate,
    relative_plate_bbox,
)
from vehicle_dataset_manager.exporting.pairing import build_pairs
from vehicle_dataset_manager.exporting.splitting import SplitSpec, assign_splits

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]

_REJECTED_REVIEW_STATES = {
    "verified_not_same_vehicle",
    "uncertain",
    "excluded",
}

_IMAGE_FIELDS = (
    "image_id", "vehicle_id", "original_filename", "archive_year", "date",
    "time", "camera_id", "vehicle_type", "review_status", "verification",
    "label_priority", "label_source", "plate_normalized", "plate_confidence",
    "plate_effective", "plate_source", "plate_validation_status",
    "direction", "vehicle_type_code", "image_sequence",
    "ini_present", "ini_parse_status",
    "sha256", "original_image", "vehicle_crop", "plate_crop", "reid_crop",
    "plate_mask_bbox", "source_updated_at", "split",
)


@dataclass(frozen=True)
class ExportOptions:
    label_priorities: tuple[str, ...] = ("human_verified",)
    mask_method: str = "solid_color"
    mask_margin: int = 6
    image_quality: int = 90
    copy_originals: bool = True
    require_plate_bbox: bool = True
    max_positive_pairs_per_vehicle: int = 20
    negative_ratio: int = 2
    split: SplitSpec = field(default_factory=SplitSpec)

    def validate(self) -> None:
        if not self.label_priorities:
            raise ValueError("at least one label priority is required")
        if self.mask_method not in {"solid_color", "blur", "inpaint"}:
            raise ValueError(f"unknown plate mask method: {self.mask_method}")
        if self.mask_margin < 0:
            raise ValueError("mask margin cannot be negative")
        if not 1 <= self.image_quality <= 100:
            raise ValueError("image quality must be between 1 and 100")
        if self.max_positive_pairs_per_vehicle < 0 or self.negative_ratio < 0:
            raise ValueError("pair limits cannot be negative")
        self.split.validate()


@dataclass
class ExportResult:
    output_dir: str
    total: int = 0
    exported: int = 0
    skipped: int = 0
    cancelled: bool = False
    pair_count: int = 0
    triplet_count: int = 0
    split_counts: dict[str, int] = field(default_factory=dict)
    skip_reasons: dict[str, int] = field(default_factory=dict)


class DatasetExporter:
    """Build the complete portable dataset tree without touching originals."""

    def __init__(self, export_repository) -> None:
        self.repository = export_repository

    def preview(self, options: ExportOptions) -> dict[str, int]:
        options.validate()
        rows = self.repository.list_candidates(options.label_priorities)
        rejected = sum(
            row.get("review_status") in _REJECTED_REVIEW_STATES for row in rows
        )
        return {"candidates": len(rows), "review_rejected": rejected}

    def export(
        self,
        output_dir,
        options: Optional[ExportOptions] = None,
        *,
        on_progress: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCallback] = None,
        created_by: Optional[str] = None,
    ) -> ExportResult:
        options = options or ExportOptions()
        options.validate()
        output = Path(output_dir).expanduser().resolve()
        config = _config_payload(options)
        self._prepare_output(output, config)
        rows = self.repository.list_candidates(options.label_priorities)
        result = ExportResult(output_dir=str(output), total=len(rows))
        records: list[dict] = []
        reasons: Counter[str] = Counter()
        existing_records = self._load_existing_records(output)

        for index, row in enumerate(rows, start=1):
            if should_cancel and should_cancel():
                result.cancelled = True
                break
            existing = existing_records.get(int(row["image_id"]))
            if existing is not None and self._can_reuse(
                output, row, existing, options
            ):
                record, reason = existing, None
            else:
                record, reason = self._export_row(output, row, options)
            if record is None:
                reasons[reason or "unknown"] += 1
            else:
                records.append(record)
            if on_progress:
                on_progress(
                    index, len(rows), str(row.get("original_filename") or "")
                )

        split_map = assign_splits(records, options.split)
        for record in records:
            record["split"] = split_map[int(record["image_id"])]
        pairs, triplets = build_pairs(
            records,
            max_positive_per_vehicle=options.max_positive_pairs_per_vehicle,
            negative_ratio=options.negative_ratio,
            seed=options.split.seed,
        )
        self._write_metadata(output, records, pairs, triplets)

        result.exported = len(records)
        result.skipped = sum(reasons.values())
        result.skip_reasons = dict(sorted(reasons.items()))
        result.pair_count = len(pairs)
        result.triplet_count = len(triplets)
        result.split_counts = dict(
            sorted(Counter(row["split"] for row in records).items())
        )
        self._write_state(output, result, config)
        if not result.cancelled:
            self.repository.record(
                str(output),
                split_config=config,
                image_count=result.exported,
                vehicle_count=len({row["vehicle_id"] for row in records}),
                created_by=created_by,
            )
        return result

    def _prepare_output(self, output: Path, config: dict) -> None:
        config_path = output / "export_config.json"
        if output.exists() and any(output.iterdir()) and not config_path.is_file():
            raise ValueError(
                "output directory is not empty and has no export_config.json"
            )
        output.mkdir(parents=True, exist_ok=True)
        if config_path.is_file():
            existing = json.loads(config_path.read_text(encoding="utf-8"))
            if existing != config:
                raise ValueError(
                    "output directory belongs to an export with different settings"
                )
        else:
            _write_json(config_path, config)
        for relative in (
            "images",
            "vehicle_crops",
            "plate_crops",
            "reid_crops",
            "metadata",
            "splits",
        ):
            (output / relative).mkdir(parents=True, exist_ok=True)

    def _load_existing_records(self, output: Path) -> dict[int, dict]:
        manifest = output / "metadata" / "manifest.jsonl"
        if not manifest.is_file():
            return {}
        records: dict[int, dict] = {}
        try:
            with manifest.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        record = json.loads(line)
                        records[int(record["image_id"])] = record
        except (OSError, ValueError, json.JSONDecodeError, KeyError):
            return {}
        return records

    def _can_reuse(
        self, output: Path, row: dict, record: dict, options: ExportOptions
    ) -> bool:
        expected = {
            "vehicle_id": row.get("vehicle_id"),
            "review_status": row.get("review_status"),
            "label_priority": row.get("label_priority"),
            "source_updated_at": row.get("updated_at"),
        }
        if any(record.get(key) != value for key, value in expected.items()):
            return False
        required = [record.get("vehicle_crop"), record.get("reid_crop")]
        if options.require_plate_bbox:
            required.append(record.get("plate_crop"))
        if options.copy_originals:
            required.append(record.get("original_image"))
        return all(
            relative and _safe_output_file(output, str(relative)).is_file()
            for relative in required
        )

    def _export_row(
        self, output: Path, row: dict, options: ExportOptions
    ) -> tuple[Optional[dict], Optional[str]]:
        if row.get("review_status") in _REJECTED_REVIEW_STATES:
            return None, f"review_{row['review_status']}"
        crop_path = Path(str(row.get("vehicle_crop_path") or ""))
        if not crop_path.is_file():
            return None, "missing_vehicle_crop"
        crop = _read_image(crop_path)
        if crop is None:
            return None, "invalid_vehicle_crop"
        crop_height, crop_width = crop.shape[:2]
        crop_bbox = infer_vehicle_crop_bbox(
            row.get("vehicle_crop_bbox"),
            row.get("vehicle_bbox"),
            crop_width=crop_width,
            crop_height=crop_height,
            source_width=row.get("width"),
            source_height=row.get("height"),
        )
        if crop_bbox is None:
            return None, "unresolved_vehicle_crop_bbox"
        plate_bbox = relative_plate_bbox(
            row.get("plate_bbox"),
            crop_bbox,
            crop_width=crop_width,
            crop_height=crop_height,
        )
        if plate_bbox is None and options.require_plate_bbox:
            return None, "missing_or_outside_plate_bbox"

        image_id = int(row["image_id"])
        stem = f"{image_id:08d}"
        source_path = Path(str(row.get("source_path") or ""))
        original_relative = ""
        if options.copy_originals:
            if not source_path.is_file():
                return None, "missing_source_image"
            suffix = source_path.suffix.lower()
            allowed = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
            if suffix not in allowed:
                suffix = ".jpg"
            original_relative = f"images/{stem}{suffix}"
            original_target = output / original_relative
            if not original_target.is_file():
                shutil.copy2(source_path, original_target)

        vehicle_relative = f"vehicle_crops/{stem}.jpg"
        plate_relative = f"plate_crops/{stem}.jpg"
        reid_relative = f"reid_crops/{stem}.jpg"
        if not _write_jpeg(output / vehicle_relative, crop, options.image_quality):
            return None, "write_vehicle_crop_failed"

        mask_bbox = None
        if plate_bbox is not None:
            x1, y1, x2, y2 = plate_bbox
            plate_crop = crop[y1:y2, x1:x2]
            if plate_crop.size == 0:
                return None, "empty_plate_crop"
            if not _write_jpeg(
                output / plate_relative, plate_crop, options.image_quality
            ):
                return None, "write_plate_crop_failed"
            masked, mask_bbox = mask_plate(
                crop,
                plate_bbox,
                method=options.mask_method,
                margin=options.mask_margin,
            )
        else:
            plate_relative = ""
            masked = crop.copy()
        if not _write_jpeg(output / reid_relative, masked, options.image_quality):
            return None, "write_reid_crop_failed"

        return {
            "image_id": image_id,
            "vehicle_id": row["vehicle_id"],
            "original_filename": row.get("original_filename") or "",
            "archive_year": row.get("archive_year"),
            "date": row.get("date"),
            "time": row.get("time"),
            "camera_id": row.get("camera_id"),
            "vehicle_type": row.get("vehicle_type"),
            "review_status": row.get("review_status"),
            "verification": row.get("verification"),
            "label_priority": row.get("label_priority"),
            "label_source": row.get("label_source"),
            "plate_normalized": (
                row.get("plate_text_normalized")
                or row.get("group_plate_normalized")
            ),
            "plate_effective": row.get("plate_text_raw"),
            "plate_source": row.get("plate_source") or "unknown",
            "plate_validation_status": row.get("plate_validation_status") or "unknown",
            "direction": row.get("direction"),
            "vehicle_type_code": row.get("vehicle_type_code"),
            "image_sequence": row.get("image_sequence"),
            "ini_present": int(bool(row.get("ini_present"))),
            "ini_parse_status": row.get("ini_parse_status") or "missing",
            "plate_confidence": row.get("plate_confidence"),
            "sha256": row.get("sha256"),
            "original_image": original_relative,
            "vehicle_crop": vehicle_relative,
            "plate_crop": plate_relative,
            "reid_crop": reid_relative,
            "plate_mask_bbox": _bbox_dict(mask_bbox),
            "source_updated_at": row.get("updated_at"),
        }, None

    def _write_metadata(
        self,
        output: Path,
        records: list[dict],
        pairs: list[dict],
        triplets: list[dict],
    ) -> None:
        metadata = output / "metadata"
        _write_csv(metadata / "images.csv", records, _IMAGE_FIELDS)
        label_rows = [
            {
                "image_id": row["image_id"],
                "vehicle_id": row["vehicle_id"],
                "split": row["split"],
                "review_status": row["review_status"],
                "verification": row["verification"],
                "label_priority": row["label_priority"],
                "label_source": row["label_source"],
            }
            for row in records
        ]
        _write_csv(metadata / "labels.csv", label_rows)
        vehicle_rows = []
        for vehicle_id in sorted({row["vehicle_id"] for row in records}):
            members = [row for row in records if row["vehicle_id"] == vehicle_id]
            vehicle_rows.append(
                {
                    "vehicle_id": vehicle_id,
                    "image_count": len(members),
                    "split": members[0]["split"],
                    "verification": members[0]["verification"],
                    "label_priority": members[0]["label_priority"],
                }
            )
        _write_csv(metadata / "vehicles.csv", vehicle_rows)
        manifest = metadata / "manifest.jsonl"
        with manifest.open("w", encoding="utf-8", newline="\n") as handle:
            for row in records:
                handle.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                )
        split_fields = ("image_id", "vehicle_id", "reid_crop", "label_priority")
        for split in ("train", "val", "test"):
            split_rows = [
                {key: row[key] for key in split_fields}
                for row in records
                if row["split"] == split
            ]
            _write_csv(
                output / "splits" / f"{split}.csv", split_rows, split_fields
            )
        _write_csv(
            metadata / "pairs.csv",
            pairs,
            ("image_a", "image_b", "label", "pair_type", "split"),
        )
        _write_csv(
            metadata / "triplets.csv",
            triplets,
            ("anchor", "positive", "negative", "split", "negative_type"),
        )

    def _write_state(self, output: Path, result: ExportResult, config: dict) -> None:
        payload = asdict(result)
        payload["complete"] = not result.cancelled
        payload["config_sha256"] = _payload_digest(config)
        _write_json(output / "export_state.json", payload)


def _config_payload(options: ExportOptions) -> dict:
    payload = asdict(options)
    return json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _payload_digest(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_image(path: Path):
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def _write_jpeg(path: Path, image: np.ndarray, quality: int) -> bool:
    ok, encoded = cv2.imencode(
        ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    )
    if not ok:
        return False
    try:
        encoded.tofile(str(path))
    except OSError:
        return False
    return True


def _bbox_dict(bbox):
    if bbox is None:
        return None
    return dict(zip(("x1", "y1", "x2", "y2"), bbox))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict], fields=None) -> None:
    fieldnames = list(fields or (rows[0].keys() if rows else []))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        if not fieldnames:
            return
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(
            {
                key: (
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list, tuple))
                    else value
                )
                for key, value in row.items()
            }
            for row in rows
        )


def _safe_output_file(output: Path, relative: str) -> Path:
    candidate = (output / relative).resolve()
    try:
        candidate.relative_to(output)
    except ValueError:
        return output / "__unsafe_path__"
    return candidate
