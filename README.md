# VOTI 台語語料工作台

從 YouTube 頻道收集台灣語音、自動分析音質與語者，人工審查後組成資料集。

- 網址：https://voti.gugulu.tw（TLS 由內網另一台機器的 proxy 處理，本機服務在 `:8080`）
- 論文：[`paper/paper.pdf`](paper/paper.pdf)
- 架構與 API 契約：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## 流程

1. **新增來源**：貼上影片、頻道或播放清單網址。頻道會列出一般影片與 Shorts，並抓取完整 metadata 與字幕資訊。
2. **篩選與下載**：依原生 CC 字幕（YouTube 自動字幕預設不收，可手動勾選）、長度、日期、類型、授權、關鍵字篩選，可勾選或「全選符合條件」批次下載。下載有限速，必要時可在設定頁上傳 cookies.txt。
3. **自動分析**（每部影片）：
   正規化 16 kHz → 音質（Brouhaha VAD／SNR／C50、Silero VAD、DNSMOS SIG/BAK/OVRL/P.808、原始音檔實際頻寬與升頻偵測、取樣率／編碼、響度、削波）→ pyannote 語者分段 → 依字幕切音段（以 VAD 修正邊界）→ ECAPA 語者向量 → Vox-Profile（年齡、性別、聲音特質、情緒、流暢度、英文口音僅供參考）→ 同頻道片頭片尾重複偵測。
4. **審查**：波形＋VAD／SNR／語者／重複區段軌道，修訂文字與邊界、設定狀態（有鍵盤快捷鍵）。
5. **語者多樣性**：分群出候選語者，查看性別、年齡、情緒、各頻道語者集中度與 Gini。
6. **資料集**：依品質門檻、排除重複片頭、排除自動字幕、每位語者時數上限等條件即時預覽，匯出 JSONL／CSV＋音檔 tar。

## 啟動

```bash
cp .env.example .env      # 設定 ADMIN_PASSWORD、HF_TOKEN 等
docker compose up -d --build                                   # CPU
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build  # GPU（需 nvidia-container-toolkit，TORCH_VARIANT=cu128）
```

本機目前在 `.env` 設了 `COMPOSE_FILE=compose.yaml:compose.gpu.yaml:compose.dev.yaml`，所以直接 `docker compose …` 就是 GPU 模式（GPU 編號由 `GPU_DEVICE_IDS` 指定，預設 1）。

- 初始管理員：`.env` 的 `ADMIN_USERNAME`／`ADMIN_PASSWORD`，**只在資料庫還沒有任何使用者時套用一次**。
- `HF_TOKEN`：pyannote `speaker-diarization-community-1` 需要先在 Hugging Face 同意條款。
- 模型快取在 `./models`（首次使用會下載；Vox-Profile Whisper 組約 37 GB、WavLM 組約 9 GB）。
- 只有 nginx 對外（`${BIND_ADDR}:${HTTP_PORT}`，預設 `0.0.0.0:8080`）。應用程式只接受 `PUBLIC_HOSTS` 與 localhost 的 Host；proxy 需保留原始 `Host` 並送 `X-Forwarded-Proto: https`。建議防火牆只允許 proxy 連 8080。

| 服務 | 用途 |
|---|---|
| `nginx` | 前端 SPA＋`/api` 反向代理 |
| `api` | FastAPI |
| `worker-fetch` | YouTube 列表、metadata、下載 |
| `worker-audio` | 正規化、切音段、資料集匯出 |
| `worker-ml` | 音質、語者分段、語者向量、重複偵測、分群（GPU） |
| `legacy-ml` | Brouhaha＋Vox-Profile 推論服務（GPU） |
| `db` / `redis` / `rustfs` | PostgreSQL＋pgvector、工作佇列、S3 物件儲存 |

常用指令：

```bash
docker compose ps
docker compose logs -f worker-ml
docker compose exec api python -m voti.cli create-user <name> --role annotator
docker compose exec api python -m voti.cli reset-password admin
```

## 開發

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d db redis rustfs   # 基礎服務開到 127.0.0.1
cd frontend && npm ci && npm run dev:mock     # 前端用假資料開發
cd frontend && npm run dev                    # 前端接 http://127.0.0.1:8000 的 API
```

後端測試（在 `backend/`）：見 `backend/tests/conftest.py` 的環境變數說明，例如
`VOTI_TEST_DB=voti_test_api VOTI_TEST_BUCKET=voti-test-api python -m pytest tests/test_api_*.py`。

前端視覺驗收：`BASE_URL=http://127.0.0.1:8080 VOTI_USER=admin VOTI_PASS=… npm run shots`（截圖存到 `screenshots/`，同時檢查各寬度沒有水平捲動）。

## 授權注意

- Vox-Profile 模型權重僅限非商業研究使用（RAIL）；Brouhaha 為 MIT；pyannote community-1 需同意條款。
- 影片授權只記錄（標準授權／CC），不阻擋下載；資料集匯出會標記每筆的授權，能否發布需另外確認。
- 本系統不讀取瀏覽器 cookies，cookies.txt 只在管理員上傳並啟用時使用。
