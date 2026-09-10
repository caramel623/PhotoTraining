"""Build/resume a portable Re-ID index from a Phase 5 dataset export."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vehicle_dataset_manager.reid import (  # noqa: E402
    DatasetIndexBuilder,
    OnnxReIDEngine,
    PreprocessConfig,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a local cosine index from Dataset/reid_crops."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("model", type=Path)
    parser.add_argument("--cuda", action="store_true")
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--checkpoint-every", type=int, default=100)
    args = parser.parse_args()

    engine = OnnxReIDEngine(
        args.model,
        use_cuda=args.cuda,
        preprocess=PreprocessConfig(width=args.width, height=args.height),
    )
    engine.warmup()
    print(f"model={engine.model_id}")
    print(f"providers={','.join(engine.providers)}")

    last_percent = -1

    def progress(done: int, total: int, item_id: str) -> None:
        nonlocal last_percent
        percent = int(done * 100 / total) if total else 100
        if percent != last_percent and (percent % 5 == 0 or done == total):
            print(f"{done}/{total} ({percent}%) image_id={item_id}", flush=True)
            last_percent = percent

    result = DatasetIndexBuilder(
        engine, checkpoint_every=args.checkpoint_every
    ).build(args.dataset, on_progress=progress)
    print(
        f"indexed={result.indexed} reused={result.reused} "
        f"failed={result.failed} index={result.index_path}"
    )
    return 0 if result.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
