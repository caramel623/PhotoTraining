"""Build a resumable embedding index from a Phase 5 portable dataset."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from vehicle_dataset_manager.reid.index import EmbeddingIndex

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


@dataclass
class IndexBuildResult:
    total: int = 0
    indexed: int = 0
    reused: int = 0
    failed: int = 0
    cancelled: bool = False
    index_path: str = "features/reid_embeddings.npz"


class DatasetIndexBuilder:
    """Index plate-masked `reid_crop` images from a P5 export."""

    def __init__(self, engine, *, checkpoint_every: int = 100) -> None:
        if checkpoint_every < 1:
            raise ValueError("checkpoint_every must be positive")
        self.engine = engine
        self.checkpoint_every = checkpoint_every

    def build(
        self,
        dataset_dir: str | Path,
        *,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> IndexBuildResult:
        root = Path(dataset_dir).expanduser().resolve()
        rows = _load_manifest(root / "metadata" / "manifest.jsonl")
        features_dir = root / "features"
        features_dir.mkdir(parents=True, exist_ok=True)
        index_path = features_dir / "reid_embeddings.npz"
        state_path = features_dir / "index_state.json"
        model_id = str(self.engine.model_id)
        index = self._load_existing(index_path, model_id)
        if index is not None:
            self._validate_resume(index, rows)
        completed = set(index.item_ids) if index is not None else set()
        result = IndexBuildResult(total=len(rows), reused=len(completed))

        seen: set[str] = set()
        since_checkpoint = 0
        for position, row in enumerate(rows, start=1):
            item_id = str(row.get("image_id", ""))
            if not item_id or item_id in seen:
                raise ValueError(f"manifest has invalid or duplicate image_id: {item_id!r}")
            seen.add(item_id)
            if item_id in completed:
                if on_progress:
                    on_progress(position, len(rows), item_id)
                continue
            if should_cancel and should_cancel():
                result.cancelled = True
                break
            image_path = _safe_dataset_file(root, str(row.get("reid_crop") or ""))
            image = _read_image(image_path)
            if image is None:
                result.failed += 1
            else:
                try:
                    embedding = self.engine.extract_embedding(image)
                    if index is None:
                        index = EmbeddingIndex(
                            int(np.asarray(embedding).size), model_id=model_id
                        )
                    index.add(item_id, embedding, _feature_metadata(row))
                    result.indexed += 1
                    since_checkpoint += 1
                except (OSError, RuntimeError, ValueError):
                    result.failed += 1
            if index is not None and since_checkpoint >= self.checkpoint_every:
                index.save(index_path)
                since_checkpoint = 0
            if on_progress:
                on_progress(position, len(rows), item_id)

        if index is not None:
            index.save(index_path)
        result.reused = len(completed)
        _write_state(state_path, result, model_id)
        return result

    @staticmethod
    def _load_existing(index_path: Path, model_id: str) -> EmbeddingIndex | None:
        if not index_path.is_file():
            return None
        index = EmbeddingIndex.load(index_path)
        if index.model_id != model_id:
            raise ValueError(
                "existing embedding index was created by a different model"
            )
        return index

    @staticmethod
    def _validate_resume(index: EmbeddingIndex, rows: list[dict]) -> None:
        current = {str(row.get("image_id", "")): row for row in rows}
        if not set(index.item_ids).issubset(current):
            raise ValueError(
                "dataset manifest changed; existing index contains removed items"
            )
        for item_id in index.item_ids:
            expected = _feature_metadata(current[item_id])
            if index.metadata_for(item_id) != expected:
                raise ValueError(
                    "dataset manifest changed; create a new embedding index"
                )


def _load_manifest(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"dataset manifest not found: {path}")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid manifest line {line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"invalid manifest line {line_number}")
            rows.append(row)
    return rows


def _safe_dataset_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if not relative or path.is_absolute():
        raise ValueError("manifest reid_crop must be a relative path")
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("manifest reid_crop escapes dataset directory") from exc
    return candidate


def _read_image(path: Path):
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def _feature_metadata(row: dict) -> dict:
    """Keep only non-plate fields needed by retrieval and audit."""
    keys = (
        "vehicle_id",
        "camera_id",
        "archive_year",
        "date",
        "time",
        "split",
        "reid_crop",
        "label_priority",
        "source_updated_at",
    )
    return {key: row.get(key) for key in keys}


def _write_state(path: Path, result: IndexBuildResult, model_id: str) -> None:
    payload = asdict(result)
    payload["complete"] = not result.cancelled
    payload["model_id"] = model_id
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
