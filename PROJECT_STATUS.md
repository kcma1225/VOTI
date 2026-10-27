# 專案狀態（2026-10-27）

## 啟動

```bash
docker compose up -d --build   # 或 scripts/start.sh；http://127.0.0.1（nginx → web，另有 worker）
```

## P0 完成度

| 項目 | 狀態 | 驗證 |
|---|---|---|
| 本地音訊＋SRT/VTT 匯入 | 完成 | `tests/test_core.py`；demo fixture 實際跑通 |
| YouTube 單支／多支網址：檢查與取得 | 完成 | 實測 Sintel（`eRsGyueVLvQ`）：檢查、取得、切出 25 段；失敗分類實測（403、無字幕） |
| 正規化、字幕解析、依 cue 切片、異常旗標 | 完成 | `tests/test_subtitles.py`、`test_core.py` |
| 來源追溯、SHA-256、可續跑工作 | 完成 | `cli verify`、`test_core.py`（恢復、重試、去重） |
| 審查介面（播放、修訂、邊界、狀態） | 完成 | `tests/test_ui.py`（Starlette TestClient：全部頁面、匯入→審查→分群→匯出流程、外部文字轉義、跨站／Host／路徑穿越防護） |
| 真實 ECAPA embedding、top-k、分群、人工修正 | 完成 | Sintel 25 段實際推論；人工覆寫在重新分群後保留（`test_integrity.py`） |
| 查詢、統計、JSONL/CSV＋音訊匯出 | 完成 | `test_integrity.py`（UTF-8、引號、快照凍結） |
| 測試、文件、狀態報告 | 完成 | 19 個測試通過；README、docs/、FOLLOW_UP.md |

## P1

| 項目 | 狀態 |
|---|---|
| API 搜尋（需 key） | 已實作，但**未實測**（沒有 key） |
| 播放清單／頻道候選展開 | 已實作，未對真實頻道實測 |
| 儲存統計 | 完成（`doctor`、總覽頁） |
| 處理耗時統計 | 部分：每項工作即時顯示進度、已執行時間、預計剩餘；尚無歷史彙總，見 F10 |
| 字詞層級強制對齊（英、中） | 完成：MMS_FA；來源頁可播放、點字跳轉 |
| Silero VAD | 未做，見 F4 |
| pyannote diarization | 未做，需使用者自備 HF 授權，見 F4 |
| 備份／驗證 | 完成（`cli backup` / `cli verify`） |

## 實測紀錄

- `pytest`：27 passed（本機 venv）。含真實 MMS_FA 對齊：macOS `say` 合成英文句，9 個字時間遞增且落在音檔內。
- 中文對齊實測（Meijia TTS「今天天氣很好，我們去公園散步。」3.47 秒）：13 字皆有時間，逗號處停頓 1.44→1.80 秒被正確保留，分數 0.67–1.00；標點不給時間。
- `doctor`：FFmpeg 9.0.1、torch 2.8.0（CPU、MPS 可用，CUDA 不可用）、node 可用、未設定 YouTube key。
- 合成 demo（Meijia TTS）：匯入 → 1 段 → 1 個真實 192 維向量 → 匯出成功。
- YouTube 實測（寫入 `/tmp/twcorpus-yt`，未寫入專案 data）：
  - yt-dlp 2025.10.22：Sintel 下載回傳 HTTP 403（SABR 串流），系統正確標記失敗並保留原因。
  - 升級 yt-dlp 2026.8.19 並加入 JS runtime 後重試：取得成功，耗時 5.7 秒。結果為 25 段，14.8 分鐘的候選時數中有 1.4 分鐘落在音段內。
  - 25 段 ECAPA 向量計算 2.6 秒。分群結果：門檻 0.3 分成 25 群，0.45 分成 18 群，0.6 分成 10 群，可觀察到混群。
  - 匯出成功，manifest 可追溯到網址、授權欄位與字幕種類。
- Docker compose（OrbStack）：nginx、web、worker 都 healthy；所有頁面經 nginx 200；只綁 `127.0.0.1:80`，8501 不再對外；陌生 Host 被 nginx 直接斷線。
- 跨站防護：headless Brave 實際從頁面送出「YouTube 網址檢查」與「排入語者向量」表單，皆 303 成功；跨站 `Origin`／`Sec-Fetch-Site: cross-site` 回 403；對頁面路徑 POST 被 nginx 擋（403）。
- 容器內 `doctor`：FFmpeg 5.1.9、node、yt-dlp 2026.8.19、speechbrain 1.0.3、torch 2.8.0+cpu。`verify`：4 個資產，0 缺失、0 變更。
- 容器內實際 ECAPA 推論：模型以實體檔案寫入 `data/models/speechbrain`，輸出 192 維向量。

## 未實測／受阻

- 台灣華語、台語、客語的真實樣本：都還沒跑過。測過的 YouTube 樣本是英文動畫，只用來驗證管線。
- YouTube Data API 搜尋：沒有 key。
- CUDA：這台機器沒有 NVIDIA GPU。
- 分群門檻校準：還沒有人工標註的同人／不同人樣本。
- 多人審查、帳號系統：依規格不做。

## 介面

- Streamlit 已移除，改為 Starlette＋Jinja2 伺服器端渲染，淺色／深色切換（預設跟隨系統），RWD（900px 以下頂部導覽、560px 以下表格轉卡片）。
- 處理任務改為右側抽屜面板（手機為底部面板），輪詢 `/api/jobs`，完成時跳通知；`/jobs` 頁面移除。來源頁新增逐字稿與播放器。
- 所有寫入與下載在 `/api/`，頁面只接受 GET；CSP 禁止 inline script／style；跨站 POST 回 403（以 `Sec-Fetch-Site` 為準，`Origin: null` 拒絕）；`Referrer-Policy: same-origin`；Host 白名單；ID 格式驗證；上傳暫存檔一定刪除。

## 已知限制

- yt-dlp 需要定期升級，YouTube 一改版就可能出現 403。
- SpeechBrain 會印出 torchaudio backend 警告，但不影響執行，因為音訊是先用 wave 讀入再交給模型。
- 一次只能跑一個 worker（compose 只定義一個，不要 `--scale worker=2`）。
- 容器只用 CPU；GPU 需另外設定 compose 裝置對應，未做。
- 字詞時間只在各句字幕時間窗內計算；字幕偏移時字詞時間會跟著偏。台語、客語不支援字詞對齊。
- torchaudio 2.8 對 `forced_align` 發出棄用警告（2.9 移除）；升級 torchaudio 前要改用替代實作。
- 預計剩餘時間是線性推估；下載速度或模型載入時間變化大時誤差也大。

## 下一步（依優先順序）

1. F1：挑 3–5 個台灣 CC 頻道，統計各階段的通過率。
2. F2：寫標註指南。
3. F5：人工標記成對樣本，校準門檻。
4. F4：接入 Silero VAD。
