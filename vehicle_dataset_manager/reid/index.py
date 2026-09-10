"""Portable cosine-similarity index with no FAISS runtime requirement."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from vehicle_dataset_manager.reid.embedding_engine import l2_normalize


@dataclass(frozen=True)
class SearchResult:
    item_id: str
    score: float
    metadata: dict


class EmbeddingIndex:
    """Small/medium portable index; AI02 may replace this with FAISS later."""

    FORMAT_VERSION = 1

    def __init__(self, dimension: int, *, model_id: str = "") -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        self.dimension = int(dimension)
        self.model_id = str(model_id)
        self._ids: list[str] = []
        self._vectors = np.empty((0, self.dimension), dtype=np.float32)
        self._metadata: list[dict] = []

    def __len__(self) -> int:
        return len(self._ids)

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(self._ids)

    def metadata_for(self, item_id: str) -> dict:
        try:
            position = self._ids.index(str(item_id))
        except ValueError as exc:
            raise KeyError(item_id) from exc
        return dict(self._metadata[position])

    def add(self, item_id: str, embedding: np.ndarray, metadata: dict | None = None) -> None:
        key = str(item_id)
        if not key:
            raise ValueError("item_id cannot be empty")
        if key in self._ids:
            raise ValueError(f"duplicate item_id: {key}")
        vector = l2_normalize(embedding)
        if vector.size != self.dimension:
            raise ValueError(
                f"embedding dimension {vector.size} does not match {self.dimension}"
            )
        self._ids.append(key)
        self._vectors = np.vstack((self._vectors, vector[None, :]))
        self._metadata.append(dict(metadata or {}))

    def search(
        self,
        embedding: np.ndarray,
        *,
        limit: int = 10,
        min_score: float = -1.0,
        exclude_ids: Iterable[str] = (),
    ) -> list[SearchResult]:
        if limit < 1 or len(self) == 0:
            return []
        query = l2_normalize(embedding)
        if query.size != self.dimension:
            raise ValueError(
                f"query dimension {query.size} does not match {self.dimension}"
            )
        excluded = {str(value) for value in exclude_ids}
        scores = self._vectors @ query
        order = np.argsort(-scores, kind="stable")
        results: list[SearchResult] = []
        for index in order:
            score = float(scores[index])
            if score < min_score:
                continue
            if self._ids[index] in excluded:
                continue
            results.append(
                SearchResult(
                    item_id=self._ids[index],
                    score=score,
                    metadata=dict(self._metadata[index]),
                )
            )
            if len(results) >= limit:
                break
        return results

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": self.FORMAT_VERSION,
            "dimension": self.dimension,
            "model_id": self.model_id,
            "metadata": self._metadata,
        }
        temporary = target.with_name(target.name + ".tmp")
        with temporary.open("wb") as handle:
            np.savez_compressed(
                handle,
                ids=np.asarray(self._ids, dtype=np.str_),
                vectors=self._vectors,
                manifest=np.asarray(json.dumps(payload, ensure_ascii=False)),
            )
        temporary.replace(target)

    @classmethod
    def load(cls, path: str | Path) -> "EmbeddingIndex":
        with np.load(Path(path), allow_pickle=False) as archive:
            payload = json.loads(str(archive["manifest"].item()))
            if payload.get("format_version") != cls.FORMAT_VERSION:
                raise ValueError("unsupported embedding index format")
            vectors = np.asarray(archive["vectors"], dtype=np.float32)
            ids = [str(value) for value in archive["ids"].tolist()]
        dimension = int(payload["dimension"])
        metadata = payload.get("metadata", [])
        if vectors.shape != (len(ids), dimension) or len(metadata) != len(ids):
            raise ValueError("corrupt embedding index")
        index = cls(dimension, model_id=str(payload.get("model_id", "")))
        index._ids = ids
        index._vectors = vectors
        index._metadata = [dict(value) for value in metadata]
        return index
