from vehicle_dataset_manager.services.plate import (
    is_plausible_plate,
    normalize_plate,
    plates_fuzzy_match,
)


def test_normalize_strips_separators():
    assert normalize_plate("BFY-1765") == "BFY1765"
    assert normalize_plate(" bfY 1765 ") == "BFY1765"
    assert normalize_plate("BFY-I765") == "BFYI765"


def test_normalize_empty():
    assert normalize_plate("") == ""
    assert normalize_plate(None) == ""


def test_fuzzy_confusables():
    # B/8 and S/5 are confusable -> same candidate group
    assert plates_fuzzy_match("BFY-1765", "8FY1765")
    assert plates_fuzzy_match("BFY1765", "BFY176S")
    # unrelated plates do not match
    assert not plates_fuzzy_match("BFY1765", "CXY1765")


def test_plausibility():
    assert is_plausible_plate("BFY1765")
    assert is_plausible_plate("8FY1765")  # confusable read of BFY1765
    assert not is_plausible_plate("HELLO")
    assert not is_plausible_plate("ABC")
    # all-digit readings (header timestamps like "11:56:54") are not plates
    assert not is_plausible_plate("115654")
    assert not is_plausible_plate("070822")
    assert not is_plausible_plate("1234567")
    # motorcycle plates (digits + 3 letters) remain filtered in v1
    assert not is_plausible_plate("928NBV")