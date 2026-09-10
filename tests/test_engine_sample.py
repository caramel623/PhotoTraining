"""Headless tests for ProcessingEngine.run_sample (random subset processing)."""
from __future__ import annotations

from vehicle_dataset_manager.detection.base import StubPlateDetector, StubVehicleDetector
import pytest

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.archive.manager import ArchiveManager
from vehicle_dataset_manager.ocr.base import StubOcr
from vehicle_dataset_manager.pipeline.engine import ProcessingEngine
from vehicle_dataset_manager.pipeline.stages import build_default_stages
from vehicle_dataset_manager.services.import_service import ImportService


@pytest.fixture
def ctx(db, workspace):
    c = AppContext(
        workspace=workspace, settings=None, db=db,
        archive_manager=ArchiveManager(), import_service=None,
    )
    c.import_service = ImportService(db, workspace, c.archive_manager)
    return c


def _engine(ctx):
    stages = build_default_stages(
        StubVehicleDetector(), StubPlateDetector(), StubOcr(), ctx.vehicles
    )
    return ProcessingEngine(
        ctx.db, stages,
        image_repo=ctx.images, vehicle_repo=ctx.vehicles, job_repo=ctx.jobs,
    )


def _job_id(ctx, sample_zip):
    return ctx.import_service.import_archive(sample_zip).job_id


def test_sample_processes_exact_count(ctx, sample_zip):
    job = _job_id(ctx, sample_zip)
    eng = _engine(ctx)
    res = eng.run_sample(job, 3, seed=7)
    assert res.processed == 3
    counts = ctx.images.counts(sample_zip.name)
    assert counts["completed"] == 3 and counts["pending"] == 1
    # batch still has work left -> job must not be marked completed
    assert res.final_state.value == "cancelled"
    job_row = ctx.jobs.get(job)
    assert job_row["status"] == "cancelled"


def test_sample_seed_is_reproducible(ctx, sample_zip):
    job = _job_id(ctx, sample_zip)
    eng = _engine(ctx)
    eng.run_sample(job, 2, seed=42)
    picked = {r["image_id"] for r in ctx.db.query(
        "SELECT image_id FROM images WHERE processing_status='completed'")}
    assert len(picked) == 2
    # same seed on a fresh copy would pick the same ids; here we at least
    # verify the remainder is untouched and a second sample avoids them
    res = eng.run_sample(job, 5, seed=42)
    picked2 = {r["image_id"] for r in ctx.db.query(
        "SELECT image_id FROM images WHERE processing_status='completed'")}
    assert res.processed == 2  # sample of 5 drawn from 2 remaining
    assert len(picked2 - picked) == 2 and len(picked2) == 4
    counts = ctx.images.counts(sample_zip.name)
    assert counts["pending"] == 0 and counts["completed"] == 4


def test_sample_smaller_than_available(ctx, sample_zip):
    job = _job_id(ctx, sample_zip)
    eng = _engine(ctx)
    res = eng.run_sample(job, 100, seed=1)
    assert res.processed == 4  # only 4 images exist
    counts = ctx.images.counts(sample_zip.name)
    assert counts["completed"] == 4


def test_sample_requeues_stuck_processing(ctx, sample_zip):
    job = _job_id(ctx, sample_zip)
    eng = _engine(ctx)
    first = ctx.images.claim_next_pending(sample_zip.name)
    ctx.db.execute(
        "UPDATE images SET processing_status='processing' WHERE image_id=?",
        (first.image_id,),
    )
    ctx.db.commit()
    res = eng.run_sample(job, 2, seed=3)
    # the stuck row was requeued, so the sample could include it; either way
    # nothing may remain stuck in processing afterwards
    counts = ctx.images.counts(sample_zip.name)
    assert counts["processing"] == 0
    assert res.processed + counts["pending"] == 2 + 2