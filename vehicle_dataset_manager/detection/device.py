"""Device (CPU / CUDA) detection and reporting.

Kept free of any hard torch / ultralytics import so the application still
starts when the ML stack is not installed. CUDA is only used when it is both
*requested* (settings) and *available* (NVIDIA driver + a CUDA build of
PyTorch). On machines without CUDA (e.g. AMD GPUs) everything runs on CPU.
"""
from __future__ import annotations

from typing import Optional


def _torch():
    """Import torch lazily; return None when the ML stack is absent."""
    try:
        import torch  # noqa: WPS433 (intentional lazy import)

        return torch
    except Exception:  # noqa: BLE001
        return None


def cuda_available() -> bool:
    """Return True only when a CUDA device is actually usable right now."""
    torch = _torch()
    if torch is None:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def gpu_name() -> str:
    """Best-effort GPU name. Returns a CUDA device name when available."""
    torch = _torch()
    if torch is not None and cuda_available():
        try:
            return torch.cuda.get_device_name(0)
        except Exception:  # noqa: BLE001
            return "NVIDIA GPU (name unavailable)"
    return "(no CUDA-capable GPU)"


def torch_info() -> Optional[str]:
    torch = _torch()
    if torch is None:
        return None
    return f"torch {torch.__version__}"


def resolve_device(use_cuda: bool) -> str:
    """Return the device string to run inference on (``cpu`` or ``cuda``)."""
    if use_cuda and cuda_available():
        return "cuda"
    return "cpu"


def gpu_report(use_cuda: bool = False) -> str:
    """Human-readable device report (README §28, shown at startup)."""
    torch = _torch()
    if torch is None:
        return (
            "GPU: n/a (PyTorch not installed)\n"
            "CUDA available: No\n"
            "Device: cpu (CPU / stub mode)"
        )
    avail = cuda_available()
    name = gpu_name() if avail else "(no CUDA-capable GPU)"
    device = resolve_device(use_cuda)
    lines = [
        f"GPU: {name}",
        f"CUDA available: {'Yes' if avail else 'No'}",
        f"Device: {device}",
    ]
    if torch_info():
        lines.append(torch_info())
    return "\n".join(lines)