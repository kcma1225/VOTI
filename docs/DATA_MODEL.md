# 資料結構與狀態流程

所有資料都存在 `CORPUS_DATA_DIR`（預設 `./data`）：

```
data/
  （資料庫在 PostgreSQL；schema 只定義在 db/init.sql，沒有 migration）
  assets/<sha[:2]>/<sha>.<ext>   # 內容定址檔案，檔名即 SHA-256
  models/speechbrain/     # 模型快取（可刪除，會重新下載）
  exports/<export_id>/    # 匯出快照
```

## 資料表

| 表 | 用途 | 重點欄位 |
|---|---|---|
| `sources` | 一部影片或一個本地檔案 | `platform`+`video_id` 唯一（本地檔以音訊 SHA-256 當 id）；`license_label`/`license_evidence`/`license_checked_at`；`taiwan_basis`；`subtitle_kind`；`status`；`failure_reason`；`metadata_json` |
| `assets` | 每個實體檔案 | `kind`：`original_audio`、`original_subtitle`、`source_metadata`、`normalized_audio`、`segment_audio`；`sha256`、`bytes`、`params_json`（轉檔參數、上游資產） |
| `tracks` | 字幕軌 | `language`；`kind`：`uploader_provided`/`auto_generated`/`translated`/`local_provided`/`unknown` |
| `cues` | 字幕條目，保留原文 | `raw_text`、`cleaned_text`、`clean_version`、`flags_json`（overlap、rolling、越界等） |
| `segments` | 切好的音段（目前版本） | `cue_ids_json`、起訖、`audio_asset_id`、`revision`、`original_text`（不可變）、`corrected_text`（人工）、`review_status`、`review_level`、`speaker_state`、`embedding_status`、`active` |
| `segment_versions` | 每個音訊 revision 的邊界、文字與切片資產 | 改邊界時寫入前後兩版 |
| `review_history` | 審查與群組覆寫的操作紀錄 | `before_json`/`after_json` |
| `embeddings` | 真實模型向量 | 以 (`segment_id`,`segment_revision`,`model_id`) 為唯一；192 維 float32 |
| `cluster_runs` / `cluster_assignments` | 每次分群的參數與結果 | 演算法、門檻、子集合；`candidate_###` |
| `cluster_overrides` | 人工群組指定 | 跨分群重跑時保留，優先順序高於候選群 |
| `word_alignments` | 字詞層級時間（MMS_FA 強制對齊） | 每 cue 的單位依序可拼回 `cleaned_text`；標點、數字 `start_sec` 為 NULL；`score`、`method`、`model_id` |
| `jobs` | 持久化工作佇列 | `kind`（含 `local_import`、`segment`、`alignment`）、`status`、`phase`、進度（加權 0–1000）、`claimed_at`、`error_code`、`attempts`、`cancel_requested` |
| `exports` | 匯出紀錄 | 篩選條件、筆數、`snapshot_json` |

## 狀態流程

**來源 `sources.status`**

```
candidate ─(檢查)→ inspected ─(確認授權並取得)→ acquired → normalized → segmented
                         └→ blocked（需登入、地區限制或無字幕）   └→ failed（附 failure_reason）
本地匯入：直接 acquired；有字幕時接著 normalized → segmented
```

**工作 `jobs.status`**

```
queued → running → completed
               ├→ failed（可重試）
               ├→ blocked（缺依賴、缺 key、存取受限、模型不可用；修正後可重試）
               ├→ cancelled（使用者取消；在檢查點生效）
               └→ interrupted（worker 中斷，重新啟動時標記；可重試）
```

同一來源、同一類型，同時只會有一個進行中的工作（部分唯一索引）。沒有對應來源的工作則依 kind+payload 去重。

**音段**

- `review_status`：`unchecked` → `passed` / `needs_fix` / `excluded`
- `review_level`：`none` / `sampled` / `fully_corrected`
- `speaker_state`：`unknown` / `single` / `multiple` / `overlap`。標為 `multiple` 或 `overlap` 的音段不計算 embedding，也不參與分群。
- `embedding_status`：`pending` → `ready` / `failed`

**修改規則**

- 只改文字或狀態：寫入 `corrected_text` 等欄位，並在 `review_history` 記錄修改前後的值（`original_text` 永不覆寫）。音訊沒有變，`revision` 不變，向量繼續有效。
- 改邊界：從 16 kHz 正規化音訊重新切片，產生新資產，`revision` 加 1，`embedding_status=pending`，舊向量不再被查詢。
- 重新切片（重新匯入字幕）：用 uuid5 產生相同 id 的音段，舊的不刪除，改設為 `active=0`。

## 追溯鏈

匯出 manifest 的每一列都可以往回追到原始來源：

```
manifest 列 → segment_id + revision + text_source (human_revision | 字幕種類)
            → audio_sha256 → assets(segment_audio).params_json → normalized_audio → original_audio
            → cue_ids → cues.raw_text → tracks → sources(url, license_evidence, checked_at)
```

## 統計口徑

- 候選時數：已檢查的來源長度。**不是可用時數。**
- 已取得時數：只計算已取得且未失敗的來源。
- 音段時數：只計算 `active=1` 的音段。另外提供依 SHA-256 去除重複後的數字。
- 已審查時數：`review_status=passed`。
- 候選群數：最近一次分群的群數，**不等於語者人數**。
