# VOTI architecture & contracts

This document is the contract between the backend API, workers and the frontend. Change it first, then code.

## Services (compose)

| service | image | role |
|---|---|---|
| `nginx` | nginx-unprivileged | Only public entry: `0.0.0.0:8080`. Serves the built SPA, proxies `/api/` to `api`. |
| `api` | `voti-backend` | FastAPI (`voti.api.app:app`) on 8000, internal only |
| `worker-scan` ×3 | `voti-backend` | `--queues scan,detail`: URL resolve, channel/playlist listing, caption details (scan first; `SCAN_WORKERS`) |
| `worker-download` ×2 | `voti-backend` | `--queues download`: audio + caption downloads (`DOWNLOAD_WORKERS`, `youtube.max_concurrent_downloads`) |
| `worker-audio` | `voti-backend` | `--queues audio,export` |
| `worker-ml` | `voti-backend` | `--queues ml` (torch; GPU via compose.gpu.yaml) |
| `legacy-ml` | `voti-legacy-ml` | Python 3.8 HTTP inference service: Brouhaha + Vox-Profile, port 8600, internal |
| `db` | pgvector/pgvector (pg17) | Postgres + pgvector |
| `redis` | redis | queue wake-ups |
| `rustfs` | rustfs/rustfs | S3 object storage, internal only |

TLS terminates at an external proxy on another LAN machine which forwards `voti.gugulu.tw` to `<this host>:8080`
with the original `Host` and `X-Forwarded-Proto: https`.

## Deployment / migrations

`voti.db.init_db()` (API startup, every worker startup, `voti init`) runs `create_all` and then the idempotent
statements in `voti.db.MIGRATIONS` (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) under an
advisory lock. Every column added to an existing table must be listed there. Current list: `jobs.run_after`,
index `jobs_running_heartbeat`, `media.discovered_at` (+ partial index `media_discovered`), partial index
`jobs_channel_kind_active` on `jobs (channel_id, kind) WHERE status IN ('queued','running')`, the caption-validation
columns of `segments` (`validity` NOT NULL DEFAULT 'unknown' — metadata-only in Postgres ≥ 11, `invalid_reason`,
`validity_source`, `lid_lang`, `lid_prob`, `align_score`, `validation` JSONB) + partial index `segments_invalid`
on `segments (media_id) WHERE validity = 'invalid'`, `media.captions_empty` (BOOLEAN NOT NULL DEFAULT false; caption
verification). New tables
(e.g. `word_alignments`) come from `create_all` and need no migration.

## Code layout

```
backend/voti/
  config.py db.py models.py storage.py queue.py      ← shared contracts (do not change signatures casually)
  auth.py                                            ← password hashing, sessions, deps (current_user, require_admin)
  api/app.py                                         ← FastAPI app factory, routers mounted under /api
  api/<area>.py                                      ← routers: auth, users, settings, sources, channels, media,
                                                       segments, speakers, quality, datasets, jobs, stats, audio
  sources/youtube.py                                 ← yt-dlp wrapper: resolve/list/detail/download
  sources/handlers.py                                ← job handlers for fetch queue
  pipeline/audio.py                                  ← ffmpeg normalize/probe, bandwidth, loudness, clipping
  pipeline/vad.py                                    ← silero
  pipeline/dnsmos.py
  pipeline/legacy_client.py                          ← HTTP client for legacy-ml (brouhaha, voxprofile)
  pipeline/quality.py diarize.py segment.py embed.py profile.py fingerprint.py cluster.py
  pipeline/align.py                                  ← media.align: MMS_FA forced alignment → word_alignments
  pipeline/validate.py                               ← media.validate: caption ↔ audio check (language ID + alignment)
  pipeline/gpu.py                                    ← CUDA OOM policy (GPU retry → CPU → retryable error)
  pipeline/handlers.py                               ← job handlers for audio/ml queues
  textconv.py                                        ← Simplified → Traditional (OpenCC) + media.convert_text handler
  scheduler.py                                       ← periodic tick (fetch workers): channel auto refresh, status repair
  workers/run.py                                     ← init_db, imports all handlers, runs voti.queue.Worker
  cli.py                                             ← `python -m voti.cli init|create-user|doctor|...`
services/legacy-ml/                                  ← separate image (py3.8), FastAPI-free minimal HTTP server ok
frontend/                                            ← React + Vite + TS + Tailwind SPA
```

## Storage keys

See `voti/storage.py` docstring. Segments are timestamps only; audio for a segment is cut on demand from
`normalized.wav` (`GET /api/audio/segments/{id}`), export materializes clips into the export archive.

### frames_quality.npz

```
hop_sec: float32 scalar        frame hop (brouhaha native, ~0.016875 s; resampled arrays share one hop)
brouhaha_vad: float32[N]       speech probability
snr: float32[N]                dB (brouhaha)
c50: float32[N]                dB (brouhaha)
silero_vad: float32[N]         speech probability resampled to hop_sec
dnsmos_t: float32[M]           window centers (s) of DNSMOS windows (9.01 s windows, 1 s hop over speech)
dnsmos: float32[M,4]           sig, bak, ovrl, p808
```

## legacy-ml HTTP contract

`POST /brouhaha` body `{"key": "<s3 key of 16k wav>"}` → `{"hop_sec": f, "vad": [..], "snr": [..], "c50": [..], "model": "..."}`
(arrays as base64 float32 little-endian under `vad_b64`, `snr_b64`, `c50_b64` to keep payloads small).

`POST /voxprofile` body `{"key": "<s3 key>", "backbone": "whisper-large-v3"|"wavlm-large", "spans": [[start,end],...]}`
→ `{"results": [{"age": f, "age_group": s, "sex": s, "sex_prob": f, "voice_quality": {...}, "emotion": {...},
"emotion_dim": {...}, "speech_flow": {...}, "accent_broad": {...}, "accent_narrow": {...}}...], "model_versions": {...}}`
Spans are clipped/padded by the service to the model's 3–15 s window (longer spans: averaged over 15 s chunks;
shorter than 1 s: result null).

`GET /healthz` → `{"ok": true, "device": "cuda|cpu", "loaded": [...]}`

## Runtime settings (`settings` table, admin-editable)

| key | value |
|---|---|
| `youtube` | `{"rate_limit_kbps": 0, "sleep_between_downloads_sec": 5, "sleep_between_requests_sec": 2, "max_concurrent_downloads": 2, "cookies_enabled": false, "auto_refresh_hours": 24, "verify_captions": true}` — `auto_refresh_hours`: re-list every channel this often to find new videos (0 = off; see "Channel listing & scheduled refresh"). `verify_captions`: `media.detail` fetches the preferred manual caption track to confirm it is not empty (see "Caption verification"); off = trust YouTube's track list (download still drops empty tracks) |
| `pipeline` | `{"voxprofile_backbone": "whisper-large-v3", "diarization_enabled": true, "segment_min_sec": 1.0, "segment_max_sec": 20.0, "edge_pad_sec": 0.1}` |
| `quality_thresholds` | `{"low_speech_ratio": 0.5, "low_snr_db": 10, "clipping_ratio": 0.001}` (used to set segment flags) |
| `validation` | `{"enabled": true, "lid_min_prob": 0.7, "lid_min_speech_sec": 1.0, "align_min_score": 0.4, "align_min_units": 4}` — caption ↔ audio validation (see "Caption validation"). `enabled: false`: `media.validate` does nothing (existing results stay). |
| `text` | `{"convert_simplified": true, "opencc_config": "s2twp"}` — convert Simplified-Chinese captions to Traditional (Taiwan) on ingestion and allow `convert-text` jobs. `opencc_config`: `s2twp` (default: Taiwan standard + Taiwanese phrases, 视频→影片, 软件→軟體), `s2tw`, `s2t`. See "Caption text conversion". |

## Caption track choice (`voti.sources.youtube.choose_tracks`)

Default (no `caption_langs`): the primary track is the best manual track by `caption_rank`:
0 `zh-TW`, `zh-Hant-TW` → 1 `zh-Hant`, `zh-Hant-*` → 2 `zh-HK`, `zh-MO` → 3 `zh`, `cmn`, other `zh-*` (script unknown) →
4 `zh-Hans*`, `zh-CN`, `zh-SG`, `zh-MY` (Simplified; converted to Traditional by OpenCC `s2twp` on ingestion, see below) →
5 `nan*`, `hak*` → 6 anything else (never machine-translated). When the best rank is 3, every other rank-3 manual
track is downloaded too and after download the primary moves to one whose content is Traditional/neutral if the
first one turned out Simplified (`sources.handlers.repick_primary_by_script`). Taiwanese/Hakka manual tracks are
always downloaded in addition. `include_auto` adds YouTube ASR (original language only) with the same ranking; a
manual track is always primary when one exists. A Simplified-only video has `has_manual_captions = true` and is
downloadable by default (its text is converted). Explicit `caption_langs` keep their exact meaning.

## Caption verification (empty CC tracks, `voti.sources.youtube.verify_captions` / `caption_state`)

YouTube metadata sometimes lists an uploader caption track that is empty or does not exist. A track is **empty** when
it has no downloadable format, its URL answers 404/410, the file is zero bytes, it parses to 0 cues (header-only
WebVTT, json3 with only window events), or no cue contains a letter/digit/CJK character after cleaning (only
`[音樂]`, `[Music]`, `♪`, `[掌聲]` …). Network/HTTP failures and unparseable content are `error`, never empty;
`bot_check`/`rate_limited` stop the job as usual (`voti.subtitles.track_content`).

- **media.detail** (when `settings.youtube.verify_captions`, or payload `verify: true`): for a video listing manual
  tracks, fetch the preferred one (caption_rank order, json3 > vtt > srt; Taiwanese/Hakka first when they are the only
  manual tracks); while it is empty try the next one — at most 3 fetches, `sleep_subtitles_sec` (1 s) apart, stop at
  the first ok or error. Stored in `Media.metadata.caption_check = {checked_at, source: "detail"|"download",
  tracks: {lang: {status: ok|empty|error, cues, speech_cues, chars, reason?, format?}}, usable_langs: [ok langs]}`.
  A fresh verification replaces the stored check; without one the stored check keeps applying.
- **Columns** (`caption_state`, also recomputed after every download): no track checked → trust the list.
  Otherwise `has_manual_captions` = a track verified ok, or a verification errored (benefit of the doubt);
  `manual_caption_langs` = verified-ok langs (preference order) then the unverified ones, never empty ones, `[]` when
  `has_manual_captions` is false; `captions_empty` = tracks are listed but none is usable and every checked one was
  empty (unchecked tracks beyond the 3-fetch cap do not count).
- **media.download** classifies every fetched track again before the audio: empty tracks are dropped (when the
  preferred manual track was empty and no other Chinese manual track was fetched, the next manual tracks are tried,
  3 in all); results merge into `caption_check` (`source: "download"`). When nothing usable is left and only
  emptiness is to blame, the job fails with `empty_captions` (no audio downloaded, no retry) → media `skipped`,
  `failure_code = "empty_captions"`, `failure_message` 「YouTube 標示有字幕，但字幕內容是空的（langs）」,
  `captions_empty`/`has_manual_captions` per the rule above. Empty + network errors → `subtitle_unavailable` (retried).
- **Re-check**: `POST /media/{id}/recheck-captions`, `POST /channels/{id}/recheck-captions` → `media.detail` with
  `{force: true, verify: true}`. A re-detail only changes status for `listed`/`failed`/`blocked` → `detailed` and
  `skipped` + `empty_captions` → `detailed` (when a track is usable now); downloaded/processed media only get their
  metadata/caption fields updated, and a failed re-check never moves them.

## Channel listing & scheduled refresh

`Channel.list_status`: `new` (never queued) → `queued` → `listing` → `listed` | `failed`. It follows the
channel's active listing job (`channel.list`, or the `source.resolve` that will chain it — that job gets the
channel's id as soon as it knows it): running `channel.list` → `listing`, queued → `queued`; requeued after a
crash / retry wait → `queued`; final failure / interruption → `failed`; cancelled → back to `listed` (or `new`).
The API derives the shown value from the active job (`ChannelSummary.list_status`), so it can never stay
`queued`/`listing` without a job; the scheduler tick also repairs stored values.

Adding a single video (or any `media.detail` / `media.download` / playlist listing that learns a video's channel)
creates a missing `Channel` immediately (title, handle, follower count from the video info; `metadata.auto_added =
{at, media_id, video_id}` or `{playlist_id}`), sets it `queued` and enqueues `channel.list` (fetch queue, one per
channel — deduplicated). Existing channels are left alone.

Scheduled refresh (`voti/scheduler.py`, run by every fetch worker after each 30 s sweep; a Redis lock
`voti:sched:tick` lets one worker act per 25 s): channels not queued/listing whose
`max(last_listed_at, newest channel.list job) + auto_refresh_hours` is past get `channel.list` with payload
`{"scheduled": true}` — at most 2 per tick and never more than 4 scheduled listings waiting, oldest due first.
A listing that is not the channel's first sets `Media.discovered_at` on the videos it creates; such videos are
`is_new` for 7 days while their status is `listed`/`detailed`. Listing always chains the caption-info sweep
(`media.detail`) for undetailed videos; nothing is downloaded automatically. The tick also re-queues
`media.align` for segments left `pending` > 2 min without an active align job.

## Character alignment (`media.align`, `voti/pipeline/align.py`)

Pipeline: `normalize → quality → diarize → segment → align → validate → embed → profile → fingerprint` (ml queue).
Per active segment, text = `corrected_text` if set else `original_text`, window `[start − 0.2 s, end + 0.2 s]`,
torchaudio `MMS_FA` (with star token) CTC forced alignment; weights downloaded once to
`MODEL_CACHE/mms_fa/mms_fa_ctc_alignment_mling_uroman.pt` (1.26 GB). Units: one per Han character (toneless
pypinyin, phrase-aware — Traditional text is mapped to Simplified with OpenCC `t2s` only for the reading lookup),
one per Latin word; digit/other alphanumeric runs are aligned against a `*` token (time = the star span, score
null); punctuation/space/symbol runs are untimed (`null`). `"".join(units) == text` always.
Skipped (stored with `reason`): `nan*`/`hak*` (`no_romanizer:<lang>` — UI falls back to sentence highlighting),
other languages (`unsupported_language:<lang>`), empty text (`no_text`). Failed: `too_long:<n>s` (> 60 s),
`audio_too_short`, `text_longer_than_audio`. In the pipeline chain a missing model never stops the media (step
result `{"skipped": true, code}`; chain continues). Payload `{"chain": false}` (edits, backfill) never touches
`Media.status`. torchaudio 2.8 warns that `forced_align` is deprecated (still works on 2.8; pin-check on upgrade).

`word_alignments` (one row per segment): `segment_id` PK, `media_id`, `revision`, `text_hash` (sha1[:16] of the
text), `status` aligned|skipped|failed|pending, `reason`, `model_id`, `units` JSONB `[[unit, start|null,
end|null, score|null], …]` (media time, seconds), `created_at` (= last update). Valid while `revision` and
`text_hash` match the segment; otherwise `stale`. `PATCH /segments/{id}` with a text or boundary change marks
the row `pending` and enqueues `media.align {chain: false, segment_ids: [...]}` (joins a queued one); a running
job re-scans pending/stale rows before it finishes.

GPU memory (`voti/pipeline/gpu.py`): ECAPA embedding, pyannote diarization and MMS_FA catch CUDA OOM
(`torch.cuda.OutOfMemoryError` or a CUDA/cuBLAS/cuDNN allocation error anywhere in the cause chain): empty the
cache, retry once on the GPU, then run the rest of the job on the CPU; if the CPU also runs out of memory the job
fails with retryable `out_of_memory` (automatic retry 30 s / 2 min / 10 min). DNSMOS (onnxruntime) falls back to
CPU on any GPU error for the worker's lifetime; Brouhaha / Vox-Profile run in legacy-ml. The CUDA cache is emptied
after every embed / diarize / align job (models stay loaded).

## Caption validation (`media.validate`, `voti/pipeline/validate.py`)

Captions are sometimes not a transcript of the audio: a Mandarin news video inserts English interview clips with
Chinese subtitles, a lecture is subtitled in translation, captions are offset/unrelated. Such segments get
`validity = "invalid"`. They stay playable and reviewable but are excluded **everywhere hours or data are counted**:
dataset preview/export (always; preview `excluded.invalid`), `GET /stats/overview` hours/segment counts, channel
`hours.segments`, media `invalid_count`/`invalid_sec`, speaker embedding/profiling (`media.embed`/`media.profile`
skip them), clustering (`speakers.cluster`), speaker diversity hours and speaker channel totals.

Signals per active segment (ml queue, after `media.align`):
- **Spoken language** — SpeechBrain `speechbrain/lang-id-voxlingua107-ecapa` (VoxLingua107, 107 classes, Apache-2.0,
  ~82 MB in `MODEL_CACHE/speechbrain/lang-id-voxlingua107-ecapa`, downloaded on first use; works offline after) on the
  segment's speech frames (Brouhaha VAD ≥ 0.5; the whole span when no frame data), ≤ 20 s, batched on the GPU.
  Skipped when the speech is shorter than `lid_min_speech_sec`. Top-3 `[code, prob]` stored.
- **Alignment confidence** — mean MMS_FA probability of the scored units (Han characters / Latin words; star and
  untimed units excluded) of the segment's current `word_alignments` row (`align_score`). Only for alignable
  captions (zh*, cmn, en); null for nan*/hak* (not aligned) and other languages.

Decision (`validation` setting):
1. no text (`text_source = none` or blank) → `unknown` (never invalid by this rule);
2. caption family (zh*/cmn → `zh`, en → `en`, ja → `ja`, he → `iw`, …; no language + Han text → `zh`): LID top ≠
   family with prob ≥ `lid_min_prob` → `invalid`, `language_mismatch`;
   nan*/hak*/yue (no VoxLingua107 class — Taiwanese is often heard as zh/vi/th/ko): only a confident top in
   {en, ja, ko, fr, de, es, it, pt, ru, nl, pl, uk, tr, ar, hi, sv, id} → `language_mismatch`;
3. else (not nan/hak/yue) `align_score` < `align_min_score` with ≥ `align_min_units` scored units → `invalid`,
   `text_mismatch`;
4. else `valid` when LID or the alignment score was available, otherwise `unknown`.

Calibration (2026-10-27, 1141 real segments of the 10 ready videos, 6 channels; mean MMS_FA unit probability):
Mandarin speech with its own caption (LID zh ≥ 0.7, n = 882): p1 0.458, p5 0.665, median 0.847; the same audio
aligned against another video's caption of similar length: median 0.289, p99 0.387, max 0.458; English speech
with Chinese (translated) captions (n = 243): median 0.198, p99 0.346, max 0.385. Threshold 0.4 → 7/882 (0.8 %)
Mandarin segments flagged (2–3 s segments whose caption carries ~13 characters/s: caption timing, not speech),
868/875 swapped captions caught (99.2 %). LID: English interview clips 0.92–1.0 `en`; Mandarin 0.97–1.0 `zh`
(short / music-backed Mandarin sometimes zh 0.51 + ko 0.48 → below 0.7, never flagged by LID). Result: DW Chinese
(Mandarin narration + English interviews) 29 valid / 29 language_mismatch / 2 text_mismatch (mixed segments);
硅谷101 conference talk (English lecture, Chinese subtitles) 4 valid (host intro) / 214 language_mismatch /
9 text_mismatch; four Mandarin-only channels 847 valid / 7 text_mismatch. English-caption thresholds are untested
(no English-caption media yet).

Storage (`segments`): `validity` valid|invalid|unknown (default unknown), `invalid_reason`
language_mismatch|text_mismatch|manual|null, `validity_source` auto|manual|null, `lid_lang`, `lid_prob`,
`align_score`, `validation` JSONB `{version, revision, text_hash, alignment_at, expected, lid: [[code, prob]×3]|null,
lid_model, align_units, align_low_fraction, thresholds, at, note?}`. A result is current while version, revision,
text hash and the alignment it used match; the job processes segments without a current result (payload
`{segment_ids}` also re-checks those; `{force: true}` re-checks all). **Manual decisions (`validity_source =
manual`) are never overwritten** by the job.

Jobs: `media.validate` (ml) in the pipeline after `media.align`; a missing model / OOM / disabled setting never
stops the chain (step result `{skipped: true, code}`). A non-chained `media.align` (edit re-alignment, backfill)
chains `media.validate {chain: false, segment_ids}`; a non-chained validate that turned segments valid again
enqueues lazy `media.embed`/`media.profile` (`chain: false`). `media.validate {chain: false}` never touches
`Media.status`. GPU: same OOM policy as embed/align; CUDA cache emptied after the job.

## Caption text conversion (Simplified → Traditional, `voti/textconv.py`)

OpenCC (`opencc` PyPI package, version pinned in requirements/base.txt) with the `text.opencc_config` setting.

Which tracks: language tag first — `zh-Hans`, `zh-Hans-*`, `zh-CN`, `zh-SG`, `zh-MY` are Simplified (converted);
`zh-TW`, `zh-Hant*`, `zh-HK`, `zh-MO` are Traditional (never touched); `zh`, `cmn`, other `zh-*` are decided by
content (characters changed by OpenCC `s2t` = Simplified-only, by `t2s` = Traditional-only; text identical in both
scripts counts for neither; needs ≥ 6 Han characters, ≥ 2 and ≥ 1 % Simplified-only characters and > 3× more
Simplified-only than Traditional-only). Any other language — in particular `nan-*` (Taiwanese Hokkien written in Han
characters), `hak*`, `yue` — is never converted.

On ingestion (`media.download`): `Cue.raw_text` keeps the original, `Cue.text` is the converted cleaned text and gets
the flag `s2t_converted` (only cues whose text changed). The stored subtitle file is unchanged. The decision is
recorded per track in the subtitle asset `params.s2t = {script, source: language|content, converted, config, opencc,
cues_converted, han, simplified_chars, traditional_chars, reason?}` and per media in
`Media.metadata.text_conversion = {converted, tracks: [...], cues_converted, segments_converted, config, opencc, at}`.
Segments built from converted cues get the segment flag `s2t_converted`; their `original_text` is the converted text.

Retroactive: job `media.convert_text` (queue `audio`) converts the cues of a media's not-yet-converted Simplified (or
never examined Chinese) tracks and the `original_text` of their segments **in place** — segment ids, boundaries,
revision, review state and `corrected_text` are kept — in one transaction, sets the flags, updates `params.s2t` /
`metadata.text_conversion` and writes one `audit_log` row (`action = "media.convert_text"`, `entity_type = "media"`,
`after` = counts). Re-running is a no-op for converted tracks. With `text.convert_simplified = false` the job does
nothing and the endpoints answer 409 `text_conversion_disabled`.

## HTTP API

All under `/api`. JSON. Auth by `voti_session` cookie (HttpOnly, SameSite=Lax, Secure when
`X-Forwarded-Proto: https`). State-changing requests require same-origin (`Sec-Fetch-Site: same-origin|none` when sent, else
`Origin` host == `Host`; requests with neither header are refused, so scripts must send `Origin`). Unknown `Host` → 400. Errors: `{"detail": "<message zh-TW>", "code": "<machine code>"}`.
List endpoints: `?limit=&offset=` (default 50, max 500), response `{"items": [...], "total": n}`.
Timestamps ISO-8601 UTC strings. IDs are UUID strings.

### Auth & users
- `POST /auth/login` `{username, password}` → `User`; sets cookie. 401 on failure (rate limited per IP).
- `POST /auth/logout` → 204
- `GET /auth/me` → `User` (401 when not logged in)
- `POST /auth/password` `{current_password, new_password}` → 204
- `GET /users` (admin) → `{items: [User], total}`
- `POST /users` (admin) `{username, display_name, password, role}` → `User`
- `PATCH /users/{id}` (admin) `{display_name?, role?, is_active?, password?}` → `User`

`User = {id, username, display_name, role: "admin"|"annotator", is_active, created_at, last_login_at}`
`UserRef = {id, display_name}` (used wherever a row points at a user)

### Settings (admin)
- `GET /settings` → `{youtube: {...}, pipeline: {...}, quality_thresholds: {...}, text: {...}, validation: {...}, cookies: {present: bool, updated_at}}`
  (`PUT /settings/validation`: `lid_min_prob`, `align_min_score` ∈ [0, 1], `lid_min_speech_sec` ∈ [0, 10],
  `align_min_units` ∈ [1, 100])
- `PUT /settings/{key}` body = value (partial ok, merged over the current value; unknown fields → 422) → updated value
- `PUT /settings/cookies` multipart `file` (Netscape cookies.txt) → `{present: true, updated_at}`; `DELETE /settings/cookies`

### Sources (adding things)
- `POST /sources/resolve` `{url}` → `{type: "video"|"channel"|"playlist", channel?: Channel, playlist?: Playlist, media?: Media, job: Job}`
  Accepts video URLs (watch, youtu.be, shorts), channel URLs (/@handle, /channel/UC…, /c/…, with or without tab
  suffix), playlist URLs. Creates rows synchronously when cheap and enqueues `source.resolve` / listing jobs.

### Channels
- `GET /channels?q=` → `{items: [ChannelSummary], total}`
- `GET /channels/{id}` → `ChannelDetail`
- `POST /channels/{id}/refresh` → `Job` (re-list; returns the active listing job when one is queued/running)
- `POST /channels/{id}/align` `{force?: false}` → `{queued: n}`: `media.align {chain: false}` for every media of the
  channel with active segments lacking a valid alignment (all of them with `force`)
- `POST /channels/{id}/validate` `{force?: false}` → `{queued: n}`: `media.validate {chain: false}` for every media of
  the channel with active segments (without a current validation unless `force`)
- `POST /validate/backfill` (admin) `{force?: false}` → `{queued: n}`: the same for every media with active segments
- `POST /channels/{id}/detail` `{media_ids?: [...]}` → `{queued: n}` (fetch full metadata for listed media; all un-detailed when omitted)
- `POST /channels/{id}/recheck-captions` → `{queued: n}`: re-fetch metadata + re-verify captions of every media of the
  channel that claims or claimed manual captions (`has_manual_captions` or `captions_empty`), excluding media already
  queued/downloading/downloaded/processing/ready and members-only. One chained `media.detail` batch job
  (`{media_ids, batch_size: 20, force: true, verify: true, recheck: true}`; re-queued at the end of the detail queue
  after each batch → rate-limited and round-robin with other channels). While one is active the call queues nothing
  and returns its media count. Progress: the channel's `media.detail` jobs.
- `POST /channels/{id}/convert-text` (any logged-in user) → `{queued: n}`: one `media.convert_text` job for every media
  of the channel that has a convertible caption track (Simplified, or Chinese never examined); 409
  `text_conversion_disabled` when the `text` setting is off
- `PATCH /channels/{id}` `{notes}` → `ChannelDetail`
- `GET /playlists?channel_id=` → `{items: [Playlist], total}`
- `GET /playlists/{id}` → `Playlist`

`Playlist = {id, platform, platform_playlist_id, channel_id, title, description, url, list_status, last_listed_at,
 created_at, media_count, thumbnail_url}` (thumbnail from the playlist's extractor metadata when present)

`ChannelSummary = {id, platform, title, handle, url, thumbnail_url, subscriber_count, video_count,
list_status: new|queued|listing|listed|failed, last_listed_at,
counts: {listed, detailed, with_manual_captions, downloaded, ready, failed, new}, hours: {listed, ready, speech,
segments (active segments that are not invalid), invalid (invalid segments)},
listing_job: {id, kind: "channel.list"|"source.resolve", status: queued|running, progress, phase, message, run_after}|null,
detail_progress: {detailed, total, failed, active: bool}, next_refresh_at: ISO|null}`
(`counts.listed` = all videos of the channel; `counts.new` = `is_new` videos; `detail_progress`: caption info
fetched for `detailed` of `total` videos, `failed` = undetailed and failed/blocked, `active` = a media.detail job
for the channel is queued/running — the UI shows "字幕資訊檢查中 detailed/total" while active. The channel page
shows its loading screen while `list_status` is `queued`/`listing` (progress from `listing_job`).
`next_refresh_at`: null when auto refresh is off; a past time means due at the next tick.)
`ChannelDetail = ChannelSummary + {description, banner_url, country, tags, notes, metadata,
speakers: {candidates: n, total_sec}, quality: {snr_mean, dnsmos_ovrl, c50_mean, speech_ratio, real_bandwidth_hz,
media_with_quality}, kind_counts: {video: n, short: n, live?: n, ...}, playlist_count: n}`

### Media (videos)
- `GET /media` — filters (all optional):
  `channel_id, playlist_id, q (title/description), kind (video|short|live, comma list), status (comma list),
   captions (manual|auto|any|none|empty), include_auto (bool, default false — auto-only videos hidden unless true or captions=auto),
   caption_lang, license (youtube|creative_commons), min_duration, max_duration, published_after, published_before,
   detailed (bool), upsampled_suspect (bool: media_quality.upsampled_suspect is / is not true),
   downloaded (bool: true = an original_audio asset exists or status ∈ downloaded|processing|ready; false = the rest),
   is_new (bool), sort (published_at|-published_at|duration|-duration|views|-views|title), limit, offset`
  → `{items: [MediaSummary], total, facets: {kind: {video: n, short: n}, captions: {manual: n, auto_only: n, none: n, unknown: n, empty: n}, license: {...}, status: {...},
     downloaded: {yes: n, no: n}, new: n}}` (each facet ignores its own dimension)
  `captions_empty` media count as `none` (also when YouTube ASR exists) and never as `manual`/`auto_only`/`unknown`;
  `empty` is the subset of `none` with `captions_empty` (`captions=empty` filters them).
- `GET /media/{id}` → `MediaDetail`
- `POST /media/download` `{media_ids?: [...], filter?: <same query object as GET /media>, caption_langs?: ["zh-TW", ...], include_auto: false}`
  → `{queued: n, skipped: n, skipped_reasons: {already_downloaded|members_only|empty_captions|no_usable_captions|not_found: n}}`.
  Either explicit ids or "everything matching filter" (select-all). Media without the required captions are skipped
  unless include_auto/no-caption allowed (POC: skip when no usable track); `captions_empty` media are skipped as
  `empty_captions` unless `include_auto` and the media has YouTube ASR.
- `POST /media/{id}/recheck-captions` → `Job` (`media.detail {force: true, verify: true}`; an already queued detail
  job of the media is upgraded to verify and returned). See "Caption verification".
- `POST /media/{id}/retry` → `Job` (re-run the failed step)
- `POST /media/{id}/reprocess` `{from_step: "media.quality"|...}` → `Job`
- `POST /media/{id}/convert-text` (any logged-in user) → `{queued: 0|1}` (0 when nothing is convertible; 409
  `text_conversion_disabled` when the `text` setting is off). Progress: the media's jobs (`kind = "media.convert_text"`).
- `GET /media/{id}/timeline` → `{duration, hop_sec, vad: [..downsampled ≤2000 pts], snr: [...], speech_regions: [[s,e]],
   diarization: [{speaker, start, end, overlap}], repeated: [{id, start, end, position, excluded}], cues: [{start,end,text}],
   segments: [SegmentSummary]}`
- `GET /media/{id}/segments` → `{items: [SegmentSummary], total}` — every active segment of the media in time order,
  unpaginated (transcript; includes `review_status`, `text`, `start_sec`/`end_sec`, `revision`)
- `GET /media/{id}/words` → `{words: {segment_id: [[unit, start|null, end|null], ...]}, segments: n,
  status: {aligned|stale|pending|skipped|failed|none: n}, job_active: bool}` — character timing for the media page
  player (only alignments valid for the segment's current text/revision)
- `GET /media/{id}/speakers` → `[MediaSpeaker]` — the diarized speakers of one media in **label order**
  (`SPEAKER_00`, `SPEAKER_01`, …; every label of the diarization turns ∪ the active segments' `local_speaker`).
  `color_index` = position in that list — the same order the timeline speaker lane, the speaker cards and every
  speaker-coloured highlight use (the UI maps it onto the categorical chart palette `--series-1…8`). Invalid segments
  are excluded from every number, the profile and the samples (`invalid_count` reports them). `[]` without diarization.
  `MediaSpeaker = {local_speaker, color_index, segment_count, total_sec (sum of segment durations), diarized_sec
  (sum of diarization turns), invalid_count, first_sec|null (first segment start), profile: {sex: {label: p},
  sex_top|null, sex_prob|null, age_mean|null, age_group: {label: p}, emotion: {label: p}, voice_quality: {label: p}
  (multi-label: mean sigmoid, not normalized), speech_flow: {label: p}, accent_broad: {label: p} (English-only
  model — reference only)}, profiled_segments, cluster: {id, label, display_name, share}|null (the current-run
  candidate speaker holding most of this speaker's assigned seconds; `share` = that fraction), sample_segment_ids
  (≤ 5 representative segments: single-speaker, 2–15 s, highest SNR first; returned in time order)}`.
  Profile aggregation: one VoiceProfile per segment (current revision; the configured backbone preferred), weighted by
  segment duration; distributions normalized to sum 1 (sex from `model_versions.sex_probs` when stored, else
  `sex`/`sex_prob`).
- `POST /media/{id}/align` `{force?: false}` → `{queued: 0|1}` (0 when the media has no segments)
- `POST /media/{id}/validate` `{force?: false}` → `{queued: 0|1}` (`media.validate {chain: false}`; 0 without segments)
- `GET /media/{id}/quality` → `MediaQuality` (all columns) + `details` (passed through as stored; the pipeline stores
  `details.spectrum: [{hz, db}]`, the long-term average spectrum of the original audio, when available)

`MediaSummary = {id, platform, platform_media_id, url, kind, title, thumbnail_url, duration_sec, published_at, view_count,
 license, has_manual_captions, has_auto_captions, manual_caption_langs, auto_caption_langs,
 captions_empty: bool (listed CC tracks are all empty), caption_check: metadata.caption_check|null, status, stage, failure_code,
 channel: {id, title}, quality?: {snr_mean, dnsmos_ovrl, real_bandwidth_hz, speech_ratio, sample_rate, upsampled_suspect},
 segment_count, discovered_at (found by a re-listing)|null, is_new (discovered ≤ 7 days ago and still listed/detailed),
 downloaded: bool, invalid_count (active segments with validity invalid), invalid_sec}`
`MediaDetail = MediaSummary + {description, tags, categories, chapters, language, like_count, comment_count, availability,
 age_limit, metadata, failure_message, jobs: [Job], tracks: [Track], quality: MediaQuality|null,
 text_converted: bool (some track converted Simplified → Traditional), text_convertible: bool (convert-text would
 still change something), text_conversion: metadata.text_conversion|null, validity_counts: {valid, invalid, unknown},
 validation_job_active: bool (a media.validate job is queued/running)}`
`Track = {id, language, kind, format, s2t_converted: bool, script: simplified|traditional|neutral|mixed|unknown|not_chinese|null
 (null = never examined), cues_converted: n|null}`

### Segments & review
- `GET /segments` — filters: `media_id, channel_id, speaker_id, review_status (comma; the review list sends
   unchecked), text_source (comma), q (original or corrected text), min_snr, min_dnsmos, min_speech_ratio, min_c50,
   min_duration, max_duration, language (comma, prefix match: zh → zh-TW), flags (comma, any-of), exclude_flags,
   has_profile, validity (comma: valid|invalid|unknown; default all — invalid segments are listed with their badge),
   sort, limit, offset`
   (sort: `start` = `media` (default: media newest-published first, then time), `snr`, `-snr`, `dnsmos`, `-dnsmos`,
   `duration`, `-duration`, `speech_ratio`, `-speech_ratio`, `created_at`, `-created_at`, `-updated_at`)
   → `{items: [SegmentSummary], total}` (items carry `media_title`, `channel_title`, `duration`)
- `GET /segments/{id}/neighbors?<same filters + sort>` → `{prev_id|null, next_id|null, index (0-based)|null, total,
   in_filter: bool}` — position in the list GET /segments returns for those filters; the segment itself is always
   placed in the ordering (also after it stopped matching, e.g. just marked passed in an `unchecked` list), so the
   reviewer's next/prev keep working; `total` counts only matching segments
- `GET /segments/{id}` → `SegmentDetail`
- `PATCH /segments/{id}` `{corrected_text?, review_status?, review_note?, speaker_state?, start_sec?, end_sec?,
  validity?: "valid"|"invalid"|"auto"}` → `SegmentDetail`. `validity` valid/invalid is a human decision
  (`validity_source = manual`, `invalid_reason = manual` when invalid; audit-logged as `segment.review`; never
  overwritten by the automatic check); `auto` drops the manual decision and re-queues `media.validate` for the
  segment. Turning a segment valid enqueues lazy `media.embed`/`media.profile`. A text or boundary change re-aligns
  and then re-validates the segment (manual decisions kept).
  (boundary change bumps revision, re-enqueues embed/profile for that media lazily; a text or boundary change
  marks the alignment pending and enqueues `media.align` for the segment. Inline review on the media page:
  `{review_status: "passed"|"needs_fix", corrected_text?}`; `corrected_text: null` restores the caption text)
- `PUT /segments/{id}/speaker` `{speaker_name}` / `DELETE` → override
- `GET /segments/{id}/similar?k=10` → `[{segment: SegmentSummary, similarity}]` (pgvector cosine)

`SegmentSummary = {id, media_id, media_title, channel_title, start_sec, end_sec, duration, text (corrected or original),
 original_text, corrected_text, text_source, local_speaker, speaker: {id, label, display_name}|null, speaker_state,
 speech_ratio, snr, c50, dnsmos_ovrl, dnsmos_p808, flags, review_status, revision, profile?: {age, sex, emotion_label},
 validity: valid|invalid|unknown, invalid_reason: language_mismatch|text_mismatch|manual|null,
 validity_source: auto|manual|null, lid_lang (VoxLingua107 code, e.g. en)|null, lid_prob|null, align_score|null}`
`SegmentDetail = SegmentSummary + {track_id, cue_ids, dnsmos_sig, dnsmos_bak, clipping_ratio, lufs, repeated_overlap,
 review_note, reviewed_by: UserRef|null, reviewed_at, profile: VoiceProfile|null, history: [AuditLog],
 words: [{t, s: start|null, e: end|null}]|null (character timing of `text`; null unless aligned and current),
 alignment: {status: aligned|skipped|failed|pending|stale|none, reason|null, model_id|null, updated_at|null},
 validation: segments.validation JSONB|null (lid top-3, expected language, thresholds, …)}`
`AuditLog = {id, at, user: UserRef|null, action, entity_type, entity_id, before, after}` (newest first)

### Audio
- `GET /audio/segments/{id}?pad=0.3&rev=<revision>` → `audio/wav` cut from normalized audio (supports HEAD).
  `rev` only busts caches: when it equals the segment's current revision the response is cacheable
  (`private, max-age=86400`), otherwise `private, no-cache` (+ `ETag`, `X-Segment-Revision`).
  `from=<absolute media seconds>` (optional, ≥ 0) starts the clip there instead — "play from this character to the
  end of the sentence" (karaoke click-to-seek); clamped into `[start − pad, end + pad − 0.05]`. `X-Clip-Start` /
  `X-Clip-End` give the clip's real bounds (the start is also clamped at 0 s).
- `GET /audio/media/{id}` → normalized wav, HTTP Range supported (proxied from S3)

### Speakers (diversity)
- `POST /speakers/cluster` `{threshold: 0.0-1.0, scope: {channel_ids?: [...]}}` → `Job`
- `GET /speakers/runs` → list of ClusterRun
- `GET /speakers?run_id=(default current)&channel_id=&q=(label/display_name)&sort=-total_sec` → `{items: [Speaker], total}`
- `GET /speakers/{id}` → `Speaker + {segments: [SegmentSummary (top 50)], channels: [{id,title,total_sec}]}`
- `PATCH /speakers/{id}` `{display_name?, verified?}`
- `GET /speakers/diversity?channel_id=` → `{by_sex: {male: sec, female: sec, unknown: sec}, by_age_group: {...},
   by_emotion: {...}, by_channel: [{channel_id, title, speakers: n, total_sec, top_speaker_share}],
   speaker_hours: [{speaker_id, label, total_sec}], gini: f}`

`Speaker = {id, run_id, label, display_name, verified, segment_count, total_sec, media_count, channel_ids,
 profile: {sex: {male: p, female: p}, age_mean, age_group: {...}, emotion: {...}, voice_quality: {...}}}`

### Quality overview
- `GET /quality/summary?channel_id=` → `{histograms: {snr: [{bin, count}], dnsmos_ovrl: [...], c50: [...],
   bandwidth_hz: [...], speech_ratio: [...]}, sample_rates: {44100: n, 48000: n}, codecs: {...}, upsampled_suspect: n, media_count: n}` (per media)

### Repeated regions (intro/outro)
- `GET /channels/{id}/repeated` → `[{group_id, position, duration, match_count, excluded, region_count, examples: [{media_id, media_title, start, end}]}]`
- `PATCH /repeated/{group_id}` `{excluded: bool}` → the updated group (same shape) + `{regions, segments_updated}`;
  applies to all regions of the group, recomputes segment repeated_overlap
- `POST /channels/{id}/dedup` → `Job`

### Datasets & export
- `GET /datasets`, `POST /datasets` `{name, description, filters}`, `GET/PATCH/DELETE /datasets/{id}`
- `POST /datasets/preview` `{filters}` → `{segment_count, total_sec, speakers: n, channels: n, by_channel: [...],
   by_sex: {...}, excluded: {repeated: n, auto_caption: n, low_quality: n, speaker_cap: n, invalid: n}}`
   (invalid segments are always excluded — not a filter; `excluded.invalid` = segments matching every other
   criterion but invalid)
- `POST /datasets/{id}/export` `{format: "jsonl"|"csv", include_audio: true}` → `DatasetExport`
- `GET /datasets/{id}/exports` → `{items: [DatasetExport], total}`; `GET /exports/{id}` → `DatasetExport`;
  `GET /exports/{id}/download/{manifest.jsonl|manifest.csv|README.md|dataset.tar}`

`Dataset = {id, name, description, filters (defaults filled in), created_by: UserRef|null, created_at, updated_at,
 export_count, segment_count|null, total_sec|null (from the latest completed export), last_export: DatasetExport|null}`
`DatasetExport = {id, dataset_id, status: queued|running|completed|failed, format, include_audio, filters (frozen),
 segment_count, total_sec, stats, progress (0..1, from its job), size_bytes|null, files: ["manifest.jsonl", ...]
 (downloadable names), file_details: [{name, size_bytes, url}], job: Job|null, created_by, created_at, finished_at}`

#### Dataset filters
```json
{
  "channel_ids": [], "media_ids": [], "speaker_ids": [],
  "text_sources": ["manual_caption"],          // add "auto_caption" to include YouTube ASR captions
  "review_status": ["passed", "unchecked"],
  "exclude_repeated": true, "max_repeated_overlap": 0.2,
  "speaker_states": ["single", "unknown"],
  "min_duration": 1.0, "max_duration": 20.0,
  "min_snr": 10, "min_c50": null, "min_dnsmos_ovrl": 2.8, "min_dnsmos_p808": null,
  "min_speech_ratio": 0.6, "min_bandwidth_hz": 7000, "exclude_upsampled": false,
  "exclude_flags": ["clipped"], "licenses": [], "sex": [], "age_groups": [],
  "max_sec_per_speaker": null
}
```

Manifest row: `{segment_id, revision, audio_path, start_sec, end_sec, duration, text, text_source, text_corrected,
text_converted (caption text was converted Simplified → Traditional, segment flag s2t_converted), language,
media: {platform, id, url, title, published_at, license}, channel: {id, title}, speaker: {id, label, display_name},
quality: {snr, c50, dnsmos_*, speech_ratio, bandwidth_hz, sample_rate}, profile: {...}, sha256}`

### Jobs & stats
- `GET /jobs?status=&kind=&media_id=&limit=` → `{items: [Job], total, counts: {queued, running, failed, ...}}`
- `POST /jobs/{id}/retry`, `POST /jobs/{id}/cancel`
- `GET /stats/overview` → `{channels, media: {listed, detailed, downloaded, ready, failed}, hours: {listed, downloaded,
   speech, segments, reviewed, invalid}, segments: {total, passed, needs_fix, invalid}, speakers: {candidates, verified}, storage_bytes,
   workers: [{name, queues, alive, last_seen_at, current_job: {id, kind, media_title}|null}],
   queue_depth: {fetch, audio, ml, export}}` (`hours.segments`/`reviewed` and `segments.total`/`passed`/`needs_fix`
   exclude invalid segments; `hours.speech` is media-level VAD speech; workers: Redis `voti:worker:<name>` announcements, plus workers that
   still own running jobs but stopped announcing → `alive: false`, `last_seen_at` = job heartbeat)
- `GET /healthz` → `{ok: true}` (no auth)

`Job = {id, kind, queue, status, progress, phase, message, error_code, error_message, attempts, media_id, channel_id,
 media_title?, channel_title? (job's channel, else its media's channel), payload, result, created_at, started_at,
 finished_at, eta_sec? (linear estimate from progress and started_at), run_after (queued for an automatic retry:
 not started before this time; null otherwise)}`
- `GET /jobs/{id}` → `Job`
- `POST /jobs/{id}/retry` resets `attempts` to 0 and clears `run_after` (a manual retry gets the automatic retries again).

## Job recovery (`voti/queue.py`)

Postgres `jobs` is authoritative; Redis only carries ids (`voti:q:<queue>` lists, `voti:delayed:<queue>` sorted sets
scored by `run_after`). Claiming is one atomic `UPDATE … WHERE status='queued' AND (run_after IS NULL OR run_after <= now)`,
so duplicate ids in Redis never cause double execution; a popped id that is not yet due is parked in the delayed set.

| what | timing |
|---|---|
| worker key `voti:worker:<hostname>:<pid>` | refreshed every 15 s by a heartbeat thread, `ex=45` s (deleted on clean exit) |
| `jobs.heartbeat_at` of the running job | same thread, every 15 s, independent of handler progress (plus every `ctx.progress()`) |
| orphan: worker key gone **and** heartbeat > 45 s old | requeued at the next sweep |
| orphan: heartbeat > 2 min old (even if the key exists) | requeued at the next sweep |
| sweep (orphans, delayed promotion, Redis re-push) | every 30 s and at every worker start; delayed ids are promoted every loop (≤ 2 s) |
| graceful shutdown (SIGTERM/SIGINT) | stop taking jobs; handler interrupted at its next `ctx.progress()`; if still busy after `VOTI_SHUTDOWN_GRACE_SEC` (8 s) the row is requeued and the process exits. Compose `stop_grace_period: 20s`. A second signal forces it. |
| automatic retry of transient errors | after 30 s, 2 min, 10 min (`max_attempts` = 4 runs for new jobs) |

Outcomes visible on `Job` (status / phase / message):
- **Shutdown** — `queued`, phase `requeued`, message "worker 停止中：工作已放回佇列…", attempt **not** counted, pushed to
  the front of its queue: resumes as soon as a worker of that queue is back. A step that was mid-way simply re-runs.
- **Recovered** (worker crashed / container killed / host rebooted / network partition) — `queued`, phase `recovered`,
  message "已自動恢復：<reason>…", attempt counted. A worker that restarts on the same host (same container hostname)
  recovers its previous life's jobs immediately at startup. With `attempts >= max_attempts` the job becomes
  `interrupted` (`error_code = "interrupted"`, zh-TW message) instead; its media becomes `failed`.
  A recovered job whose cancel was requested becomes `cancelled`.
- **Retry wait** — transient error: `queued`, phase `retry_wait`, `run_after` = next start, `error_code`/`error_message`
  keep the last error, message "暫時性錯誤（code），N 秒後自動重試（第 k/max 次）". When the attempts are exhausted the job
  ends `failed` (or `blocked` for blocked+retryable errors such as `legacy_ml_unavailable`).
  Transient = `JobError(retryable=True)` (legacy-ml unreachable / timeout / HTTP 5xx, YouTube `network_or_extractor`,
  `subtitle_unavailable`, `audio_unavailable`, S3 audio fetch failing on connection errors) or an unclassified
  exception whose cause chain is a connection reset / refused / timeout, httpx transport error or 5xx, botocore
  connection error or S3 5xx/SlowDown, SQLAlchemy/psycopg `OperationalError`, Redis connection error.
- **Blocked** errors that need a person (bot check, 429 rate limit, cookies, age/members/region restriction, HF token or
  model terms, DNSMOS model missing) are never retried automatically.
- **Redis loss** (restart without the AOF, flush): every sweep and every worker start re-pushes queued jobs that are in
  neither the list nor the delayed set (the periodic sweep skips jobs younger than 10 s).

Media / entity state follows the job: a requeued `media.download` puts the media back to `queued`; pipeline steps keep
`processing` while queued for a retry; a terminal failure/interruption sets the media `failed`/`blocked`
(`skipped` for `no_usable_captions`, `members_only`, `empty_captions` — `queue.SKIP_CODES`), `playlist.list` `listing` → `failed`, a dataset export
`queued` while waiting and `failed` at the end. `Channel.list_status` follows `channel.list` / `source.resolve`
(requeued/recovered → `queued`, terminal → `failed`, cancelled → `listed`/`new`; see "Channel listing").
`media.convert_text`, `media.align` and `media.validate` with `chain: false` never change `Media.status`.

Idempotency: every handler can re-run after a partial run — rows are replaced in one transaction (cues per track,
speech regions, diarization turns, repeated regions, playlist items), upserted (segments by uuid5 id, voice profiles,
embeddings `ON CONFLICT DO NOTHING`, assets by S3 key), and S3 objects are overwritten under the same key. A stale
`original.<ext>` is deleted only after the new download committed. A download slot held by the interrupted attempt
(or by a job that is no longer running) is taken over instead of waiting for its 15 min expiry.
