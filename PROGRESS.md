# 進度紀錄 — 2025.7z 真實資料測試

> 2026-09-13：v0.0.8 原圖替換、崩潰修復與備份清除資料庫已編譯本機 EXE，211 項測試通過，正式 EXE 啟動及離線安裝器通過。[交接](HANDOFF_2026-09-13_v008.md)。未發布 GitHub。

> 最新原始碼：Qt 日誌跨執行緒修復、工作區照片掃描及同原始壓縮檔照片修復，尚未發布。[交接](HANDOFF_2026-09-12_CRASH_PHOTO_REPAIR.md)。

> v0.0.7：修正 frozen pip／distlib 資源載入，187 項回歸通過，正式 EXE 兩次真實離線安裝通過。CUDA 仍採額外下載，真實照片 0 張。[交接](HANDOFF_2026-09-12_CUDA_INSTALLER.md)。

> 2026-09-12 最新進度：v0.0.6 已完成 INI／增量重掃與完整繁體中文教學，最終回歸 183 項通過，使用真實照片 0 張。詳見 [最新交接](HANDOFF_2026-09-12_INI.md)；以下為歷史驗證紀錄。

> 更新時間：2026-09-09
> 範圍：Phase 1 收尾 + 巢狀資料結構支援 + 用真實 `2025.7z` 做端到端測試

---

## 一、本次做了什麼

### 1. 偵測到真實資料結構（關鍵發現）
`2025.7z`（2.55GB）裡**不是影像**，而是「月份目錄 → 每日 ZIP」的兩層結構：

```
2025.7z/
  02/20250217.zip   ← 每天一個 zip
  02/20250218.zip
  ...
  10/20251023.zip
  09/ 10/ 11/        ← 部分月份目錄為空
```

每個「每日 ZIP」內是**照片 + 同名 .ini 側檔**配對：

```
20250228_020807_915_RS015_2520_A.jpg
20250228_020807_915_RS015_2520_A.ini
```

- 檔名格式：`日期_時間_毫秒_相機站號_連號_方向`（例：`20250228_020807_915_RS015_2520_A`）
- 照片：**1152×768 RGB**
- `.ini` 為 **Big5(cp950)** 編碼，藏著真正的測速資料：

| INI 鍵 | 意涵 | 示例 |
|---|---|---|
| `主機` | 相機站號 | RS015 |
| `車速` | 量測車速 | 052km/h |
| `速限` | 該點速限 | 030km/h |
| `地點` | 位置 | 外環-棒球場往西宿舍方向 |
| `證號` | 裝置序號 | 2412BAD00801 |
| `方向` | 方向 | 車尾 / 1 |
| `日期`/`時間`/`影像序號` | 時間與連號 | 2025/02/28、02:08:07、2520 |

> 原本 Phase 1 的匯入只掃一層，面對此結構會找到 **0 張影像**。這是本次要補的根因。

### 2. 實作（外科式改動，皆附測試）
| 檔案 | 改動 |
|---|---|
| `archive/manager.py` | 新增 `ARCHIVE_EXTS`、`extract_archives_recursively()` + `_extract_nested()`（遞迴解巢狀壓縮；解完移除中間 ZIP 避免重複 pass） |
| `services/metadata.py` | 新增 `IniSidecarParser`（Big5/GB18030 等編碼自動偵測、中文鍵→正規欄位、車速/速限/日期/時間正規化） |
| `pipeline/stages.py` | `MetadataStage` 支援 `ini_parser`：讀取同目錄 `.ini` 側檔並合併（側檔優先）；`build_default_stages` 接上 |
| `pipeline/engine.py` | `_persist` 落庫新欄位 `speed_limit` / `location` / `device_serial` |
| `database/schema.py` | 新增 **migration v2**（`ALTER TABLE images ADD COLUMN` 上述 3 欄，純加法） |
| `database/repositories.py` | `_update_fields` 白名單加入 3 欄 |
| `services/import_service.py` | 改呼叫 `extract_archives_recursively` |

### 3. 新增測試
- `tests/test_nested_and_ini.py`（4 項）：INI 欄位解析、zip-of-zip 巢狀解壓、巢狀匯入→處理→欄位落庫、**resume 不重複**。
- 全測試：**29 項全綠**（25 舊 + 4 新）。

---

## 二、真實測試結果（2025.7z）

| 項目 | 結果 |
|---|---|
| 匯入（巢狀解壓） | 成功，`verified=True`，`backend=py7zr`，`year=2025` |
| 影像數 | **9133 張**（jpg 9133 + ini 9133，約 2.4GB） |
| 處理（stub pipeline） | **9133 完成 / 0 失敗 / 0 遺漏**（約 8.6 分鐘，~15 張/秒） |
| Resume 重跑 | **0 張**（不重複處理）✅ |
| 原始 `2025.7z` | **未修改**（仍 2,553,518,748 bytes）✅ |
| 資料範圍 | 2025/02 → 2025/11，相機 **RS015**（100%） |
| 車速 | min 51 / max 125 / avg 56.9（全部有值） |
| 超速 | **9133/9133 全超速**（皆 > 30km/h）→ 符合測速相機「只抓違規」特性 |
| 地點 | 外環-棒球場往西宿舍方向（一致） |
| DB | `runs/2025/database/vehicle_dataset.db`，約 **6.9MB** |

樣本（已落庫）：
```
20250217_002102_652_RS015_1202_A.jpg  cam=RS015  v=52  lim=30  dir=1  loc=外環-棒球場往西宿舍方向
20250217_044512_754_RS015_1204_A.jpg  cam=RS015  v=60  lim=30  dir=1  loc=外環-棒球場往西宿舍方向
```

**結論：Phase 1 的匯入→處理→DB 管線，在真實 2.5GB 資料上端到端跑通，且可中斷重跑、原始檔不動。**

---

## 三、做到哪裡（Phase 狀態）

- ✅ **Phase 1（架構 + 管線 + DB + 巢狀匯入 + INI 元數據）**：完成並用真實資料驗證。
- ✅ **Phase 2（真實車輛/車牌偵測，Ultralytics YOLO）**：完成。
- ✅ **Phase 3（OCR，PaddleOCR + 車牌品質分級 + 分組）**：完成。
- ✅ **Phase 4（人工稽核 GUI）**：完成。
- ✅ **Phase 5（資料集輸出：遮罩裁切 / 正負配對 / 時間切分）**：完成。
- 🟨 **Phase 6（Re-ID embedding + AI02 交接）**：本機整合完成，AI02 實機驗證待辦。

---

## 四、檔案清單（本次）

- 新增：`scripts/list_archive.py`、`scripts/run_real.py`、`scripts/apply_edits.py`、`tests/test_nested_and_ini.py`
- 修改：`archive/manager.py`、`services/metadata.py`、`pipeline/stages.py`、`pipeline/engine.py`、`database/schema.py`、`database/repositories.py`、`services/import_service.py`
- 產出（工作區）：`runs/2025/`（DB + `extracted/2025/` 共 9133 張照片與 ini）

### 如何重跑
```powershell
cd D:\CPTR-CODE\PhotoTraining
.venv\Scripts\python.exe -m pytest --basetemp <fresh> -q     # 29 項測試
.venv\Scripts\python.exe scripts\list_archive.py 2025.7z     # 不展開就數內容
.venv\Scripts\python.exe scripts\run_real.py <workspace> 2025.7z import   # 匯入
.venv\Scripts\python.exe scripts\run_real.py <workspace> 2025.7z process  # 處理
.venv\Scripts\python.exe scripts\run_real.py <workspace> 2025.7z summary  # 統計
```

---

## 五、已知限制 / 注意

1. **巢狀解壓重複 pass**：首次真實匯入發生在修正前，會把同一批日誌 ZIP 解滿 6 個 pass（多花約 6 分鐘）。修正已加入（解完 `unlink` 中間 ZIP）；**目前的 `runs/2025/extracted/` 仍留有 218 個中間 ZIP**，是舊邏輯產物，不影響照片與 DB。若要乾淨樹狀可重新匯入。
2. **`方向` 欄位**：INI 中 `方向` 出現兩次（`車尾` 與 `1`），解析採「最後一個」→ 存成 `1`，較有意義的「車尾（車尾方向）」被覆蓋。後續可改成優先保留非數字值。
3. **進度回傳**：巢狀多層時 `on_progress` 非單一正規化 0..N（純顯示問題，不影響結果）。
4. **偵測/OCR 仍為 stub**：Phase 2/3 尚未接真實模型，目前無 vehicle bbox、無車牌 OCR、無車輛分組。

## 六、下一步（建議）
- **Phase 2**：先查 GPU/CUDA、確認 Ultralytics 版本與 YOLO API/類別索引（機車+汽車），再於 `detection/yolo_detector.py` 實作（繼承 `BaseDetector`），產出車輛 bbox 與**車輛裁切圖**（新檔案、不改原圖），填 `NO_VEHICLE`/`MULTIPLE_VEHICLES` 品質旗標。
- 保留既有 stub 作為可切換的 fallback。
- 依 README §39：訓練標籤優先序 `human_verified > high_confidence_plate_match > automatic_candidate`，絕不把「相同 OCR」當作 100% 同車。
---

## 七、2026-09-10 樣本驗證批次（隨機 200 張，真實模型）

> 背景：全批 9061 張 pending 用 CPU 跑約需 15 小時，太久。改「隨機抽 200 張」驗證真實管線（YOLO + PaddleOCR mobile）功能正常。

### 新增功能：`ProcessingEngine.run_sample()`
- 從 PENDING 用**確定性亂數排序**抽 N 張（`seed` 可重現；SQLite `RANDOM()` 不接參數，故用 LCG hash `(seed*374761393 + image_id*668265263) % 4294967291`）。
- 先 `reset_stuck_processing()`（crash 後卡 PROCESSING 的列自動回 PENDING），再逐張 `claim_image()` 原子搶領。
- 樣本跑完 job 標 `CANCELLED`（代表批次未跑完，剩餘 PENDING 保留），與 `run()` 的 MAX 提前停止語意一致。
- 新增 repo 方法：`ImageRepository.reset_stuck_processing()`、`ImageRepository.claim_image()`。

### 新腳本：`scripts/run_sample.py`
```powershell
$env:PYTHONPATH=(Get-Location).Path
$env:YOLO_CONFIG_DIR="runs\2025"
$env:OCR_ENGINE="paddle"; $env:OCR_PRESET="mobile"
.venv\Scripts\python.exe scripts\run_sample.py runs\2025 2025.7z 200 42
```
（參數：workspace、archive、張數、seed；`run_real.py` 的結尾 `db.close()` 已包進 `if __name__ == "__main__"`，讓 run_sample 可 import 重用 `build_engine()`。）

### 執行結果（2026-09-10，CPU，~23 分鐘，約 6.9 秒/張）
| 項目 | 數值 |
|---|---|
| 樣本 | 200 張（seed=42，自 9061 pending 隨機抽取） |
| processed / failed | **200 / 0** |
| 有車牌 / NO_PLATE | 165 / 35 |
| DB 狀態 | completed 72→**272**、pending 9061→**8861**、processing 0 |
| 車輛群組 | vehicles 56→**205**、plates 59→**224**（全 HIGH、conf 0.87–0.99）|
| 重複牌（>1 張）| 10 組（最多 3 張/牌，如 MXB1806、NDD5097、PEN1237）→ 分組邏輯正常運作 |

### 驗證
- 新增 `tests/test_engine_sample.py`（4 項：抽樣張數、seed 可重現、count 超過可用數、stuck processing 自動回隊列）→ 全測試 **92 全綠**。
- UI smoke OK。剩餘 8861 張仍可隨時用 `run_sample.py`（換 seed）或 `run_real.py process` 續跑。

---

## 八、2026-09-10 Phase 4 完成（人工複審 GUI）

- 新增 Qt-free `ReviewModel`：群組摘要、逐圖狀態、修改車牌、群組確認、merge/split。
- Review 頁完成群組清單、摘要、縮圖 Grid、群組/影像狀態篩選、狀態色碼與進度條。
- 快捷鍵：Enter/Space/N/U/X/E/M/S；所有寫入經 repository，原始影像不變。
- 縮圖由背景 thread pool 載入並在解碼時縮小，避免大型照片阻塞 GUI。
- 處理完成或切換到 Review 分頁會自動刷新；程式關閉會釋放 PaddleOCR sidecar。
- Phase 4 測試 **19 全綠**，全專案 pytest **111 全綠**，offscreen UI smoke 通過。
- 本階段未執行新照片處理（0 張）；下一步為 Phase 5 Dataset Export。

---

## 九、2026-09-10 Phase 5 完成（Dataset Export）

- 新增安全匯出核心：完整 portable 目錄、metadata/manifest、train/val/test、pairs、triplets。
- Re-ID crop 會遮蔽車牌，支援 solid color／blur／inpaint；原圖只讀且輸出副本。
- 安全預設只輸出 `human_verified`，並可排除缺少可靠 plate bbox 的影像。
- group hash／時間／攝影機三種切分都以車輛群組為單位，避免同車跨集合洩漏。
- 可取消、同設定續跑與增量重用；不同設定不允許寫入既有輸出目錄。
- Export GUI 完成預覽、設定、背景執行、進度、取消與續跑。
- DB schema 升至 v4，保存實際 vehicle crop bbox，避免車牌座標轉換錯誤。
- 修正 `JobRunner.is_running` 使用不存在的 `QRunnable.isRunning()`，確保連續背景工作正常。
- Phase 5 專項 **21 全綠**，全專案 pytest **132 全綠**，offscreen UI smoke 通過。
- 本階段只使用合成影像測試；真實照片處理／匯出 **0 張**。
- 詳細重跑與輸出格式見 `PHASE5.md`；下一步為 Phase 6。

---

## 十、2026-09-11 Phase 6 本機整合完成

- ONNX Re-ID engine：可替換模型、CPU/CUDA provider、安全 fallback、L2-normalized embedding。
- Portable cosine index：NPZ 無 pickle、metadata 與模型 identity、top-k／threshold／exclude 搜尋。
- Dataset index builder：只讀 P5 `reid_crops`、路徑越界防護、checkpoint、取消續跑、Dataset 變更偵測。
- Settings、AppContext、Dataset Export GUI 與 CLI wiring 完成。
- AI02 交付：GPU Dockerfile、ONNX Runtime requirements、FAISS converter 與模型契約。
- Docker context 採 allowlist，排除原始照片、workspace、DB、logs、cache 與模型。
- P6 核心／wiring 14 項、相關 Export GUI 3 項；全專案 pytest **146/146**，UI smoke 通過。
- 真實照片與正式模型執行：**0 張／0 個**。
- 待辦：AI02 上選定與驅動相容的官方 CUDA/cuDNN image，放入正式 ONNX 模型，再驗證 CUDA provider、效能與 FAISS GPU。
# 2026-09-12：Qt 閃退與照片完整性修復（尚未發布）

- 日誌改為有界佇列，由主執行緒分批顯示，避免背景工作直接操作 Qt。
- 匯入頁新增原圖完整性掃描與相同原始壓縮檔修復；驗證 SHA256 後只更新失效照片路徑，保留全部標註與舊副本。
- Windows 中文照片路徑改用位元組解碼；完整教學見 README，細節見 HANDOFF_2026-09-12_CRASH_PHOTO_REPAIR.md。
- 追加專案資訊「備份並清除資料庫」，文字確認、備份驗證、交易回復，保留照片／設定；不操作使用者現有資料庫。
- 最終全套 **201 passed in 40.20s**，續修／巢狀 7Z 等專項 9 項通過；真實照片 0 張。尚未建置或發布新 EXE。
