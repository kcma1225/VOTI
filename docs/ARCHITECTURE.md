# VOTI architecture & contracts

This document is the contract between the backend API, workers and the frontend. Change it first, then code.

## Services (compose)

| service | image | role |
|---|---|---|
| `nginx` | nginx-unprivileged | Only public entry: `0.0.0.0:8080`. Serves the built SPA, proxies `/api/` to `api`. |
| `api` | `voti-backend` | FastAPI (`voti.api.app:app`) on 8000, internal only |
| `worker-fetch` | `voti-backend` | `python -m voti.workers.run --queues fetch` (yt-dlp, rate limited) |
| `worker-audio` | `voti-backend` | `--queues audio,export` |
| `worker-ml` | `voti-backend` | `--queues ml` (torch; GPU via compose.gpu.yaml) |
| `legacy-ml` | `voti-legacy-ml` | Python 3.8 HTTP inference service: Brouhaha + Vox-Profile, port 8600, internal |
| `db` | pgvector/pgvector (pg17) | Postgres + pgvector |
| `redis` | redis | queue wake-ups |
| `rustfs` | rustfs/rustfs | S3 object storage, internal only |

TLS terminates at an external proxy on another LAN machine which forwards `voti.gugulu.tw` to `<this host>:8080`
with the original `Host` and `X-Forwarded-Proto: https`.

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
  pipeline/handlers.py                               ← job handlers for audio/ml queues
  workers/run.py                                     ← imports all handlers, runs voti.queue.worker_loop
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
| `youtube` | `{"rate_limit_kbps": 0, "sleep_between_downloads_sec": 5, "sleep_between_requests_sec": 2, "max_concurrent_downloads": 1, "cookies_enabled": false}` |
| `pipeline` | `{"voxprofile_backbone": "whisper-large-v3", "diarization_enabled": true, "segment_min_sec": 1.0, "segment_max_sec": 20.0, "edge_pad_sec": 0.1}` |
| `quality_thresholds` | `{"low_speech_ratio": 0.5, "low_snr_db": 10, "clipping_ratio": 0.001}` (used to set segment flags) |

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
- `GET /settings` → `{youtube: {...}, pipeline: {...}, quality_thresholds: {...}, cookies: {present: bool, updated_at}}`
- `PUT /settings/{key}` body = value (partial ok, merged over the current value; unknown fields → 422) → updated value
- `PUT /settings/cookies` multipart `file` (Netscape cookies.txt) → `{present: true, updated_at}`; `DELETE /settings/cookies`

### Sources (adding things)
- `POST /sources/resolve` `{url}` → `{type: "video"|"channel"|"playlist", channel?: Channel, playlist?: Playlist, media?: Media, job: Job}`
  Accepts video URLs (watch, youtu.be, shorts), channel URLs (/@handle, /channel/UC…, /c/…, with or without tab
  suffix), playlist URLs. Creates rows synchronously when cheap and enqueues `source.resolve` / listing jobs.

### Channels
- `GET /channels?q=` → `{items: [ChannelSummary], total}`
- `GET /channels/{id}` → `ChannelDetail`
- `POST /channels/{id}/refresh` → `Job` (re-list)
- `POST /channels/{id}/detail` `{media_ids?: [...]}` → `{queued: n}` (fetch full metadata for listed media; all un-detailed when omitted)
- `PATCH /channels/{id}` `{notes}` → `ChannelDetail`
- `GET /playlists?channel_id=` → `{items: [Playlist], total}`
- `GET /playlists/{id}` → `Playlist`

`Playlist = {id, platform, platform_playlist_id, channel_id, title, description, url, list_status, last_listed_at,
 created_at, media_count, thumbnail_url}` (thumbnail from the playlist's extractor metadata when present)

`ChannelSummary = {id, platform, title, handle, url, thumbnail_url, subscriber_count, video_count, list_status,
last_listed_at, counts: {listed, detailed, with_manual_captions, downloaded, ready, failed}, hours: {listed, ready, speech}}`
`ChannelDetail = ChannelSummary + {description, banner_url, country, tags, notes, metadata,
speakers: {candidates: n, total_sec}, quality: {snr_mean, dnsmos_ovrl, c50_mean, speech_ratio, real_bandwidth_hz,
media_with_quality}, kind_counts: {video: n, short: n, live?: n, ...}, playlist_count: n}`

### Media (videos)
- `GET /media` — filters (all optional):
  `channel_id, playlist_id, q (title/description), kind (video|short|live, comma list), status (comma list),
   captions (manual|auto|any|none), include_auto (bool, default false — auto-only videos hidden unless true or captions=auto),
   caption_lang, license (youtube|creative_commons), min_duration, max_duration, published_after, published_before,
   detailed (bool), upsampled_suspect (bool: media_quality.upsampled_suspect is / is not true), sort (published_at|-published_at|duration|-duration|views|-views|title), limit, offset`
  → `{items: [MediaSummary], total, facets: {kind: {video: n, short: n}, captions: {manual: n, auto_only: n, none: n, unknown: n}, license: {...}, status: {...}}}`
- `GET /media/{id}` → `MediaDetail`
- `POST /media/download` `{media_ids?: [...], filter?: <same query object as GET /media>, caption_langs?: ["zh-TW", ...], include_auto: false}`
  → `{queued: n, skipped: n}`. Either explicit ids or "everything matching filter" (select-all). Media without the
  required captions are skipped unless include_auto/no-caption allowed (POC: skip when no usable track).
- `POST /media/{id}/retry` → `Job` (re-run the failed step)
- `POST /media/{id}/reprocess` `{from_step: "media.quality"|...}` → `Job`
- `GET /media/{id}/timeline` → `{duration, hop_sec, vad: [..downsampled ≤2000 pts], snr: [...], speech_regions: [[s,e]],
   diarization: [{speaker, start, end, overlap}], repeated: [{id, start, end, position, excluded}], cues: [{start,end,text}],
   segments: [SegmentSummary]}`
- `GET /media/{id}/quality` → `MediaQuality` (all columns) + `details` (passed through as stored; the pipeline stores
  `details.spectrum: [{hz, db}]`, the long-term average spectrum of the original audio, when available)

`MediaSummary = {id, platform, platform_media_id, url, kind, title, thumbnail_url, duration_sec, published_at, view_count,
 license, has_manual_captions, has_auto_captions, manual_caption_langs, auto_caption_langs, status, stage, failure_code,
 channel: {id, title}, quality?: {snr_mean, dnsmos_ovrl, real_bandwidth_hz, speech_ratio, sample_rate, upsampled_suspect},
 segment_count}`
`MediaDetail = MediaSummary + {description, tags, categories, chapters, language, like_count, comment_count, availability,
 age_limit, metadata, failure_message, jobs: [Job], tracks: [{id, language, kind}], quality: MediaQuality|null}`

### Segments & review
- `GET /segments` — filters: `media_id, channel_id, speaker_id, review_status, text_source, q, min_snr, min_dnsmos,
   min_speech_ratio, flags (comma, any-of), exclude_flags, has_profile, sort, limit, offset`
   (sort: `start` (default: media then time), `snr`, `-snr`, `dnsmos`, `-dnsmos`, `duration`, `-duration`,
   `speech_ratio`, `-speech_ratio`, `created_at`, `-created_at`, `-updated_at`) → `{items: [SegmentSummary], total}`
- `GET /segments/{id}` → `SegmentDetail`
- `PATCH /segments/{id}` `{corrected_text?, review_status?, review_note?, speaker_state?, start_sec?, end_sec?}` → `SegmentDetail`
  (boundary change bumps revision, re-enqueues embed/profile for that media lazily)
- `PUT /segments/{id}/speaker` `{speaker_name}` / `DELETE` → override
- `GET /segments/{id}/similar?k=10` → `[{segment: SegmentSummary, similarity}]` (pgvector cosine)

`SegmentSummary = {id, media_id, media_title, channel_title, start_sec, end_sec, duration, text (corrected or original),
 original_text, corrected_text, text_source, local_speaker, speaker: {id, label, display_name}|null, speaker_state,
 speech_ratio, snr, c50, dnsmos_ovrl, dnsmos_p808, flags, review_status, revision, profile?: {age, sex, emotion_label}}`
`SegmentDetail = SegmentSummary + {track_id, cue_ids, dnsmos_sig, dnsmos_bak, clipping_ratio, lufs, repeated_overlap,
 review_note, reviewed_by: UserRef|null, reviewed_at, profile: VoiceProfile|null, history: [AuditLog]}`
`AuditLog = {id, at, user: UserRef|null, action, entity_type, entity_id, before, after}` (newest first)

### Audio
- `GET /audio/segments/{id}?pad=0.3&rev=<revision>` → `audio/wav` cut from normalized audio (supports HEAD).
  `rev` only busts caches: when it equals the segment's current revision the response is cacheable
  (`private, max-age=86400`), otherwise `private, no-cache` (+ `ETag`, `X-Segment-Revision`).
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
   by_sex: {...}, excluded: {repeated: n, auto_caption: n, low_quality: n}}`
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

Manifest row: `{segment_id, revision, audio_path, start_sec, end_sec, duration, text, text_source, language,
media: {platform, id, url, title, published_at, license}, channel: {id, title}, speaker: {id, label, display_name},
quality: {snr, c50, dnsmos_*, speech_ratio, bandwidth_hz, sample_rate}, profile: {...}, sha256}`

### Jobs & stats
- `GET /jobs?status=&kind=&media_id=&limit=` → `{items: [Job], total, counts: {queued, running, failed, ...}}`
- `POST /jobs/{id}/retry`, `POST /jobs/{id}/cancel`
- `GET /stats/overview` → `{channels, media: {listed, detailed, downloaded, ready, failed}, hours: {listed, downloaded,
   speech, segments, reviewed}, segments: {total, passed, needs_fix}, speakers: {candidates, verified}, storage_bytes,
   workers: [{name, queues, alive, last_seen_at, current_job: {id, kind, media_title}|null}],
   queue_depth: {fetch, audio, ml, export}}` (workers: Redis `voti:worker:<name>` announcements, plus workers that
   still own running jobs but stopped announcing → `alive: false`, `last_seen_at` = job heartbeat)
- `GET /healthz` → `{ok: true}` (no auth)

`Job = {id, kind, queue, status, progress, phase, message, error_code, error_message, attempts, media_id, channel_id,
 media_title?, channel_title? (job's channel, else its media's channel), payload, result, created_at, started_at,
 finished_at, eta_sec? (linear estimate from progress and started_at)}`
- `GET /jobs/{id}` → `Job`
