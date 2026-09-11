"""Phase 5 local dataset export page."""
from __future__ import annotations

import getpass
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.exporting import (
    DatasetExporter,
    ExportOptions,
    ExportResult,
    SplitSpec,
)
from vehicle_dataset_manager.reid import DatasetIndexBuilder, IndexBuildResult, StubReID


class ExportPage(QWidget):
    def __init__(self, ctx: AppContext, runner) -> None:
        super().__init__()
        self.ctx = ctx
        self.runner = runner
        self.exporter = DatasetExporter(ctx.exports)
        self._build_ui()
        self._load_defaults()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        destination = QGroupBox("輸出位置")
        destination_form = QFormLayout(destination)
        path_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setObjectName("exportOutputPath")
        self.btn_browse = QPushButton("瀏覽…")
        path_row.addWidget(self.output_path, 1)
        path_row.addWidget(self.btn_browse)
        destination_form.addRow("資料集資料夾", path_row)
        root.addWidget(destination)

        options = QGroupBox("標籤與影像")
        options_form = QFormLayout(options)
        self.label_policy = QComboBox()
        self.label_policy.setObjectName("exportLabelPolicy")
        self.label_policy.addItem(
            "僅限人工確認（建議）", ("human_verified",)
        )
        self.label_policy.addItem(
            "人工確認＋高信心車牌",
            ("human_verified", "high_confidence_plate_match"),
        )
        self.label_policy.addItem(
            "包含自動候選項目",
            (
                "human_verified",
                "high_confidence_plate_match",
                "automatic_candidate",
            ),
        )
        self.mask_method = QComboBox()
        self.mask_method.addItem("純色遮罩", "solid_color")
        self.mask_method.addItem("模糊", "blur")
        self.mask_method.addItem("影像修補", "inpaint")
        self.mask_margin = QSpinBox()
        self.mask_margin.setRange(0, 60)
        self.image_quality = QSpinBox()
        self.image_quality.setRange(1, 100)
        self.image_quality.setSuffix("%")
        self.copy_originals = QCheckBox("將原始影像複製到 Dataset/images")
        self.copy_originals.setChecked(True)
        self.require_plate = QCheckBox(
            "略過沒有可靠車牌範圍的影像（建議）"
        )
        self.require_plate.setChecked(True)
        options_form.addRow("訓練標籤", self.label_policy)
        options_form.addRow("車牌遮罩", self.mask_method)
        options_form.addRow("遮罩邊界", self.mask_margin)
        options_form.addRow("JPEG 品質", self.image_quality)
        options_form.addRow(self.copy_originals)
        options_form.addRow(self.require_plate)
        root.addWidget(options)

        split = QGroupBox("避免資料洩漏的資料切分")
        split_form = QFormLayout(split)
        self.split_strategy = QComboBox()
        self.split_strategy.setObjectName("exportSplitStrategy")
        self.split_strategy.addItem("群組雜湊（70／15／15）", "group_hash")
        self.split_strategy.addItem("依時間切分", "time")
        self.split_strategy.addItem("依相機切分", "camera")
        self.val_years = QLineEdit()
        self.val_years.setPlaceholderText("例如：2025")
        self.test_years = QLineEdit()
        self.test_years.setPlaceholderText("例如：2026")
        self.val_cameras = QLineEdit()
        self.val_cameras.setPlaceholderText("以逗號分隔相機 ID")
        self.test_cameras = QLineEdit()
        self.test_cameras.setPlaceholderText("例如：RS017")
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(42)
        split_form.addRow("切分方式", self.split_strategy)
        split_form.addRow("驗證集年份", self.val_years)
        split_form.addRow("測試集年份", self.test_years)
        split_form.addRow("驗證集相機", self.val_cameras)
        split_form.addRow("測試集相機", self.test_cameras)
        split_form.addRow("固定亂數種子", self.seed)
        root.addWidget(split)

        actions = QHBoxLayout()
        self.btn_preview = QPushButton("預覽可匯出數量")
        self.btn_export = QPushButton("匯出資料集")
        self.btn_index = QPushButton("建立 Re-ID 索引")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        actions.addWidget(self.btn_preview)
        actions.addWidget(self.btn_export)
        actions.addWidget(self.btn_index)
        actions.addWidget(self.btn_cancel)
        actions.addStretch(1)
        self.status_label = QLabel("準備就緒。")
        actions.addWidget(self.status_label)
        root.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setObjectName("exportLog")
        self.log.setReadOnly(True)
        self.log.setPlaceholderText(
            "匯出結果與略過原因會顯示於此；全程不使用網路。"
        )
        root.addWidget(self.log, 1)

        self.btn_browse.clicked.connect(self._browse)
        self.btn_preview.clicked.connect(self._preview)
        self.btn_export.clicked.connect(self._start_export)
        self.btn_index.clicked.connect(self._start_index)
        self.btn_cancel.clicked.connect(self.runner.stop)
        self.split_strategy.currentIndexChanged.connect(self._update_split_fields)

    def _load_defaults(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_path.setText(str(self.ctx.workspace.exports_dir / f"Dataset_{stamp}"))
        mask_index = self.mask_method.findData(self.ctx.settings.mask.method)
        if mask_index >= 0:
            self.mask_method.setCurrentIndex(mask_index)
        self.mask_margin.setValue(self.ctx.settings.mask.margin)
        self.image_quality.setValue(self.ctx.settings.export.image_quality)
        self._update_split_fields()

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "選擇空白或可繼續匯出的資料集資料夾",
            str(self.ctx.workspace.exports_dir),
        )
        if selected:
            self.output_path.setText(selected)

    def _options(self) -> ExportOptions:
        strategy = self.split_strategy.currentData()
        split = SplitSpec(
            strategy=strategy,
            seed=self.seed.value(),
            val_years=_parse_ints(self.val_years.text()),
            test_years=_parse_ints(self.test_years.text()),
            val_cameras=_parse_strings(self.val_cameras.text()),
            test_cameras=_parse_strings(self.test_cameras.text()),
        )
        return ExportOptions(
            label_priorities=tuple(self.label_policy.currentData()),
            mask_method=str(self.mask_method.currentData()),
            mask_margin=self.mask_margin.value(),
            image_quality=self.image_quality.value(),
            copy_originals=self.copy_originals.isChecked(),
            require_plate_bbox=self.require_plate.isChecked(),
            split=split,
        )

    def _preview(self) -> None:
        try:
            preview = self.exporter.preview(self._options())
        except ValueError as exc:
            QMessageBox.warning(self, "匯出設定", str(exc))
            return
        eligible = preview["candidates"] - preview["review_rejected"]
        self.status_label.setText(
            f"可匯出：{eligible}／{preview['candidates']} 張候選影像"
        )

    def _start_export(self) -> None:
        if self.runner.is_running:
            QMessageBox.information(self, "匯出", "目前已有其他工作正在執行。")
            return
        output = self.output_path.text().strip()
        if not output:
            QMessageBox.warning(self, "匯出", "請選擇資料集資料夾。")
            return
        try:
            options = self._options()
            preview = self.exporter.preview(options)
        except ValueError as exc:
            QMessageBox.warning(self, "匯出設定", str(exc))
            return
        if preview["candidates"] == 0:
            QMessageBox.information(
                self,
                "匯出",
                "沒有符合所選標籤規則的已完成群組影像。",
            )
            return

        def do_export(progress_cb, should_cancel):
            return self.exporter.export(
                output,
                options,
                on_progress=progress_cb,
                should_cancel=should_cancel,
                created_by=getpass.getuser(),
            )

        self.log.clear()
        self.log.appendPlainText("開始本機匯出：" + output)
        self._set_running(True)
        signals = self.runner.start(do_export)
        signals.progress.connect(self._progress)
        signals.finished.connect(self._done)
        signals.error.connect(self._error)

    def _start_index(self) -> None:
        if self.runner.is_running:
            QMessageBox.information(self, "Re-ID", "目前已有其他工作正在執行。")
            return
        root = Path(self.output_path.text().strip()).expanduser()
        if not (root / "metadata" / "manifest.jsonl").is_file():
            QMessageBox.warning(
                self, "Re-ID", "請選擇已完成匯出的資料集資料夾。"
            )
            return
        engine = getattr(self.ctx, "reid", None)
        if engine is None or isinstance(engine, StubReID):
            QMessageBox.information(
                self,
                "Re-ID",
                "請先在「設定」頁指定現有的 ONNX Re-ID 模型。",
            )
            return
        checkpoint_every = getattr(
            getattr(self.ctx.settings, "reid", None), "checkpoint_every", 100
        )

        def do_index(progress_cb, should_cancel):
            return DatasetIndexBuilder(
                engine, checkpoint_every=checkpoint_every
            ).build(
                root,
                on_progress=progress_cb,
                should_cancel=should_cancel,
            )

        self.log.clear()
        self.log.appendPlainText("開始建立本機 Re-ID 索引：" + str(root))
        self._set_running(True)
        signals = self.runner.start(do_index)
        signals.progress.connect(self._progress)
        signals.finished.connect(self._index_done)
        signals.error.connect(self._error)

    def _progress(self, done: int, total: int, current: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.progress.setFormat(f"{done}/{total}  {current}")

    def _done(self, result) -> None:
        self._set_running(False)
        if not isinstance(result, ExportResult):
            self.status_label.setText("匯出已停止。")
            return
        state = "已取消，可繼續" if result.cancelled else "已完成"
        self.status_label.setText(
            f"{state}：已匯出={result.exported}，已略過={result.skipped}"
        )
        self.log.appendPlainText(
            f"{state}\n影像：{result.exported}／{result.total}\n"
            f"資料切分：{result.split_counts}\n影像配對：{result.pair_count}\n"
            f"三元組：{result.triplet_count}\n略過原因：{result.skip_reasons}"
        )
        if not result.cancelled:
            QMessageBox.information(
                self, "匯出完成", f"資料集已寫入本機：\n{result.output_dir}"
            )

    def _error(self, message: str) -> None:
        self._set_running(False)
        self.status_label.setText("匯出失敗。")
        self.log.appendPlainText("錯誤：" + message)
        QMessageBox.critical(self, "匯出失敗", message)

    def _index_done(self, result) -> None:
        self._set_running(False)
        if not isinstance(result, IndexBuildResult):
            self.status_label.setText("Re-ID 索引建立已停止。")
            return
        state = "已取消，可繼續" if result.cancelled else "已完成"
        self.status_label.setText(
            f"{state}：已建立索引={result.indexed}，重複使用={result.reused}，"
            f"失敗={result.failed}"
        )
        self.log.appendPlainText(
            f"{state}\n已建立索引：{result.indexed}／{result.total}\n"
            f"重複使用：{result.reused}\n失敗：{result.failed}\n"
            f"索引：{result.index_path}"
        )

    def _set_running(self, running: bool) -> None:
        self.btn_preview.setEnabled(not running)
        self.btn_export.setEnabled(not running)
        self.btn_index.setEnabled(not running)
        self.btn_cancel.setEnabled(running)

    def _update_split_fields(self, *_args) -> None:
        strategy = self.split_strategy.currentData()
        year_enabled = strategy == "time"
        camera_enabled = strategy == "camera"
        for widget in (self.val_years, self.test_years):
            widget.setEnabled(year_enabled)
        for widget in (self.val_cameras, self.test_cameras):
            widget.setEnabled(camera_enabled)


def _parse_ints(text: str) -> tuple[int, ...]:
    values = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError as exc:
            raise ValueError(f"年份格式無效：{part}") from exc
    return tuple(sorted(set(values)))


def _parse_strings(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            part.strip()
            for part in text.replace(";", ",").split(",")
            if part.strip()
        )
    )
