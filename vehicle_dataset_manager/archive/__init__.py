"""Archive handling: ZIP / 7z detection, listing, extraction, verification.

Backends:
* ZIP  -> stdlib ``zipfile``
* 7z   -> 7-Zip CLI if found on the machine, otherwise the pure-Python ``py7zr``

Original archives are only ever *read*; extraction writes to a destination dir.
"""
from vehicle_dataset_manager.archive.manager import (
    ArchiveEntry,
    ArchiveManager,
    ExtractionResult,
    VerifyResult,
    detect_archive_type,
    find_sevenz_cli,
)

__all__ = [
    "ArchiveEntry",
    "ArchiveManager",
    "ExtractionResult",
    "VerifyResult",
    "detect_archive_type",
    "find_sevenz_cli",
]