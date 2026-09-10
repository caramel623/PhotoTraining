"""Convert the portable NumPy index to a FAISS inner-product index on AI02."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("portable_index", type=Path)
    parser.add_argument("faiss_index", type=Path)
    parser.add_argument("--gpu", action="store_true")
    args = parser.parse_args()

    import faiss

    with np.load(args.portable_index, allow_pickle=False) as archive:
        vectors = np.asarray(archive["vectors"], dtype=np.float32)
        ids = [str(value) for value in archive["ids"].tolist()]
        manifest = json.loads(str(archive["manifest"].item()))
    if vectors.ndim != 2 or vectors.shape[0] != len(ids):
        raise ValueError("corrupt portable index")
    faiss.normalize_L2(vectors)
    cpu_index = faiss.IndexFlatIP(vectors.shape[1])
    target = cpu_index
    if args.gpu:
        resources = faiss.StandardGpuResources()
        target = faiss.index_cpu_to_gpu(resources, 0, cpu_index)
    target.add(vectors)
    if args.gpu:
        target = faiss.index_gpu_to_cpu(target)

    args.faiss_index.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(target, str(args.faiss_index))
    sidecar = args.faiss_index.with_suffix(args.faiss_index.suffix + ".json")
    sidecar.write_text(
        json.dumps(
            {
                "format_version": 1,
                "model_id": manifest.get("model_id", ""),
                "dimension": vectors.shape[1],
                "ids": ids,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"vectors={len(ids)} dimension={vectors.shape[1]}")
    print(f"index={args.faiss_index}")
    print(f"ids={sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
