from __future__ import annotations

import sys

from vehicle_dataset_manager.core.portable_runtime import activate_portable_cuda_runtime


def test_activate_portable_cuda_runtime_prepends_valid_target(tmp_path):
    runtime_root = tmp_path / "cuda-runtime"
    target = runtime_root / "versions" / "cuda-test"
    torch_dir = target / "torch"
    torch_dir.mkdir(parents=True)
    (torch_dir / "__init__.py").write_text("", encoding="utf-8")
    (runtime_root / "active.txt").write_text("cuda-test", encoding="utf-8")
    target_text = str(target.resolve())
    try:
        selected = activate_portable_cuda_runtime(runtime_root)
        assert selected == target.resolve()
        assert sys.path[0] == target_text
    finally:
        while target_text in sys.path:
            sys.path.remove(target_text)


def test_activate_portable_cuda_runtime_rejects_unsafe_marker(tmp_path):
    runtime_root = tmp_path / "cuda-runtime"
    runtime_root.mkdir()
    (runtime_root / "active.txt").write_text("../outside", encoding="utf-8")
    assert activate_portable_cuda_runtime(runtime_root) is None
