# Phase 5 — Dataset Export

> 完成日期：2026-09-10  
> 原則：全本地、原始照片永不修改、預設只採人工確認標籤、可取消續跑。

## 完成範圍

- `vehicle_dataset_manager/exporting/masking.py`：plate bbox 轉為 vehicle-crop 相對座標，支援純色、模糊與 inpaint 遮罩。
- `vehicle_dataset_manager/exporting/splitting.py`：group hash、time、camera 三種可重現切分；同一車輛群組永不跨 split。
- `vehicle_dataset_manager/exporting/pairing.py`：positive/negative pairs 與 triplets；正樣本優先跨鏡頭／跨時間，負樣本優先困難案例。
- `vehicle_dataset_manager/exporting/dataset_exporter.py`：安全預覽、完整輸出、取消、續跑、增量重用及匯出紀錄。
- `vehicle_dataset_manager/ui/export_page.py`：輸出路徑、標籤策略、遮罩與 split 設定、預覽、背景匯出、取消／續跑。
- DB schema v4 新增 `images.vehicle_crop_bbox`，pipeline 會保存實際裁切座標。

## 安全預設

- 只採 `human_verified` 車輛群組。
- 預設要求可靠 plate bbox，無法安全定位車牌時跳過該張。
- 原始照片只讀；輸出 original 是 byte-for-byte 複本。
- manifest 與 CSV 不寫入來源絕對路徑。
- 不同設定不可覆寫既有匯出目錄。
- 續跑只重用來源時間與標籤狀態仍相符、且輸出檔存在的項目。

## 輸出結構

~~~text
Dataset/
├── images/
├── vehicle_crops/
├── plate_crops/
├── reid_crops/
├── metadata/
│   ├── images.csv
│   ├── vehicles.csv
│   ├── labels.csv
│   ├── manifest.jsonl
│   ├── pairs.csv
│   └── triplets.csv
├── splits/
│   ├── train.csv
│   ├── val.csv
│   └── test.csv
├── export_config.json
└── export_state.json
~~~

## 驗證

~~~powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest `
  tests/test_job_runner.py `
  tests/test_dataset_exporter.py `
  tests/test_export_masking.py `
  tests/test_export_splitting_pairing.py `
  tests/test_export_page.py `
  --basetemp .tmp/pytest-p5 -q

.\.venv\Scripts\python.exe -m pytest --basetemp .tmp/pytest-all -q
.\.venv\Scripts\python.exe scripts/smoke_ui.py
~~~

- Phase 5 專項：21/21。
- 全專案：132/132。
- UI：`UI SMOKE OK`。
- P5 全用合成影像驗證，真實照片處理／匯出：0 張。

## 下一步

Phase 6：Re-ID embedding engine、特徵索引與 AI02（Ubuntu RTX A5000）執行交接。
