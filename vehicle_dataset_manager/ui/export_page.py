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

        destination = QGroupBox("Destination")
        destination_form = QFormLayout(destination)
        path_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setObjectName("exportOutputPath")
        self.btn_browse = QPushButton("Browse…")
        path_row.addWidget(self.output_path, 1)
        path_row.addWidget(self.btn_browse)
        destination_form.addRow("Dataset folder", path_row)
        root.addWidget(destination)

        options = QGroupBox("Labels and images")
        options_form = QFormLayout(options)
        self.label_policy = QComboBox()
        self.label_policy.setObjectName("exportLabelPolicy")
        self.label_policy.addItem(
            "Human verified only (recommended)", ("human_verified",)
        )
        self.label_policy.addItem(
            "Human + high-confidence plate",
            ("human_verified", "high_confidence_plate_match"),
        )
        self.label_policy.addItem(
            "Include automatic candidates",
            (
                "human_verified",
                "high_confidence_plate_match",
                "automatic_candidate",
            ),
        )
        self.mask_method = QComboBox()
        self.mask_method.addItems(["solid_color", "blur", "inpaint"])
        self.mask_margin = QSpinBox()
        self.mask_margin.setRange(0, 60)
        self.image_quality = QSpinBox()
        self.image_quality.setRange(1, 100)
        self.image_quality.setSuffix("%")
        self.copy_originals = QCheckBox("Copy originals into Dataset/images")
        self.copy_originals.setChecked(True)
        self.require_plate = QCheckBox(
            "Skip images without a reliable plate bbox (recommended)"
        )
        self.require_plate.setChecked(True)
        options_form.addRow("Training labels", self.label_policy)
        options_form.addRow("Plate mask", self.mask_method)
        options_form.addRow("Mask margin", self.mask_margin)
        options_form.addRow("JPEG quality", self.image_quality)
        options_form.addRow(self.copy_originals)
        options_form.addRow(self.require_plate)
        root.addWidget(options)

        split = QGroupBox("Leakage-safe split")
        split_form = QFormLayout(split)
        self.split_strategy = QComboBox()
        self.split_strategy.setObjectName("exportSplitStrategy")
        self.split_strategy.addItem("Group hash (70/15/15)", "group_hash")
        self.split_strategy.addItem("Time based", "time")
        self.split_strategy.addItem("Camera based", "camera")
        self.val_years = QLineEdit()
        self.val_years.setPlaceholderText("Example: 2025")
        self.test_years = QLineEdit()
        self.test_years.setPlaceholderText("Example: 2026")
        self.val_cameras = QLineEdit()
        self.val_cameras.setPlaceholderText("Comma-separated camera IDs")
        self.test_cameras = QLineEdit()
        self.test_cameras.setPlaceholderText("Example: RS017")
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(42)
        split_form.addRow("Strategy", self.split_strategy)
        split_form.addRow("Validation years", self.val_years)
        split_form.addRow("Test years", self.test_years)
        split_form.addRow("Validation cameras", self.val_cameras)
        split_form.addRow("Test cameras", self.test_cameras)
        split_form.addRow("Deterministic seed", self.seed)
        root.addWidget(split)

        actions = QHBoxLayout()
        self.btn_preview = QPushButton("Preview Eligible Count")
        self.btn_export = QPushButton("Export Dataset")
        self.btn_index = QPushButton("Build Re-ID Index")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        actions.addWidget(self.btn_preview)
        actions.addWidget(self.btn_export)
        actions.addWidget(self.btn_index)
        actions.addWidget(self.btn_cancel)
        actions.addStretch(1)
        self.status_label = QLabel("Ready.")
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
            "Export results and skip reasons appear here. No network is used."
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
        self.mask_method.setCurrentText(self.ctx.settings.mask.method)
        self.mask_margin.setValue(self.ctx.settings.mask.margin)
        self.image_quality.setValue(self.ctx.settings.export.image_quality)
        self._update_split_fields()

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select an empty or resumable dataset folder",
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
            mask_method=self.mask_method.currentText(),
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
            QMessageBox.warning(self, "Export settings", str(exc))
            return
        eligible = preview["candidates"] - preview["review_rejected"]
        self.status_label.setText(
            f"Eligible: {eligible} / {preview['candidates']} candidate image(s)"
        )

    def _start_export(self) -> None:
        if self.runner.is_running:
            QMessageBox.information(self, "Export", "Another job is already running.")
            return
        output = self.output_path.text().strip()
        if not output:
            QMessageBox.warning(self, "Export", "Choose a dataset folder.")
            return
        try:
            options = self._options()
            preview = self.exporter.preview(options)
        except ValueError as exc:
            QMessageBox.warning(self, "Export settings", str(exc))
            return
        if preview["candidates"] == 0:
            QMessageBox.information(
                self,
                "Export",
                "No completed grouped images match the selected label policy.",
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
        self.log.appendPlainText("Local export started: " + output)
        self._set_running(True)
        signals = self.runner.start(do_export)
        signals.progress.connect(self._progress)
        signals.finished.connect(self._done)
        signals.error.connect(self._error)

    def _start_index(self) -> None:
        if self.runner.is_running:
            QMessageBox.information(self, "Re-ID", "Another job is already running.")
            return
        root = Path(self.output_path.text().strip()).expanduser()
        if not (root / "metadata" / "manifest.jsonl").is_file():
            QMessageBox.warning(
                self, "Re-ID", "Choose a completed Phase 5 dataset folder."
            )
            return
        engine = getattr(self.ctx, "reid", None)
        if engine is None or isinstance(engine, StubReID):
            QMessageBox.information(
                self,
                "Re-ID",
                "Configure an existing ONNX Re-ID model in Settings first.",
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
        self.log.appendPlainText("Local Re-ID indexing started: " + str(root))
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
            self.status_label.setText("Export stopped.")
            return
        state = "Cancelled; resumable" if result.cancelled else "Complete"
        self.status_label.setText(
            f"{state}: exported={result.exported}, skipped={result.skipped}"
        )
        self.log.appendPlainText(
            f"{state}\nImages: {result.exported}/{result.total}\n"
            f"Splits: {result.split_counts}\nPairs: {result.pair_count}\n"
            f"Triplets: {result.triplet_count}\nSkip reasons: {result.skip_reasons}"
        )
        if not result.cancelled:
            QMessageBox.information(
                self, "Export complete", f"Dataset written locally to:\n{result.output_dir}"
            )

    def _error(self, message: str) -> None:
        self._set_running(False)
        self.status_label.setText("Export failed.")
        self.log.appendPlainText("ERROR: " + message)
        QMessageBox.critical(self, "Export failed", message)

    def _index_done(self, result) -> None:
        self._set_running(False)
        if not isinstance(result, IndexBuildResult):
            self.status_label.setText("Re-ID indexing stopped.")
            return
        state = "Cancelled; resumable" if result.cancelled else "Complete"
        self.status_label.setText(
            f"{state}: indexed={result.indexed}, reused={result.reused}, "
            f"failed={result.failed}"
        )
        self.log.appendPlainText(
            f"{state}\nIndexed: {result.indexed}/{result.total}\n"
            f"Reused: {result.reused}\nFailed: {result.failed}\n"
            f"Index: {result.index_path}"
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
            raise ValueError(f"invalid year: {part}") from exc
    return tuple(sorted(set(values)))


def _parse_strings(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            part.strip()
            for part in text.replace(";", ",").split(",")
            if part.strip()
        )
    )
