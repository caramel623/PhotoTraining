import logging
import os
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from vehicle_dataset_manager.core.logging import APP_NAME, LogBroadcaster
from vehicle_dataset_manager.ui.logs_page import LogsPage


def test_background_log_flood_is_bounded_and_gui_only_updates_when_flushed():
    app = QApplication.instance() or QApplication([])
    logger = logging.getLogger(APP_NAME)
    handler = LogBroadcaster()
    old_handlers = logger.handlers[:]
    logger.handlers = [handler]
    try:
        page = LogsPage(SimpleNamespace())
        main_thread = threading.get_ident()
        updates = []
        page.view.textChanged.connect(lambda: updates.append(threading.get_ident()))
        def emit():
            for i in range(6000):
                handler.handle(logging.LogRecord(APP_NAME, 40, "", 0, f"synthetic {i}", (), None))
        worker = threading.Thread(target=emit)
        worker.start()
        worker.join()
        assert not updates
        assert len(page._pending) == 5000
        for _ in range(20):
            page._flush_logs()
        assert updates and set(updates) == {main_thread}
        assert "synthetic 5999" in page.view.toPlainText()
        assert page.view.document().blockCount() <= 5000
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert handler._callbacks == []
    finally:
        logger.handlers = old_handlers
