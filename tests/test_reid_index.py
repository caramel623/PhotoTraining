from __future__ import annotations

import numpy as np
import pytest

from vehicle_dataset_manager.reid import EmbeddingIndex


def test_index_search_filter_and_exclusion():
    index = EmbeddingIndex(2, model_id="unit-test")
    index.add("a", np.array([1.0, 0.0]), {"vehicle_id": "V1"})
    index.add("b", np.array([0.8, 0.2]), {"vehicle_id": "V2"})
    index.add("c", np.array([-1.0, 0.0]), {"vehicle_id": "V3"})
    results = index.search(
        np.array([1.0, 0.0]), limit=3, min_score=0.0, exclude_ids={"a"}
    )
    assert [result.item_id for result in results] == ["b"]
    assert results[0].metadata == {"vehicle_id": "V2"}


def test_index_round_trip_is_portable_and_pickle_free(tmp_path):
    path = tmp_path / "index.npz"
    original = EmbeddingIndex(3, model_id="reid.onnx:abc")
    original.add("影像-1", np.array([1.0, 2.0, 3.0]), {"camera": "RS015"})
    original.save(path)
    restored = EmbeddingIndex.load(path)
    assert len(restored) == 1
    assert restored.model_id == "reid.onnx:abc"
    result = restored.search(np.array([1.0, 2.0, 3.0]))[0]
    assert result.item_id == "影像-1"
    assert result.score == pytest.approx(1.0)


def test_index_rejects_duplicates_and_wrong_dimension():
    index = EmbeddingIndex(2)
    index.add("a", np.array([1.0, 0.0]))
    with pytest.raises(ValueError, match="duplicate"):
        index.add("a", np.array([0.0, 1.0]))
    with pytest.raises(ValueError, match="dimension"):
        index.add("b", np.array([1.0, 2.0, 3.0]))
    with pytest.raises(ValueError, match="query dimension"):
        index.search(np.array([1.0, 2.0, 3.0]))
