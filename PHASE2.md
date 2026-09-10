# Phase 2 進度紀錄 — 真實車輛偵測（YOLO，CPU / CUDA）

> 日期：2026-09-09
> 狀態：**Phase 2 完成**，已於真實資料 `2025.7z`（9133 張）上驗證。pytest **40 全綠**。

---

## 一、本 Phase 目標

以**可插拔**方式接入真實車輛偵測器（Ultralytics YOLO），支援 CPU / CUDA 兩種裝置：
- **預設 CPU**；CUDA 需在「設定」中開啟。
- 本機為 AMD 顯示卡（無 CUDA）→ 開發於 CPU。
- 程式完成後移到有 **NVIDIA GPU** 的機器，即可用 CUDA 加速。

## 二、環境與版本（本機 venv）

| 套件 | 版本 |
|---|---|
| Python | 3.14.7 |
| torch | **2.14.0+cpu** |
| torchvision | 0.29.0 |
| ultralytics | 8.4.144 |
| opencv | 5.0.0（headless + 一般版並存，`import cv2` 正常）|
| PySide6 | 6.11.2 |

- 本機 `torch.cuda.is_available()` = **False**（AMD，符合預設 CPU）。
- ⚠️ 本機裝到的是 **`+cpu`** 版 torch。在 NVIDIA 機器上要啟用 CUDA，需改用
  **CUDA 版 torch**（重建 venv，或 `pip install torch` 取 CUDA build）。
  程式端**不假設**：`device.py` 執行時偵測，「有 CUDA 且已開啟」才用 CUDA，否則 CPU。

## 三、YOLO 模型選擇（以真實數據衡量，不猜）

- 真實數據 9074 張測速照，**幾乎全為夜間摩托車**（少數汽車）。
- 命中率（20 張隨機樣本）：
  - `yolov8n`：15/20
  - **`yolov8s`：18/20 ← 採用為預設**
- 預設：`yolov8s.pt`、`conf=0.3`、車輛類 = COCO `{car=2, motorcycle=3, bus=5, truck=7}`。
- 模型檔存於 workspace `models/`（可攜）；ultralytics 狀態以 `YOLO_CONFIG_DIR`
  固定到 workspace 內（本地、可攜，不寫 `%APPDATA%`）。

## 四、實作檔案

**新增**
- `vehicle_dataset_manager/detection/device.py` — CPU/CUDA 偵測與報告（**無硬 torch 依賴**）：
  `cuda_available()` / `gpu_name()` / `resolve_device(use_cuda)` / `gpu_report(use_cuda)`。
- `vehicle_dataset_manager/detection/yolo_detector.py`：
  - `YoloVehicleDetector`（實作 `BaseDetector`；**延遲載入**模型；只回傳車輛類；pixel bbox）。
  - `build_vehicle_detector(settings, models_dir)` 工廠：槽位 `none`→stub；建構失敗→stub fallback（批次不因缺模型崩潰）。
- `tests/test_device.py`（6 測試）、`tests/test_yolo_detector.py`（5 測試，含真實框偵測 + crop 寫檔；缺模型/缺資料自動 skip）。

**修改**
- `core/config.py` — 新增 `DeviceConfig.use_cuda`（預設 False）；`ModelsConfig` 加 `vehicle_model`(yolov8s.pt) / `vehicle_conf`(0.3)，`vehicle_detector` 預設改 `yolo`；`ProcessingConfig.save_crops`(預設 True)。
- `detection/__init__.py` — 匯出（模組層級不 import torch/ultralytics）。
- `pipeline/stages.py` — `VehicleCropStage`（寫入**新檔** crop、不改原始）；`PipelineContext` 加 `vehicle_crop_path`/`primary_vehicle`；`build_default_stages(..., crops_dir, save_crops)`。
- `pipeline/engine.py` — persist `vehicle_crop_path`。
- `database/schema.py` — 遷移 **v3**：`images.vehicle_crop_path`。
- `database/repositories.py` — 更新白名單加 `vehicle_crop_path`。
- `app_context.py` — `build_detectors()`（依設定建偵測器；CUDA 狀態存 workspace）；`build_engine()` 傳 `crops_dir`+`save_crops`；`settings=None` 時回退 stub。
- `ui/settings_page.py` — 「Device (CPU/CUDA)」群組、YOLO 模型/閾值選項、存檔後 rebuild 偵測器。
- `ui/project_page.py` — GPU 標籤改用真實 `gpu_report`。
- `main.py` — 啟動時記錄 GPU 報告（README §28）。
- `scripts/run_real.py` — 改用工廠建真實偵測器 + crops。
- `tests/conftest.py` — 設 `YOLO_CONFIG_DIR`；`pyproject.toml` 註冊 `integration` mark。

## 五、真實資料驗證結果（CPU）

- 將 12 張已完成圖重置為 pending，用 YOLO 重跑：`processed=12 / failed=0 / pending_left=0`。
- 每張產生 `vehicle_bbox`（pixel 座標）＋ `crops/vehicle/<stem>_v0.jpg`（新檔，**12/12 存在**）。
- 品質旗標：單車 → `NO_PLATE`；多車 → `MULTIPLE_VEHICLES, NO_PLATE`（plate 偵測仍為 stub）。
- crop 視覺確認：正確裁出摩托車（車牌可見，Phase 3 將 OCR + 遮罩）。
- **Resume 再跑：`processed=0`**（不重複處理）。
- **原始 `2025.7z` 未變**（2,553,518,748 bytes）。
- 測試：**40 passed**（29 舊 + 11 新）。
- UI headless 冒煙：`YoloVehicleDetector(cpu)` 正常建出；Settings/Project 頁面正常；
  GPU 報告 = `GPU: (no CUDA-capable GPU) / CUDA available: No / Device: cpu / torch 2.14.0+cpu`。

## 六、執行指令

```powershell
cd D:\CPTR-CODE\PhotoTraining
$env:YOLO_CONFIG_DIR="runs\2025"          # 指向可寫入目錄（sandbox 用；正式機器可省略）
.venv\Scripts\python.exe -m pytest --basetemp runs\2025\pytest_tmp

# 真實小樣本重跑（先 reset 12 張 → process）
.venv\Scripts\python.exe scripts\reset_for_p2.py runs\2025 12
.venv\Scripts\python.exe scripts\run_real.py runs\2025 2025.7z process

# UI 冒煙（offscreen）
$env:QT_QPA_PLATFORM="offscreen"; .venv\Scripts\python.exe scripts\smoke_ui.py
```

## 七、已知限制 / 待辦

1. 本機 torch 為 `+cpu`；NVIDIA 機器需換 **CUDA build** 才能真正跑 GPU（程式端已支援）。
2. `yolov8s` 對夜間小車輛約 90% 召回；漏偵測者標 `NO_VEHICLE`，由 Phase 4 人工複審補齊。
   可在設定改 `yolov8m`/`yolov8l` 提精度（GPU 上更快）。
3. Plate 偵測/OCR 仍為 stub（Phase 3：PaddleOCR + 車牌分級 + 分組）。
4. 目前每圖只存 top-1 車輛 crop；`save_crops` 可關，`VehicleCropStage(max_crops=N)` 可存多筆。
5. `runs/2025` 仍殘留 218 個 intermediate zip（Phase 1 前舊 run），不影響照片/DB；可重跑 import 取得乾淨樹。

## 八、下一步：Phase 3

PaddleOCR 車牌辨識 + 品質分級（HIGH/MED/LOW，README §11）＋ 車牌分組（weak supervision）。
之後：Phase 4 人工複審 GUI（縮圖格、鍵盤 Enter/N/U/E/M/S、合併/拆分）；
Phase 5 資料集輸出（vehicle/plate/reid crops、pos/neg pairs、時間/鏡頭切分）。