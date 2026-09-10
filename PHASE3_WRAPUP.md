# Phase 3 收尾紀錄 — BUG 修復＋數據收尾＋功能驗證

> 日期：2026-09-10
> 狀態：**完成**。pytest 88 全綠、UI 冒煙通過、`runs/2025` 資料庫狀態已校正。

---

## 一、驗證結果（全部實際跑過）

| 項目 | 結果 |
|---|---|
| pytest 全量 | **88 全綠**（82 舊＋6 新：2 label_priority＋4 stage 品質門控）|
| UI 冒煙（offscreen）| `UI SMOKE OK`：MainWindow、Settings、Project 頁正常；YoloVehicleDetector(cpu)＋PaddlePlateDetector 建出 |
| 真實 DB（runs/2025，收尾後）| images：72 completed / 9061 pending / 0 failed；vehicles 56（全 `automatic_candidate`）|

## 二、發現並修復的 BUG（3＋1）

### BUG 1：`vehicles.label_priority` 從未寫入（潛伏）
- **症狀**：schema 有欄位（`TEXT`），但全專案沒有任何程式碼寫入 → 56 個 vehicle 全為 NULL，README §39 的 label 優先序（`human_verified > high_confidence_plate_match > automatic_candidate`）無法落實；Phase 4/5 會直接踩到。
- **修復**（`database/repositories.py`）：
  - 新增模組常數 `_VERIFICATION_PRIORITY`＋helper `_best_label_priority(*values)`。
  - `get_or_create_for_plate`／`split` 建立時寫 `'automatic_candidate'`。
  - `set_verification` 同步更新：`VERIFIED→human_verified`、`PARTIALLY_VERIFIED→high_confidence_plate_match`、`AUTOMATIC_ONLY→automatic_candidate`。
  - `merge` 後 target 取兩組中較高的優先序（verified 組被併入 auto 組時不降級）。
- **數據回填**：56 筆 → `automatic_candidate`（`scripts/requeue_stub_runs.py` 內）。

### BUG 2：`GroupingStage.min_quality` 被忽略（潛伏）
- **症狀**：constructor 接受 `min_quality`，但 `run()` 硬編碼只過濾 LOW/UNKNOWN → 傳 MEDIUM 與 HIGH 行為完全一樣。
- **修復**（`pipeline/stages.py`）：改用 `(LOW, MEDIUM, HIGH)` 序數比較，低於 `min_quality` 才跳過；UNKNOWN 仍一律跳過。

### BUG 3：OCR 空結果缺 `NO_PLATE` 旗標
- **症狀**：有 plate bbox 但 OCR 認不出文字時，`quality_flags` 保持 `[]` → 該圖在 Phase 4 複審 UI 無法用「NO_PLATE」過濾到（實測 72 張中有 2 張正屬此情況）。
- **修復**（`pipeline/stages.py` `OcrStage`）：空結果分支補 `NO_PLATE`（防重複）。

### 順手修：`ReviewRepository.upsert` 註記被清掉
- **症狀**：update 時 `reviewer`/`note` 為 None 會覆蓋舊值 → 複審改狀態就丟註記。
- **修復**：新值為 None 時保留現有值。

**新增測試**：`tests/test_grouping_stage.py`（4：HIGH 入組、min_quality=HIGH 時 MEDIUM 被擋、LOW 永不入組、OCR 空結果→NO_PLATE）；`tests/test_merge_split.py` 加 2（建立/驗證的 label_priority、merge 優先序提升）。

## 三、數據收尾（`runs/2025`）

執行 `scripts/requeue_stub_runs.py`（冪等，可重複跑）：

1. **`label_priority` 回填**：56 筆 NULL → `automatic_candidate`。
2. **stub 影像回 pending**：**9061 張**在 Phase 1 以 **stub 模型**跑完（每張 `["NO_VEHICLE","NO_PLATE"]`、detections 表 0 筆），當時被記為 completed。現重置為 `pending`，下次 `process` 會用真實 YOLO＋Paddle 重跑。

**紀錄更正**：`PHASE3.md` 的「9133/9133 completed」是「DB 狀態全 completed」，但**真正走過 YOLO＋PaddleOCR 的只有 72 張**（Phase 3 G4 驗證樣本）。收尾後 DB 才反映事實：72 completed（真模型）／9061 pending（待真模型）。

**72 張真實跑分組統計**（收尾前）：
- 車輛偵測：72/72（0 張 NO_VEHICLE；9 張 MULTIPLE_VEHICLES）。
- 車牌：59 張抓到（全 high，0.88–0.998）→ 56 個 vehicle group。
- NO_PLATE：13 張（11 張有旗標＋2 張因 BUG 3 缺旗標）。

## 四、下一步執行指令

```powershell
cd D:\CPTR-CODE\PhotoTraining
# 全批次真實跑（9061 張 pending；CPU 約 6s/張≈15 小時；可中断續跑）
$env:OCR_ENGINE="paddle"
.venv\Scripts\python.exe scripts\run_real.py runs\2025 2025.7z process
# 提精度可切 server（約 5 倍慢）：
$env:OCR_PRESET="server"
# 全量測試（88）
.venv\Scripts\python.exe -m pytest --basetemp runs\2025\pytest_tmp
# UI 冒煙
.venv\Scripts\python.exe scripts\smoke_ui.py
```

## 五、檔案變更清單

| 檔案 | 變更 |
|---|---|
| `vehicle_dataset_manager/database/repositories.py` | label_priority（建立/驗證/merge）、review 註記保留 |
| `vehicle_dataset_manager/pipeline/stages.py` | `GroupingStage.min_quality` 生效、`OcrStage` NO_PLATE 補旗標 |
| `tests/test_merge_split.py` | ＋2 label_priority 測試 |
| `tests/test_grouping_stage.py` | **新增**（4 測試）|
| `scripts/requeue_stub_runs.py` | **新增**（一次性數據收尾，冪等）|

## 六、下一步：Phase 4（人工複審 GUI）

範圍（沿用 HANDOFF 規劃）：縮圖格＋狀態色碼、鍵盤 Enter/N/U/E/M/S、車牌編輯對話框、merge/split UI、依狀態過濾、進度指標。DB 欄位已就緒：`images.review_status`、`vehicles.verification`/`label_priority`、`reviews` 審計表。先跑全批次真實處理（第四节指令）再進 Phase 4，複審時才有完整結果。
