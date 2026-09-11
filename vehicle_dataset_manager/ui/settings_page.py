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

        paths = QGroupBox("路徑")
        pf = QFormLayout(paths)
        self.workspace_edit = QLineEdit()
        self.archive_dir = QLineEdit()
        self.output_dir = QLineEdit()
        self.temp_dir = QLineEdit()
        pf.addRow("工作區", self.workspace_edit)
        pf.addRow("壓縮檔目錄", self.archive_dir)
        pf.addRow("輸出目錄", self.output_dir)
        pf.addRow("暫存目錄", self.temp_dir)
        root.addWidget(paths)

        proc = QGroupBox("處理設定")
        procf = QFormLayout(proc)
        self.batch_size = QSpinBox(); self.batch_size.setRange(1, 1024)
        self.worker_count = QSpinBox(); self.worker_count.setRange(1, 64)
        self.conf_threshold = QSpinBox(); self.conf_threshold.setRange(0, 100)
        self.conf_threshold.setSuffix("%")
        procf.addRow("批次大小", self.batch_size)
        procf.addRow("工作執行緒數", self.worker_count)
        procf.addRow("信心門檻", self.conf_threshold)
        root.addWidget(proc)

        device = QGroupBox("運算裝置（CPU／CUDA）")
        df = QFormLayout(device)
        self.use_cuda = QCheckBox("可用時使用 CUDA（GPU）")
        self.use_cuda.stateChanged.connect(self._on_use_cuda_toggled)
        df.addRow(self.use_cuda)
        self.cuda_index = QLineEdit()
        self.cuda_index.setPlaceholderText(cuda_env.DEFAULT_CUDA_WHEEL_INDEX)
        df.addRow("CUDA 套件來源（wheel index）", self.cuda_index)
        self.device_info = QLabel(); self.device_info.setWordWrap(True)
        df.addRow("狀態", self.device_info)
        btns = QHBoxLayout()
        self.btn_detect = QPushButton("偵測環境")
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
        self.ocr_preset = QComboBox()
        self.ocr_preset.addItem("輕量版（mobile）", "mobile")
        self.ocr_preset.addItem("伺服器版（server）", "server")
        self.ocr_status = QLabel("尚未偵測。")
        self.btn_ocr_detect = QPushButton("偵測 PaddleOCR（.venv-ocr）")
        oc.addRow("車牌信心門檻", self.plate_conf)
        oc.addRow("Paddle 模型預設", self.ocr_preset)
        oc.addRow("背景程序狀態", self.ocr_status)
        oc.addRow("", self.btn_ocr_detect)
        root.addWidget(ocr)

        mask = QGroupBox("車牌遮罩")
        mf = QFormLayout(mask)
        self.mask_method = QComboBox()
        self.mask_method.addItem("純色遮罩", "solid_color")
        self.mask_method.addItem("模糊", "blur")
        self.mask_method.addItem("影像修補", "inpaint")
        self.mask_margin = QSpinBox(); self.mask_margin.setRange(0, 60)
        mf.addRow("處理方式", self.mask_method)
        mf.addRow("邊界（像素）", self.mask_margin)
        root.addWidget(mask)

        models = QGroupBox("模型（可替換；none 代表停用）")
        modf = QFormLayout(models)
        self.model_vehicle = QComboBox()
        self.model_vehicle.addItem("停用", "none")
        self.model_vehicle.addItem("YOLO", "yolo")
        self.model_vehicle_model = QComboBox()
        self.model_vehicle_model.addItems(
            ["yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt"]
        )
        self.model_vehicle_conf = QSpinBox()
        self.model_vehicle_conf.setRange(1, 100); self.model_vehicle_conf.setSuffix("%")
        self.model_plate = QComboBox()
        self.model_plate.addItem("停用", "none")
        self.model_plate.addItem("ONNX", "onnx")
        self.model_plate.addItem("PaddleOCR", "paddle")
        self.plate_status = QLabel("-")
        self.model_ocr = QComboBox()
        self.model_ocr.addItem("停用", "none")
        self.model_ocr.addItem("PaddleOCR", "paddle")
        self.model_reid = QComboBox()
        self.model_reid.addItem("停用", "none")
        self.model_reid.addItem("ONNX", "onnx")
        self.reid_model_path = QLineEdit()
        self.reid_model_path.setPlaceholderText(
            "<workspace>/models/reid.onnx"
        )
        self.reid_input_width = QSpinBox(); self.reid_input_width.setRange(1, 4096)
        self.reid_input_height = QSpinBox(); self.reid_input_height.setRange(1, 4096)
        modf.addRow("車輛偵測器", self.model_vehicle)
        modf.addRow("車輛模型（YOLO）", self.model_vehicle_model)
        modf.addRow("車輛偵測信心", self.model_vehicle_conf)
        modf.addRow("車牌偵測器", self.model_plate)
        modf.addRow("車牌模型狀態", self.plate_status)
        modf.addRow("OCR", self.model_ocr)
        modf.addRow("Re-ID", self.model_reid)
        modf.addRow("Re-ID ONNX 模型", self.reid_model_path)
        modf.addRow("Re-ID 輸入寬度", self.reid_input_width)
        modf.addRow("Re-ID 輸入高度", self.reid_input_height)
        root.addWidget(models)

        root.addStretch(1)
        bottom = QHBoxLayout()
        self.btn_save = QPushButton("儲存設定")
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
        self.ocr_preset.setCurrentIndex(
            max(0, self.ocr_preset.findData(s.ocr.model_preset or "mobile"))
        )
        mask_index = self.mask_method.findData(s.mask.method)
        if mask_index >= 0:
            self.mask_method.setCurrentIndex(mask_index)
        self.mask_margin.setValue(s.mask.margin)
        self.use_cuda.setChecked(s.device.use_cuda)
        self.cuda_index.setText(s.device.cuda_wheel_index or cuda_env.DEFAULT_CUDA_WHEEL_INDEX)
        self.device_info.setText(_device_status(s.device.use_cuda))
        self.model_vehicle.setCurrentIndex(
            max(0, self.model_vehicle.findData(s.models.vehicle_detector))
        )
        vm = s.models.vehicle_model
        existing = [self.model_vehicle_model.itemText(i) for i in range(self.model_vehicle_model.count())]
        if vm not in existing:
            self.model_vehicle_model.addItem(vm)
        self.model_vehicle_model.setCurrentText(vm)
        self.model_vehicle_conf.setValue(int(round(s.models.vehicle_conf * 100)))
        self.model_plate.setCurrentIndex(
            max(0, self.model_plate.findData(s.models.plate_detector))
        )
        self._update_plate_status()
        self.model_ocr.setCurrentIndex(max(0, self.model_ocr.findData(s.models.ocr)))
        self.model_reid.setCurrentIndex(max(0, self.model_reid.findData(s.models.reid)))
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
        s.ocr.model_preset = str(self.ocr_preset.currentData())
        s.mask.method = str(self.mask_method.currentData())
        s.mask.margin = self.mask_margin.value()
        s.device.use_cuda = self.use_cuda.isChecked()
        s.device.cuda_wheel_index = self.cuda_index.text().strip() or cuda_env.DEFAULT_CUDA_WHEEL_INDEX
        s.models.vehicle_detector = str(self.model_vehicle.currentData())
        s.models.vehicle_model = self.model_vehicle_model.currentText()
        s.models.vehicle_conf = self.model_vehicle_conf.value() / 100.0
        s.models.plate_detector = str(self.model_plate.currentData())
        s.models.ocr = str(self.model_ocr.currentData())
        s.models.reid = str(self.model_reid.currentData())
        s.reid.model_path = self.reid_model_path.text().strip() or None
        s.reid.input_width = self.reid_input_width.value()
        s.reid.input_height = self.reid_input_height.value()
        self.ctx.save_settings()
        self.ctx.build_detectors()
        self.device_info.setText(_device_status(s.device.use_cuda))
        self._update_plate_status()
        QMessageBox.information(self, "設定", "設定已儲存。")

    def _update_plate_status(self) -> None:
        """Report ONNX plate model availability for the onnx slot."""
        if self.model_plate.currentData() != "onnx":
            self.plate_status.setText("-")
            return
        from vehicle_dataset_manager.detection.onnx_plate_detector import (
            DEFAULT_MODEL_PATH,
        )

        if not DEFAULT_MODEL_PATH.is_file():
            self.plate_status.setText("缺少模型：" + str(DEFAULT_MODEL_PATH))
            self.plate_status.setStyleSheet("color: #b35900;")
            return
        try:
            import onnxruntime  # noqa: F401

            self.plate_status.setText("可使用：" + str(DEFAULT_MODEL_PATH))
            self.plate_status.setStyleSheet("color: #1a7f37;")
        except ImportError:
            self.plate_status.setText("未安裝 onnxruntime（請執行 pip install onnxruntime）")
            self.plate_status.setStyleSheet("color: #b35900;")

    def _detect_ocr(self) -> None:
        from vehicle_dataset_manager.ocr.paddle_client import default_ocr_python

        self.ocr_status.setText("偵測中…")
        self.btn_ocr_detect.setEnabled(False)
        py = default_ocr_python()
        if py.exists():
            self.ocr_status.setText(
                "可使用：" + str(py)
            )
            self.ocr_status.setStyleSheet("color: #1a7f37;")
        else:
            self.ocr_status.setText(
                "找不到：" + str(py)
                + "  （建立方式：py -3.13 -m venv .venv-ocr；pip install paddlepaddle paddleocr）"
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
        action = "環境偵測" if kind == "detect" else "安裝"
        self.cuda_log.appendPlainText("-- 開始" + action + " --")
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
            self.cuda_log.appendPlainText("[錯誤] " + str(result))
            self.device_info.setText("執行失敗：" + str(result))
            return
        if kind == "detect" and isinstance(result, CudaEnvironmentReport):
            self._show_report(result)
        elif kind == "install":
            ok = bool(getattr(result, "success", False))
            code = int(getattr(result, "returncode", -1))
            if ok:
                self.cuda_log.appendPlainText("[成功] 安裝完成。請重新啟動程式以載入 CUDA 版 PyTorch。")
                self.device_info.setText("已安裝 CUDA 版 PyTorch（需重新啟動生效）")
                self._detect()
            else:
                self.cuda_log.appendPlainText("[失敗] pip 回傳碼：" + str(code))
                tail = str(getattr(result, "output_tail", ""))
                if tail:
                    self.cuda_log.appendPlainText(tail)
                self.device_info.setText("安裝失敗（回傳碼 " + str(code) + "）")

    def _show_report(self, report: CudaEnvironmentReport) -> None:
        self.device_info.setText(_STATUS_LABELS.get(report.status, report.status.value))
        self.cuda_log.appendPlainText("GPU：" + (report.gpu_name or "未偵測到 NVIDIA GPU"))
        self.cuda_log.appendPlainText("CUDA 可用：" + ("是" if report.cuda_available else "否"))
        torch_line = "PyTorch：" + (report.torch_version or "未安裝")
        if report.torch_installed:
            if report.torch_is_cpu_build:
                torch_line = torch_line + "（CPU 版）"
            else:
                torch_line = torch_line + "  (CUDA " + str(report.torch_cuda_version) + ")"
        self.cuda_log.appendPlainText(torch_line)
        if report.nvidia.found:
            self.cuda_log.appendPlainText("NVIDIA 驅動程式：" + report.nvidia.driver_version)
        self.cuda_log.appendPlainText("狀態：" + _STATUS_LABELS.get(report.status, report.status.value))
        self.cuda_log.appendPlainText(report.message)
        if report.pip_command:
            self.cuda_log.appendPlainText("建議指令：" + report.pip_command)


def _device_status(use_cuda: bool = False) -> str:
    if not use_cuda:
        return "CPU 模式（CUDA 已停用；可按「偵測環境」查看詳細資訊）"
    from vehicle_dataset_manager.detection.device import (
        cuda_available,
        gpu_name,
        torch_info,
    )

    if cuda_available():
        return f"CUDA 可用：是（{gpu_name()}）"
    info = torch_info()
    extra = f"［{info}］" if info else "（未安裝 PyTorch）"
    return f"CUDA 可用：否（CPU 模式）{extra}"
