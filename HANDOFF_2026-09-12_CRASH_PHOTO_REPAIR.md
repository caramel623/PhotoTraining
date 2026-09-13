# Qt 閃退與工作區照片修復交接

## 本次範圍

使用者要求修復崩潰，新增照片完整性掃描，允許重新提供相同原始壓縮檔修復照片。未要求本輪發布，尚未建置新 EXE、提交或推送 GitHub；版本維持 0.0.7，既有 EXE 不包含本次修改。

## 修復

- LogsPage 原本直接訂閱背景日誌並操作文字介面，已改成有鎖、最多 5000 筆的純 Python 佇列；Qt 主執行緒每 100 ms 最多取 250 筆。銷毀頁面解除訂閱，讀檔只保留最後 5000 行。
- 此為已驗證的跨執行緒缺陷，符合 Qt6Gui.dll / c0000005 與大量 decode error 的時間關係；沒有 native dump，不能證明是唯一閃退原因。
- LoadImageStage 使用 Path.read_bytes + cv2.imdecode，避免 Windows 中文路徑經 cv2.imread 失敗。掃描與 pipeline 使用同一讀取方法。

## 掃描與修復

- PhotoHealthService.scan 只讀資料庫登錄的原圖（包含遺失路徑），區分 missing/unreadable/corrupt/changed/unverified/ok，可取消，介面摘要顯示最多 1000 筆明細。不掃裁切圖、未登錄檔案，不更動 DB。
- 匯入頁新增 REPAIR_PHOTOS 模式與確認對話框；查核原始 archive SHA256 已存在，不以同檔名認定原始檔。
- 解壓至全新 extracted/repair-* 快照，再驗證影像解碼與歷史 SHA256；只對失效的既有影像逐筆交易更新 source_path，保留人工標註、群組、metadata、OCR、處理狀態。不新增影像、不跑 AI。
- 健康照片不換路徑，舊副本保留；取消會保留已成功 checkpoint，可續修。不自動刪除快照；會占用額外解壓空間。
- 舊資料無 SHA256 或不匹配時不猜測身份，不會自動修復。失敗影像仍需使用者按影像處理的重試／處理待辦。
- 修復寫入 import_jobs 歷史訊息（修復張數及新快照路徑），不建立 AI 處理工作；沒有 DB schema 遷移。
- README 已補完整繁體中文操作說明，明示原始碼新增、尚未發布。

## 驗證

- 含資料庫清除的最終全專案 **201 passed in 40.20s**；照片／日誌／匯入介面專項 9 項通過。
- 背景 6000 筆合成日誌：背景執行時不改 Qt，主執行緒 flush 才更新、佇列有界、頁面銷毀解訂閱。
- 合成照片涵蓋 missing/corrupt/changed/正常/無雜湊/中文路徑、拒絕同名不同壓縮檔、無效原圖不啟用、重複修復無變更、取消續修、7Z 內含 ZIP。
- 比對修復前後 images 所有欄位，除了 source_path 完全一致。真實照片使用量 0；未接觸使用者部署機 E: 的照片或資料庫。

## 追加：備份並清除資料庫

- 專案資訊新增文字確認，必須完整輸入「清除資料庫」。工作執行中拒絕；清除期間暫停頁面操作與關閉。
- Database.backup_and_clear 使用 SQLite backup（涵蓋 WAL），quick_check 後才將 .partial 改成 .sqlite3；備份位於 database/backups。
- 只在單一交易清除固定 allowlist 的 15 個應用資料表。保留 schema_version 與自增序列、原圖／模型／設定／輸出；未知表或未完成交易拒絕清除，失敗回復。
- 完成刷新匯入／處理／複核／群組及狀態列；備份不可自動上傳，尚無一鍵還原介面。
- 新增 5 項資料庫／介面測試與 1 項啟動前接妥完成通知測試，專項連同既有 runner 測試共 7 項通過；只清除測試 fixture，未清除使用者資料庫。

## 後續發布

如需發行，先建置並驗證 onedir EXE，再做私人檔案排除檢查。目標機 Qt 原生崩潰仍需新版實機驗證；本輪沒有修改 CUDA 安裝器或 GPU 驅動。
