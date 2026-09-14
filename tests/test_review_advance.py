from PySide6.QtWidgets import QMessageBox
from PySide6.QtGui import QShortcut
import pytest

from tests.test_review_page import _app, _seed_page
from vehicle_dataset_manager.ui.review_page import ReviewPage, GROUP_ROLE


def seed_groups(db, workspace):
    ctx, _, _ = _seed_page(db, workspace)
    for index in range(2):
        group = ctx.vehicles.get_or_create_for_plate(f"TST{index}123")
        image = ctx.images.upsert_from_scan(original_filename=f"extra{index}.jpg",
                                            source_path=str(workspace.root / f"extra{index}.jpg"))
        ctx.vehicles.add_member(group, image)
    return ctx


def test_confirm_advances_skips_verified_and_wraps(db, workspace, monkeypatch):
    app = _app()
    page = ReviewPage(seed_groups(db, workspace))
    ids = [page.group_list.item(i).data(GROUP_ROLE) for i in range(page.group_list.count())]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: notices.append(True))
    page.model.confirm_group(ids[1])
    page.refresh_groups(select_vehicle=ids[2])
    page.btn_confirm.click()
    assert page._selection.vehicle_id == ids[0]
    assert not notices
    page.btn_confirm.click()
    assert notices == []
    assert "沒有其他待檢核群組" in page.summary_label.text()
    assert all(g.verification == "verified" for g in page.model.list_groups())
    page.close()
    page._thumb_pool.waitForDone(2000)


def test_confirm_advances_when_current_disappears_from_filter(db, workspace, monkeypatch):
    app = _app()
    page = ReviewPage(seed_groups(db, workspace))
    page.group_filter.setCurrentIndex(page.group_filter.findData("automatic_only"))
    ids = [page.group_list.item(i).data(GROUP_ROLE) for i in range(page.group_list.count())]
    page.refresh_groups(select_vehicle=ids[1])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    page.btn_confirm.click()
    assert page._selection.vehicle_id == ids[2]
    assert page.group_list.count() == 2
    page.close()
    page._thumb_pool.waitForDone(2000)


@pytest.mark.parametrize("key", ["Return", "Enter"])
def test_enter_confirms_entire_group_without_dialog(db, workspace, monkeypatch, key):
    app = _app()
    page = ReviewPage(seed_groups(db, workspace))
    selected = page._selection.vehicle_id
    def forbidden(*args):
        raise AssertionError("Confirmation must not display a dialog")
    monkeypatch.setattr(QMessageBox, "question", forbidden)
    monkeypatch.setattr(QMessageBox, "information", forbidden)
    shortcut = next(s for s in page.findChildren(QShortcut) if s.key().toString() == key)
    assert not shortcut.autoRepeat()
    shortcut.activated.emit()
    assert page._selection.vehicle_id != selected
    assert page.model.group_summary(selected).verification == "verified"
    page.close()
    page._thumb_pool.waitForDone(2000)
