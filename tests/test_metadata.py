from vehicle_dataset_manager.services.metadata import FilenameMetadataParser


def test_year_and_camera():
    p = FilenameMetadataParser()
    m = p.parse("2024_CamA_001234.jpg")
    assert m["year"] == 2024
    assert m["camera_id"] == "A"


def test_datetime_speed_direction():
    p = FilenameMetadataParser()
    m = p.parse("IMG_2024-05-15_12:30:45_speed60kmh_east.jpg")
    assert m["date"] == "2024-05-15"
    assert m["time"] == "12:30:45"
    assert m["speed"] == 60
    assert m["direction"] == "east"


def test_no_metadata_does_not_raise():
    p = FilenameMetadataParser()
    m = p.parse("random.jpg")
    assert m["year"] is None
    assert m["camera_id"] is None