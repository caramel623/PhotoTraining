from pathlib import Path

from vehicle_dataset_manager.core.enums import GroupSource, GroupVerification, ReviewStatus
from vehicle_dataset_manager.database.repositories import (
    ImageRepository,
    ReviewRepository,
    VehicleRepository,
)
from vehicle_dataset_manager.review_model import GroupImage, ReviewModel, _resolve_display_path


def _model(db):
    return ReviewModel(VehicleRepository(db), ImageRepository(db), ReviewRepository(db))


def _seed_group(db, n=3, year="2025", plate="BFY1765", prefix="cam"):
    img = ImageRepository(db)
    veh = VehicleRepository(db)
    vid = veh.get_or_create_for_plate(plate)
    ids = []
    for i in range(n):
        iid = img.upsert_from_scan(
            original_filename=f"{prefix}{i}.jpg",
            source_path=f"/x/{prefix}{i}.jpg",
            camera_id="RS015",
            archive_year=int(year),
        )
        img.set_result_fields(iid, date=f"{year}-0{i+1}-01", plate_text_normalized=plate)
        veh.add_member(vid, iid)
        ids.append(iid)
    return vid, ids


def test_members_for_review_columns(db):
    vid, ids = _seed_group(db)
    rows = _model(db).vehicles.members_for_review(vid)
    assert len(rows) == 3
    row = rows[0]
    for col in ("image_id", "source_path", "vehicle_crop_path", "review_status", "date"):
        assert col in row
    assert row["review_status"] == "unreviewed"
    assert row["date"] == "2025-01-01"


def test_list_groups_summary(db):
    vid, ids = _seed_group(db)
    groups = _model(db).list_groups()
    g = next(x for x in groups if x.vehicle_id == vid)
    assert g.plate_normalized == "BFY1765"
    assert g.image_count == 3
    assert g.cameras == ["RS015"]
    assert g.years == [2025]


def test_list_groups_filter_verification(db):
    vid, ids = _seed_group(db)
    m = _model(db)
    m.vehicles.set_verification(vid, GroupVerification.VERIFIED)
    assert [x.vehicle_id for x in m.list_groups(verification="verified")] == [vid]
    assert m.list_groups(verification="automatic_only") == []


def test_group_images_resolves_display_path(db, tmp_path):
    vid, ids = _seed_group(db)
    # no files -> display_path None
    assert all(g.display_path is None for g in _model(db).group_images(vid))
    # create a crop file for one image
    crop = tmp_path / "crop0.jpg"
    crop.write_bytes(b"x")
    src = tmp_path / "cam0.jpg"
    src.write_bytes(b"y")
    ImageRepository(db).set_result_fields(ids[0], vehicle_crop_path=str(crop), )
    # fix source path too (set_result_fields doesn't allow source_path)
    db.execute("UPDATE images SET source_path=? WHERE image_id=?", (str(src), ids[0]))
    gs = _model(db).group_images(vid)
    got = next(g for g in gs if g.image_id == ids[0])
    assert got.display_path == str(crop)  # crop preferred


def test_set_image_status_persists(db):
    vid, ids = _seed_group(db)
    m = _model(db)
    m.set_image_status(ids[0], ReviewStatus.VERIFIED_SAME, vehicle_id=vid)
    rec = ImageRepository(db).get(ids[0])
    assert rec.review_status == "verified_same_vehicle"


def test_edit_image_plate_relinks(db):
    vid, ids = _seed_group(db)
    m = _model(db)
    other = m.vehicles.get_or_create_for_plate("XYZ999", source=GroupSource.MANUAL)
    new_vid = m.edit_image_plate(ids[0], "xyz999", current_vehicle=vid)
    assert new_vid == other
    # image moved: not in vid, in other
    assert ids[0] not in {r["image_id"] for r in m.vehicles.members_for_review(vid)}
    assert ids[0] in {r["image_id"] for r in m.vehicles.members_for_review(other)}
    # plate updated + normalized
    assert ImageRepository(db).get(ids[0]).plate_text_normalized == "XYZ999"


def test_suggest_verification_empty(db):
    veh = VehicleRepository(db)
    vid = veh.get_or_create_for_plate("AAA111")  # no members
    assert _model(db).suggest_verification(vid) == GroupVerification.AUTOMATIC_ONLY


def test_suggest_verification_all_same(db):
    vid, ids = _seed_group(db)
    m = _model(db)
    for i in ids:
        m.set_image_status(i, ReviewStatus.VERIFIED_SAME, vehicle_id=vid)
    assert m.suggest_verification(vid) == GroupVerification.VERIFIED


def test_suggest_verification_mixed(db):
    vid, ids = _seed_group(db)
    m = _model(db)
    m.set_image_status(ids[0], ReviewStatus.VERIFIED_SAME, vehicle_id=vid)
    m.set_image_status(ids[1], ReviewStatus.VERIFIED_SAME, vehicle_id=vid)
    m.set_image_status(ids[2], ReviewStatus.VERIFIED_NOT_SAME, vehicle_id=vid)
    assert m.suggest_verification(vid) == GroupVerification.PARTIALLY_VERIFIED


def test_suggest_verification_uncertain_is_not_fully_verified(db):
    vid, ids = _seed_group(db, n=2)
    m = _model(db)
    m.set_image_status(ids[0], ReviewStatus.VERIFIED_SAME, vehicle_id=vid)
    m.set_image_status(ids[1], ReviewStatus.UNCERTAIN, vehicle_id=vid)
    assert m.suggest_verification(vid) == GroupVerification.PARTIALLY_VERIFIED


def test_suggest_verification_unreviewed(db):
    vid, ids = _seed_group(db)
    m = _model(db)
    m.set_image_status(ids[0], ReviewStatus.VERIFIED_SAME, vehicle_id=vid)
    m.set_image_status(ids[1], ReviewStatus.VERIFIED_SAME, vehicle_id=vid)
    # ids[2] still unreviewed -> automatic_only
    assert m.suggest_verification(vid) == GroupVerification.AUTOMATIC_ONLY


def test_confirm_group(db):
    vid, ids = _seed_group(db)
    assert _model(db).confirm_group(vid) == GroupVerification.VERIFIED
    assert VehicleRepository(db).get(vid)["verification"] == "verified"


def test_sync_verification_persists_suggested_state(db):
    vid, ids = _seed_group(db, n=2)
    m = _model(db)
    for image_id in ids:
        m.set_image_status(image_id, ReviewStatus.VERIFIED_SAME, vehicle_id=vid)
    assert m.sync_verification(vid) == GroupVerification.VERIFIED
    assert m.vehicles.get(vid)["verification"] == "verified"
    assert m.vehicles.get(vid)["label_priority"] == "human_verified"


def test_edit_image_plate_rejects_empty_value(db):
    vid, ids = _seed_group(db, n=1)
    try:
        _model(db).edit_image_plate(ids[0], " -- ", current_vehicle=vid)
    except ValueError as exc:
        assert "letter or digit" in str(exc)
    else:
        raise AssertionError("empty normalized plate should be rejected")


def test_merge_split_wrappers(db):
    m = _model(db)
    a, a_ids = _seed_group(db, n=2, plate="AAA111", prefix="a")
    b, b_ids = _seed_group(db, n=2, plate="BBB222", prefix="b")
    assert m.merge(b, a) == a
    assert m.vehicles.get(b) is None
    assert {r["image_id"] for r in m.vehicles.members_for_review(a)} == set(a_ids + b_ids)

    new_id = m.split(a, [b_ids[0]])
    assert new_id != a
    assert {r["image_id"] for r in m.vehicles.members_for_review(new_id)} == {b_ids[0]}
    assert {r["image_id"] for r in m.vehicles.members_for_review(a)} == {
        *a_ids,
        b_ids[1],
    }
