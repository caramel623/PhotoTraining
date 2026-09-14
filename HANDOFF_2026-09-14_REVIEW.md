# 人工檢核操作修正

- 整組確認原本 refresh_groups(select_vehicle=原群組) 導致停留原處；現改為依目前清單順序跳到下一個未確認群組、尾端回繞，略過已確認及 metadata 佇列。
- Enter／數字鍵盤 Enter 直接確認整組，不再二次確認。長按不連發；無剩餘待檢核群組只在頁面提示，不彈視窗。
- 點車輛縮圖開啟等比例大圖預覽，點圖片外空白或 Esc 關閉；點圖片本身不關閉。Enter 在預覽中不確認群組。
- Ctrl／Shift 點擊保留多選，鍵盤移動選取不自動開預覽。大圖取目前 display_path（車輛裁切優先，否則原圖），不是小縮圖快取；大檔案使用 QImageReader 縮放解碼。
- 使用合成圖片測試，沒有修改使用者照片或標註。
- 最終完整回歸：218 passed in 47.65s；含整組確認跳轉、Enter 綁定、預覽放大／背景關閉／Esc 與標註不變。
- 已編譯 v0.0.9 onedir EXE：正式程式啟動空白工作區 10 秒、版本日誌及空資料庫通過；兩次離線安裝器 success／installed／launcher_created 皆 true。
- 發行目錄無私人照片／INI／資料庫／日誌；PNG 僅 matplotlib 介面圖示。指定車牌模型 SHA256 核對通過。
- 交付位於 dist/VehicleDatasetManager；附 README 與 RELEASE_v0.0.9.md。本輪只做本機 commit，不推送 GitHub。
