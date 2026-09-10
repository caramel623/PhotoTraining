import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from vehicle_dataset_manager.workers.base import JobRunner


class _Pool:
    def __init__(self):
        self.worker = None

    def start(self, worker):
        self.worker = worker


def test_job_runner_tracks_state_without_qrunnable_is_running():
    app = QApplication.instance() or QApplication([])
    runner = JobRunner()
    runner.pool = _Pool()
    signals = runner.start(lambda progress, cancel: None)
    assert runner.is_running
    runner.stop()
    assert runner.pool.worker.cancel_flag[0] is True
    signals.finished.emit(None)
    assert not runner.is_running

    second = runner.start(lambda progress, cancel: None)
    assert runner.is_running
    second.error.emit("expected")
    assert not runner.is_running
