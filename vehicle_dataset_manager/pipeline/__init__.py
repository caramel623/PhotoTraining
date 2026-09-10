"""Resumable processing pipeline (engine + pluggable stages)."""
from vehicle_dataset_manager.pipeline.engine import ProcessingEngine, EngineResult
from vehicle_dataset_manager.pipeline.stages import (
    PipelineContext,
    Stage,
    StageError,
    build_default_stages,
)

__all__ = [
    "ProcessingEngine", "EngineResult",
    "PipelineContext", "Stage", "StageError", "build_default_stages",
]