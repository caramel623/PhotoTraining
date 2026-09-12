# v0.0.7 封裝版 CUDA 安裝器修正

## 問題與根因

使用者在 A5000／驅動 597.06 上下載 torch CUDA wheel 後，pip 在匯入 distlib.scripts 時失敗。distlib 依套件 loader 的型別尋找 resource finder，沒有內建 PyInstaller loader 的登錄，因而無法讀取 Windows launcher 資源。這是封裝資源讀取問題，不是此份日誌顯示的下載或 GPU 運算失敗。

## 修改

- services/frozen_pip.py：僅在 frozen 模式註冊 public register_finder API；使用已由 --collect-all pip 放在磁碟上的資源，檢查 t64.exe／w64.exe，並讀取 MZ 檔頭。
- detection/cuda_env.py：pip_main 匯入前執行相容處理，錯誤沿原有 InstallResult／日誌回報。
- services/installer_check.py 與 main.py：--check-installer 全新目錄，產生本地微型 wheel，以同一個安裝器連續安裝兩次，驗證套件與 console launcher，寫 report.json。
- build_windows.ps1：固定 pip==26.2.1；維持 CUDA 額外下載與 onedir。
- tests/test_frozen_pip.py：重現未知 loader、修正後讀取、缺少資源提示、來源模式與真實安裝／禁止覆寫測試。

## 安全與限制

- 沒有預裝 CUDA 套件，不自動修改使用者 NVIDIA 驅動。
- 不使用真實照片，測試套件完全合成；診斷輸出只寫到使用者指定的新目錄。
- 使用同一個凍結版安裝器實際安裝微型 wheel，不只測試成功回傳碼或 EXE 能否開啟。
- 目標 A5000 的 CUDA 可用性仍需使用者安裝後重新啟動驗證。pip deprecation 訊息不代表安裝失敗；以成功狀態和環境偵測為準。

## 驗證

完整回歸：187 passed in 40.16s。來源模式兩次離線套件安裝已通過，包含 Windows console launcher 產生。

正式 Windows onedir 建置成功。使用該 EXE 執行 --check-installer，report.json 的 frozen=true，兩次 success=true、installed=true、launcher_created=true，程序回傳 0。此為真實 pip 安裝，不是 mock。

一般 EXE 啟動／建立空白工作區亦通過。發行 ZIP 374,507,180 bytes，4,289 項目完整性驗證通過；無私人照片／INI／DB／日誌，無預裝 CUDA，指定 ONNX 模型雜湊正確。

ZIP SHA256：b795be368fc618f0df9f41da6aa305df1dac56f2b861994f0cfa7f59438750d2。
