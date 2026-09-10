"""Pluggable Re-ID embedding engines (used in Phase 6)."""
from vehicle_dataset_manager.reid.base import BaseReID, StubReID
from vehicle_dataset_manager.reid.embedding_engine import (
    OnnxReIDEngine,
    PreprocessConfig,
    l2_normalize,
)
from vehicle_dataset_manager.reid.index import EmbeddingIndex, SearchResult
from vehicle_dataset_manager.reid.dataset_indexer import (
    DatasetIndexBuilder,
    IndexBuildResult,
)

__all__ = [
    "BaseReID",
    "StubReID",
    "OnnxReIDEngine",
    "PreprocessConfig",
    "l2_normalize",
    "EmbeddingIndex",
    "SearchResult",
    "DatasetIndexBuilder",
    "IndexBuildResult",
]
