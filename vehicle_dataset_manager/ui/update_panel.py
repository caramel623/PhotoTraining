"""User-triggered commit/Release checks and confirmed portable EXE updates."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QMessageBox

from vehicle_dataset_manager import __version__
from vehicle_dataset_manager.services.app_update import check_updates, stage_release, MANIFEST, REPO_URL
from vehicle_dataset_manager.services.update_apply import launch_helper


class _Task(QThread):
    result = Signal(object)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn())
        except Exception as exc:
            self.result.emit(exc)


def local_commit():
    if getattr(sys, "frozen", False):
        try:
            data = json.loads((Path(sys.executable).parent / MANIFEST).read_text(encoding="utf-8"))
            return None if data.get("dirty") else data.get("commit")
        except (OSError, ValueError):
            return None
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2], text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()
    except Exception:
        return None


class UpdatePanel(QWidget):
    busy_changed = Signal(bool)
    exit_ready = Signal()

    def __init__(self, ctx, is_busy, parent=None):
        super().__init__(parent)
        self.ctx, self.is_busy = ctx, is_busy
        self.task = None
        self.info = None
        self.helper = None
        self.is_working = False
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll_helper)
        layout = QVBoxLayout(self)
        self.label = QLabel(f"目前版本：{__version__}\n更新來源：{REPO_URL}")
        self.label.setWordWrap(True)
        self.check = QPushButton("線上檢查 Commit 與 Release")
        self.install = QPushButton("下載並更新 EXE…")
        self.install.setEnabled(False)
        layout.addWidget(self.label)
        layout.addWidget(self.check)
        layout.addWidget(self.install)
        self.check.clicked.connect(self._check)
        self.install.clicked.connect(self._download)

    def _start(self, fn, callback):
        if self.is_working or self.is_busy():
            QMessageBox.information(self, "更新", "請等目前工作結束後再更新。")
            return
        self.is_working = True
        self.check.setEnabled(False)
        self.install.setEnabled(False)
        self.busy_changed.emit(True)
        self.task = _Task(fn, self)
        # Deliver result after the QThread has finished, so closing the window
        # cannot destroy a running worker.
        self.pending = None
        self.task.result.connect(self._receive)
        self.callback = callback
        self.task.finished.connect(self._finished)
        self.task.start()

    @Slot(object)
    def _receive(self, result):
        self.pending = result

    @Slot()
    def _finished(self):
        self.is_working = False
        self.busy_changed.emit(False)
        self.check.setEnabled(True)
        self.callback(self.pending)

    def _check(self):
        self.label.setText("正在檢查 GitHub…")
        self._start(lambda: check_updates(__version__, local_commit()), self._checked)

    def _checked(self, info):
        if isinstance(info, Exception) or not isinstance(info, dict):
            self.label.setText(f"檢查失敗：{info}")
            return
        self.info = info
        states = {"identical": "與遠端相同", "ahead": "遠端有新 commit",
                  "behind": "本機領先遠端", "diverged": "分支已分岔", "unknown": "無法比較"}
        self.label.setText(
            f"目前版本：{__version__}\n本機 Commit：{info['current_commit'] or '未知／未提交建置'}\n"
            f"遠端 Commit：{info['commit'] or '查詢失敗'}（{states.get(info['commit_status'], '無法比較')}）\n"
            f"最新 Release：{info['release'] or '無法取得'}\n"
            + ("有新版 Release。" if info["new_release"] else "未發現較新的正式版，不會降級。")
            + "\n" + "\n".join(info["errors"]))
        self.install.setEnabled(bool(info["new_release"] and info["asset"]
                                     and getattr(sys, "frozen", False)))

    def _download(self):
        if not self.info:
            return
        if QMessageBox.question(self, "下載更新",
                "下載並驗證新版套件？下載後會再次詢問是否關閉程式並套用更新。"
                "\n照片、資料庫、設定、模型及 OCR／CUDA 環境都會保留。") != QMessageBox.Yes:
            return
        self.label.setText("正在下載並驗證更新套件，請稍候…")
        self._start(lambda: stage_release(self.info, self.ctx.workspace.cache_dir / "app-updates"),
                    self._downloaded)

    def _downloaded(self, staged):
        if isinstance(staged, Exception) or staged is None:
            self.label.setText(f"下載未完成，程式未變更：{staged}")
            return
        if self.is_busy():
            self.label.setText("工作仍在執行，未套用更新。請稍後重新檢查。")
            return
        if QMessageBox.question(self, "關閉並套用更新",
                f"套件已通過校驗。立即關閉程式並更新至 {self.info['release']}？\n"
                "舊程式會保留於程式資料夾的 .updates；失敗時嘗試回復。") != QMessageBox.Yes:
            self.label.setText("已取消套用；目前程式未變更。")
            return
        try:
            self.directory, self.helper = launch_helper(staged, Path(sys.executable).parent,
                self.ctx.workspace.root, self.info["release"])
        except Exception as exc:
            self.label.setText(f"更新未啟動：{exc}")
            return
        self.is_working = True
        self.busy_changed.emit(True)
        self.label.setText("正在啟動並驗證更新輔助程式，請稍候…")
        self.deadline = time.monotonic() + 90
        self.timer.start()

    def _poll_helper(self):
        if (self.directory / "ready").is_file() and self.helper.poll() is None:
            self.timer.stop()
            self.is_working = False
            self.busy_changed.emit(False)
            self.exit_ready.emit()
        elif self.helper.poll() is not None or time.monotonic() > self.deadline:
            self.timer.stop()
            if self.helper.poll() is None:
                self.helper.terminate()  # Only our waiting, not-yet-authorized helper.
                self.helper.wait(timeout=5)
            self.is_working = False
            self.busy_changed.emit(False)
            self.label.setText("更新輔助程式未就緒，未關閉或修改目前程式。")
