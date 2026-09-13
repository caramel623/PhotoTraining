import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from vehicle_dataset_manager.services.import_service import ImportService
from vehicle_dataset_manager.database.repositories import ImageRepository, VehicleRepository


def test_backup_clear_and_reimport(db, workspace, sample_zip):
    service = ImportService(db, workspace)
    service.import_archive(sample_zip)
    db.execute("PRAGMA foreign_keys=ON")
    files = {Path(r[0]): Path(r[0]).read_bytes() for r in db.query("SELECT source_path FROM images")}
    schema = [tuple(r) for r in db.query("SELECT * FROM schema_version")]
    counts = {r[0]: db.query_one(f'SELECT COUNT(*) FROM "{r[0]}"')[0] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    backup = db.backup_and_clear()
    assert backup.is_file() and backup.parent == workspace.database_dir / "backups"
    with sqlite3.connect(backup) as restored:
        assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        for table, count in counts.items():
            assert restored.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == count
            if table != "schema_version":
                assert db.query_one(f'SELECT COUNT(*) FROM "{table}"')[0] == 0
    assert [tuple(r) for r in db.query("SELECT * FROM schema_version")] == schema
    assert all(p.read_bytes() == content for p, content in files.items())
    assert service.import_archive(sample_zip).images_new == 4


def test_backup_failure_never_clears(db, workspace, sample_zip, monkeypatch):
    ImportService(db, workspace).import_archive(sample_zip)
    def fail(*args, **kwargs):
        raise OSError("synthetic backup failure")
    monkeypatch.setattr(sqlite3, "connect", fail)
    with pytest.raises(OSError, match="backup failure"):
        db.backup_and_clear()
    assert db.query_one("SELECT COUNT(*) FROM images")[0] == 4


def test_clear_failure_rolls_back_all_tables(db, workspace, sample_zip):
    ImportService(db, workspace).import_archive(sample_zip)
    db.execute("CREATE TRIGGER prevent_clear BEFORE DELETE ON images BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    db.commit()
    before = [tuple(r) for r in db.query("SELECT * FROM import_jobs")]
    with pytest.raises(sqlite3.DatabaseError, match="synthetic failure"):
        db.backup_and_clear()
    assert db.query_one("SELECT COUNT(*) FROM images")[0] == 4
    assert [tuple(r) for r in db.query("SELECT * FROM import_jobs")] == before
    assert list((workspace.database_dir / "backups").glob("*.sqlite3"))


def test_unknown_schema_refuses_clear(db):
    db.execute("CREATE TABLE unexpected_data(value TEXT)")
    db.commit()
    with pytest.raises(RuntimeError, match="結構"):
        db.backup_and_clear()


def test_project_page_confirmation_and_busy_guard(db, workspace, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    from vehicle_dataset_manager.ui.project_page import ProjectPage
    app = QApplication.instance() or QApplication([])
    ctx = SimpleNamespace(db=db, workspace=workspace,
        images=ImageRepository(db), vehicles=VehicleRepository(db),
        settings=SimpleNamespace(device=SimpleNamespace(use_cuda=False)),
        archive_manager=SimpleNamespace(sevenz_cli=None))
    calls = []
    class Runner:
        is_running = False
        def start(self, fn, **callbacks):
            calls.append(True)
            callbacks['on_finished'](fn(None, None))
    runner = Runner()
    page = ProjectPage(ctx, runner)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("wrong", True))
    page._clear_database()
    assert not calls
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("清除資料庫", False))
    page._clear_database()
    assert not calls
    runner.is_running = True
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("清除資料庫", True))
    page._clear_database()
    assert not calls
    runner.is_running = False
    page._clear_database()
    assert calls == [True] and page.btn_clear_database.isEnabled()
    page.close()
