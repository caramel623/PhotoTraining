"""Vehicle Dataset Manager.

A Windows-local tool that builds a manually-verifiable Vehicle/Motorcycle
Re-ID training dataset from historical speed-camera photos (ZIP/7Z).

Core principle: plate OCR is used as a *weak supervision / grouping* signal,
never as a Re-ID feature. The Re-ID model must learn vehicle appearance.
"""

from vehicle_dataset_manager.core.portable_runtime import (
    activate_portable_cuda_runtime as _activate_portable_cuda_runtime,
)

_activate_portable_cuda_runtime()
del _activate_portable_cuda_runtime

__version__ = "0.1.0"
