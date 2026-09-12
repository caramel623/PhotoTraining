# v0.0.6 INI 與增量重掃交接

## 已實作

- schema v4 → v5 加法遷移，保留既有影像／偵測／OCR／群組／複核資料；遷移 DDL 納入交易。
- INI UTF-8／BOM／CP950／Big5 解碼，保存原始行、重複鍵及警告；日期時間、車號檢查；INI 不做 OCR 字元猜測。
- 有效車牌人工優先，其次合法 INI，再其次 OCR。INI 與 OCR 分開保存，INI／檔名不一致產生 QA 警告。
- archives、archive_members、image_sources、ini_sources、import_jobs 追蹤檔案與 metadata 來源。
- 內容雜湊解壓快照；同檔名換內容不覆寫舊資產。影像 SHA256 跨壓縮檔與格式去重。
- archive 已存在並不是略過解析的理由；記錄解析器版本，三種模式皆能檢查新版 metadata。
- 單影像交易 checkpoint：單筆失敗回復該筆，其他成功資料可續用；取消／失敗不標記掃描完成。
- 重掃工作不執行偵測／OCR／Re-ID。人工合併／拆分、已複核、排除與註記受保護。
- INI 候選來源 plate_ini_exact；多來源不同車牌保留原有效值並交人工查核。
- 人工複核頁新增含未分組影像的衝突佇列；metadata／來源提示；匯入頁新增取消與最近 100 筆歷史。
- 同名影像裁切以 image_id 區分，避免覆寫；manifest 增加標籤來源及 INI 資訊。
- README 改為完整繁體中文操作教學，原規格搬至 docs/PROJECT_SPECIFICATION.md。

## 驗證與資料安全

- 最終全套測試：183 passed in 39.24s；INI 與增量匯入專項 20 項通過。
- 合成資料 UI smoke 通過：衝突佇列含未分組影像，人工修改可用，群組合併停用。
- Windows onedir 建置成功；編譯版以獨立空白工作區啟動並建立資料庫，程序持續正常執行。
- 發行目錄檢查無 JPG／JPEG／INI／DB／日誌；PNG 僅第三方套件介面圖示。指定模型 SHA256 核對通過。
- 發行 ZIP：VehicleDatasetManager-v0.0.6-windows-x64.zip，374,493,884 bytes；4,284 個項目完整性檢查通過。
- ZIP SHA256：83569BB400798C730C95A8B1637F5D88623322D455E475AD8E5EBA2B37243927。
- 新增測試涵蓋舊資料補 INI、解析器版本變更、重複來源、同名不同內容、ZIP → 7Z、取消、交易失敗、人工合併／拆分及多來源衝突。
- 測試只使用合成影像；真實照片使用量 0。新增測試內的姓名、地點、車牌改為虛構資料。
- 發行仍使用 onedir；指定車牌 ONNX 權重加入發行檔而不進 Git。

## 已知限制與操作注意

- 重掃仍會讀取／解壓壓縮檔，並讀取 INI；它避免的是昂貴 AI 重算，不是零 I/O 快速略過。
- 大型 7Z 的取消需要等目前 bulk extraction 返回。完成 checkpoint 可重跑接續，不以刪 DB 當作復原。
- 舊版 SHA256 為空且來源影像已遺失時，不靠檔名猜測身份；請恢復原解壓檔再掃。
- 不自動壓縮或清除舊內容快照，也不合併舊版本已存在的重複影像列，避免破壞複核關係。
- 多來源衝突不任意挑可信來源；人工車牌優先，但來源警告可能保留，訓練前請人工排除或釐清。
- metadata 衝突佇列不是車輛群組，不能整組合併／拆分／確認。
- P6 AI02 A5000／正式模型驗證仍待目標機部署；OCR 線上模型下載與 NVIDIA CUDA 不在合成回歸測試保證範圍。

## 接續驗證命令

```powershell
.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .tmp\ini-release-tests
.\build_windows.ps1
```

使用者完整操作說明以 README 為主，歷史 HANDOFF 中的版本與測試數不要當成目前最新狀態。
