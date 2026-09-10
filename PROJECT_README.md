# Vehicle Dataset Manager

Windows-local tool that builds a **manually-verifiable Vehicle / Motorcycle
Re-ID training dataset** from historical speed-camera photos (ZIP / 7Z).

> Core principle: **plate OCR is a weak-supervision / grouping signal only.**
> The Re-ID model must learn *vehicle appearance* (body, lights, frame, cargo
> box, wheels, exhaust, stickers, mods) — never the plate text. Plates may be
> obscured, vary across years/cameras/lighting.

- Fully local: no cloud APIs, no telemetry, no image upload.
- Originals are never modified — every crop/mask/annotation is a new file.
- Resumable: per-image status lives in SQLite; a crash resumes where it stopped.
- Pluggable models: detector / OCR / Re-ID are interfaces, swappable later.

The full requirements & design spec are in [`README.md`](README.md) (Traditional
Chinese). This file documents the **implemented** project.

---

## Phase status

| Phase | Scope | Status |
|-------|-------|--------|
| **1** | Core + SQLite + GUI skeleton + Archive Manager + resumable pipeline | **Done** |
| **2** | Image processing + vehicle/plate detection (YOLO, CPU/CUDA) + CUDA env detect/download (2.5) | **Done** |
| **3** | OCR (PaddleOCR sidecar) + plate quality grading + plate grouping | **Done (2026-09-09; see PHASE3.md)** — bugs fixed + data requeued (2026-09-10; see PHASE3_WRAPUP.md) — sample run: 200 random images with real models, 200/200 OK (see PROGRESS.md §7) |
| **4** | Manual review UI + merge/split + per-image labels | **Done (2026-09-10; 19 Phase 4 tests, 111 total)** |
| 5 | Dataset export (crops, plate-masked Re-ID crops, splits, pairs) | next |
| 6 | Re-ID integration (embedding engine, AI02 hand-off) | next |

Phase 1–3 run end-to-end: import ZIP/7Z → extract → register images as PENDING →
process (metadata, YOLO vehicle detect + crop, PaddleOCR plate OCR via a separate
Python 3.13 sidecar venv `.venv-ocr`, quality grading HIGH/MED/LOW, plate grouping) →
resumable/cancellable. Stub engines remain as automatic fallback when a model/venv
is unavailable.

---

## Project layout

```
vehicle_dataset_manager/
├── main.py                 # entry point
├── app_context.py          # shared services + repositories
├── core/                   # enums, workspace layout, settings, logging
├── database/               # SQLite schema + migrations + repositories
├── archive/                # zip/7z detect/list/extract/verify (CLI + py7zr)
├── pipeline/               # resumable batch engine + pluggable stages
├── detection/              # BaseDetector + stubs + YOLO + cuda_env (Phase 2/2.5)
├── ocr/                    # BaseOcr + stub + PaddleOCR client/server (Phase 3)
├── reid/                   # BaseReID + stub (Phase 6)
├── services/               # plate normalization, metadata parser, import
├── workers/                # QRunnable / QThreadPool primitives
└── ui/                     # main window + 8 pages
tests/                      # pytest suite
build_windows.ps1           # PyInstaller build
```

## Install

```powershell
cd D:\CPTR-CODE\PhotoTraining
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt   # pytest
```

Tested on **Python 3.14** with PySide6 6.11, OpenCV (headless) 5.0, numpy 2.5,
py7zr 1.1, Pillow 12, pydantic 2.13. If 7-Zip is installed its CLI is used for
7z (auto-detected); otherwise `py7zr` handles 7z. ZIP always uses stdlib `zipfile`.

## Run

```powershell
python run.py                 # default workspace: Documents\VehicleDatasetManager
python run.py --workspace D:\data\vdm   # custom workspace
```

Or the installed entry point: `vehicle-dataset-manager`.

Workflow: **Import** page → add ZIP/7Z (or a folder of them) → *Import & Register*
→ **Processing** page → *Process All Pending* (can Cancel / Resume / Retry Failed).

## Test

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest
```

Covers: archive detect/list/extract/cancel, plate normalization + fuzzy,
filename/metadata parsing, DB CRUD, image lifecycle, job counters, merge/split,
and the full import→process→resume→cancel pipeline.

> If you see `PermissionError ... pytest-of-user`, the default pytest temp dir is
> locked; re-run with `python -m pytest --basetemp <fresh-folder>`.

## Build (Windows)

```powershell
.\build_windows.ps1                 # onedir → dist\VehicleDatasetManager\
.\build_windows.ps1 -OneFile        # single exe
.\build_windows.ps1 -Clean          # clean build/ dist/ first
```

Model files are **not** bundled — drop them in `models/` beside the exe.

---

## Data safety

- No network calls, telemetry, or cloud sync by default.
- Original archives/images are read-only to the app.
- Every derived artefact (crops, masks, annotations) is a new file.
- All results restorable from SQLite (`<workspace>\database\vehicle_dataset.db`).

## Workspace layout

```
<workspace>/
├── database/     # vehicle_dataset.db (+ -wal/-shm)
├── archives/  extracted/  crops/  plates/  reid/  exports/
├── cache/  thumbs/  models/  logs/
└── settings.json
```

`logs/` holds `app.log`, `processing.log`, `error.log` (viewable in the Logs tab).
