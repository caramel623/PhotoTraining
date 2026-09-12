import sqlite3

from vehicle_dataset_manager.database.repositories import (
    ImageRepository,
    JobRepository,
    ReviewRepository,
    VehicleRepository,
)
from vehicle_dataset_manager.core.enums import GroupVerification, JobState, ReviewStatus
from vehicle_dataset_manager.database import schema


def test_schema_migrated(db):
    assert db.migrate() == 5
    columns = {row["name"] for row in db.query("PRAGMA table_info(images)")}
    assert "vehicle_crop_bbox" in columns
    assert {"ini_parser_version", "ini_plate_text", "manual_plate_text", "plate_source"} <= columns
    tables = {row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"archives", "archive_members", "image_sources", "import_jobs"} <= tables


def test_schema_v4_database_upgrades_without_deleting_rows(tmp_path):
    connection = sqlite3.connect(tmp_path / "v4.sqlite3")
    try:
        for migration in schema._MIGRATIONS[:4]:
            for statement in migration:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES (4, 'old')")
        connection.execute(
            "INSERT INTO images (original_filename, source_path, processing_status, review_status, quality_flags, created_at, updated_at) VALUES ('old.jpg','C:/old.jpg','completed','unreviewed','[]','old','old')"
        )
        connection.commit()
        assert schema.migrate(connection) == 5
        assert connection.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
        assert connection.execute("SELECT plate_source FROM images").fetchone()[0] == "unknown"
    finally:
        connection.close()


def test_image_dedup(db):
    repo = ImageRepository(db)
    a = repo.upsert_from_scan(original_filename="a.jpg", source_path="/x/a.jpg", original_archive="2024.zip")
    b = repo.upsert_from_scan(original_filename="a.jpg", source_path="/x/a.jpg", original_archive="2024.zip")
    assert a == b


def test_image_lifecycle(db):
    repo = ImageRepository(db)
    i1 = repo.upsert_from_scan(original_filename="a.jpg", source_path="/x/a.jpg")
    i2 = repo.upsert_from_scan(original_filename="b.jpg", source_path="/x/b.jpg")
    c = repo.claim_next_pending()
    assert c is not None
    repo.mark_completed(c.image_id, plate_text_normalized="BFY1765")
    counts = repo.counts()
    assert counts["completed"] == 1
    assert counts["pending"] == 1


def test_review_updates_image_status(db):
    img = ImageRepository(db)
    veh = VehicleRepository(db)
    i = img.upsert_from_scan(original_filename="a.jpg", source_path="/x/a.jpg")
    v = veh.get_or_create_for_plate("ABC123")
    ReviewRepository(db).upsert(i, ReviewStatus.VERIFIED_SAME, vehicle_id=v)
    assert img.get(i).review_status == str(ReviewStatus.VERIFIED_SAME)


def test_job_counters(db):
    jobs = JobRepository(db)
    jid = jobs.create("process", "2024.zip", total=5)
    jobs.set_state(jid, JobState.RUNNING)
    jobs.update_counters(jid, processed=2, skipped=0, failed=1, pending=2)
    job = jobs.get(jid)
    assert job["status"] == str(JobState.RUNNING)
    assert job["processed"] == 2 and job["failed"] == 1
