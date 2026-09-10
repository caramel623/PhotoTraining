"""Pluggable Re-ID embedding engines (used in Phase 6)."""
from vehicle_dataset_manager.reid.base import BaseReID, StubReID

__all__ = ["BaseReID", "StubReID"]