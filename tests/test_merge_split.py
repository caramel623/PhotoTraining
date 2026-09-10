from vehicle_dataset_manager.core.enums import GroupVerification
from vehicle_dataset_manager.database.repositories import ImageRepository, VehicleRepository


def _seed(db):
    img = ImageRepository(db)
    ids = [img.upsert_from_scan(original_filename=f"f{i}.jpg", source_path=f"/x/f{i}.jpg") for i in range(3)]
    return ids


def test_same_plate_same_group(db):
    veh = VehicleRepository(db)
    assert veh.get_or_create_for_plate("BFY1765") == veh.get_or_create_for_plate("bfy1765")


def test_merge(db):
    veh = VehicleRepository(db)
    ids = _seed(db)
    a = veh.get_or_create_for_plate("AAA111")
    b = veh.get_or_create_for_plate("BBB222")
    veh.add_member(a, ids[0])
    veh.add_member(b, ids[1])
    veh.add_member(b, ids[0])  # conflict: also in a
    target = veh.merge(b, a)
    assert target == a
    assert veh.get(b) is None
    # ids[0] stays in a (no dup), ids[1] moved into a
    member_ids = {m["image_id"] for m in veh.members_of(a)}
    assert member_ids == {ids[0], ids[1]}


def test_split(db):
    veh = VehicleRepository(db)
    ids = _seed(db)
    a = veh.get_or_create_for_plate("AAA111")
    for i in ids:
        veh.add_member(a, i)
    new_id = veh.split(a, [ids[2]])
    assert new_id != a
    assert len(veh.members_of(a)) == 2
    assert len(veh.members_of(new_id)) == 1
    assert veh.members_of(new_id)[0]["image_id"] == ids[2]

def test_label_priority_on_create_and_verify(db):
    veh = VehicleRepository(db)
    vid = veh.get_or_create_for_plate("AAA111")
    assert veh.get(vid)["label_priority"] == "automatic_candidate"
    veh.set_verification(vid, GroupVerification.PARTIALLY_VERIFIED)
    assert veh.get(vid)["label_priority"] == "high_confidence_plate_match"
    veh.set_verification(vid, GroupVerification.VERIFIED)
    assert veh.get(vid)["label_priority"] == "human_verified"


def test_merge_promotes_target_priority(db):
    veh = VehicleRepository(db)
    ids = _seed(db)
    a = veh.get_or_create_for_plate("AAA111")
    b = veh.get_or_create_for_plate("BBB222")
    veh.add_member(a, ids[0])
    veh.add_member(b, ids[1])
    veh.set_verification(a, GroupVerification.VERIFIED)
    # human-verified group absorbed into automatic group -> target promoted
    veh.merge(a, b)
    assert veh.get(b)["label_priority"] == "human_verified"
    assert veh.get(a) is None
