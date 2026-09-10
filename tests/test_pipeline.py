"""End-to-end import + processing + resume + cancel (headless, stub models)."""
from __future__ import annotations

import pytest

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.archive.manager import ArchiveManager
from vehicle_dataset_manager.detection.base import StubPlateDetector, StubVehicleDetector
from vehicle_dataset_manager.ocr.base import StubOcr
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
    from vehicle_dataset_manager.pipeline.engine import ProcessingEngine
    from vehicle_dataset_manager.pipeline.stages import build_default_stages
    stages = build_default_stages(StubVehicleDetector(), StubPlateDetector(), StubOcr(), ctx.vehicles)
    return ProcessingEngine(ctx.db, stages, image_repo=ctx.images, vehicle_repo=ctx.vehicles, job_repo=ctx.jobs)


def test_import_registers_pending(ctx, sample_zip):
    res = ctx.import_service.import_archive(sample_zip)
    assert res.images_found == 4
    assert res.images_new == 4
    assert res.archive_year == 2024
    counts = ctx.images.counts(sample_zip.name)
    assert counts["pending"] == 4


def test_import_dedup_on_rescan(ctx, sample_zip):
    ctx.import_service.import_archive(sample_zip)
    res2 = ctx.import_service.import_archive(sample_zip)
    assert res2.images_new == 0
    assert res2.images_found == 4


def test_process_all_and_resume(ctx, sample_zip):
    job = ctx.import_service.import_archive(sample_zip).job_id
    eng = _engine(ctx)
    r1 = eng.run(job)
    assert r1.processed == 4
    assert r1.final_state.value == "completed"
    # completed images persist their derived fields
    row = ctx.db.query_one("SELECT width, height, sha256, camera_id FROM images LIMIT 1")
    assert row["width"] == 300 and row["height"] == 200
    assert row["sha256"] and row["camera_id"] == "A"
    # re-run: nothing re-processed
    r2 = eng.run(job)
    assert r2.processed == 0


def test_cancel_then_resume(ctx, sample_zip):
    job = ctx.import_service.import_archive(sample_zip).job_id
    eng = _engine(ctx)
    seen = {"n": 0}

    def on_progress(done, total, cur):
        seen["n"] += 1

    r1 = eng.run(job, on_progress=on_progress, should_cancel=lambda: seen["n"] >= 2)
    assert r1.final_state.value == "cancelled"
    assert r1.processed == 2
    counts = ctx.images.counts(sample_zip.name)
    assert counts["completed"] == 2 and counts["pending"] == 2
    r2 = eng.run(job)
    assert r2.processed == 2
    counts = ctx.images.counts(sample_zip.name)
    assert counts["completed"] == 4 and counts["pending"] == 0