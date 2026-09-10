# Phase 3 進度紀錄 — 車牌 OCR（PaddleOCR sidecar）＋品質分級＋分組

> 日期：2026-09-09
> 狀態：**Phase 3 完成**（G1–G5 全部實測通過；pytest 82 全綠；真實資料 9133/9133 completed、59 車牌、56 分組）。

---

## 一、本 Phase 目標

接上真實車牌 OCR（PaddleOCR）＋車牌品質分級（HIGH/MED/LOW）＋車牌分組（weak supervision）。
`GroupingStage` 已存在且接好；本 Phase 只需讓 `plate_norm`/`ocr_quality` 產生真實值。

## 二、線上查證結果（2026-09-09，不猜）

| 項目 | 結果 |
|---|---|
| paddleocr | **3.7.0**（最新；相依 `paddlex>=3.7.0`）|
| paddlepaddle | **3.3.1**（最新；PyPI wheel 只到 **cp313**，無 cp314）|
| onnxruntime | 1.29.0（有 cp314，但需自行實作 det/rec 後處理，暫不採用）|
| 本機 venv | Python **3.14.7** → **paddlepaddle 無法裝進主 venv** |
| 本機 Python 3.13 | `C:\Program Files\Python313\python.exe`（3.13.14）可用 |

**結論：PaddleOCR 跑在獨立 sidecar venv（`.venv-ocr`，Python 3.13），主程式以 JSON 通訊。**
這也符合「模型可替換」與「穩定第一」原則，並隔離 PaddlePaddle 與 torch 的 runtime。

### 已驗證事實（本機實測）
- `.venv-ocr` 已建：`paddlepaddle 3.3.1` + `paddleocr 3.7.0` + `paddlex 3.7.2`。
- **Paddle 3.3.1 有 PIR/oneDNN bug**（`ConvertPirAttribute2RuntimeAttribute not support ArrayAttribute<DoubleAttribute>`）→ 必須 `enable_mkldnn=False`（paddlex 會轉為 `run_mode="paddle"`）。
- paddlex 快取目錄受 `PADDLE_PDX_CACHE_HOME` 控制；`C:\Users\user` 在沙箱唯讀 → sidecar 需設定可寫的 HOME/快取。
- API（3.x，與 2.x 完全不同）：
  - `PaddleOCR(text_detection_model_name=..., text_recognition_model_name=..., use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, enable_mkldnn=False)`
  - `eng.predict(img)`（`ocr()` 已 deprecated）→ result dict：`rec_texts` / `rec_scores` / `rec_polys`（np array (4,2) int16，影像座標）
  - `ocr_version` 參數只接受 `PP-OCRv3/v4/v5/v6`；mobile/server 要個別用 model name 指定。
- 模型（官方自動下載）：`PP-OCRv5_mobile_det` / `PP-OCRv5_mobile_rec`（約幾十 MB，下載到快取目錄）。

### 真實資料偵測結果
- **全圖 1152×768**（RS015 測速照）：上方有燒錄 metadata header（日期/主機/速限/地點等 ~15 個文字區）。
  車牌會出現 2 次：放大車牌特寫（左上，大字）＋車輛上的實際車牌（小）。
- **mobile 全圖 ~5.5s/張（CPU）**，4/4 全抓到車牌（0.83–0.95）；server 全圖 ~29s/張（0.88–0.98）。
- **vehicle crop 上的 OCR ~0.5s/張、12/12 成功（0.88–0.99）**（Phase 2 裁切品質好）。
- 預設採用 **PP-OCRv5_mobile**（快 5 倍；設定可切 server）。
- header 文字（如「RS015」「20250217」「030」）皆通不過 `is_plausible_plate` → 過濾有效。
  注意 OCR 會把車牌分隔字元讀成 `·` 或 `-`（如 `NRW·3579`），`normalize_plate` 會去掉非字母數字。

## 三、架構

```
主程式 (Python 3.14, .venv)
  ocr/paddle_client.py
    PaddleOcrProcess   ← spawn/管理 sidecar 子程序（JSON lines, stdin/stdout）
    PaddleOcr          ← BaseOcr.recognize(bgr)（挑最佳 plate-plausible 結果）
    PaddlePlateDetector← BaseDetector.detect(bgr)（全圖 OCR → is_plausible_plate 過濾）
        │  subprocess
        ▼
sidecar (Python 3.13, .venv-ocr)
  ocr/paddle_server.py   ← 載入 PaddleOCR 一次；回傳 results [{text,score,box}]
```

- 通訊：JSON lines（stdout 只有 protocol；log 全走 stderr）。
- 首次 `ocr` 請求前 `init` 載入模型（含首次下載，timeout 放寬）。
- sidecar 掛掉 → client 自動重啟並重試一次；再失敗 → StageError（該圖 FAILED，可 retry）。
- 快取/模型：`PADDLE_PDX_CACHE_HOME` 預設 `<workspace>/cache/paddlex`（本地可攜）。
- 保留 stub fallback：paddle 不可用時批次照常跑（NO_PLATE/UNKNOWN）。

### 流程（沿用既有 stages）
DetectStage: vehicle(YOLO) + plate(PaddleOCR 全圖→plate 候選)
OcrStage: 最佳 plate bbox → crop → recognize → plate_raw/norm/confidence
_grouping: plate_norm + quality(HIGH/MED) → vehicle group（既有 GroupingStage）_

## 四、實作分組（逐組驗證）

- [x] G1：`ocr/paddle_protocol.py`（共同 encode/decode）＋ `ocr/paddle_server.py`（sidecar）
- [x] G2：`ocr/paddle_client.py`（Process/Ocr/PlateDetector）＋ 離線測試（fake server，不需 paddle）
- [x] G3：config（OcrConfig 擴充、ModelsConfig 槽位）＋ `app_context.build_detectors()` ＋ `scripts/run_real.py`
- [x] G4：headless 真實資料驗證（reset 12＋既有 pending 共 72 張 → process → DB 檢查 plates/vehicles）
- [x] G5：UI settings（OCR 區段：引擎/模型/偵測按鈕）＋ 文件（更新 HANDOFF/PROJECT_README）

### G1 驗證結果（2026-09-09 實測）
- **stream 驗證**：paddlex Creating model／模型訊息全走 **stderr**；predict 兩流皆無輸出。server 端另把 sys.stdout 重導至 stderr、protocol 直寫原始 stdout buffer → stdout 100% 乾淨（pipe 層實測）。
- **scripts/probe_paddle_server.py 實測**：.venv-ocr spawn sidecar → init（10.3s，模型已快取）→ 8/8 crop 回 
esult（200–500ms/張）→ ping/pong → shutdown 乾淨退出 code 0。
- **pytest 65 全綠**（55 舊＋10 新 	ests/test_paddle_protocol.py，純離線、不需 paddle）。
- **新發現（修正舊結論）**：ocr_version="PP-OCRv5" 在 paddlex 內解析成 **server** det＋rec（_pipelines/ocr.py L360-391），所以先前 probe_ocr 的 12/12 是 **server** 的結果。12 crop 實測：
  - mobile：11/12、~0.31s/張；漏 1 張（20250417_...4381，server 讀到 668HVQ 0.991）；部分分數偏低（0.658–0.94）。
  - server：12/12、~0.88s/張（0.88–0.99）。
  - 決策不變：預設 **mobile**（全圖偵測 5.5s vs 29s，全批次 9133 張 ~15h vs ~76h）；漏抓的標 NO_PLATE 由 Phase 4 人工複審補齊；設定可切 server。motorcycle 牌（如 928-NBV）會被 is_plausible_plate 正確過濾。
- 新增檔案：ehicle_dataset_manager/ocr/paddle_protocol.py、ehicle_dataset_manager/ocr/paddle_server.py（含 --selftest）、scripts/probe_paddle_server.py、	ests/test_paddle_protocol.py。

### G2 驗證結果（2026-09-09 實測）
- ehicle_dataset_manager/ocr/paddle_client.py：PaddleOcrProcess（spawn 管理＋stdin/stdout 通道＋reader 執行緒綁定 process/queue，舊世代的 EOF 不會污染 restart）＋ PaddleOcr（挑最佳 plate-plausible，無則 None）＋ PaddlePlateDetector（全圖 OCR → is_plausible_plate 過濾）＋ uild_paddle_engines()（共用單一 sidecar）。
- 重試邏輯：PaddleOcrError → process.restart()（close+start 重新 init）→ 重試一次 → 再失敗丟 PaddleOcrError（G3 會轉 StageError → 該圖 FAILED、可 retry）。
- spawn 環境：PYTHONPATH=repo root、PYTHONIOENCODING=utf-8、PADDLE_PDX_CACHE_HOME＋HOME/USERPROFILE→快取目錄（預設 <repo>/runs/home/.paddlex，G3 改傳 <workspace>/cache/paddlex）、Windows CREATE_NO_NEW_WINDOW。
- **離線測試 10 全綠**（	ests/test_paddle_client.py＋scripts/fake_paddle_server.py，不需 paddle）：ocr/boxes、ping、close 冪等、死程序→fatal、kill 後 restart 恢復、sleep 逾時、stdout 雜訊跳過、recognize 挑最佳 plausible、crash 後 retry 成功、plate detector 過濾。
- **pytest 75 全綠**（55 舊＋10 protocol＋10 client）。
- ocr/__init__.py 已匯出新符號（PaddleOcr/PaddleOcrProcess/PaddlePlateDetector/PaddleOcrError/uild_paddle_engines/default_ocr_python）。


### G3 驗證結果（2026-09-09 實測）
- `core/config.py`：`OcrConfig` 擴充（model_preset/ocr_python/cache_dir/enable_mkldnn/init_timeout/request_timeout）；`ModelsConfig` 加 `plate_detector`/`ocr` 槽（"none"=stub、"paddle"=sidecar）。
- `app_context.build_detectors()`：`models.ocr="paddle"` 時建 paddle engines（共用單一 sidecar、cache 傳 `<workspace>/cache/paddlex`）；任何建構失敗 → 自動 fallback stub（批次照常跑）。
- `scripts/run_real.py`：`OCR_ENGINE=paddle` 環境變數開真實 OCR（headless 用）。
- **測試**：`tests/test_paddle_wiring.py`（5：stub 預設、paddle 建構、缺 .venv-ocr → stub fallback、close 冪等）＋ `tests/test_paddle_stage_errors.py`（2：PaddleOcrError → StageError → 該圖 FAILED 可 retry）→ **pytest 82 全綠**。

### G4 驗證結果（2026-09-09 實測）
- **首次跑（72 張 pending，含 reset 的 12 張）**：processed=72、failed=0、~512s（~7s/張，YOLO＋全圖 mobile OCR＋車牌 crop OCR，CPU）。
- **DB 結果**：70/72 抓到車牌（2 張 NO_PLATE）；plates/ocr_results 各 70、vehicles 68（同 plate 多圖 → 同分組）。
- **發現 1（純數字假陽性）**：16 張把 header 燒錄時間（如「時間：11:56:54」）讀成 6 位純數字並通過 `is_plausible_plate` 的 lenient 規則 → 誤判成車牌且 quality=high。
  **修法**：`services/plate.py` 的 `is_plausible_plate` 加「至少一個字母」（TW 車牌皆含字母；純 6 位數字多為 header 時間）＋ `tests/test_plate.py` 補回歸測試（115654/070822/1234567 → False；928NBV 機車牌 v1 仍過濾）。
- **重跑 16 張**：11 張 NO_PLATE（mobile 對小車牌漏抓，預期中，Phase 4 人工補）、5 張抓到真車牌（MXHZ788/NYJ5862/CE8086/NYJ9982/PAQ593，0.88–0.998）；純數字車牌歸零。
- **發現 2（vehicle id 撞車，潛伏 bug）**：重跑時 4 張 FAILED（`UNIQUE constraint failed: vehicles.vehicle_id`）。根因：`VehicleRepository._next_vehicle_id` 用 `COUNT(*)+1`，刪除 vehicle 行後 count 與 max id 脫鉤。**修法**：改 `MAX(vehicle_id)+1`（id 零補足 6 位，字串比較＝數值比較）。4 張重跑成功。
- **最終狀態**：9133/9133 completed、0 failed；plates=59（全 high）、vehicles=56、members=59；純數字車牌=0。
- **pytest 82 全綠**（含新增 plate 回歸測試）。
- 執行指令：
```powershell
cd D:\CPTR-CODE\PhotoTraining
$env:PYTHONPATH=(Get-Location).Path; $env:YOLO_CONFIG_DIR="runs\2025"; $env:OCR_ENGINE="paddle"; $env:QT_QPA_PLATFORM="offscreen"
.\.venv\Scripts\python.exe scripts\run_real.py runs\2025 2025.7z process
```
- **G4 結論**：paddle sidecar 全链路（spawn→init→detect→OCR→分組→DB）headless 驗證通過；2 個小 bug 已修。剩 G5（UI settings＋文件）。

### G5 驗證結果（2026-09-09 實測）
- `ui/settings_page.py`：
  - Models 群組：`Plate detector`／`OCR` 下拉加 `paddle`（原僅 `none`）。
  - OCR 群組：加 `Paddle model preset`（mobile/server）＋ `Detect PaddleOCR (.venv-ocr)` 按鈕＋狀態標籤（READY=綠/NOT FOUND=橙，含重建指令提示）。
  - `_load`/`_save` 同步 `ocr.model_preset`；Save 後 `ctx.build_detectors()` 重建引擎（paddle 建不起來自動 fallback stub，既有邏輯）。
- **offscreen 冒煙（.venv）**：combo items `['none','paddle']`、preset 預設 mobile、偵測回 `READY: ...\.venv-ocr\Scripts\python.exe`、存檔 round-trip `models.ocr=paddle`＋`model_preset` 正確落 settings.json。
- **pytest 82 全綠**（UI 無新離線測試檔；行為經冒煙驗證）。
- **注意**：冒煙把 `runs\2025\settings.json` 的 ocr 槽設成 `paddle`（＝本專案預設要用 paddle，符合預期）；preset 為 `mobile`。

## 五、執行指令（驗證用）

```powershell
cd D:\CPTR-CODE\PhotoTraining
$env:PYTHONPATH=(Get-Location).Path
# sidecar 單獨冒煙（.venv-ocr）
.\.venv-ocr\Scripts\python.exe scripts\probe_paddle_server.py
# 全部測試
.venv\Scripts\python.exe -m pytest --basetemp runs\2025\pytest_tmp
# 真實小樣本（先 reset）
.venv\Scripts\python.exe scripts\reset_for_p2.py runs\2025 12
$env:OCR_ENGINE="paddle"
.venv\Scripts\python.exe scripts\run_real.py runs\2025 2025.7z process
```

## 六、已知限制 / 注意

1. `.venv-ocr` 不進 git；正式機器需重建（`py -3.13 -m venv .venv-ocr` + `pip install paddlepaddle paddleocr`）。設定頁顯示偵測狀態。
2. `enable_mkldnn=False` 是 Paddle 3.3.1 的 workaround；未來 paddle 升級可再測開啟。
3. 車牌 OCR 仍是**弱監督訊號**：分組 = automatic_candidate；訓練 label 優先 human_verified（README §39）。
4. CPU 全批次 ~6s/張（偵測＋辨識）→ 9133 張約 15 小時（可續跑）；NVIDIA 機器裝 GPU 版 paddle 可大幅加速。
5. mobile 模型對極小/模糊車牌召回較低 → 漏的標 NO_PLATE，Phase 4 人工複審補齊；可切 server 提精度。