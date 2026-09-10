"""Pluggable vehicle/plate detection engines.

Importing this package does NOT import torch/ultralytics (they are loaded
lazily inside the detector / cuda_env), so the app starts even when the ML
stack is absent.
"""
from vehicle_dataset_manager.detection import cuda_env
from vehicle_dataset_manager.detection.base import (
    BaseDetector,
    Detection,
    StubVehicleDetector,
    StubPlateDetector,
)
from vehicle_dataset_manager.detection.cuda_env import (
    DEFAULT_CUDA_WHEEL_INDEX,
    CudaEnvironmentReport,
    CudaStatus,
    InstallResult,
    NvidiaInfo,
    build_pip_argv,
    build_pip_command,
    detect_environment,
    install_cuda_torch,
    probe_nvidia_smi,
    run_command,
)
from vehicle_dataset_manager.detection.device import (
    cuda_available,
    gpu_name,
    gpu_report,
    resolve_device,
)
from vehicle_dataset_manager.detection.yolo_detector import (
    VEHICLE_CLASS_IDS,
    YoloVehicleDetector,
    build_vehicle_detector,
)

__all__ = [
    "BaseDetector",
    "Detection",
    "StubVehicleDetector",
    "StubPlateDetector",
    "cuda_available",
    "gpu_name",
    "gpu_report",
    "resolve_device",
    "VEHICLE_CLASS_IDS",
    "YoloVehicleDetector",
    "build_vehicle_detector",
    # CUDA environment detection / install
    "cuda_env",
    "DEFAULT_CUDA_WHEEL_INDEX",
    "CudaEnvironmentReport",
    "CudaStatus",
    "InstallResult",
    "NvidiaInfo",
    "build_pip_argv",
    "build_pip_command",
    "detect_environment",
    "install_cuda_torch",
    "probe_nvidia_smi",
    "run_command",
]