"""Settings page: paths, models, processing, OCR, mask, export, device (CPU/CUDA).

The Device group exposes the "Use CUDA" toggle plus a live environment
*detect* / *download-install* workflow backed by ``detection/cuda_env.py``.
Detection and installation run on a background QThread so the slow torch import
/ pip download never blocks the GUI.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vehicle_dataset_manager.app_context import AppContext
from vehicle_dataset_manager.detection import cuda_env
from vehicle_dataset_manager.detection.cuda_env import CudaEnvironmentReport, CudaStatus


class _CudaWorker(QThread):
    """Run a ``fn(line_cb)`` callable off the GUI thread."""

    line = Signal(str)
    done = Signal(object)

    def __init__(self, fn: Callable) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        def line_cb(line: str) -> None:
            self.line.emit(str(line))

        try:
            self.done.emit(self._fn(line_cb))
        except Exception as exc:  # noqa: BLE001 - report, don't crash the GUI
            self.done.emit(exc)


_STATUS_LABELS = {
    CudaStatus.READY: "CUDA 已就緒",
    CudaStatus.TORCH_CPU_BUILD: "需安裝 CUDA 版 PyTorch",
    CudaStatus.NO_TORCH: "未安裝 PyTorch",
    CudaStatus.NO_GPU: "未偵測到 NVIDIA GPU／驅動",
    CudaStatus.CUDA_UNAVAILABLE: "CUDA 不可用（驅動／版本不相容）",
}


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._worker = None
        self._last_kind = ""
        self._build_ui()
        self._load()

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        paths = QGroupBox("Paths")
        pf = QFormLayout(paths)
        self.workspace_edit = QLineEdit()
        self.archive_dir = QLineEdit()
        self.output_dir = QLineEdit()
        self.temp_dir = QLineEdit()
        pf.addRow("Workspace", self.workspace_edit)
        pf.addRow("Archive directory", self.archive_dir)
        pf.addRow("Output directory", self.output_dir)
        pf.addRow("Temporary directory", self.temp_dir)
        root.addWidget(paths)

        proc = QGroupBox("Processing")
        procf = QFormLayout(proc)
        self.batch_size = QSpinBox(); self.batch_size.setRange(1, 1024)
        self.worker_count = QSpinBox(); self.worker_count.setRange(1, 64)
        self.conf_threshold = QSpinBox(); self.conf_threshold.setRange(0, 100)
        self.conf_threshold.setSuffix("%")
        procf.addRow("Batch size", self.batch_size)
        procf.addRow("Worker count", self.worker_count)
        procf.addRow("Confidence threshold", self.conf_threshold)
        root.addWidget(proc)

        device = QGroupBox("Device (CPU / CUDA)")
        df = QFormLayout(device)
        self.use_cuda = QCheckBox("Use CUDA (GPU) when available")
        self.use_cuda.stateChanged.connect(self._on_use_cuda_toggled)
        df.addRow(self.use_cuda)
        self.cuda_index = QLineEdit()
        self.cuda_index.setPlaceholderText(cuda_env.DEFAULT_CUDA_WHEEL_INDEX)
        df.addRow("CUDA build (wheel index)", self.cuda_index)
        self.device_info = QLabel(); self.device_info.setWordWrap(True)
        df.addRow("Status", self.device_info)
        btns = QHBoxLayout()
        self.btn_detect = QPushButton("偵測環境 (Detect)")
        self.btn_install = QPushButton("下載/安裝 CUDA 版 PyTorch")
        btns.addWidget(self.btn_detect)
        btns.addWidget(self.btn_install)
        btns.addStretch(1)
        df.addRow(btns)
        self.cuda_log = QPlainTextEdit()
        self.cuda_log.setReadOnly(True)
        self.cuda_log.setFixedHeight(120)
        self.cuda_log.setPlaceholderText("環境偵測與安裝日誌將顯示於此。")
        df.addRow(self.cuda_log)
        root.addWidget(device)

        ocr = QGroupBox("OCR")
        oc = QFormLayout(ocr)
        self.plate_conf = QSpinBox(); self.plate_conf.setRange(0, 100); self.plate_conf.setSuffix("%")
        self.ocr_preset = QComboBox(); self.ocr_preset.addItems(["mobile", "server"])
        self.ocr_status = QLabel("Not detected yet.")
        self.btn_ocr_detect = QPushButton("Detect PaddleOCR (.venv-ocr)")
        oc.addRow("Plate confidence threshold", self.plate_conf)
        oc.addRow("Paddle model preset", self.ocr_preset)
        oc.addRow("Sidecar status", self.ocr_status)
        oc.addRow("", self.btn_ocr_detect)
        root.addWidget(ocr)

        mask = QGroupBox("Plate Mask")
        mf = QFormLayout(mask)
        self.mask_method = QComboBox(); self.mask_method.addItems(["solid_color", "blur", "inpaint"])
        self.mask_margin = QSpinBox(); self.mask_margin.setRange(0, 60)
        mf.addRow("Method", self.mask_method)
        mf.addRow("Margin (px)", self.mask_margin)
        root.addWidget(mask)

        models = QGroupBox("Models (pluggable; 'none' = stub)")
        modf = QFormLayout(models)
        self.model_vehicle = QComboBox(); self.model_vehicle.addItems(["none", "yolo"])
        self.model_vehicle_model = QComboBox()
        self.model_vehicle_model.addItems(
            ["yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt"]
        )
        self.model_vehicle_conf = QSpinBox()
        self.model_vehicle_conf.setRange(1, 100); self.model_vehicle_conf.setSuffix("%")
        self.model_plate = QComboBox(); self.model_plate.addItems(["none", "onnx", "paddle"])
        self.plate_status = QLabel("-")
        self.model_ocr = QComboBox(); self.model_ocr.addItems(["none", "paddle"])
        self.model_reid = QComboBox(); self.model_reid.addItems(["none", "onnx"])
        self.reid_model_path = QLineEdit()
        self.reid_model_path.setPlaceholderText(
            "<workspace>/models/reid.onnx"
        )
        self.reid_input_width = QSpinBox(); self.reid_input_width.setRange(1, 4096)
        self.reid_input_height = QSpinBox(); self.reid_input_height.setRange(1, 4096)
        modf.addRow("Vehicle detector", self.model_vehicle)
        modf.addRow("Vehicle model (YOLO)", self.model_vehicle_model)
        modf.addRow("Vehicle confidence", self.model_vehicle_conf)
        modf.addRow("Plate detector", self.model_plate)
        modf.addRow("Plate model status", self.plate_status)
        modf.addRow("OCR", self.model_ocr)
        modf.addRow("Re-ID", self.model_reid)
        modf.addRow("Re-ID ONNX model", self.reid_model_path)
        modf.addRow("Re-ID input width", self.reid_input_width)
        modf.addRow("Re-ID input height", self.reid_input_height)
        root.addWidget(models)

        root.addStretch(1)
        bottom = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        bottom.addWidget(self.btn_save)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self.btn_save.clicked.connect(self._save)
        self.btn_detect.clicked.connect(self._detect)
        self.btn_install.clicked.connect(self._install)
        self.btn_ocr_detect.clicked.connect(self._detect_ocr)

    def _load(self) -> None:
        s = self.ctx.settings
        self.workspace_edit.setText(s.paths.workspace or str(self.ctx.workspace.root))
        self.archive_dir.setText(s.paths.archive_dir or "")
        self.output_dir.setText(s.paths.output_dir or "")
        self.temp_dir.setText(s.paths.temp_dir or "")
        self.batch_size.setValue(s.processing.batch_size)
        self.worker_count.setValue(s.processing.worker_count)
        self.conf_threshold.setValue(int(round(s.processing.confidence_threshold * 100)))
        self.plate_conf.setValue(int(round(s.ocr.plate_confidence_threshold * 100)))
        self.ocr_preset.setCurrentText(s.ocr.model_preset or "mobile")
        self.mask_method.setCurrentText(s.mask.method)
        self.mask_margin.setValue(s.mask.margin)
        self.use_cuda.setChecked(s.device.use_cuda)
        self.cuda_index.setText(s.device.cuda_wheel_index or cuda_env.DEFAULT_CUDA_WHEEL_INDEX)
        self.device_info.setText(_device_status())
        self.model_vehicle.setCurrentText(s.models.vehicle_detector)
        vm = s.models.vehicle_model
        existing = [self.model_vehicle_model.itemText(i) for i in range(self.model_vehicle_model.count())]
        if vm not in existing:
            self.model_vehicle_model.addItem(vm)
        self.model_vehicle_model.setCurrentText(vm)
        self.model_vehicle_conf.setValue(int(round(s.models.vehicle_conf * 100)))
        self.model_plate.setCurrentText(s.models.plate_detector)
        self._update_plate_status()
        self.model_ocr.setCurrentText(s.models.ocr)
        self.model_reid.setCurrentText(s.models.reid)
        self.reid_model_path.setText(s.reid.model_path or "")
        self.reid_input_width.setValue(s.reid.input_width)
        self.reid_input_height.setValue(s.reid.input_height)
        if s.device.use_cuda:
            self._detect()

    def _save(self) -> None:
        s = self.ctx.settings
        s.paths.workspace = self.workspace_edit.text().strip() or None
        s.paths.archive_dir = self.archive_dir.text().strip() or None
        s.paths.output_dir = self.output_dir.text().strip() or None
        s.paths.temp_dir = self.temp_dir.text().strip() or None
        s.processing.batch_size = self.batch_size.value()
        s.processing.worker_count = self.worker_count.value()
        s.processing.confidence_threshold = self.conf_threshold.value() / 100.0
        s.ocr.plate_confidence_threshold = self.plate_conf.value() / 100.0
        s.ocr.model_preset = self.ocr_preset.currentText()
        s.mask.method = self.mask_method.currentText()
        s.mask.margin = self.mask_margin.value()
        s.device.use_cuda = self.use_cuda.isChecked()
        s.device.cuda_wheel_index = self.cuda_index.text().strip() or cuda_env.DEFAULT_CUDA_WHEEL_INDEX
        s.models.vehicle_detector = self.model_vehicle.currentText()
        s.models.vehicle_model = self.model_vehicle_model.currentText()
        s.models.vehicle_conf = self.model_vehicle_conf.value() / 100.0
        s.models.plate_detector = self.model_plate.currentText()
        s.models.ocr = self.model_ocr.currentText()
        s.models.reid = self.model_reid.currentText()
        s.reid.model_path = self.reid_model_path.text().strip() or None
        s.reid.input_width = self.reid_input_width.value()
        s.reid.input_height = self.reid_input_height.value()
        self.ctx.save_settings()
        self.ctx.build_detectors()
        self.device_info.setText(_device_status())
        self._update_plate_status()
        QMessageBox.information(self, "Settings", "Saved.")

    def _update_plate_status(self) -> None:
        """Report ONNX plate model availability for the onnx slot."""
        if self.model_plate.currentText() != "onnx":
            self.plate_status.setText("-")
            return
        from vehicle_dataset_manager.detection.onnx_plate_detector import (
            DEFAULT_MODEL_PATH,
        )

        if not DEFAULT_MODEL_PATH.is_file():
            self.plate_status.setText("MISSING: " + str(DEFAULT_MODEL_PATH))
            self.plate_status.setStyleSheet("color: #b35900;")
            return
        try:
            import onnxruntime  # noqa: F401

            self.plate_status.setText("READY: " + str(DEFAULT_MODEL_PATH))
            self.plate_status.setStyleSheet("color: #1a7f37;")
        except ImportError:
            self.plate_status.setText("onnxruntime not installed (pip install onnxruntime)")
            self.plate_status.setStyleSheet("color: #b35900;")

    def _detect_ocr(self) -> None:
        from vehicle_dataset_manager.ocr.paddle_client import default_ocr_python

        self.ocr_status.setText("Detecting...")
        self.btn_ocr_detect.setEnabled(False)
        py = default_ocr_python()
        if py.exists():
            self.ocr_status.setText(
                "READY: " + str(py)
            )
            self.ocr_status.setStyleSheet("color: #1a7f37;")
        else:
            self.ocr_status.setText(
                "NOT FOUND: " + str(py)
                + "  (create: py -3.13 -m venv .venv-ocr; pip install paddlepaddle paddleocr)"
            )
            self.ocr_status.setStyleSheet("color: #b35900;")
        self.btn_ocr_detect.setEnabled(True)

    # -- CUDA workflow ----------------------------------------------------
    def _index(self) -> str:
        return self.cuda_index.text().strip() or cuda_env.DEFAULT_CUDA_WHEEL_INDEX

    def _on_use_cuda_toggled(self, state: int) -> None:
        if state == 2:  # Qt.Checked
            self._detect()

    def _set_busy(self, busy: bool) -> None:
        self.btn_detect.setEnabled(not busy)
        self.btn_install.setEnabled(not busy)

    def _run_worker(self, kind: str, fn: Callable) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._last_kind = kind
        self._set_busy(True)
        self.cuda_log.appendPlainText("-- " + kind + " start --")
        worker = _CudaWorker(fn)
        worker.line.connect(self._on_line)
        worker.done.connect(self._on_done)
        self._worker = worker
        worker.start()

    def _detect(self) -> None:
        self._run_worker("detect", lambda cb: cuda_env.detect_environment(self._index()))

    def _install(self) -> None:
        self._run_worker("install", lambda cb: cuda_env.install_cuda_torch(self._index(), line_cb=cb))

    def _on_line(self, line: str) -> None:
        self.cuda_log.appendPlainText(line)

    def _on_done(self, result: object) -> None:
        self._set_busy(False)
        kind = self._last_kind
        if isinstance(result, Exception):
            self.cuda_log.appendPlainText("[ERROR] " + str(result))
            self.device_info.setText("執行失敗：" + str(result))
            return
        if kind == "detect" and isinstance(result, CudaEnvironmentReport):
            self._show_report(result)
        elif kind == "install":
            ok = bool(getattr(result, "success", False))
            code = int(getattr(result, "returncode", -1))
            if ok:
                self.cuda_log.appendPlainText("[OK] 安裝完成。請重新啟動程式以載入 CUDA 版 PyTorch。")
                self.device_info.setText("已安裝 CUDA 版 PyTorch（需重新啟動生效）")
                self._detect()
            else:
                self.cuda_log.appendPlainText("[FAIL] pip return code " + str(code))
                tail = str(getattr(result, "output_tail", ""))
                if tail:
                    self.cuda_log.appendPlainText(tail)
                self.device_info.setText("安裝失敗（回傳碼 " + str(code) + "）")

    def _show_report(self, report: CudaEnvironmentReport) -> None:
        self.device_info.setText(_STATUS_LABELS.get(report.status, report.status.value))
        self.cuda_log.appendPlainText("GPU: " + (report.gpu_name or "(no NVIDIA GPU detected)"))
        self.cuda_log.appendPlainText("CUDA available: " + ("Yes" if report.cuda_available else "No"))
        torch_line = "torch: " + (report.torch_version or "not installed")
        if report.torch_installed:
            if report.torch_is_cpu_build:
                torch_line = torch_line + "  (CPU build)"
            else:
                torch_line = torch_line + "  (CUDA " + str(report.torch_cuda_version) + ")"
        self.cuda_log.appendPlainText(torch_line)
        if report.nvidia.found:
            self.cuda_log.appendPlainText("NVIDIA driver: " + report.nvidia.driver_version)
        self.cuda_log.appendPlainText("status: " + report.status.value)
        self.cuda_log.appendPlainText(report.message)
        if report.pip_command:
            self.cuda_log.appendPlainText("suggested: " + report.pip_command)


def _device_status() -> str:
    from vehicle_dataset_manager.detection.device import (
        cuda_available,
        gpu_name,
        torch_info,
    )

    if cuda_available():
        return f"CUDA available: Yes  ({gpu_name()})"
    info = torch_info()
    extra = f"  [{info}]" if info else "  (PyTorch not installed)"
    return f"CUDA available: No (CPU mode){extra}"
