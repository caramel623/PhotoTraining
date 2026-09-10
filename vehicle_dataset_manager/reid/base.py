"""Re-ID embedding abstraction.

Re-ID features must come from *vehicle appearance*, never from the plate.
The concrete engine (e.g. a trained network) is injected later.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class BaseReID(ABC):
    name: str = "base"

    @abstractmethod
    def extract_embedding(self, image: np.ndarray) -> Optional[np.ndarray]:
        """Return a feature vector for a (plate-masked) vehicle crop, or None."""
        raise NotImplementedError

    def warmup(self) -> None:
        return None


class StubReID(BaseReID):
    name = "stub-reid"

    def extract_embedding(self, image: np.ndarray) -> Optional[np.ndarray]:
        return None