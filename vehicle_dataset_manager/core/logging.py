"""Centralised logging.

Three file sinks:
    app.log         general application activity (INFO+)
    processing.log  pipeline / per-image progress (DEBUG+)
    error.log       errors only (ERROR+)

A ``LogBroadcaster`` handler is attached so the GUI log viewer (and any other
subsystem) can subscribe to log records in real time without polling files.

Errors inside the pipeline are logged and swallowed by the engine, so a single
bad image never crashes a batch.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Dict

APP_NAME = "vdm.app"
PROCESSING_NAME = "vdm.processing"
ERROR_NAME = "vdm.error"

#: Subscriber signature: callback(level:int, logger_name:str, message:str)
LogCallback = Callable[[int, str, str], None]


class LogBroadcaster(logging.Handler):
    """Dispatch log records to in-process subscribers (e.g. GUI log viewer)."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: list[LogCallback] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: LogCallback) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: LogCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001 - never let formatting crash the pipeline
            msg = record.getMessage()
        with self._lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(record.levelno, record.name, msg)
            except Exception:  # noqa: BLE001
                pass


def _file_handler(path: Path, level: int) -> logging.FileHandler:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    return handler


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> Dict[str, logging.Logger]:
    """Configure and return the app/processing/error loggers.

    Repeated calls are safe: handlers are cleared before being re-added.
    """
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    broadcaster = LogBroadcaster()
    broadcaster.setLevel(level)
    broadcaster.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))

    def _build(name: str, handlers: list[logging.Handler], lvl: int) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.setLevel(lvl)
        logger.propagate = False
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
        for handler in handlers:
            logger.addHandler(handler)
        logger.addHandler(broadcaster)
        return logger

    app_logger = _build(
        APP_NAME, [_file_handler(logs_dir / "app.log", logging.INFO)], level
    )
    processing_logger = _build(
        PROCESSING_NAME, [_file_handler(logs_dir / "processing.log", logging.DEBUG)], logging.DEBUG
    )
    error_logger = _build(
        ERROR_NAME, [_file_handler(logs_dir / "error.log", logging.ERROR)], logging.ERROR
    )

    return {
        "app": app_logger,
        "processing": processing_logger,
        "error": error_logger,
        "broadcaster": broadcaster,
    }


def get_loggers() -> Dict[str, object]:
    """Return the loggers created by :func:`setup_logging` (or no-ops)."""
    import warnings

    loggers = {
        "app": logging.getLogger(APP_NAME),
        "processing": logging.getLogger(PROCESSING_NAME),
        "error": logging.getLogger(ERROR_NAME),
    }
    try:
        return loggers
    except Exception:  # pragma: no cover
        warnings.warn("logging not configured", stacklevel=2)
        return loggers