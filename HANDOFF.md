# 交接手冊 — Phase 2.5（CUDA）＋ Phase 3（PaddleOCR 車牌）

> 2026-09-14 最新：[v0.0.9 人工檢核跳組、Enter 與放大預覽](HANDOFF_2026-09-14_REVIEW.md)，已編譯並驗證本機 EXE，218 項測試通過。

> 最新：[v0.0.8 原圖替換與本機 EXE 交付](HANDOFF_2026-09-13_v008.md)，211 項測試通過；本機已編譯，未發布 GitHub。

> 最新工作請先讀 [Qt 閃退與照片掃描修復（尚未發布）](HANDOFF_2026-09-12_CRASH_PHOTO_REPAIR.md)，再讀 [v0.0.7 CUDA 安裝器修正](HANDOFF_2026-09-12_CUDA_INSTALLER.md) 與 [v0.0.6 INI 交接](HANDOFF_2026-09-12_INI.md)；本文以下為歷史紀錄。

> 日期：2026-09-09
> 目的：給「下一個新對話」用。說明整體進度、本次實做了什麼、如何驗證、環境陷阱與下一步。
> 說明用繁中；指令/程式碼用英文。
> **2026-09-09 更新：Phase 3 已完成（詳見 `PHASE3.md`）；本文第 8 節「下一步」中的待辦已全數完成，第 11 節總覽已更新。**

## 0. 一頁總覽（TL;DR）
- 專案：Windows 本地「車輛影像資料集建立＋手動標註」工具（Vehicle Re-ID）。全本地、無雲端、**不修改原始檔**。
- 已完成：Phase 1（架構/DB/巢狀匯入/INI）、Phase 2（YOLO 車輛偵測＋車輛裁切，CPU/CUDA）、Phase 2.5（選用 CUDA 時偵測環境並下載依賴）、**Phase 3（PaddleOCR 車牌 OCR sidecar＋品質分級＋分組）**。
- 下一步：將 P6 容器與 ONNX 模型部署到 AI02，實機驗證 RTX A5000 CUDA provider 與 FAISS。
- 測試：**146 全綠**。P6 本機整合完成；AI02 A5000 實機仍待部署驗證。
- **新增 sidecar venv**：`.venv-ocr`（Python 3.13，paddlepaddle 3.3.1＋paddleocr 3.7.0）；主程式（Python 3.14）以 JSON lines 通訊。正式機器需重建（`py -3.13 -m venv .venv-ocr`＋`pip install paddlepaddle paddleocr`），「設定→OCR→Detect」可查狀態。

## 1. 本次（Phase 2.5）做了什麼
**功能**：當使用者在「設定」勾選 *Use CUDA*，程式會：
1. **偵測環境**：PyTorch 是否安裝、CPU 版或 CUDA 版（看 `torch.version.cuda`）、是否有 NVIDIA GPU/驅動（跑 `nvidia-smi`）、CUDA 是否可用（`torch.cuda.is_available()`）。
2. **給出診斷＋建議指令**（5 種狀態，見下）。
3. **下載/安裝依賴**：`pip` 裝對應 CUDA 版 `torch`＋`torchvision`（`--index-url https://download.pytorch.org/whl/cu126`），**背景執行**＋即時日誌，不卡 UI。

**偵測狀態（`CudaStatus`）**
| 狀態 | 意義 | 建議 |
|---|---|---|
| `ready` | CUDA 可用 | 直接啟用 |
| `torch_cpu_build` | 有 GPU 但裝 CPU 版 torch | 裝 CUDA 版 torch |
| `no_torch` | 完全沒裝 PyTorch | 裝 CUDA 版 torch |
| `no_gpu` | 找不到 nvidia-smi（無 NVIDIA）| 先裝 NVIDIA 驅動 |
| `cuda_unavailable` | torch 是 CUDA 版、有驅動但不可用 | 更新驅動／改相符 CUDA 版本 |

**新增/修改檔案**
- 新增 `vehicle_dataset_manager/detection/cuda_env.py` — 核心（Qt-free，可離線測試）。
- 修改 `vehicle_dataset_manager/detection/__init__.py` — 匯出 cuda_env 符號。
- 修改 `vehicle_dataset_manager/core/config.py` — `DeviceConfig` 加 `cuda_wheel_index: str = "cu126"`。
- 修改 `vehicle_dataset_manager/ui/settings_page.py` — Device 群組加「偵測環境」「下載/安裝 CUDA 版 PyTorch」按鈕、CUDA 版本欄位、即時日誌框、背景 QThread worker；勾 Use CUDA 自動偵測。
- 新增 `tests/test_cuda_env.py` — 15 個離線測試。
- 新增 `scripts/probe_cuda_install.py` — 真實端到端安裝探測（工具）。

**cuda_env.py 主要 API**
- `detect_environment(cuda_wheel_index=None, nvidia_timeout=15) -> CudaEnvironmentReport`
- `probe_nvidia_smi(timeout=15) -> NvidiaInfo`
- `build_pip_argv(...) -> list[str]`、`build_pip_command(...) -> str`
- `run_command(argv, line_cb=None, timeout=None) -> InstallResult`
- `install_cuda_torch(cuda_wheel_index=None, python=None, line_cb=None, extra_args=None, timeout=None) -> InstallResult`
- 常數 `DEFAULT_CUDA_WHEEL_INDEX = "cu126"`（已對本機 torch 2.14.0 驗證為官方最新）。
- 資料類別：`CudaStatus`（enum）、`NvidiaInfo`、`CudaEnvironmentReport`、`InstallResult`。

## 2. 環境與版本（本機 venv，別猜）
| 專案 | 值 |
|---|---|
| Python | 3.14.7（`C:\Python314\python.exe`；venv 在 `.venv`）|
| torch（**本機 dev**）| **2.14.0+cpu**（CPU wheel）|
| CUDA 版 torch（已驗證可下載）| **2.14.0+cu126**（官方 index 最新）|
| torchvision | 0.29.0 |
| ultralytics | 8.4.144 |
| opencv | 5.0.0（headless＋一般版並存，`import cv2` 正常）|
| PySide6 | 6.11.2 |
| nvidia-smi | 本機**無**（AMD）→ 偵測回 `no_gpu`（預期）|
| 網路 | 本沙箱**有**網路（可 pip 下載）|

- 已驗證：`cu126` 是 torch 2.14.0 的**有效官方 index**（`pip index versions torch --index-url .../cu126` → LATEST 2.14.0+cu126）。
- `build_pip_command` 輸出為 **Windows 友善**格式（只在含空白時加雙引號，可直接貼進 cmd/PowerShell）。

## 3. 驗證結果（全部實際跑過）
- **pytest 55 全綠**（40 舊＋15 新 `test_cuda_env`）。
- **本機實測 `detect_environment()`**：`status=no_gpu`、`torch 2.14.0+cpu`、`cpu_build=True`、`nvidia.found=False`（AMD，符合預期）。
- **pip dry-run**：`Would install torch-2.14.0+cu126 torchvision-0.29.0+cu126`（指令有效）。
- **真實下載測試**：用 `install_cuda_torch("cu126")` 裝到一次性 venv → `success=True code=0`；實測下載 `torch-2.14.0+cu126 ... win_amd64.whl (2623.2 MB)`（~56 MB/s，45 秒）＋torchvision＋全部依賴；裝完 `torch.version.cuda=12.6`、`cuda.is_available()=False`（AMD 無 GPU；NVIDIA 機器上應為 True）。已刪一次性 venv。

## 4. 如何執行 / 驗證
```powershell
cd D:\CPTR-CODE\PhotoTraining
$env:YOLO_CONFIG_DIR="runs\2025"
$env:QT_QPA_PLATFORM="offscreen"
# 全部測試（55）
.venv\Scripts\python.exe -m pytest --basetemp runs\2025\pytest_tmp
# 只看 CUDA 環境測試
.venv\Scripts\python.exe -m pytest tests\test_cuda_env.py --basetemp runs\2025\pytest_tmp
# 本機實測偵測（預期 no_gpu）
.venv\Scripts\python.exe -c "from vehicle_dataset_manager.detection import cuda_env; r=cuda_env.detect_environment(); print(r.status.value, r.torch_version, r.cuda_available)"
# 真實下載探測（會下載~3GB 到指定 venv；參數＝目標 venv 的 python）
$env:PYTHONPATH=(Get-Location).Path
.venv\Scripts\python.exe -m venv runs\cuda_probe_venv
.venv\Scripts\python.exe scripts\probe_cuda_install.py runs\cuda_probe_venv\Scripts\python.exe
```
注意：`pytest` 用 `--basetemp`（預設 temp 被鎖）；conftest 已設 `YOLO_CONFIG_DIR`，一般直接 `pytest` 也行。

## 5. 檔案撰寫技巧（**重要**，沿用）
`apply_patch` 在本環境不穩。改用 UTF-8 無 BOM 的 here-string 寫檔（單引號 here-string：內容字面、不逸出）：
```powershell
$enc = [System.Text.UTF8Encoding]::new($false)   # UTF-8 無 BOM
$content = @'  <檔案內容>  '@                     # 單引號 here-string（一行式，避免歧義）
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path 'path\file.py'), $content, $enc)
```
- here-string 用單引號開/結（at + 單引號 / 單引號 + at）；內容中 `$`、反引號、`'`、`"` 皆字面。
- 只有「整行以 at + 單引號 開頭」那行才會結束 here-string；`@dataclass`、`@pytest.mark...` 這類行首 at 後還有字元，不會誤結。
- **改檔**：寫暫存 Python 編輯腳本，`t.replace(old,new,1)`，先 `assert t.count(old)==1` 錨點唯一再改，最後 `py_compile.compile(..., doraise=True)`。
- **踩過的坑**：
  - 檔案「最後一行無結尾換行」→ 錨點若帶 `\n` 會匹配不到；改用**不帶 `\n` 的單行錨點**。
  - PowerShell `Start-Process` 遇 `'Path'`/`'PATH'` 字典鍵衝突 → **背景工作改用 exec session**（跑指令 yield 拿 session ID，再 poll/wait 輪詢），別用 Start-Process。
  - `scripts\*.py` 獨立執行時 `sys.path[0]` 是**腳本所在目錄**而非 CWD → 需 `$env:PYTHONPATH=(Get-Location).Path`。
  - PowerShell 主控台 codepage 會把 TC `print` 變亂碼（**只是顯示**；資料是正確 UTF-8）。
  - f-string 裡別放 `\"`；用串接或單引號。

## 6. NVIDIA 機器上如何啟用 CUDA
1. 先裝**新版 NVIDIA 驅動**（要支援 cu126；建議 55x 以上）。
2. 裝 CUDA 版 torch（二選一）：
   - 用程式：「設定 → 勾 Use CUDA → 下載/安裝 CUDA 版 PyTorch」；或
   - 手動：`<venv>\Scripts\python.exe -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu126`
3. **重啟程式**（同一 process 無法熱載入新 torch）。
4. 「設定 → 偵測環境」應顯示「CUDA 已就緒」；勾 Use CUDA → 偵測器自動 `.to("cuda")`。
- 程式端不需改代碼：`device.py` 執行時偵測 `torch.cuda.is_available()`，「有 CUDA 且已開啟」才用 CUDA，否則 CPU（`resolve_device`）。

## 7. 已知限制 / 未盡事項
1. 本機 dev venv 仍是 **CPU 版 torch**（2.14.0+cpu）→ 本機永遠跑 CPU；CUDA 在 NVIDIA 機器啟用。
2. 安裝後需**重啟**才生效（torch 無法 hot-reload）。
3. 預設 CUDA 版本 `cu126`；目標機器需求不同可改「設定 → CUDA build (wheel index)」或手動 `pip` 指定。
4. `probe_nvidia_smi` 只讀 GPU 名稱＋驅動版本（未解析 VRAM 等，目前不需）。
5. 尚未接 Phase 3 的車牌偵測器；`plate_detector`/`ocr` 仍是 stub。

## 8. 下一步：Phase 3（車牌 OCR＋品質分級＋分組）
依「不猜」原則，動工前先：
1. **查 PaddleOCR 可用性**：試裝 `paddleocr`/`paddle` 到 `.venv`，**驗證確切版本與 API**（版本間 API 會變；注意 PaddlePaddle 與本機 CPU torch 是否衝突、是否需獨立 runtime——小心測試）。
2. 在 `ocr/paddleocr_engine.py` 實作 `BaseOcr.recognize(bgr)->Optional[OcrResult]`（`ocr/base.py` 已有 `BaseOcr`/`StubOcr`）。
3. 車牌品質分級 HIGH/MED/LOW（README §11）＋以車牌為標籤分組（README §10/§12）：`GroupingStage` 已存在於 `stages.py` 且已接好（一旦 `plate_norm`/`ocr_quality` 非 UNKNOWN 就啟動）。
4. 車牌**偵測器**目前仍是 stub —— Phase 3 可能需一個 plate detector（或用 YOLO/車輛裁切 ROI）餵給 `OcrStage`。
5. 更新 `app_context.build_detectors()`/`build_engine()` 注入真實 OCR＋plate detector，**保留 stub 當 fallback**。
6. 先 headless 在真實影像上驗證，再碰 UI。

## 9. 架構速查
- 套件 `vehicle_dataset_manager/`：`core/ database/ archive/ pipeline/ detection/ ocr/ reid/ services/ workers/ ui/`。模型＝介面＋stub。Engine 為 **Qt-free**（可 headless 測）。
- `detection/`：`base.py`（`Detection`/`BaseDetector`/`Stub*`）、`device.py`（CPU/CUDA 偵測與回報，無硬性 torch import）、`yolo_detector.py`（`YoloVehicleDetector`/`build_vehicle_detector`）、**`cuda_env.py`（本次：環境偵測＋pip 下載安裝）**。
- `pipeline/stages.py` 順序：Load→Metadata→Detect→[VehicleCrop]→Ocr→Grouping。
- DB：SQLite、10 張表、`schema_version=3`、`check_same_thread=False`+RLock。
- Resume：`ImageRepository.claim_next_pending(archive_path)` 只取 PENDING、原子標 PROCESSING。
- 資料邏輯（README §39）：label 優先 `human_verified > high_confidence_plate_match > automatic_candidate`；同一 OCR 不等於 100% 同一車。

## 10. 使用者偏好（**務必遵守**）
- **切勿一次跑到底**：分組建立、逐組驗證；**不要一次倒出所有檔案的完整程式碼**。
- 每個 Phase 必須**能獨立執行與測試**。
- **API/package 不確定 → 先查本機已裝版本與官方文件，不要猜**（曾因此失手）。
- 優先序：1) 穩定 2) 不丟資料 3) 可續跑 4) 易手動標註 5) 本地安全 6) 大圖效能 7) 模型可換 8) UI 美觀（最後）。
- 完全本地：無雲端/遙測；**原始檔永不修改**。
- 對外說明用**繁體中文**（自然處）；指令/程式碼用英文。
- **注意 context 剩餘量**（曾提醒會低於 40%）→ 勤寫 MD、分段驗證、別一次讀太多。

## 11. Phase 總覽
- ✅ Phase 1：架構＋pipeline＋DB＋巢狀匯入＋INI（真實資料驗證）。
- ✅ Phase 2：YOLO 車輛偵測＋車輛裁切，CPU/CUDA（真實資料驗證）。
- ✅ **Phase 2.5：選用 CUDA 時偵測環境＋下載依賴（本次，真實下載驗證）**。
- ✅ **Phase 3：PaddleOCR 車牌 OCR（sidecar）＋品質分級＋分組（2026-09-09 完成，詳見 `PHASE3.md`）**。
- ✅ **Phase 3 收尾：2026-09-10 完成（3+1 bug 修復、88 測試全綠、stub 跑的 9061 張回 pending、label_priority 回填；詳見 `PHASE3_WRAPUP.md`）**。
- ✅ **2026-09-10 樣本驗證：隨機抽 200 張真實模型處理（200/200 成功、165 有牌／35 NO_PLATE、0 失敗）；新增 `ProcessingEngine.run_sample()`＋`scripts/run_sample.py`（seed 可重現）；92 測試全綠；詳見 `PROGRESS.md` 第七節**。
- ✅ **Phase 4：手動審核 GUI（縮圖格、狀態色碼、快捷鍵、車牌編輯、merge/split；2026-09-10 完成）**。
- ✅ **Phase 5：資料集輸出（完整目錄、車牌遮罩 Re-ID crop、group/time/camera 切分、正負對與 triplet、可取消續跑；2026-09-10 完成，詳見 `PHASE5.md`）**。
- 🟨 **Phase 6：ONNX embedding、portable cosine index、GUI／CLI、AI02 Docker 與 FAISS 交接已完成；等待 AI02 RTX A5000 實機與正式模型驗證（詳見 `PHASE6.md`）**。

## 12. 2026-09-11 v0.0.2 繁體中文介面

- 主視窗、八個頁籤、按鈕、表頭、篩選器、狀態列、提示與錯誤對話框均改為繁體中文。
- 工作、複核、群組來源與確認狀態會顯示中文名稱，但資料庫與設定檔的內部代碼維持不變。
- 遮罩方式、模型開關及 PaddleOCR 預設使用「中文顯示文字＋原始 data 值」，不影響舊設定。
- 載入 Qt「qtbase_zh_TW」，讓「是／否」等系統按鈕也使用繁體中文。
- 版本升至 0.0.2；Windows 發行仍為 onedir 資料夾版，不製作大型單一 EXE。
- 完整測試全數通過；正式 EXE 已驗證中文視窗標題、全新工作區與資料庫建立。
- 本次沒有讀取或處理真實照片（**0 張**），模型、照片、資料庫與工作區不會加入發行檔。
- computer-use 畫面擷取因 Windows 沙箱 helper 錯誤無法啟動；已改以 Qt 元件測試、
  Qt 翻譯載入測試及實際 EXE 視窗標題完成驗證。

## 13. 2026-09-11 v0.0.3 設定頁捲動修正

- 設定群組改放在可垂直捲動的內容區，小視窗不再把所有欄位擠在一起。
- 「儲存設定」按鈕固定在捲動區外的底部，任何捲動位置都能直接操作。
- 新增 640×420 小視窗回歸測試，驗證捲動範圍存在且儲存按鈕不在捲動內容內。
- 未指定工作區時，預設使用 VehicleDatasetManager.exe 所在資料夾；命令列或設定指定的路徑仍優先。
- 本次沒有讀取或處理真實照片（**0 張**）。

## 14. 2026-09-11 v0.0.4 封裝版 CUDA 安裝修正

- 修正封裝版誤執行 VehicleDatasetManager.exe -m pip，導致參數解析失敗。
- 封裝版改用內建 pip，下載 torch／torchvision 到程式旁的 cuda-runtime 版本目錄。
- 只有完整安裝成功後才切換 active.txt；失敗會移除不完整版本並保留 CPU 備援。
- 下次啟動會先載入已啟用的 portable CUDA runtime；runtime 目錄已排除於 Git。
- 設定頁不再顯示不可執行的 EXE 指令，安裝成功後明確要求重新啟動。
- 本機僅做離線模擬與封裝測試；A5000 實際下載與 CUDA 可用性仍需在目標機驗證。
- 本次沒有讀取或處理真實照片（**0 張**）。

## 15. 2026-09-12 v0.0.5 一鍵安裝 OCR 依賴與模型

- 設定頁新增「下載／安裝所有必要依賴與 OCR 模型」及獨立進度日誌。
- 若缺少 Python 3.13，使用 winget 的 Python.Python.3.13 使用者層級套件安裝。
- 建立程式旁 .venv-ocr，安裝 paddlepaddle 3.3.1、paddleocr 3.7.0、paddlex 3.7.2。
- 依設定的 mobile／server 預設執行 sidecar selftest，下載並驗證 OCR 模型。
- 封裝版附帶 sidecar 所需 Python 原始碼，外部 Python 3.13 可直接啟動。
- OCR 成功後自動選擇 PaddleOCR，並自動選用發行包內的 ONNX 車牌偵測器。
- NVIDIA 主機接續安裝 CUDA runtime；無 NVIDIA 時安全略過並保留 CPU。
- Campus 台灣車牌 ONNX 權重由授權工作區於建置時核對 SHA256 後加入發行包，不提交 Git。
- Re-ID 權重仍由使用者自行提供。
- 本機測試不下載模型、不處理真實照片（**0 張**）；目標機需完成線上安裝驗證。
