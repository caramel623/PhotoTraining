from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from vehicle_dataset_manager.core.enums import ReviewStatus
from vehicle_dataset_manager.database.repositories import (
    ImageRepository,
    ReviewRepository,
    VehicleRepository,
)
from vehicle_dataset_manager.ui.review_page import ReviewPage


def _app():
    return QApplication.instance() or QApplication([])


def _seed_page(db, workspace):
    images = ImageRepository(db)
    vehicles = VehicleRepository(db)
    reviews = ReviewRepository(db)
    vehicle_id = vehicles.get_or_create_for_plate("BFY1765")
    image_ids = []
    for index in range(2):
        crop = workspace.crops_dir / f"review_{index}.jpg"
        image = QImage(80, 60, QImage.Format.Format_RGB32)
        image.fill(0xFF336699 + index)
        assert image.save(str(crop))
        image_id = images.upsert_from_scan(
            original_filename=f"review_{index}.jpg",
            source_path=str(crop),
            camera_id="RS015",
            archive_year=2025,
        )
        images.set_result_fields(
            image_id,
            date=f"2025-01-0{index + 1}",
            vehicle_crop_path=str(crop),
            plate_text_normalized="BFY1765",
        )
        vehicles.add_member(vehicle_id, image_id)
        image_ids.append(image_id)
    ctx = SimpleNamespace(
        workspace=workspace,
        images=images,
        vehicles=vehicles,
        reviews=reviews,
    )
    return ctx, vehicle_id, image_ids


def test_review_page_loads_grid_and_applies_status(db, workspace):
    app = _app()
    ctx, vehicle_id, image_ids = _seed_page(db, workspace)
    page = ReviewPage(ctx)
    page.show()
    app.processEvents()

    assert page.group_list.count() == 1
    assert page.image_list.count() == 2
    assert "BFY1765" in page.summary_label.text()
    assert page.review_progress.value() == 0

    page.image_list.setCurrentRow(0)
    page._apply_status(ReviewStatus.VERIFIED_SAME)
    page.image_list.setCurrentRow(1)
    page._apply_status(ReviewStatus.VERIFIED_SAME)

    assert all(ctx.images.get(image_id).review_status == "verified_same_vehicle" for image_id in image_ids)
    assert ctx.vehicles.get(vehicle_id)["verification"] == "verified"
    assert page.review_progress.value() == 2

    page.image_filter.setCurrentIndex(
        page.image_filter.findData(ReviewStatus.VERIFIED_SAME.value)
    )
    assert page.image_list.count() == 2
    page.close()
    page._thumb_pool.waitForDone(2000)


def test_review_page_installs_required_shortcuts(db, workspace):
    _app()
    ctx, _vehicle_id, _image_ids = _seed_page(db, workspace)
    page = ReviewPage(ctx)
    keys = {
        shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)
        for shortcut in page.findChildren(QShortcut)
    }
    assert {"Return", "Enter", "Space", "N", "U", "E", "M", "S"}.issubset(keys)
    page.close()
    page._thumb_pool.waitForDone(2000)


def test_review_page_edits_plate_and_splits_selection(db, workspace, monkeypatch):
    _app()
    ctx, original_group, image_ids = _seed_page(db, workspace)
    page = ReviewPage(ctx)
    page.image_list.setCurrentRow(0)

    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("XYZ-999", True)
    )
    page._edit_plate()
    edited_group = ctx.vehicles.vehicle_for_image(image_ids[0])
    assert edited_group != original_group
    assert ctx.images.get(image_ids[0]).plate_text_normalized == "XYZ999"

    page.refresh_groups(select_vehicle=original_group)
    page.image_list.setCurrentRow(0)
    extra_id = ctx.images.upsert_from_scan(
        original_filename="extra.jpg",
        source_path=str(workspace.root / "extra.jpg"),
    )
    ctx.vehicles.add_member(original_group, extra_id)
    page.refresh_groups(select_vehicle=original_group)
    page.image_list.setCurrentRow(0)
    selected_id = page._selected_image_ids()[0]
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    page._split_selected()
    assert ctx.vehicles.vehicle_for_image(selected_id) != original_group
    page.close()
    page._thumb_pool.waitForDone(2000)


def test_review_page_merges_current_group_into_selected_target(db, workspace, monkeypatch):
    _app()
    ctx, target, _image_ids = _seed_page(db, workspace)
    source = ctx.vehicles.get_or_create_for_plate("XYZ999")
    source_image = ctx.images.upsert_from_scan(
        original_filename="source.jpg",
        source_path=str(workspace.root / "source.jpg"),
    )
    ctx.vehicles.add_member(source, source_image)
    page = ReviewPage(ctx)
    page.refresh_groups(select_vehicle=source)

    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args, **kwargs: (f"{target} — BFY1765", True),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    page._merge_group()
    assert ctx.vehicles.get(source) is None
    assert ctx.vehicles.vehicle_for_image(source_image) == target
    page.close()
    page._thumb_pool.waitForDone(2000)
