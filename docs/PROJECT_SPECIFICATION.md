# 原始專案規格（歷史設計文件）

本文件保留最初的需求與歷史說明；實際安裝、操作與目前限制請以 [使用教學](../README.md) 為準。

注意：
不要一次輸出整個專案所有檔案的完整程式碼。
先分析並建立 Phase 1 MVP。
每一個 Phase 必須能獨立執行與測試。
如果檔案很多，請一次建立一組合理的檔案，確認架構正確後再繼續。
任何 API / package 用法如果不確定，優先查本機已安裝版本與官方文件，不要猜。

你是一位資深 Python / Computer Vision / Desktop GUI / Machine Learning Dataset Engineer。

我要你幫我從零開始開發一套「Windows 11 本地車輛影像資料集建立與人工標註工具」。

這不是單純的圖片瀏覽器，而是一個為「Vehicle Re-Identification（車輛再識別 / Vehicle Re-ID）」準備訓練資料的 Dataset Builder。

==================================================
一、專案背景
==================================================

我的環境：

- Windows 11
- 本地執行
- 不允許把任何圖片或車牌資料上傳到雲端服務
- 主要處理校內測速攝影機照片
- 資料年份：2024 ～ 目前
- 有三個固定位置的攝影機
- 每個攝影機都有相對固定的拍攝角度
- 主要目標是「機車」，但也希望架構可以支援汽車
- 每張測速照片通常包含：
  - 車輛本體
  - 車牌
  - 日期
  - 時間
  - 攝影機 / 主機資訊
  - 速度
  - 車輛方向等資訊
- 車牌有時會被遮蔽，因此最終 Re-ID 模型不能依賴車牌
- 車牌只能用於：
  1. 建立資料標籤
  2. 將歷史照片自動分成「可能同一台車」的群組
  3. 建立 positive / negative training pairs
- 最終真正的 Re-ID 模型必須學習車身外觀，而不是車牌文字

我目前已經有大量歷史照片，且照片已經依「年份」打包成 ZIP 或 7Z 壓縮檔。

所以程式必須可以直接從 ZIP / 7Z 匯入資料。

==================================================
二、核心目標
==================================================

請設計並實作一個 Windows GUI 工具：

Vehicle Dataset Manager

主要工作流程：

原始 ZIP / 7Z
    ↓
解壓 / 掃描
    ↓
圖片分析
    ↓
車輛偵測
    ↓
車牌偵測
    ↓
OCR
    ↓
車牌文字標準化
    ↓
依車牌建立候選 Vehicle Group
    ↓
人工確認 / 修正
    ↓
產生 Vehicle ID
    ↓
建立車輛 Crop
    ↓
建立車牌 Crop
    ↓
建立「車牌遮蔽後」的 Re-ID Crop
    ↓
輸出 Dataset
    ↓
之後交給 AI02 的 Re-ID 訓練系統

==================================================
三、非常重要的設計原則
==================================================

1. 完全本地執行

不得使用：
- OpenAI API
- Google Vision API
- AWS Rekognition
- Azure Vision
- 任何雲端 OCR
- 任何雲端圖片分析 API

所有影像與車牌資訊都只能留在本機。

2. 原始照片絕對不能修改

所有：
- crop
- mask
- resize
- OCR
- annotation
- processed image

都必須產生新的檔案。

3. 程式必須可中斷 / 可恢復

處理 10 萬張甚至更多圖片時：
- 程式關閉後可以繼續
- 不得重複處理已完成照片
- 每張照片的處理狀態要儲存在 SQLite
- 必須記錄 error / warning / skipped
- GUI 顯示總進度

4. 不要把所有資料一次載入 RAM

必須逐張或分批處理。

5. 大量圖片處理應使用 background worker / thread / process，
GUI 絕對不能因 AI 分析而卡死。

==================================================
四、壓縮檔支援
==================================================

主程式必須支援：

- .zip
- .7z

優先策略：

如果系統有安裝 7-Zip：
可以自動尋找常見安裝位置，例如：

C:\Program Files\7-Zip\7z.exe
C:\Program Files (x86)\7-Zip\7z.exe

可以呼叫 7-Zip CLI。

但是程式不能「一定依賴使用者已經安裝 7-Zip」。

如果找不到 7-Zip：

- ZIP 使用 Python 標準庫 zipfile
- 7Z 使用 py7zr 或其他可靠的 Python library

請將壓縮處理封裝成獨立模組，例如：

archive_manager.py

介面至少要支援：

- list_archive()
- extract_archive()
- detect_archive_type()
- verify_archive()
- progress callback
- cancellation
- error handling

==================================================
五、推薦技術架構
==================================================

請優先使用現代、目前仍維護中的套件。

不要使用已經過時的 GUI toolkit。

GUI：

- Python
- PySide6

影像：

- OpenCV
- Pillow

資料庫：

- SQLite

Machine Learning：

- PyTorch
- 可搭配 ONNX Runtime
- 如適合，可以保留 TensorRT/ONNX 部署接口

車輛 Detection：

優先使用現代 YOLO 系列，例如 Ultralytics YOLO。

但請把 Detection Model 做成可插拔架構。

例如：

detector/
    base_detector.py
    yolo_detector.py

OCR 也必須做成可插拔：

ocr/
    base_ocr.py
    paddleocr_engine.py

Re-ID 也必須做成可插拔：

reid/
    base_reid.py
    embedding_engine.py

不要把整個程式寫死在某一個模型上。

==================================================
六、GUI 功能
==================================================

請使用 PySide6 建立完整桌面 GUI。

主畫面建議分成：

1. Project
2. Import
3. Processing
4. Review
5. Vehicle Groups
6. Dataset Export
7. Settings
8. Logs

==================================================
七、Import 功能
==================================================

使用者可以：

- 選擇單一 ZIP
- 選擇單一 7Z
- 選擇多個 ZIP/7Z
- 選擇整個資料夾，掃描所有 ZIP/7Z

例如：

2024.zip
2025.zip
2026.zip

也可能：

2024.7z
2025.7z
2026.7z

程式需要自動判斷年份。

如果壓縮檔名稱有：

2024
2025
2026

優先從檔名取得 year。

如果無法判斷，讓使用者手動指定。

==================================================
八、原始資料 metadata
==================================================

每一張照片至少需要保存：

- image_id
- original_filename
- original_archive
- archive_year
- source_path
- camera_id
- datetime
- date
- time
- vehicle_type
- speed
- direction
- plate_text_raw
- plate_text_normalized
- plate_confidence
- vehicle_bbox
- plate_bbox
- processing_status
- review_status

如果照片本身包含測速系統的文字 overlay，
可以透過 OCR 或檔名解析取得：

- 日期
- 時間
- 主機 / Camera
- 速度
- 方向

但是不要假設所有圖片格式都完全相同。

請將 parser 設計成：
Metadata Parser interface。

==================================================
九、車輛 Detection
==================================================

第一優先是：

- motorcycle

第二優先：

- car

架構必須能擴充：

- bus
- truck
- bicycle
- scooter

對每張圖片：

1. 找出車輛
2. 保存 bounding box
3. 保存 confidence
4. 如果有多輛車，標記 MULTIPLE_VEHICLES

不要直接把整張測速照片拿去做 Re-ID。

必須先產生 vehicle crop。

==================================================
十、車牌 Detection + OCR
==================================================

車牌是一個「資料標籤工具」，不是 Re-ID 特徵。

請設計：

Plate Detector
+
OCR Engine

支援：

- 找車牌位置
- crop 車牌
- OCR
- confidence
- 原始 OCR 字串
- normalized 字串

需要針對台灣車牌做合理的 normalization。

例如：

BFY-1765
BFY1765
BFY-I765
BFY176S
8FY1765

不要直接完全視為不同 ID。

可以提供：

- 大小寫統一
- 移除空白
- 移除不必要符號
- 常見 OCR confusion
- edit distance
- 候選群組

但是：
「OCR correction」必須保留原始結果，不可以覆蓋原始 OCR。

==================================================
十一、OCR 品質分級
==================================================

每張照片的 plate OCR 至少分：

HIGH
MEDIUM
LOW
UNKNOWN

HIGH：

- plate bbox 清楚
- OCR confidence 高
- 格式合理

MEDIUM：

- 有可能正確
- 但 confidence 不高

LOW：

- 字串不合理
- 影像品質不足
- OCR 結果不可靠

UNKNOWN：

- 沒有找到車牌
- 車牌完全遮蔽
- 車牌太小

只有 HIGH / 部分 MEDIUM
可以拿來作自動 Vehicle Group。

LOW / UNKNOWN 不應直接形成可靠 training label。

==================================================
十二、Vehicle Group
==================================================

這是整個程式最重要的功能之一。

例如 OCR 得到：

BFY1765

則建立：

Vehicle Group #000123

裡面自動加入：

2024 / Camera A / image001
2024 / Camera B / image017
2025 / Camera A / image221
2025 / Camera C / image502
2026 / Camera B / image901

相同 normalized plate：

→ Candidate Same Vehicle

不同 plate：

→ Candidate Different Vehicle

但是請不要直接把「同 OCR = 100% 同一車」。

所有分組都要有 confidence / source：

plate_exact
plate_fuzzy
manual
reid
etc.

==================================================
十三、人工 Review GUI
==================================================

這部分非常重要。

要設計一個方便大量人工確認的畫面。

例如：

左側：
Vehicle Group

右側：
圖片縮圖 Grid

上方顯示：

Plate:
BFY-1765

Images:
17

Camera:
A / B / C

Years:
2024 / 2025 / 2026

下面圖片：

[img] [img] [img] [img]
[img] [img] [img] [img]
[img] [img] [img] [img]

使用者可以：

- 同車
- 非同車
- 不確定
- 排除
- 修改車牌
- 修改 Vehicle ID

必須有 keyboard shortcuts。

例如：

Enter = Confirm
Space = Select
N = Not same
U = Unknown
E = Edit
M = Merge
S = Split

快捷鍵設計可以再提出合理方案。

==================================================
十四、Merge / Split
==================================================

Vehicle Group 必須支援：

Merge：

Vehicle_0001
Vehicle_0023

→ 合併成 Vehicle_0001

Split：

Vehicle_0001

→ 拆成：

Vehicle_0001
Vehicle_0101

人工拆分後：

原始照片不能被修改。

只修改 metadata 與 group relationship。

==================================================
十五、人工標籤
==================================================

每張照片要能標記：

- verified_same_vehicle
- verified_not_same_vehicle
- uncertain
- excluded

每個 Vehicle Group 可以：

- verified
- partially_verified
- automatic_only

這些資訊未來要能輸出成 training label。

==================================================
十六、Re-ID Crop
==================================================

這是非常重要的要求。

Re-ID 不可以依賴車牌。

所以：

原始照片
    ↓
Vehicle crop
    ↓
Plate bbox
    ↓
Mask plate
    ↓
Re-ID crop

車牌可以用：

- 純色矩形遮罩
- blur
- inpaint

第一版優先用簡單穩定的方法：
「純色遮罩 + 保持原尺寸」。

產生：

vehicle_crop.jpg
plate_crop.jpg
reid_crop_masked.jpg

並且保留：

plate_mask_bbox

==================================================
十七、Dataset 結構
==================================================

請輸出類似：

Dataset/
├── images/
├── vehicle_crops/
├── plate_crops/
├── reid_crops/
├── metadata/
│   ├── images.csv
│   ├── vehicles.csv
│   ├── labels.csv
│   └── manifest.jsonl
└── splits/
    ├── train.csv
    ├── val.csv
    └── test.csv

Vehicle Group 可以：

Vehicle_000001
Vehicle_000002
Vehicle_000003

不要用車牌直接當資料夾名稱。

==================================================
十八、Positive / Negative training pair
==================================================

自動建立：

Positive：
相同 Vehicle ID

Negative：
不同 Vehicle ID

但是不要只產生隨機 negative。

請優先建立：

1. same camera / different date
2. different camera / same vehicle
3. different year / same vehicle
4. same model/color-like vehicle / different vehicle
5. OCR identical but manually separated
6. visually similar but different vehicle

最後一類就是 Hard Negative。

資料格式例如：

anchor
positive
negative

以及：

image_a
image_b
label

==================================================
十九、時間切分
==================================================

不要單純 random split。

需要支援：

Time-based split。

例如：

Train:
2024 + 2025

Test:
2026

也可以支援：

Camera split：

Train:
Camera A + B

Test:
Camera C

以及：

Cross-year + Cross-camera：

Train:
2024/2025
Camera A/B/C

Test:
2026
Camera A/B/C

GUI 可以讓使用者建立 split configuration。

==================================================
二十、Duplicate Detection
==================================================

要支援：

- SHA256
- perceptual hash

找到：

- 完全相同圖片
- 非常相似圖片

不要刪原圖。

只標記：

duplicate
near_duplicate

避免相同連拍圖片同時出現在 train/test。

==================================================
二十一、資料品質
==================================================

自動分析：

- image resolution
- blur
- brightness
- vehicle size
- plate size
- OCR confidence
- vehicle detection confidence

標記：

BAD_IMAGE
LOW_RESOLUTION
BLURRY
TOO_SMALL
OCR_LOW_CONFIDENCE
NO_VEHICLE
NO_PLATE
MULTIPLE_VEHICLES
DUPLICATE

==================================================
二十二、Database
==================================================

請使用 SQLite。

建議至少：

images
vehicles
vehicle_members
plates
detections
ocr_results
reviews
processing_jobs
duplicates
dataset_exports

所有處理結果必須可以從 SQLite 恢復。

不要把所有狀態只放在 JSON。

==================================================
二十三、可恢復處理
==================================================

如果：

100000 張

處理到：

62341

然後程式關閉。

下次重新開啟：

只能從 62342 開始。

GUI 要顯示：

Processed
Skipped
Failed
Pending

並可以：

Retry Failed

==================================================
二十四、Log
==================================================

需要：

- app.log
- processing.log
- error.log

GUI 也要有 log viewer。

錯誤不得直接讓整個 batch crash。

例如某張圖片損壞：

image 5234 ERROR

繼續：

image 5235

==================================================
二十五、設定
==================================================

設定頁至少包含：

Paths
- Workspace
- Archive directory
- Output directory
- Temporary directory

Models
- detector model
- OCR model
- Re-ID model
- Windows 發行包內含 Campus_Violation_Helper 的台灣車牌 ONNX 偵測權重與 CC BY 4.0 授權文件

Processing
- batch size
- worker count
- confidence threshold

OCR
- plate confidence threshold
- 一鍵安裝 Python 3.13 PaddleOCR sidecar、套件與所選 OCR 模型
- 安裝進度與逐項成功／失敗狀態

Mask
- mask method

Export
- output format

==================================================
二十六、Workspace
==================================================

請預設使用：

程式所在資料夾（Windows 發行版中即 VehicleDatasetManager.exe 所在位置）

但讓使用者自由選擇。

建議：

Workspace/
├── database/
├── cache/
├── archives/
├── extracted/
├── crops/
├── plates/
├── reid/
├── exports/
├── logs/
└── models/

原始壓縮檔不應該被修改。

==================================================
二十七、效能要求
==================================================

大量圖片處理時：

GUI 必須保持 responsive。

使用：

- QThread
或
- QThreadPool / QRunnable
或合理的 multiprocessing

AI inference 可以 batch processing。

圖片解碼不要一次載入全部。

CPU / GPU 工作要可以分離。

==================================================
二十八、GPU
==================================================

如果 Windows 有 NVIDIA GPU：

優先使用 CUDA。

但是程式必須可以：

- GPU
- CPU

兩種模式運作。

程式啟動時顯示：

GPU:
NVIDIA XXXXX

CUDA available:
Yes/No

==================================================
二十九、AI Model 必須可替換
==================================================

不要將模型寫死。

所有模型透過 interface。

例如：

class VehicleDetector:
    detect(image)

class PlateDetector:
    detect(image)

class OCREngine:
    recognize(image)

class ReIDEngine:
    extract_embedding(image)

未來我會把 Re-ID 系統放到另一台 Ubuntu AI02 RTX A5000 24GB 主機。

Windows GUI 第一階段主要負責：

資料整理
人工確認
產生 Dataset

所以要預留：

Export Dataset

==================================================
三十、AI02 未來整合
==================================================

架構未來會是：

Windows GUI
        ↓
Dataset Export
        ↓
AI02 Ubuntu 26.04
        ↓
Docker
        ↓
RTX A5000
        ↓
Re-ID Training / Embedding / FAISS

第一版不要強行加入網路 API。

但請讓程式未來容易新增：

- REST API
- SMB
- network share
- dataset sync

==================================================
三十一、GUI 要支援大資料量
==================================================

不要用一次建立數十萬個 QWidget。

圖片 Grid 必須：

- lazy loading
- thumbnail cache
- pagination / virtual scrolling

大型資料集瀏覽時不能因為縮圖而卡死。

==================================================
三十二、程式碼品質
==================================================

請使用：

- type hints
- dataclasses / Pydantic（適合處）
- logging
- pathlib
- proper exception handling
- modular architecture

禁止把所有程式塞進 main.py。

==================================================
三十三、建議專案架構
==================================================

請規畫成：

vehicle_dataset_manager/
│
├── app/
│   ├── main.py
│   │
│   ├── core/
│   ├── database/
│   ├── archive/
│   ├── image/
│   ├── detection/
│   ├── ocr/
│   ├── reid/
│   ├── grouping/
│   ├── dataset/
│   ├── workers/
│   └── services/
│
├── ui/
│   ├── main_window.py
│   ├── import_page.py
│   ├── processing_page.py
│   ├── review_page.py
│   ├── vehicle_group_page.py
│   ├── export_page.py
│   └── settings_page.py
│
├── models/
├── scripts/
├── tests/
├── resources/
├── requirements.txt
├── pyproject.toml
├── README.md
└── build_windows.ps1

你可以根據實際需求調整，但必須保持模組化。

==================================================
三十四、測試
==================================================

必須建立 pytest tests。

至少測：

1. ZIP detection
2. 7Z detection
3. archive extraction
4. filename parsing
5. metadata parsing
6. plate normalization
7. OCR fuzzy matching
8. duplicate detection
9. database CRUD
10. resume processing
11. vehicle merge
12. vehicle split
13. dataset export
14. mask generation

==================================================
三十五、Windows 打包
==================================================

最終希望能建立：

VehicleDatasetManager.exe

優先考慮：

PyInstaller

並提供：

build_windows.ps1

讓我可以：

.\build_windows.ps1

產生：

dist/
    VehicleDatasetManager.exe

如果模型檔很大，不需要全部塞進 exe。

模型可以放：

models/

==================================================
三十六、不要一次產生不可維護的大型程式
==================================================

這是非常重要的。

請採：

Phase 1
→ Core + Database + GUI Skeleton + Archive Manager

Phase 2
→ Image processing + detection

Phase 3
→ OCR + plate grouping

Phase 4
→ Manual review

Phase 5
→ Dataset export

Phase 6
→ Re-ID integration

不要一次產生幾萬行沒有辦法測試的程式。

==================================================
三十七、第一版 MVP 的真正目標
==================================================

第一版先做到：

1. 選擇 ZIP / 7Z
2. 解壓 / 掃描
3. 顯示圖片
4. 建立 SQLite
5. 記錄 metadata
6. 車輛 Detection
7. 車牌 Detection
8. OCR
9. 車牌 normalization
10. 自動建立候選 Vehicle Group
11. 人工修改 / 確認
12. Vehicle Group merge / split
13. 車輛 crop
14. 車牌 crop
15. 車牌 mask
16. Export Dataset
17. 可中斷
18. 可繼續
19. Error handling
20. Log

==================================================
三十八、請先不要做的事情
==================================================

第一版不要：

- Cloud API
- Web application
- Docker
- Ubuntu deployment
- Remote API
- FAISS training search
- Fine-tuning
- 自動執法判定

目前重點是：

「建立高品質、可人工確認的 Re-ID Dataset。」

==================================================
三十九、重要的資料邏輯
==================================================

不要把：

OCR same plate

直接等同：

100% same vehicle

而是建立：

automatic_candidate

以及：

human_verified

最終 training label 優先：

human_verified

其次：

high_confidence_plate_match

最後：

automatic_candidate

==================================================
四十、資料安全
==================================================

程式必須：

- 預設禁止任何 Internet upload
- 不使用 Telemetry
- 不上傳圖片
- 不將 OCR 結果送到外部服務
- 不自動同步雲端硬碟

如果任何第三方套件可能使用 telemetry，
請說明並盡可能停用。

==================================================
四十一、開發方式
==================================================

現在不要只給我概念。

請直接開始建立可執行專案。

但請分階段輸出。

第一輪請先完成：

A. 完整專案架構
B. pyproject.toml
C. requirements / dependency strategy
D. SQLite schema
E. Main Window
F. Archive Manager
G. Image Repository
H. Processing Job System
I. 基本 Log
J. Resume mechanism
K. pytest 基礎測試
L. README
M. Windows build script

第一輪完成後，再逐步加入：

OCR
Detection
Review UI
Vehicle Group
Dataset Export

==================================================
四十二、重要要求：不要假裝某些模型存在
==================================================

如果你需要使用某個 AI 模型：

請先確認它：
- 名稱正確
- Python API 正確
- 本地可以執行
- Windows 支援情況
- License

如果某個方案不確定，不要虛構 API。

請使用 abstraction interface，
讓模型可以在之後替換。

==================================================
四十三、針對我這個專案的優先順序
==================================================

優先順序：

1. 穩定
2. 資料不遺失
3. 可恢復
4. 人工標註方便
5. 本地安全
6. 大量圖片效能
7. 模型可替換
8. 最後才是 UI 美觀

==================================================
四十四、你開始之前先做的事情
==================================================

在真正大量寫程式碼之前：

先提出：

1. 你建議的完整 architecture
2. 套件選擇
3. SQLite schema
4. Processing pipeline
5. GUI layout
6. 哪些部分第一階段完成
7. 哪些部分留到第二階段
8. 潛在技術風險
9. 哪些模型 / OCR library 可能有版本相容問題

然後直接開始實作 Phase 1 MVP。

不要只回答「可以」。
請實際產生程式碼。
每次完成一個可以執行的階段後，說明：
- 新增了哪些檔案
- 如何安裝
- 如何執行
- 如何測試
- 下一階段要做什麼

==================================================
最後一個非常重要的要求
==================================================

這套程式的核心用途是：

「利用已有歷史測速照片中的車牌作為弱監督訊號，自動建立 Vehicle ID 與正負樣本，之後訓練一個不依賴車牌的 Vehicle/Motorcycle Re-ID 模型。」

因此整體架構必須始終維持：

Plate OCR
    ↓
Label / Grouping

而不是：

Plate OCR
    ↓
Vehicle Re-ID feature

Re-ID 最終必須使用：

車身外觀
輪廓
車燈
車架
車箱
輪圈
排氣管
貼紙
改裝件
其他視覺特徵

並且支援：

跨年份
跨相機
不同光線
車牌遮蔽

請以這個目標設計整個軟體。
