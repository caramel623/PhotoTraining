from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from tests.test_review_page import _app, _seed_page
from vehicle_dataset_manager.ui.image_preview import ImagePreview
from vehicle_dataset_manager.ui.review_page import ReviewPage


def test_preview_enlarges_and_closes_only_on_backdrop_or_escape(db, workspace):
    app = _app()
    ctx, group, ids = _seed_page(db, workspace)
    parent = QWidget()
    parent.resize(800, 600)
    parent.show()
    preview = ImagePreview(ctx.images.get_details(ids[0])["vehicle_crop_path"], parent)
    preview.show()
    app.processEvents()
    assert preview.has_image
    assert preview.image.pixmap().width() > 220
    assert preview.image.width() <= 736 and preview.image.height() <= 500
    QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=preview.image.geometry().center())
    assert preview.isVisible()
    QTest.keyClick(preview, Qt.Key.Key_Return)
    assert preview.isVisible()
    QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert not preview.isVisible()
    preview.show()
    QTest.keyClick(preview, Qt.Key.Key_Escape)
    assert not preview.isVisible()
    parent.close()


def test_missing_photo_preview_is_dismissible(workspace):
    app = _app()
    preview = ImagePreview(str(workspace.root / "missing.jpg"))
    preview.show()
    app.processEvents()
    assert not preview.has_image
    QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert not preview.isVisible()


def test_click_opens_preview_without_changing_review(db, workspace, monkeypatch):
    app = _app()
    ctx, group, ids = _seed_page(db, workspace)
    page = ReviewPage(ctx)
    opened = []
    def inspect(preview):
        opened.append(preview.has_image)
        return 0
    monkeypatch.setattr(ImagePreview, "exec", inspect)
    page.image_list.itemClicked.emit(page.image_list.item(0))
    assert opened == [True]
    assert all(ctx.images.get(i).review_status == "unreviewed" for i in ids)
    page.close()
    page._thumb_pool.waitForDone(2000)
