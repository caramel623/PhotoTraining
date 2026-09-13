"""Qt background-worker primitives.

``Worker`` wraps an arbitrary callable so long-running work (import,
processing) runs off the GUI thread. ``JobRunner`` owns a ``QThreadPool`` and a
single active worker and exposes ``start`` / ``stop``. Progress, logs, and the
final result are delivered to the GUI via Qt signals.

The cancel flag is a plain ``bool`` list (thread-safe enough for a flag) that
the engine/workers poll cooperatively.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, QThreadPool

log = logging.getLogger("vdm.app")


class WorkerSignals(QObject):
    started = Signal()
    progress = Signal(int, int, str)      # done, total, current
    log = Signal(int, str)                # levelno, message
    finished = Signal(object)             # result object
    error = Signal(str)


class _Cancellable(Exception):
    pass


class Worker(QRunnable):
    """Run ``fn(progress_cb, should_cancel)`` in a background thread."""

    def __init__(self, fn: Callable, *args: Any, cancel_flag: Optional[List[bool]] = None) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.signals = WorkerSignals()
        self.cancel_flag = cancel_flag if cancel_flag is not None else [False]
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - Qt override
        self.signals.started.emit()
        try:
            result = self.fn(self._progress_cb, self._should_cancel, *self.args)
            self.signals.finished.emit(result)
        except _Cancellable:
            self.signals.finished.emit(None)
        except Exception as exc:  # noqa: BLE001
            log.exception("worker failed")
            self.signals.error.emit(str(exc))

    # -- callbacks passed to the worker function ------------------------
    def _progress_cb(self, done: int, total: int, current: str) -> None:
        self.signals.progress.emit(int(done), int(total), str(current))

    def _should_cancel(self) -> bool:
        return bool(self.cancel_flag[0])


class JobRunner:
    """Manage a pool and a single active worker with start/stop."""

    def __init__(self, max_workers: int = 1) -> None:
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max_workers)
        self._worker: Optional[Worker] = None
        self._running = False
        self.signals: Optional[WorkerSignals] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, fn: Callable, *args: Any, on_finished=None, on_error=None, on_progress=None) -> WorkerSignals:
        if self.is_running:
            raise RuntimeError("a job is already running")
        self._worker = Worker(fn, *args)
        self.signals = self._worker.signals
        self.signals.finished.connect(self._job_finished)
        self.signals.error.connect(self._job_finished)
        # Register receivers before scheduling even an immediately finishing job.
        if on_finished is not None:
            self.signals.finished.connect(on_finished)
        if on_error is not None:
            self.signals.error.connect(on_error)
        if on_progress is not None:
            self.signals.progress.connect(on_progress)
        self._running = True
        self.pool.start(self._worker)
        return self.signals

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel_flag[0] = True

    def _job_finished(self, *_args: Any) -> None:
        self._running = False
