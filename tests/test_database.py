from vehicle_dataset_manager.database.repositories import (
    ImageRepository,
    JobRepository,
    ReviewRepository,
    VehicleRepository,
)
from vehicle_dataset_manager.core.enums import GroupVerification, JobState, ReviewStatus


def test_schema_migrated(db):
    assert db.migrate() == 4
    columns = {row["name"] for row in db.query("PRAGMA table_info(images)")}
    assert "vehicle_crop_bbox" in columns


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
