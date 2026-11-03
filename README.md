# 島嶼之聲 · Voice of the Island (VOTI)

VOTI is a self-hosted workbench for building Taiwanese speech corpora from public video. It collects audio and
captions from YouTube, analyses every recording automatically (speech activity, quality, speakers, voice traits,
caption ↔ audio agreement), lets annotators review sentence by sentence, and exports curated datasets with full
metadata.

- Production URL: https://voti.gugulu.tw (TLS is terminated by a proxy on another LAN machine; this stack serves plain HTTP on `:8080`)
- Paper: [`paper/paper.pdf`](paper/paper.pdf)
- Architecture, storage layout, settings and the full HTTP API contract: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

---

## Contents

- [What it does](#what-it-does)
- [How a video flows through the system](#how-a-video-flows-through-the-system)
- [The web app](#the-web-app)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Operations](#operations)
- [Development](#development)
- [Models and licences](#models-and-licences)

---

## What it does

| Area | Details |
|---|---|
| **Sources** | YouTube video, channel and playlist URLs. Channels are listed with both their Videos and Shorts tabs (live streams are excluded). Adding a single video also adds its channel. Members-only videos are detected and marked unsupported. The source layer is pluggable (`backend/voti/sources/`) so other platforms can be added later. |
| **Metadata** | Everything yt-dlp exposes (title, description, tags, duration, upload date, view/like counts, licence, chapters, thumbnails, caption tracks…), stored per video, plus an optional fast pass through the YouTube Data API v3. |
| **Captions** | Uploader captions are preferred (zh-TW first); YouTube auto-captions are collectable but off by default. Empty caption tracks are detected (with a manual re-check). Simplified Chinese is converted to Traditional (Taiwan) with OpenCC. Videos without CC but with burned-in subtitles are transcribed by **hard-subtitle OCR**; videos with neither are marked *skipped (no subtitles)*. There is no ASR. |
| **Downloading** | Filter a channel by native CC, auto-captions, duration, date, licence, type (video / Short), keyword and downloaded state, then select (Shift-click ranges, or "select all matching") and download. Downloads are rate-limited and parallel; an admin can upload a `cookies.txt`. Licences are recorded, never used to block. |
| **Audio analysis** | Normalisation to 16 kHz mono; quality: Brouhaha VAD / SNR / C50, Silero VAD, DNSMOS SIG / BAK / OVRL / P.808, real upper bandwidth and upsampling detection, source sample rate / codec, loudness, clipping. |
| **Speakers** | pyannote diarization, ECAPA speaker embeddings, Vox-Profile traits (age, sex, voice quality, emotion, fluency, accent), automatic cross-video speaker clustering, a voice similarity map and matrix, speaker diversity dashboards. |
| **Segments** | Caption cues become sentence segments; boundaries are refined with VAD and forced alignment; a sentence spoken by two people is split at the speaker change. Every segment gets per-character timings (karaoke highlighting) and a validity verdict. |
| **Validation** | Language ID (VoxLingua107) and alignment confidence flag captions that do not transcribe the audio (translated subtitles, inserted foreign-language clips, offsets). Invalid segments stay reviewable but are excluded from every hour count, clustering and export. |
| **Dedup** | Repeated intros/outros within a channel are fingerprinted and can be excluded from datasets. |
| **Review** | Sentence list with filters and full-text search, a per-segment reviewer with waveform and tracks, inline pass / needs-fix, editing text and boundaries, keyboard shortcuts. |
| **Datasets** | Filter by quality, speaker, licence, review state, caption source, demographics and per-speaker hour caps with a live preview; export JSONL + CSV manifests and a tar of 16 kHz WAV clips. |
| **Operations** | Job queue that survives restarts, scheduled channel refresh, automatic re-clustering, CPU-only or GPU deployment, bilingual UI (Traditional Chinese / English), admin and annotator roles. |

---

## How a video flows through the system

```
URL ─▶ source.resolve ─▶ channel.list ─▶ media.api_detail / media.detail      (scan + detail queues)
                                              │  metadata, caption tracks, empty-CC check
                         user selects ──▶ media.download                         (download queue)
                                              │  audio (+ captions; + video when OCR is needed)
                                              ▼
 media.normalize ─▶ media.quality ─▶ media.diarize ─▶ media.ocr ─▶ media.segment ─▶ media.align
   (audio)           (ml)              (ml)            (ml, only     (audio)          (ml)
                                                        without CC)
        ─▶ media.validate ─▶ media.embed ─▶ media.profile ─▶ media.fingerprint ─▶ channel.dedup
             (ml)              (ml)           (ml)             (ml)                 (ml)

 scheduler (every ~30 s): channel refresh · queue reconciliation · re-alignment safety net · speakers.cluster
```

1. **Resolve and list.** A pasted URL is classified (video / channel / playlist / handle). The channel's name and
   avatar appear immediately; its videos are listed in the background and enriched with metadata and caption
   availability. With YouTube Data API keys this takes one request per 50 videos instead of one yt-dlp call per video.
2. **Choose and download.** The download dialog only offers caption languages that really exist across the whole
   selection. A video with no usable CC is downloaded with its video stream for OCR (if OCR is enabled); a video with
   neither CC nor burned-in subtitles ends up `skipped` with reason `no_subtitles`.
3. **Normalise and measure.** `normalized.wav` (16 kHz mono) is written to object storage, along with frame-level
   VAD / SNR / C50 arrays, DNSMOS scores, bandwidth and loudness.
4. **Diarize.** pyannote `speaker-diarization-community-1` produces speaker turns per video.
5. **OCR (only without CC).** PP-OCRv5 reads the subtitle band at several frames per second, merges identical lines
   into timed cues and rejects text from side panels such as live-chat overlays.
6. **Segment.** Caption cues become segments; edges are trimmed and extended with VAD so the first and last
   syllables are not cut, and each segment gets a quality snapshot and flags (low SNR, clipping, low speech ratio…).
7. **Align.** MMS_FA CTC forced alignment gives per-character times, which drive karaoke highlighting and
   click-to-seek. If the alignment shows that the speaker changes mid-sentence, the segment is split there.
8. **Validate.** Spoken-language ID plus alignment confidence decide `valid`, `invalid` (`text_mismatch`,
   `language_mismatch`) or `unknown` (e.g. `ocr_unverified`, `no_signal`).
9. **Embed and profile.** ECAPA embeddings (pgvector) and Vox-Profile traits per valid segment.
10. **Fingerprint and dedup.** Audio fingerprints find intros/outros repeated across a channel's videos.
11. **Cluster.** When a batch of embeddings has settled (no new ones for 10 minutes), the scheduler re-clusters all
    speakers with the current threshold. Names and verified marks carry over to the matching new speakers.

Analysis-changing upgrades are applied to already stored audio by re-queuing the affected steps (a *backfill*); the
pipeline steps are idempotent and keyed by segment revision.

---

## The web app

| Page | What you do there |
|---|---|
| **Overview** | Corpus totals (hours, valid hours, videos, speakers), queue activity, recent channels. |
| **Add source** | Paste a video / channel / playlist URL. |
| **Channels** | Channel cards with listing status; delete a channel (with a warning when it has downloaded data). |
| **Channel** | Video table with all filters, Shift range selection, batch download / delete, re-check captions, refresh listing. |
| **Video** | Player with tracks, transcript with speaker colours, filter and full-text search, sentence playback with character karaoke, inline pass / needs-fix, OCR evidence, quality panels. Deleting a video clears its downloaded and processed data; the video stays in its channel as *not downloaded*. |
| **Review** | The segment list (filters by channel, status, validity, speaker, quality…) and the per-segment reviewer with a back button that returns to the same list position. |
| **Speakers** | Candidate speakers ordered by amount of speech; rename and verify; clustering history (manual and automatic runs). **Similarity** view: a t-SNE voice map (colour = voice sex, size = hours, links = similarity) and a clustered similarity matrix, with a side panel to play each speaker's samples and compare voices. |
| **Speaker** | Traits (age, sex, emotion, voice quality), channels, top samples with karaoke playback. |
| **Diversity** | Sex / age / emotion distributions and per-channel speaker concentration (Gini). |
| **Quality** | Corpus-wide distributions of SNR, C50, DNSMOS, bandwidth, speech ratio. |
| **Datasets** | Build a filter, preview counts and hours, export, download. |
| **Jobs** | Every job with status, progress, errors, retry and cancel. |
| **Settings** | YouTube (rate limits, cookies, refresh interval, API keys), pipeline, quality thresholds, validation, OCR, text conversion, users. |

The UI is responsive (desktop, tablet, phone), supports light and dark themes, and follows the browser language
(Traditional Chinese or English) unless the user picks one.

---

## Architecture

```
            LAN TLS proxy (voti.gugulu.tw)
                        │  http, original Host + X-Forwarded-Proto
                        ▼
 ┌──────────────── nginx :8080 ────────────────┐
 │  React SPA          /api ─▶ api (FastAPI)    │
 └──────────────────────────┬──────────────────┘
                            │
         ┌──────────────────┼────────────────────┐
         ▼                  ▼                    ▼
   PostgreSQL 17       Redis 8              RustFS (S3)
   + pgvector          queues, locks        audio, arrays, exports
   (jobs = source of
    truth)
         ▲                  ▲                    ▲
         └─── workers: scan ×3 · download ×2 · audio ×2 · ml ×1 ───┘
                                                   │ HTTP
                                                   ▼
                                legacy-ml (Brouhaha + Vox-Profile)
```

| Service | Role |
|---|---|
| `nginx` | Serves the built frontend and proxies `/api`. The only service published to the network (`${BIND_ADDR}:${HTTP_PORT}`). |
| `api` | FastAPI app: auth (session cookie), REST API, audio clip streaming. |
| `worker-scan` ×`SCAN_WORKERS` (3) | `scan` + `detail` queues: URL resolution, channel listing, metadata and caption checks. Also runs the scheduler. |
| `worker-download` ×`DOWNLOAD_WORKERS` (2) | `download` queue: yt-dlp audio / caption / video downloads. |
| `worker-audio` ×`AUDIO_WORKERS` (2) | `audio` + `export` queues: normalisation, segmentation, text conversion, dataset export (CPU). |
| `worker-ml` | `ml` queue: quality, diarization, OCR, alignment, validation, embeddings, profiles, fingerprints, clustering (GPU if available). |
| `legacy-ml` | Internal inference service for Brouhaha and Vox-Profile (`services/legacy-ml`). |
| `db` | PostgreSQL 17 with pgvector. |
| `redis` | Queue lists `voti:q:<queue>`, scheduler and worker locks (AOF persistence). |
| `rustfs` | S3-compatible object storage for audio, frame arrays, OCR crops and exports. |

**Job queue.** The `jobs` table is the source of truth; Redis lists only carry job IDs. Workers heartbeat while
running; on restart, jobs whose worker vanished are re-queued and anything in the table but missing from Redis is
re-pushed, so a crash or power loss resumes where it stopped. Pipeline jobs chain the next step on success.

**Schema migrations.** `voti.db.init_db()` runs on API and worker start-up: `create_all` plus idempotent
`ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` statements under an advisory lock. There is no separate
migration tool.

**Code layout**

```
backend/voti/
  api/          FastAPI routers (auth, sources, channels, media, segments, speakers, datasets, jobs, settings…)
  sources/      YouTube: yt-dlp listing / download, caption selection, Data API fast pass
  pipeline/     normalize, quality, dnsmos, vad, diarize, ocr + hardsub, segment, align, validate,
                embed, profile, fingerprint, cluster
  workers/      worker entry point (python -m voti.workers.run --queues …)
  queue.py      job model, enqueue / chaining / recovery
  scheduler.py  periodic maintenance (refresh, reconcile, re-align, auto-cluster)
  datasets.py   dataset filters and export
  cli.py        init / create-user / reset-password
backend/tests/  pytest suite (API, pipeline, queue recovery, OCR, captions…)
services/legacy-ml/  Brouhaha + Vox-Profile inference service
frontend/       React 19 + Vite + TypeScript + Tailwind 4 + TanStack Query + Radix
docs/ARCHITECTURE.md
```

---

## Getting started

### Requirements

- Docker with Compose v2.
- Disk: allow tens of GB for models in `./models` (the Vox-Profile Whisper backbone is about 37 GB, the WavLM one
  about 9 GB) plus the audio you collect.
- Optional: an NVIDIA GPU with `nvidia-container-toolkit`. Everything also runs on CPU, only slower.
- A Hugging Face token with the terms of `pyannote/speaker-diarization-community-1` accepted.

### 1. Configure

```bash
cp .env.example .env
chmod 600 .env
```

Edit at least `ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, `S3_SECRET_KEY` and `HF_TOKEN` (see [Configuration](#configuration)).

### 2. Start

```bash
# CPU only
docker compose up -d --build

# NVIDIA GPU (set TORCH_VARIANT=cu128 in .env so the CUDA images are built and tagged)
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
```

To make plain `docker compose …` use the GPU overlay, add it to `.env`:

```dotenv
COMPOSE_FILE=compose.yaml:compose.gpu.yaml
GPU_DEVICE_IDS=1        # which host GPU worker-ml and legacy-ml get
```

On first start the API creates the tables, the S3 bucket and the initial admin from `ADMIN_USERNAME` /
`ADMIN_PASSWORD`. **The admin is created only when the database has no users**; changing these variables later has
no effect (use `reset-password` instead). Models are downloaded on first use.

### 3. Open

Go to `http://<host>:8080` (or your public domain behind the proxy), sign in, then:

1. **Settings → YouTube**: add one or more YouTube Data API keys (recommended), adjust rate limits.
2. **Add source**: paste a channel URL.
3. Open the channel, filter (e.g. *has native CC*), select videos and **Download**.
4. Watch progress in **Jobs**; processed videos appear as *ready* and their sentences in **Review**.

### Behind a TLS proxy

Only nginx listens on the network. The app accepts requests whose `Host` is in `PUBLIC_HOSTS` or is
`localhost` / `127.0.0.1`, so the proxy must keep the original `Host` header and send `X-Forwarded-Proto: https`
(session cookies are then marked `Secure`). State-changing requests are checked against the `Origin` /
`Sec-Fetch-Site` headers. Restrict port 8080 to the proxy machine with a firewall.

---

## Configuration

### Environment (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `BIND_ADDR` / `HTTP_PORT` | `0.0.0.0` / `8080` | Where nginx listens. |
| `PUBLIC_HOSTS` | `voti.gugulu.tw` | Comma-separated host names the app answers to. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / — | Initial admin, applied once on an empty database. |
| `POSTGRES_PASSWORD` | `voti` | Database password (change before first start). |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `voti` / — | RustFS credentials (change before first start). |
| `TORCH_VARIANT` | `cpu` | `cpu` or `cu128`; selects the PyTorch build of the images. |
| `DEVICE` | `auto` | `auto`, `cpu`, `cuda` or `cuda:N` inside the ML containers. |
| `GPU_DEVICE_IDS` | `1` | Host GPU index given to `worker-ml` and `legacy-ml` by `compose.gpu.yaml`. |
| `HF_TOKEN` | — | Hugging Face token for pyannote. |
| `YOUTUBE_API_KEY` | — | Optional extra Data API key (keys are normally managed in the UI). |
| `SCAN_WORKERS` / `DOWNLOAD_WORKERS` / `AUDIO_WORKERS` | `3` / `2` / `2` | Worker replica counts. |

Never commit `.env`; it holds secrets.

### Runtime settings (web UI, admin only)

Stored in the `settings` table and applied without a restart. Full schema in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#runtime-settings-settings-table-admin-editable).

| Group | Main options |
|---|---|
| **YouTube** | Download rate limit, sleeps between requests / downloads, concurrent downloads, `cookies.txt` upload and toggle, automatic channel refresh interval (default 24 h, 0 = off), empty-caption verification. |
| **YouTube Data API keys** | Several keys, rotated; each Google Cloud project has 10,000 units/day (one unit = 50 videos) shared by all its keys. Keys are masked in the UI and never logged. |
| **Pipeline** | Vox-Profile backbone (default `whisper-large-v3`, the strongest; WavLM is lighter), diarization on/off, segment min/max length, edge padding. |
| **Quality thresholds** | Speech ratio, SNR and clipping limits used to flag segments. |
| **Validation** | On/off, language-ID minimum probability and speech length, minimum alignment score. |
| **OCR** | On/off, automatic OCR for videos without captions, tracking fps, discovery step, GPU / CPU model size (PP-OCRv5 server or mobile), keep crops for review, minimum confidence. |
| **Text** | Simplified → Traditional conversion and its OpenCC profile (`s2twp` default, `s2tw`, `s2t`). |

**Getting a YouTube Data API key:** Google Cloud Console → create a project → *APIs & Services → Library* → enable
**YouTube Data API v3** → *Credentials → Create credentials → API key* → restrict the key to YouTube Data API v3.
Paste it under **Settings → YouTube → YouTube Data API keys**.

---

## Operations

```bash
docker compose ps                                   # service state
docker compose logs -f worker-ml                    # follow a worker
docker compose exec api python -m voti.cli create-user <name> --role annotator   # or --role admin
docker compose exec api python -m voti.cli reset-password <name>                 # also re-activates the user

# rebuild and roll out after a code change
docker compose build api nginx
docker compose up -d --no-build --force-recreate api nginx worker-scan worker-download worker-audio worker-ml
```

- **Roles.** `admin` manages sources, downloads, deletions, settings and users; `annotator` reviews and edits segments.
- **Recovery.** Restarting any container is safe; interrupted jobs are resumed automatically. Failed jobs can be
  retried from the Jobs page.
- **Re-clustering.** Speakers are re-clustered automatically after each batch settles. **Speakers → Run clustering**
  starts a manual run with a chosen threshold (cosine similarity of ECAPA voiceprints, average linkage; higher =
  stricter = more speakers). The automatic runs reuse the threshold and scope of the current run.
- **Backfills.** After deploying a change that affects analysis (segmentation, alignment, validation…), re-queue the
  affected steps for already processed videos so stored data matches the new code.
- **Deleting.** Deleting a channel removes it with all its media and data. Deleting a video only clears its
  downloaded and processed data. Both ask for confirmation in the UI.
- **Data locations.** Docker volumes `pgdata`, `redisdata`, `objects` (RustFS); models in `./models`.

---

## Development

### Infrastructure on localhost

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d db redis rustfs
# Postgres 127.0.0.1:55432 · Redis 127.0.0.1:56379 · RustFS 127.0.0.1:59000
```

### Frontend

```bash
cd frontend
npm ci
npm run dev:mock      # mock data, no backend needed
npm run dev           # against the API at http://127.0.0.1:8000
npm run typecheck
npm run build
```

Visual acceptance captures every page at desktop, tablet and phone widths into `screenshots/` (gitignored) and fails
on horizontal overflow:

```bash
npm run build:mock && npm run preview:mock &     # mock build on :4173 (the default target)
npm run shots                                    # or: npm run check:overflow (no files written)
BASE_URL=http://127.0.0.1:8080 VOTI_USER=admin VOTI_PASS=… npm run shots   # against the live stack
```

`THEMES`, `VIEWPORTS`, `LANG` and `ONLY` narrow the run. UI changes are expected to pass it in both themes.

Strings live in typed message files under `frontend/src/i18n/messages/` (zh-TW and en share one shape, so a missing
translation is a type error).

### Backend tests

Tests run against the dev infrastructure but in their **own** database, bucket and Redis DB, never the production
ones:

```bash
cd backend
VOTI_TEST_DB=voti_test_api VOTI_TEST_BUCKET=voti-test-api VOTI_TEST_REDIS_DB=9 \
  python -m pytest tests/test_api_*.py
```

`backend/tests/conftest.py` documents the variables. Pipeline integration tests that need models skip when the
models are not available. The ML tests are easiest to run inside the backend image, which has the model
dependencies installed.

### Conventions

- New columns on existing tables go into `voti.db.MIGRATIONS` (idempotent).
- New job kinds are registered in `voti/queue.py` (`KINDS` maps kind → queue; add to `PIPELINE` if part of the chain).
- API contract changes are documented in `docs/ARCHITECTURE.md`.

---

## Models and licences

| Component | Used for | Licence / terms |
|---|---|---|
| pyannote `speaker-diarization-community-1` | Diarization | Accept the terms on Hugging Face |
| Brouhaha | VAD, SNR, C50 | MIT |
| Silero VAD | VAD | MIT |
| DNSMOS (P.835 / P.808) | Quality scores | ONNX models from microsoft/DNS-Challenge (see its licence) |
| SpeechBrain ECAPA (VoxCeleb) | Speaker embeddings | Apache-2.0 |
| SpeechBrain VoxLingua107 ECAPA | Language ID | Apache-2.0 |
| torchaudio MMS_FA | Forced alignment | CC-BY-NC 4.0 (MMS weights) |
| Vox-Profile | Age, sex, voice quality, emotion, fluency, accent | Non-commercial research (RAIL) |
| PP-OCRv5 via RapidOCR | Hard-subtitle OCR | Apache-2.0 |
| OpenCC | Simplified → Traditional | Apache-2.0 |

- Vox-Profile was trained mostly on English speech; its age and emotion estimates for Taiwanese speakers are
  shown as reference only.
- Video licences (standard YouTube / Creative Commons) are recorded per video and carried into every manifest row;
  whether a dataset may be redistributed has to be checked separately.
- VOTI never reads browser cookies. A `cookies.txt` is used only when an admin uploads it and turns it on.
