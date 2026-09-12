"""Phase 4 manual-review screen.

The page deliberately delegates every data mutation to :class:`ReviewModel`.
Thumbnail decoding runs on a small private thread pool so navigating a group
does not freeze the GUI when source photographs are large.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.core.enums import ReviewStatus
from vehicle_dataset_manager.review_model import GroupImage, ReviewModel
from vehicle_dataset_manager.ui.i18n import display_value


GROUP_ROLE = int(Qt.ItemDataRole.UserRole)
IMAGE_ROLE = GROUP_ROLE + 1

_STATUS_LABELS = {
    ReviewStatus.UNREVIEWED.value: "尚未複核",
    ReviewStatus.VERIFIED_SAME.value: "同一車輛",
    ReviewStatus.VERIFIED_NOT_SAME.value: "不同車輛",
    ReviewStatus.UNCERTAIN.value: "不確定",
    ReviewStatus.EXCLUDED.value: "已排除",
}

_STATUS_COLOURS = {
    ReviewStatus.UNREVIEWED.value: QColor("#f0f0f0"),
    ReviewStatus.VERIFIED_SAME.value: QColor("#d8f3dc"),
    ReviewStatus.VERIFIED_NOT_SAME.value: QColor("#ffd6d6"),
    ReviewStatus.UNCERTAIN.value: QColor("#fff1b8"),
    ReviewStatus.EXCLUDED.value: QColor("#d9dde3"),
}


class _ThumbnailSignals(QObject):
    loaded = Signal(int, int, QImage)


class _ThumbnailTask(QRunnable):
    def __init__(self, generation: int, image_id: int, path: str, size: QSize) -> None:
        super().__init__()
        self.generation = generation
        self.image_id = image_id
        self.path = path
        self.size = size
        self.signals = _ThumbnailSignals()

    def run(self) -> None:
        reader = QImageReader(self.path)
        reader.setAutoTransform(True)
        original_size = reader.size()
        if original_size.isValid():
            reader.setScaledSize(
                original_size.scaled(
                    self.size, Qt.AspectRatioMode.KeepAspectRatio
                )
            )
        image = reader.read()
        if not image.isNull() and (
            image.width() > self.size.width() or image.height() > self.size.height()
        ):
            image = image.scaled(
                self.size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.signals.loaded.emit(self.generation, self.image_id, image)


@dataclass
class _Selection:
    vehicle_id: Optional[str] = None
    image_id: Optional[int] = None


class ReviewPage(QWidget):
    """Human verification workflow for candidate vehicle groups."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.model = ReviewModel(ctx.vehicles, ctx.images, ctx.reviews)
        self._selection = _Selection()
        self._images: list[GroupImage] = []
        self._image_items: dict[int, QListWidgetItem] = {}
        self._thumb_generation = 0
        self._thumb_size = QSize(220, 150)
        self._thumb_pool = QThreadPool(self)
        self._thumb_pool.setMaxThreadCount(2)
        self._shortcuts: list[QShortcut] = []
        self._build_ui()
        self._install_shortcuts()
        self.refresh_groups()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("群組"))
        self.group_filter = QComboBox()
        self.group_filter.setObjectName("reviewGroupFilter")
        self.group_filter.addItem("全部", None)
        self.group_filter.addItem("僅自動判定", "automatic_only")
        self.group_filter.addItem("部分已複核", "partially_verified")
        self.group_filter.addItem("已確認", "verified")
        self.group_filter.addItem("INI／OCR 衝突佇列（含未分組影像）", "metadata_conflicts")
        toolbar.addWidget(self.group_filter)
        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("影像"))
        self.image_filter = QComboBox()
        self.image_filter.setObjectName("reviewImageFilter")
        self.image_filter.addItem("全部", None)
        for status, label in _STATUS_LABELS.items():
            self.image_filter.addItem(label, status)
        toolbar.addWidget(self.image_filter)
        self.btn_refresh = QPushButton("重新整理")
        toolbar.addWidget(self.btn_refresh)
        toolbar.addStretch(1)
        self.help_label = QLabel(
            "Enter＝同一車輛  Space＝下一張  N＝不同車輛  U＝不確定  "
            "X＝排除  E＝修改車牌  M＝合併  S＝拆分"
        )
        self.help_label.setStyleSheet("color: #666;")
        toolbar.addWidget(self.help_label)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.group_list = QListWidget()
        self.group_list.setObjectName("reviewGroupList")
        self.group_list.setMinimumWidth(285)
        self.group_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        splitter.addWidget(self.group_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        summary_row = QHBoxLayout()
        self.summary_label = QLabel("請選擇車輛群組。")
        self.summary_label.setObjectName("reviewSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        summary_row.addWidget(self.summary_label, 1)
        self.btn_confirm = QPushButton("確認整個群組")
        self.btn_merge = QPushButton("合併…")
        self.btn_split = QPushButton("拆分選取影像…")
        self.btn_edit = QPushButton("修改車牌…")
        for button in (self.btn_confirm, self.btn_merge, self.btn_split, self.btn_edit):
            summary_row.addWidget(button)
        right_layout.addLayout(summary_row)
        self.image_metadata_label = QLabel("請選擇影像以查看 INI／OCR 標籤來源。")
        self.image_metadata_label.setObjectName("reviewImageMetadata")
        self.image_metadata_label.setWordWrap(True)
        self.image_metadata_label.setTextFormat(Qt.PlainText)
        right_layout.addWidget(self.image_metadata_label)

        self.image_list = QListWidget()
        self.image_list.setObjectName("reviewImageGrid")
        self.image_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.image_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.image_list.setMovement(QListWidget.Movement.Static)
        self.image_list.setWrapping(True)
        self.image_list.setIconSize(self._thumb_size)
        self.image_list.setGridSize(QSize(260, 225))
        self.image_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.image_list.setWordWrap(True)
        right_layout.addWidget(self.image_list, 1)

        status_row = QHBoxLayout()
        self.btn_same = QPushButton("同一車輛")
        self.btn_not_same = QPushButton("不同車輛")
        self.btn_uncertain = QPushButton("不確定")
        self.btn_excluded = QPushButton("排除")
        for button in (
            self.btn_same,
            self.btn_not_same,
            self.btn_uncertain,
            self.btn_excluded,
        ):
            status_row.addWidget(button)
        status_row.addStretch(1)
        self.review_progress_label = QLabel("已複核 0／0")
        self.review_progress = QProgressBar()
        self.review_progress.setObjectName("reviewProgress")
        self.review_progress.setFixedWidth(220)
        status_row.addWidget(self.review_progress_label)
        status_row.addWidget(self.review_progress)
        right_layout.addLayout(status_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 950])
        root.addWidget(splitter, 1)

        self.group_filter.currentIndexChanged.connect(self.refresh_groups)
        self.image_filter.currentIndexChanged.connect(self._reload_current_group)
        self.btn_refresh.clicked.connect(self.refresh_groups)
        self.group_list.currentItemChanged.connect(self._on_group_changed)
        self.image_list.currentItemChanged.connect(self._on_image_changed)
        self.btn_same.clicked.connect(
            lambda: self._apply_status(ReviewStatus.VERIFIED_SAME)
        )
        self.btn_not_same.clicked.connect(
            lambda: self._apply_status(ReviewStatus.VERIFIED_NOT_SAME)
        )
        self.btn_uncertain.clicked.connect(
            lambda: self._apply_status(ReviewStatus.UNCERTAIN)
        )
        self.btn_excluded.clicked.connect(
            lambda: self._apply_status(ReviewStatus.EXCLUDED)
        )
        self.btn_confirm.clicked.connect(self._confirm_group)
        self.btn_edit.clicked.connect(self._edit_plate)
        self.btn_merge.clicked.connect(self._merge_group)
        self.btn_split.clicked.connect(self._split_selected)
        self._update_actions()

    def _install_shortcuts(self) -> None:
        bindings = (
            ("Return", lambda: self._apply_status(ReviewStatus.VERIFIED_SAME)),
            ("Enter", lambda: self._apply_status(ReviewStatus.VERIFIED_SAME)),
            ("Space", self._next_image),
            ("N", lambda: self._apply_status(ReviewStatus.VERIFIED_NOT_SAME)),
            ("U", lambda: self._apply_status(ReviewStatus.UNCERTAIN)),
            ("X", lambda: self._apply_status(ReviewStatus.EXCLUDED)),
            ("E", self._edit_plate),
            ("M", self._merge_group),
            ("S", self._split_selected),
        )
        for key, slot in bindings:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def refresh_groups(self, *_args, select_vehicle: Optional[str] = None) -> None:
        wanted = select_vehicle or self._selection.vehicle_id
        verification = self.group_filter.currentData()
        groups = self.model.list_groups(verification=verification)
        self.group_list.blockSignals(True)
        self.group_list.clear()
        selected_item = None
        for group in groups:
            plate = group.plate_normalized or "（無車牌）"
            item = QListWidgetItem(
                f"{plate}  ·  {group.image_count} 張影像\n"
                f"{group.vehicle_id}  ·  {display_value(group.verification)}"
            )
            item.setData(GROUP_ROLE, group.vehicle_id)
            item.setToolTip(
                f"來源：{display_value(group.source)}\n相機：{', '.join(group.cameras) or '-'}\n"
                f"年份：{', '.join(map(str, group.years)) or '-'}"
            )
            self.group_list.addItem(item)
            if group.vehicle_id == wanted:
                selected_item = item
        self.group_list.blockSignals(False)
        if selected_item is not None:
            self.group_list.setCurrentItem(selected_item)
        elif self.group_list.count():
            self.group_list.setCurrentRow(0)
        else:
            self._selection = _Selection()
            self._clear_group()

    def _on_group_changed(
        self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]
    ) -> None:
        if current is None:
            self._clear_group()
            return
        self._selection.vehicle_id = current.data(GROUP_ROLE)
        self._load_group(self._selection.vehicle_id)

    def _reload_current_group(self, *_args) -> None:
        if self._selection.vehicle_id:
            self._load_group(self._selection.vehicle_id)

    def _load_group(self, vehicle_id: str) -> None:
        summary = self.model.group_summary(vehicle_id)
        if summary is None:
            self.refresh_groups()
            return
        self._images = self.model.group_images(vehicle_id)
        cameras = ", ".join(summary.cameras) or "-"
        years = ", ".join(map(str, summary.years)) or "-"
        self.summary_label.setText(
            f"<b>{summary.plate_normalized or '（無車牌）'}</b> &nbsp; "
            f"{summary.vehicle_id}<br>"
            f"{summary.image_count} 張影像 · 相機：{cameras} · 年份：{years} · "
            f"確認狀態：{display_value(summary.verification)}"
        )
        self._thumb_generation += 1
        generation = self._thumb_generation
        wanted_status = self.image_filter.currentData()
        self.image_list.clear()
        self._image_items.clear()
        placeholder = self._placeholder_icon()
        first_selected = None
        for image in self._images:
            if wanted_status and image.review_status != wanted_status:
                continue
            details = [image.original_filename, _STATUS_LABELS.get(image.review_status, image.review_status)]
            meta = " · ".join(x for x in (image.camera_id, image.date) if x)
            if meta:
                details.append(meta)
            item = QListWidgetItem(placeholder, "\n".join(details))
            item.setData(IMAGE_ROLE, image.image_id)
            item.setToolTip(
                f"影像 ID：{image.image_id}\nINI 車牌：{image.ini_plate_text or '-'}\n"
                f"OCR 車牌：{image.ocr_plate_text or '-'}\n"
                f"有效車牌：{image.effective_plate_text or image.plate_text_normalized or '-'}\n"
                f"來源：{image.plate_source}\n驗證：{image.plate_validation_status}\n"
                f"信心值：{image.confidence if image.confidence is not None else '-'}\n"
                f"路徑：{image.display_path or '（檔案不存在）'}"
            )
            item.setBackground(QBrush(_STATUS_COLOURS.get(image.review_status, QColor("white"))))
            self.image_list.addItem(item)
            self._image_items[image.image_id] = item
            if image.image_id == self._selection.image_id:
                first_selected = item
            if image.display_path:
                task = _ThumbnailTask(
                    generation, image.image_id, image.display_path, self._thumb_size
                )
                task.signals.loaded.connect(self._thumbnail_loaded)
                self._thumb_pool.start(task)
        if first_selected is not None:
            self.image_list.setCurrentItem(first_selected)
        elif self.image_list.count():
            self.image_list.setCurrentRow(0)
        self._update_progress()
        self._update_actions()

    def _placeholder_icon(self) -> QIcon:
        image = QImage(self._thumb_size, QImage.Format.Format_RGB32)
        image.fill(QColor("#c7cbd1"))
        return QIcon(QPixmap.fromImage(image))

    def _thumbnail_loaded(self, generation: int, image_id: int, image: QImage) -> None:
        if generation != self._thumb_generation or image.isNull():
            return
        item = self._image_items.get(image_id)
        if item is not None:
            item.setIcon(QIcon(QPixmap.fromImage(image)))

    def _on_image_changed(
        self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]
    ) -> None:
        self._selection.image_id = current.data(IMAGE_ROLE) if current else None
        image = next((item for item in self._images if item.image_id == self._selection.image_id), None)
        if image is None:
            self.image_metadata_label.setText("請選擇影像以查看 INI／OCR 標籤來源。")
        else:
            warning = " ⚠ " + (image.conflict_type or image.plate_validation_status) if image.metadata_conflict or image.plate_validation_status == "OCR_MISMATCH" else ""
            self.image_metadata_label.setText(
                f"INI 車牌：{image.ini_plate_text or '-'}　OCR 車牌：{image.ocr_plate_text or '-'}　"
                f"有效車牌：{image.effective_plate_text or image.plate_text_normalized or '-'}　"
                f"來源：{image.plate_source}　驗證：{image.plate_validation_status}" + warning
            )
            details = self.model.images.get_details(image.image_id) or {}
            metadata = "　".join(
                f"{label}：{details.get(key) if details.get(key) is not None else '-'}"
                for key, label in (
                    ("date", "日期"), ("time", "時間"), ("camera_id", "主機"),
                    ("location", "地點"), ("image_sequence", "序號"),
                    ("vehicle_speed", "車速"), ("speed_limit", "速限"),
                    ("direction_text", "方向"), ("direction_code", "方向代碼"),
                    ("vehicle_type_code", "車種"), ("violation_code", "違規"),
                    ("ini_encoding", "編碼"), ("ini_parse_status", "解析"),
                )
            )
            self.image_metadata_label.setText(self.image_metadata_label.text() + "\n" + metadata)
            sources = self.model.images.db.query(
                "SELECT a.archive_filename,s.member_path,s.raw_metadata FROM ini_sources s "
                "JOIN archives a ON a.archive_id=s.archive_id WHERE s.image_id=?", (image.image_id,)
            )
            self.image_metadata_label.setToolTip("\n\n".join(
                f"{row['archive_filename']} / {row['member_path']}\n{row['raw_metadata']}" for row in sources
            ))
        self._update_actions()

    def _selected_image_ids(self) -> list[int]:
        items = self.image_list.selectedItems()
        if not items and self.image_list.currentItem() is not None:
            items = [self.image_list.currentItem()]
        return [int(item.data(IMAGE_ROLE)) for item in items]

    def _apply_status(self, status: ReviewStatus) -> None:
        vehicle_id = self._selection.vehicle_id
        image_ids = self._selected_image_ids()
        if not vehicle_id or not image_ids:
            return
        for image_id in image_ids:
            self.model.set_image_status(image_id, status, vehicle_id=vehicle_id)
        if vehicle_id != "__metadata__":
            self.model.sync_verification(vehicle_id)
        self._selection.image_id = image_ids[-1]
        self.refresh_groups(select_vehicle=vehicle_id)

    def _confirm_group(self) -> None:
        vehicle_id = self._selection.vehicle_id
        if not vehicle_id or vehicle_id == "__metadata__":
            return
        if QMessageBox.question(
            self,
            "確認車輛群組",
            "要將整個群組標記為已經人工確認嗎？",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.model.confirm_group(vehicle_id)
        self.refresh_groups(select_vehicle=vehicle_id)

    def _edit_plate(self) -> None:
        vehicle_id = self._selection.vehicle_id
        image_ids = self._selected_image_ids()
        if not vehicle_id or not image_ids:
            return
        image_id = image_ids[0]
        current = next((x for x in self._images if x.image_id == image_id), None)
        plate, ok = QInputDialog.getText(
            self,
            "修改車牌",
            "正確的車牌文字：",
            text=current.plate_text_normalized if current else "",
        )
        if not ok:
            return
        try:
            target = self.model.edit_image_plate(
                image_id, plate, current_vehicle=vehicle_id
            )
        except ValueError as exc:
            QMessageBox.warning(self, "修改車牌", str(exc))
            return
        self._selection.image_id = image_id
        self.refresh_groups(select_vehicle=target)

    def _merge_group(self) -> None:
        source = self._selection.vehicle_id
        if not source or source == "__metadata__":
            return
        choices = [
            f"{g.vehicle_id} — {g.plate_normalized or '（無車牌）'}"
            for g in self.model.list_groups()
            if g.vehicle_id != source
        ]
        if not choices:
            QMessageBox.information(self, "合併", "目前沒有其他可合併的車輛群組。")
            return
        selected, ok = QInputDialog.getItem(
            self, "合併群組", "選擇要保留的目標群組：", choices, 0, False
        )
        if not ok:
            return
        target = selected.split(" — ", 1)[0]
        if QMessageBox.question(
            self,
            "合併群組",
            f"要將 {source} 合併到 {target} 嗎？\n只會變更群組關係，原始影像不會被修改。",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            kept = self.model.merge(source, target)
        except ValueError as exc:
            QMessageBox.critical(self, "合併失敗", str(exc))
            return
        self.refresh_groups(select_vehicle=kept)

    def _split_selected(self) -> None:
        vehicle_id = self._selection.vehicle_id
        if vehicle_id == "__metadata__":
            return
        image_ids = self._selected_image_ids()
        if not vehicle_id or not image_ids:
            QMessageBox.information(self, "拆分", "請先選取一張或多張影像。")
            return
        if len(image_ids) >= len(self._images):
            QMessageBox.warning(
                self, "拆分", "請勿選取全部影像，來源群組至少必須保留一張影像。"
            )
            return
        if QMessageBox.question(
            self,
            "拆分群組",
            f"要將選取的 {len(image_ids)} 張影像移到新的車輛群組嗎？",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            new_id = self.model.split(vehicle_id, image_ids)
        except ValueError as exc:
            QMessageBox.critical(self, "拆分失敗", str(exc))
            return
        self._selection.image_id = image_ids[0]
        self.refresh_groups(select_vehicle=new_id)

    def _next_image(self) -> None:
        count = self.image_list.count()
        if not count:
            return
        row = self.image_list.currentRow()
        self.image_list.setCurrentRow((row + 1) % count)

    def _update_progress(self) -> None:
        total = len(self._images)
        reviewed = sum(
            image.review_status != ReviewStatus.UNREVIEWED.value for image in self._images
        )
        self.review_progress.setRange(0, max(total, 1))
        self.review_progress.setValue(reviewed)
        self.review_progress_label.setText(f"已複核 {reviewed}／{total}")

    def _update_actions(self) -> None:
        has_group = bool(self._selection.vehicle_id) and self._selection.vehicle_id != "__metadata__"
        has_image = bool(self._selected_image_ids())
        for button in (self.btn_confirm, self.btn_merge):
            button.setEnabled(has_group)
        for button in (
            self.btn_split,
            self.btn_edit,
            self.btn_same,
            self.btn_not_same,
            self.btn_uncertain,
            self.btn_excluded,
        ):
            button.setEnabled(has_group and has_image)
        self.btn_edit.setEnabled(has_image)
        self.btn_uncertain.setEnabled(has_image)
        self.btn_excluded.setEnabled(has_image)

    def _clear_group(self) -> None:
        self._thumb_generation += 1
        self._images = []
        self._image_items.clear()
        self.image_list.clear()
        self.summary_label.setText("沒有符合目前篩選條件的車輛群組。")
        self.review_progress.setRange(0, 1)
        self.review_progress.setValue(0)
        self.review_progress_label.setText("已複核 0／0")
        self._update_actions()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._thumb_generation += 1
        self._thumb_pool.clear()
        super().closeEvent(event)
