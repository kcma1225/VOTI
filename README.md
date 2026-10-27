# 台灣語音語料收集與整理系統

本機研究工具。可匯入本地音訊與字幕，或匯入 YouTube 影片網址。系統會正規化音訊、依字幕切片、標記異常、記錄來源與 SHA-256，並計算真實 ECAPA 語者向量。之後可在網頁上審查、修訂與分群，最後匯出 JSONL／CSV 和音檔。

- 預設只綁定 `127.0.0.1`，沒有帳號系統，不做公開部署。
- 字幕一律視為「未校對」，經人工修訂的文字另外保存。
- 分群結果是「候選相似群」，不是確認身分，也不代表語者人數。
- 匯出資料一律標記 `publication_rights_confirmed: false`。能否發布必須另外確認。

## 論文

專案對應的論文放在 [`paper/paper.pdf`](paper/paper.pdf)。

## 1. 環境需求

| 項目 | 需求 | 已實測 |
|---|---|---|
| Docker | Docker Desktop、OrbStack 或 Docker Engine，含 `docker compose` v2 | OrbStack（macOS） |
| 磁碟 | 映像約 2 GB（含 CPU 版 torch）；原始音訊、16 kHz WAV 與切片各存一份，約為原始音訊大小的 3 倍以上 | — |
| CPU/GPU | 容器內只用 CPU。`SPEAKER_DEVICE` 設成 `cuda`／`mps` 會直接報錯，不會假裝成功 | CPU |

FFmpeg 5.1.9、node（yt-dlp 解析 YouTube 用）、Python 套件都已裝在映像內。映像基底以 digest 釘住，Python 套件只取 2026-10-13 前發布的版本。

## 2. 啟動與停止

```bash
cd /path/to/corpus
cp .env.example .env              # 選用；可留空，所有設定都有預設值
docker compose up -d --build      # 或 scripts/start.sh（會等到兩個服務都 healthy）
```

開啟 http://127.0.0.1 （或 http://localhost ）。只有 nginx 對外，且只綁定 `127.0.0.1:80`，區網其他機器連不到；`web`、`worker` 只在 compose 內部網路。

| 服務 | 內容 | 健康檢查 |
|---|---|---|
| `nginx` | 反向代理，唯一入口（`deploy/nginx/default.conf`）；`/` 頁面只允許 GET，`/api/` 轉送寫入與下載 | `/api/healthz` |
| `web` | Starlette 網頁介面（uvicorn，只在內部 8501） | `/api/healthz` |
| `worker` | `python -m twcorpus.cli worker --loop`，處理 YouTube 檢查／取得與 embedding 工作 | `data/worker.heartbeat` 60 秒內有更新 |

```bash
docker compose ps                 # 狀態
docker compose logs -f worker     # 追蹤 worker 日誌
docker compose restart worker
docker compose down               # 停止；檔案留在 ./data，資料庫留在 pgdata volume
```

- 音檔、匯出與模型快取在 `./data`（容器內 `/data`）；資料庫在 PostgreSQL 的具名 volume `pgdata`。`docker compose down` 不會刪資料，`down -v` 會清空資料庫。
- 容器以 uid 1000 的非 root 使用者執行。Linux 主機上若 `./data` 寫入失敗，執行 `sudo chown -R 1000:1000 data`。
- YouTube 檢查與取得、embedding 都是排隊工作，需要 worker 在執行才會處理。本地匯入則在網頁上直接同步完成。worker 停止超過 60 秒時，總覽頁會顯示警告。

### 介面

- 伺服器端渲染（Starlette＋Jinja2），不需要前端建置。所有外部文字（標題、字幕、說明）一律轉義後顯示。
- 右上角可切換淺色／深色；未手動切換時跟隨系統設定，選擇會記在瀏覽器。
- 寬螢幕為側邊欄；900px 以下改為頂部捲動導覽；560px 以下表格改為卡片排列。
- 有 CSP（禁止 inline script／style）、跨站 POST 阻擋與 Host 白名單（`ALLOWED_HOSTS`；nginx 也只接受 `localhost`、`127.0.0.1`）。上傳上限由 `MAX_UPLOAD_MB` 與 nginx `client_max_body_size` 控制（預設 2048 MB）。

### 路徑

| 路徑 | 方法 | 用途 |
|---|---|---|
| `/`、`/sources`、`/sources/{id}`（含 `/review` 音段審查等子頁）、`/segments`、`/clusters`、`/export` | GET | 頁面（`/jobs` 轉到 `/?jobs=open`） |
| `/api/sources/local`、`/api/sources/inspect`、`/api/sources/candidates`、`/api/sources/{id}/edit｜acquire｜subtitles｜align` | POST | 匯入、檢查、補件、字詞對齊 |
| `/api/jobs/embedding`、`/api/jobs/{id}/retry｜cancel` | POST | 工作（帶 `Accept: application/json` 時回 JSON） |
| `/api/review/{segment}`、`/api/clusters/run`、`/api/clusters/{segment}/group`、`/api/export` | POST | 審查、分群、匯出 |
| `/api/media/segments/{segment}`、`/api/media/sources/{id}`、`/api/exports/{id}/manifest.jsonl｜manifest.csv｜README.txt` | GET | 音檔（支援 Range）與匯出下載 |
| `/api/healthz`、`/api/stats`、`/api/jobs` | GET | 健康檢查與 JSON 狀態 |

POST 只接受同站請求：瀏覽器送的 `Sec-Fetch-Site` 必須是 `same-origin`；沒有這個標頭時，`Origin` 必須和 `Host` 相同。從其他網站、`file://` 頁面或 sandbox iframe 送出的表單一律 403。沒有 CORS，也不需要：頁面與 API 同源。

### 不用 Docker（開發用）

需 Python 3.11/3.12、FFmpeg 與 node 在 `PATH` 中：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-model.txt   # 含 torch / speechbrain，約 1–2 GB
.venv/bin/uvicorn twcorpus.web.app:create_app --factory --host 127.0.0.1 --port 8501   # 不經 nginx，開 http://127.0.0.1:8501
.venv/bin/python -m twcorpus.cli worker --loop     # 另一個終端機
```

`huggingface_hub` 固定在 `0.36.0`。SpeechBrain 1.0.3 遇到 1.x 以上的版本會出現 `use_auth_token` 錯誤。

## 3. 模型與 API key

- **語者向量**：使用 `speechbrain/spkrec-ecapa-voxceleb`。第一次執行 embedding 工作時會從 Hugging Face 下載（約 90 MB），以實體檔案存到 `data/models/speechbrain`。不需要 HF token。下載失敗時工作會標為 `blocked`，不會寫入任何向量。
- **字詞對齊**：torchaudio `MMS_FA`（CTC 強制對齊，uroman 風格輸入）。權重從 `dl.fbaipublicfiles.com` 下載約 1.26 GB 到 `data/models/mms_fa`，下載有實際位元組進度。中文以 `pypinyin` 逐字轉無聲調拼音；英文去除變音符號後小寫。每句只在字幕 cue 時間窗（前後各加 0.25 秒）內對齊，所以字幕整體偏移時字詞時間也會偏。
- **YouTube 搜尋**：選用功能。在 `.env` 設定 `YOUTUBE_API_KEY`（請自行在 Google Cloud 建立，本工具不會替你建立）。沒有 key 時仍可直接貼網址檢查。搜尋只送出 `videoLicense=creativeCommon`、`videoCaption=closedCaption` 條件，結果只是候選。
- 本工具不讀取瀏覽器 cookies，也不繞過登入、年齡、地區或付費限制。遇到這類影片時，會標記為 `blocked` 並提示改走本地補件。

## 4. 首次操作順序

1. **總覽**：展開「環境診斷與容量」，確認 FFmpeg 與模型套件都已安裝。
2. **匯入與來源 › 本地配對**：上傳音訊與 SRT/VTT。填入語言、字幕種類與授權證據（不確定就選 `unknown`）。
3. 或在 **YouTube URL** 分頁貼上網址，建立「檢查」工作。檢查完成後，到 **來源清單／補件** 填寫授權證據並勾選確認，再建立「取得」工作。
4. **處理任務**（側邊欄或頂部按鈕開啟的側邊面板，手機上為底部面板）：每項工作有進度條、目前階段、已執行時間與預計剩餘時間，可重試或取消。匯入、檢查、取得、切片、字詞對齊、embedding 都在 worker 背景執行，關閉頁面不影響；完成或失敗時右下角跳出通知。預計時間是依該工作目前實測速度的線性推估。
5. **來源詳情 › 逐字稿與播放**：線上播放整段標準化音訊，點句首時間或字跳到該處，播放時標示目前句子與字。英文、中文（與音訊同語言的字幕）可按「計算字詞時間」排入 MMS_FA 強制對齊；首次會下載約 1.26 GB 模型到 `data/models/mms_fa`。數字、符號不給時間，不做插值。
6. **音段審查**：播放音段，修訂文字，調整起訖（會重新切片並建立新版本），設定審查狀態與語者狀態。
7. **聲音相似群**：建立 embedding 工作，等待 worker 完成，再依門檻執行分群，查看 top-k 相似音段，並以人工指定群組覆寫。
8. **統計與匯出**：查看各階段的時數統計，依條件匯出快照到 `data/exports/<id>/`。

測試資料的產生方式見 [docs/DEMO_DATA.md](docs/DEMO_DATA.md)。

## 5. 命令列

```bash
docker compose exec worker python -m twcorpus.cli stats
docker compose exec worker python -m twcorpus.cli export --review-status passed --corrected-only
docker compose exec worker python -m twcorpus.cli doctor          # FFmpeg、套件、裝置與磁碟空間
docker compose exec worker python -m twcorpus.cli verify          # 重算全部資產 SHA-256
docker compose exec worker python -m twcorpus.cli backup /data/backups
docker compose run --rm --no-deps web python -m pytest -q -p no:cacheprovider   # 測試
```

匯入本地檔案時，先把檔案放進 `./data` 底下（例如 `data/inbox/`），再以容器路徑執行：

```bash
docker compose exec worker python -m twcorpus.cli import-local /data/inbox/audio.m4a --subtitle /data/inbox/audio.srt --language zh
```

## 6. 備份與還原

`backup` 在單一 repeatable-read 交易內把每張資料表輸出成 JSON lines（`database/`），連同 `assets/` 一起複製到新目錄，但不含 `.env`、模型快取和 exports。還原方式：把備份的 `assets/` 複製到 `CORPUS_DATA_DIR`，在空的資料庫執行 `python -m twcorpus.cli restore <備份目錄>`，再執行 `verify` 確認所有雜湊都一致。Docker 下：`docker compose exec worker python -m twcorpus.cli restore /data/<備份目錄>`。

**資料庫 schema**：只有 `db/init.sql`，沒有 migration。PostgreSQL 容器在資料 volume 第一次建立時執行它一次。要改 schema，直接編輯 `db/init.sql`，然後 `docker compose down -v && docker compose up -d --build` 重建資料庫（會清空資料；先 `backup`，需要時再 `restore`）。

## 7. 故障排除

| 症狀 | 處理 |
|---|---|
| `找不到 FFmpeg/ffprobe` | 映像已內建。若用 venv 開發，安裝 FFmpeg 並確認在 `PATH` 中，然後執行 `doctor` |
| 工作一直停在 `queued` | worker 沒有執行。`docker compose ps` 確認 `worker` 為 healthy，看 `docker compose logs worker` |
| 網頁回 `Invalid host header` | 只能用 `127.0.0.1` 或 `localhost` 開啟；其他名稱需加入 `ALLOWED_HOSTS` |
| 按鈕送出回 403「拒絕跨站請求」 | 請從本站頁面操作，網址列要是 `127.0.0.1` 或 `localhost`。舊版曾因 `Referrer-Policy: no-referrer` 讓瀏覽器送出 `Origin: null` 而誤擋，已修正；若仍出現，請強制重新整理（舊頁面快取） |
| 連 http://127.0.0.1 失敗 | `docker compose ps` 確認 `nginx` 為 healthy；本機 80 埠被其他程式占用時，把 `compose.yaml` 的 `127.0.0.1:80:8080` 改成例如 `127.0.0.1:8080:8080` |
| 工作停在 `running`，worker 已關閉 | worker 重新啟動時會把超過 20 分鐘的工作標為 `interrupted`，再到「處理任務」按重試 |
| `模型下載或載入受阻` | 檢查網路與 Hugging Face 連線，確認 `huggingface_hub==0.36.0`，然後重試工作 |
| YouTube `access_restricted`／`region_restricted` | 需要登入、年齡驗證或有地區限制，本工具不會繞過。若有合法副本，請改用本地配對 |
| YouTube `network_or_extractor`，訊息含 HTTP 403 | YouTube 經常變動。先在 `requirements.txt` 更新 `yt-dlp[default]` 版本，`docker compose up -d --build` 重建後，再到「處理任務」重試 |
| YouTube `subtitle_unavailable` | 該語言或種類的字幕無法下載。可改選其他軌道，或用本地字幕補件 |
| `切片邊界無效或越界` | 字幕時間超出音訊長度，或起訖相反。請檢查字幕檔 |
| 啟動時說資料庫尚無資料表 | 資料庫 volume 是在 `db/init.sql` 存在之前建立的；`docker compose down -v` 後重新 `up` |
| 想直接查資料庫 | `docker compose exec db psql -U corpus corpus` |

## 8. 文件

- [docs/DATA_MODEL.md](docs/DATA_MODEL.md)：資料表、狀態流程與追溯方式
- [docs/DEMO_DATA.md](docs/DEMO_DATA.md)：合成測試資料
- [FOLLOW_UP.md](FOLLOW_UP.md)：F1–F14 後續工作
- [PROJECT_STATUS.md](PROJECT_STATUS.md)：目前完成度、實測結果與限制
