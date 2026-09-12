# Vehicle Dataset Manager v0.0.7

修正封裝版在 CUDA 套件下載完成後，安裝階段出現：

```text
Unable to locate finder for 'pip._vendor.distlib'
```

## 修正內容

- 在呼叫內建 pip 前，為封裝載入器註冊 distlib 檔案資源尋找器。
- 預先檢查 Windows 啟動器資源；不完整的程式資料夾會提供明確錯誤提示。
- 建置固定已驗證的 pip 26.2.1，避免未測試的 pip 更新改變安裝流程。
- 新增 --check-installer 離線診斷，在真正 EXE 中實際安裝微型測試套件及建立 Windows 啟動器。

## 如何更新

1. 關閉舊程式並備份工作區。
2. 下載 ZIP 後完整解壓，更新程式檔案；保留 database、extracted、crops、settings.json、cuda-runtime 等本機資料。
3. 開啟新版，到設定頁重新執行 CUDA 下載／安裝。
4. 安裝成功後重新啟動，再做環境偵測。

CUDA 仍採另外下載，沒有預裝進發行包，也不需要重新下載照片或刪除資料庫。程式仍是 EXE＋資料夾版本，包含指定台灣車牌 ONNX 權重。

## 驗證範圍

187 項回歸測試通過。正式 EXE 連續兩次真實離線套件安裝成功，Windows 啟動器正常產生，程序回傳 0。微型套件測試驗證 pip 寫入與 Windows 啟動器流程，不等於 A5000 的完整 CUDA 套件安裝或 GPU 運算實測。

ZIP SHA256：`b795be368fc618f0df9f41da6aa305df1dac56f2b861994f0cfa7f59438750d2`
