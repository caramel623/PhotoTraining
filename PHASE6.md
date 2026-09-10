# Phase 6 — Re-ID integration and AI02 hand-off

> 本機整合日期：2026-09-11  
> 狀態：Windows 程式與 AI02 交接骨架完成；AI02 RTX A5000 實機待驗證。

## 已完成

- `OnnxReIDEngine`：單輸入 ONNX Runtime engine，可選 CPU/CUDA provider。
- 明確 preprocessing：BGR→RGB、resize、NCHW float32、ImageNet mean/std。
- 所有 embedding 先檢查 finite/non-zero，再做 L2 normalization。
- `EmbeddingIndex`：portable NPZ cosine index，不使用 pickle。
- `DatasetIndexBuilder`：讀取 P5 manifest 的遮牌 `reid_crop`，可 checkpoint、取消與續跑。
- 模型或 Dataset metadata 改變時拒絕混用舊 embedding。
- Settings／AppContext 可選 `none` 或 `onnx`；模型不存在時安全使用 stub。
- Dataset Export GUI 新增 `Build Re-ID Index`。
- CLI：`scripts/build_reid_index.py`。
- AI02：`ai02/Dockerfile`、GPU requirements、FAISS converter、模型契約與部署步驟。

## 安全界線

- 只對 P5 已遮牌的 `reid_crops/` 建立特徵。
- embedding metadata 不保存 `plate_normalized` 或其他車牌文字。
- manifest 只能引用 Dataset 內的相對路徑。
- Docker build context 使用 allowlist，不傳送照片、`runs/`、DB、logs、cache 或模型權重。
- 不會自動連線 AI02、不會啟用 REST API、不會下載模型。

## 驗證

~~~powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest `
  tests/test_reid_embedding.py `
  tests/test_reid_index.py `
  tests/test_reid_dataset_indexer.py `
  tests/test_reid_wiring.py `
  tests/test_export_page.py `
  --basetemp .tmp/pytest-p6 -q

.\.venv\Scripts\python.exe -m pytest --basetemp .tmp/pytest-all -q
.\.venv\Scripts\python.exe scripts/smoke_ui.py
~~~

- P6 core/wiring：14 項。
- 相關 Export GUI：3 項。
- 全專案：146/146。
- GUI：`UI SMOKE OK`。
- 真實照片處理／embedding：0 張。

## AI02 待辦

1. 依 AI02 NVIDIA driver 選擇官方相容 CUDA + cuDNN runtime image。
2. 使用正式訓練模型匯出 ONNX，確認模型 preprocessing 契約。
3. 掛載使用者核准的 P5 Dataset 與唯讀模型目錄，執行 GPU embedding。
4. 確認 ONNX Runtime provider 是 `CUDAExecutionProvider`。
5. 安裝官方支援的 Conda FAISS GPU 組合，轉換 portable index 並量測搜尋。

完整 AI02 指令見 `ai02/README.md`。
