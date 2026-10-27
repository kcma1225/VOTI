"""繁體中文網頁介面（Starlette + Jinja）。伺服器端渲染；外部文字一律轉義顯示。

頁面路徑只接受 GET；寫入、媒體下載與 JSON 都在 /api/ 底下（docker compose 由 nginx 轉送）。

開發啟動：uvicorn twcorpus.web.app:create_app --factory --host 127.0.0.1 --port 8501
"""
from __future__ import annotations

import json
from collections import Counter
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from .. import __version__
from ..config import Settings, load_settings
from .. import align
from ..core import (add_subtitles, asset_path, delete_source, export_corpus, get_source, list_exports, list_segments,
                    list_sources, list_tracks, relative, review_segment, statistics, update_source)
from ..db import connect, init_db, rows
from ..jobs import enqueue, estimate, list_jobs, override_group, request_cancel, retry, run_clustering, similar
from ..youtube import expand_candidates, normalize_video_url, search_candidates
from . import labels as L

HERE = Path(__file__).resolve().parent
HEX = re.compile(r"^[0-9a-f]{32}$")
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".opus", ".webm", ".aac", ".mkv", ".mov"}
SUBTITLE_EXT = {".srt", ".vtt"}
NOTICES = {"queued": "工作已排入背景佇列；離開此頁也會繼續執行，進度見右側「處理任務」面板。", "saved": "已儲存。",
           "subtitles": "字幕已保存，切片已排入背景佇列。", "retried": "工作已重新排入。", "cancel": "已要求取消。",
           "deleted": "來源已刪除。", "clustered": "已建立新的分群執行。", "grouped": "人工群組已保存。", "exported": "已建立匯出快照。"}
REVIEW_FILTERS = ("status", "language", "kind", "q", "group", "flag")


# ---------- plumbing ----------

def same_origin(headers: dict[str, str]) -> bool:
    """CSRF check for writes. No accounts and no cookies, so this is the whole defence.

    Sec-Fetch-Site is authoritative when the browser sends it. Otherwise Origin must name this host;
    "null" (sandboxed frames, file:// pages) is refused. Clients that send neither are not browsers.
    """
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return True
    return origin != "null" and urlsplit(origin).netloc == headers.get("host", "")


class GuardMiddleware:
    """Reject cross-site writes and add security headers."""

    # Referrer-Policy must not be no-referrer: browsers then send "Origin: null" on same-site form posts.
    HEADERS = [(b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; "
                b"img-src 'self' data:; media-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
               (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
               (b"x-frame-options", b"DENY")]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            if not same_origin(headers):
                return await PlainTextResponse("拒絕跨站請求：請從本站頁面送出表單。", status_code=403)(
                    scope, receive, send)

        async def wrapped(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + self.HEADERS
            await send(message)
        await self.app(scope, receive, wrapped)


def cfg(request: Request) -> Settings:
    return request.app.state.settings


def ident(value: str) -> str:
    if not HEX.fullmatch(value or ""):
        raise HTTPException(404)
    return value


def render(request: Request, template: str, *, status: int = 200, error: str | None = None, **context):
    notice = NOTICES.get(request.query_params.get("notice", ""))
    ref = request.query_params.get("ref", "")
    if notice and HEX.fullmatch(ref):
        notice = f"{notice}（ID {ref[:12]}）"
    return request.app.state.templates.TemplateResponse(
        request, template, {"notice": notice, "error": error, **context}, status_code=status)


def redirect(path: str, notice: str | None = None, ref: str | None = None, **query) -> RedirectResponse:
    params = {k: v for k, v in query.items() if v}
    if notice:
        params["notice"] = notice
    if ref:
        params["ref"] = ref
    return RedirectResponse(path + ("?" + urlencode(params) if params else ""), status_code=303)


def text(form, key: str, limit: int = 4000) -> str:
    value = form.get(key)
    return value.strip()[:limit] if isinstance(value, str) else ""


async def save_upload(settings: Settings, upload, allowed: set[str], folder: Path | None = None,
                      stem: str = "upload") -> Path | None:
    # Contents go to a fresh local file; the client-supplied name only contributes a vetted suffix.
    if not isinstance(upload, UploadFile) or not upload.filename:
        return None
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in allowed:
        raise ValueError(f"不支援的檔案類型：{suffix or '無副檔名'}")
    if folder is not None:
        target = folder / f"{stem}{suffix}"
    else:
        with tempfile.NamedTemporaryFile(dir=settings.data_dir, prefix="upload-", suffix=suffix, delete=False) as f:
            target = Path(f.name)
    await upload.seek(0)

    def copy():
        with target.open("wb") as out:
            shutil.copyfileobj(upload.file, out, 1024 * 1024)
    await run_in_threadpool(copy)
    return target


@lru_cache(maxsize=1)
def tool_report() -> dict:
    # Installed tools do not change while the container runs; avoid spawning ffmpeg on every page view.
    from ..diagnostics import tools
    return tools()


def worker_seen(settings: Settings) -> float | None:
    beat = settings.data_dir / "worker.heartbeat"
    return time.time() - beat.stat().st_mtime if beat.exists() else None


def duration(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.1f} 分鐘"
    return f"{hours:.2f} 小時"


TAIPEI = timezone(timedelta(hours=8))

def local_time(stored: str | None, length: int = 16) -> str:
    """Stored timestamps are UTC ISO strings; show them as UTC+8 ("YYYY-MM-DD HH:MM:SS" cut to length)."""
    if not stored:
        return "—"
    try:
        moment = datetime.fromisoformat(stored)
    except ValueError:
        return stored[:length]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M:%S")[:length]

def clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    minutes, rest = divmod(float(seconds), 60)
    hours, minutes = divmod(int(minutes), 60)
    return (f"{hours}:{minutes:02d}:{rest:04.1f}" if hours else f"{minutes}:{rest:04.1f}")


def youtube_link(url: str | None, start: float | None = None) -> str | None:
    # Only links we stored ourselves (canonical watch URLs) are rendered as clickable.
    if not url or not url.startswith("https://www.youtube.com/watch?v="):
        return None
    return url + (f"&t={int(start)}s" if start is not None else "")


# ---------- overview ----------

async def overview(request: Request):
    s = cfg(request)
    from ..diagnostics import compute_devices, storage
    devices = await run_in_threadpool(compute_devices) if request.query_params.get("devices") == "1" else None
    return render(request, "overview.html", active="overview", title="總覽",
                  stats=statistics(s), sources=list_sources(s)[:6], jobs=list_jobs(s)[:6],
                  tools=await run_in_threadpool(tool_report), space=await run_in_threadpool(storage, s.data_dir),
                  devices=devices, settings=s, worker_age=worker_seen(s))


# ---------- sources ----------

def filter_url(filters: dict, **changes) -> str:
    """Link to /sources with the current filters, changed as given (empty values drop out)."""
    params = {k: v for k, v in {**filters, **changes}.items() if v}
    return "/sources" + ("?" + urlencode(params) if params else "")


async def sources_page(request: Request, error: str | None = None, status: int = 200,
                       candidates: list | None = None, tab: str | None = None):
    s = cfg(request)
    # `tab` names the import dialog to open (after a failed import, or from a link); None keeps the list alone.
    tab = tab or request.query_params.get("tab", "")
    if tab not in ("local", "urls", "candidates"):
        tab = ""
    everything = list_sources(s)
    q = request.query_params
    filters = {k: q.get(k, "") for k in ("domain", "platform", "language", "status", "q")}
    needle = filters["q"].strip().lower()

    def keep(src, skip=None):
        for key, column in (("domain", "domain"), ("platform", "platform"), ("language", "audio_language"),
                            ("status", "status")):
            if key != skip and filters[key] and src[column] != filters[key]:
                return False
        return not needle or needle in " ".join(str(src[c] or "") for c in ("title", "channel_name", "description")).lower()

    shown = [src for src in everything if keep(src)]
    # Domain chips count what the other filters leave, so choosing a domain never hides the alternatives.
    domain_counts = Counter(src["domain"] for src in everything if keep(src, "domain"))
    segments = statistics(s)["distribution"]["source_id"]
    return render(request, "sources.html", status=status, error=error, active="sources", title="來源清單",
                  tab=tab, sources=shown, total=len(everything), filters=filters, domain_counts=domain_counts,
                  filtered=any(filters.values()), segments=segments,
                  candidates=candidates, has_key=bool(s.youtube_api_key))


async def import_local_post(request: Request):
    """Only the upload happens in the request; probing, hashing, conversion and slicing run in the worker,
    so leaving the page does not stop them. The files wait in data/uploads/<token>/ until then."""
    s = cfg(request)
    folder = s.data_dir / "uploads" / uuid.uuid4().hex
    try:
        async with request.form(max_files=2, max_fields=20) as form:
            folder.mkdir(parents=True)
            audio = await save_upload(s, form.get("audio"), AUDIO_EXT, folder, "audio")
            if audio is None:
                raise ValueError("請先選擇音訊或影片檔")
            subtitle = await save_upload(s, form.get("subtitle"), SUBTITLE_EXT, folder, "subtitle")
            label = L.pick(text(form, "license_label"), L.LICENSES, "授權觀察")
            options = dict(
                language=L.pick(text(form, "language"), L.LANGUAGES, "音訊語言"),
                subtitle_language=L.pick(text(form, "subtitle_language"), L.LANGUAGES, "字幕語言"),
                subtitle_kind=L.pick(text(form, "subtitle_kind"), L.SUBTITLE_KINDS, "字幕來源"),
                speaker_count=L.pick(text(form, "speaker_count"), L.SPEAKER_COUNT, "語者數"),
                taiwan_basis=L.pick(text(form, "taiwan_basis"), L.TAIWAN_BASIS, "台灣語者證據"),
                domain=L.pick(text(form, "domain") or "unknown", L.DOMAINS, "領域"),
                license_label=label, license_evidence=text(form, "license_evidence"),
                title=text(form, "title", 300) or Path(form["audio"].filename).name[:300])
        if label != "unknown" and not options["license_evidence"]:
            raise ValueError("非「未知」的授權觀察必須附上證據")
        jid = enqueue(s, "local_import", payload={"upload_dir": relative(s, folder), "audio": audio.name,
                                                  "subtitle": subtitle.name if subtitle else None,
                                                  "options": options})
        return redirect("/sources", "queued", jid, jobs="open")
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        return await sources_page(request, str(exc), 400, tab="local")


async def inspect_urls_post(request: Request):
    s = cfg(request)
    async with request.form(max_files=0, max_fields=60) as form:
        raw = text(form, "urls", 20000).splitlines() + [u for u in form.getlist("url") if isinstance(u, str)]
    try:
        canonical = list(dict.fromkeys(normalize_video_url(u)[1] for u in raw if u.strip()))
        if not canonical:
            raise ValueError("請輸入或勾選影片網址")
        if len(canonical) > 20:
            raise ValueError("每批最多 20 支")
        jid = enqueue(s, "youtube_inspect", payload={"urls": canonical})
        return redirect("/sources", "queued", jid, jobs="open")
    except Exception as exc:
        return await sources_page(request, str(exc), 400, tab="urls")


async def candidates_post(request: Request):
    s = cfg(request)
    async with request.form(max_files=0, max_fields=20) as form:
        mode, values = text(form, "mode"), {k: text(form, k, 500) for k in ("url", "query", "after", "channel", "limit")}
    try:
        limit = int(values["limit"] or 10)
        if mode == "search":
            found = await run_in_threadpool(search_candidates, s.youtube_api_key, values["query"], limit,
                                            values["after"] or None, values["channel"] or None)
        else:
            found = await run_in_threadpool(expand_candidates, values["url"], limit)
        return await sources_page(request, candidates=found, tab="candidates")
    except Exception as exc:
        return await sources_page(request, str(exc), 400, tab="candidates")


def stored_audio(settings: Settings, source_id: str, kind: str) -> Path | None:
    with connect(settings.database) as db:
        row = db.execute("SELECT path FROM assets WHERE source_id=? AND kind=? "
                         "ORDER BY created_at DESC LIMIT 1", (source_id, kind)).fetchone()
    path = asset_path(settings, row["path"]) if row else None
    return path if path and path.is_file() else None

def normalized_audio(settings: Settings, source_id: str) -> Path | None:
    return stored_audio(settings, source_id, "normalized_audio")


def source_scope(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    source = get_source(s, sid)
    if not source:
        raise HTTPException(404)
    return s, sid, source


def source_heading(source) -> dict:
    return {"source": source, "title": source["title"] or "未命名來源", "active": "sources",
            "link": youtube_link(source["url"])}


async def source_page(request: Request, error: str | None = None, status: int = 200):
    """Main view: metadata and transcript only. Everything that changes the source lives under /manage."""
    s, sid, source = source_scope(request)
    segment_count = statistics(s)["distribution"]["source_id"].get(sid, 0)
    tracks = list_tracks(s, sid)
    chosen = request.query_params.get("track", "")
    track = next((t for t in tracks if t["id"] == chosen), tracks[0] if tracks else None)
    cues, aligned = [], 0
    if track:
        words = align.track_words(s, track["id"])
        with connect(s.database) as db:
            found = rows(db, "SELECT id,ordinal,start_sec,end_sec,cleaned_text FROM cues WHERE track_id=? "
                         "ORDER BY ordinal", (track["id"],))
        for cue in found:
            units = words.get(cue["id"])
            # Only show word units that still spell the cue exactly; otherwise fall back to the plain cue.
            if units and "".join(u["word"] for u in units) != cue["cleaned_text"]:
                units = None
            aligned += bool(units)
            cues.append({**cue, "units": units})
    return render(request, "source.html", status=status, error=error, **source_heading(source),
                  tracks=tracks, track=track, cues=cues, aligned=aligned, segment_count=segment_count,
                  tags=(json.loads(source["metadata_json"] or "{}").get("tags") or [])[:20],
                  playable=bool(stored_audio(s, sid, "playback_audio") or normalized_audio(s, sid)))


async def source_manage_page(request: Request, error: str | None = None, status: int = 200):
    """Hub of the secondary functions; each row opens its own page."""
    s, sid, source = source_scope(request)
    tracks = list_tracks(s, sid)
    listed = json.loads(source["metadata_json"] or "{}").get("tracks", []) if source["platform"] == "youtube" else []
    return render(request, "source_manage.html", status=status, error=error, **source_heading(source),
                  tracks=tracks, listed_count=len(listed), has_mp3=bool(stored_audio(s, sid, "playback_audio")),
                  has_original=bool(stored_audio(s, sid, "original_audio")))


async def source_edit_page(request: Request, error: str | None = None, status: int = 200):
    s, sid, source = source_scope(request)
    return render(request, "source_edit.html", status=status, error=error, **source_heading(source))


async def source_fetch_page(request: Request, error: str | None = None, status: int = 200):
    s, sid, source = source_scope(request)
    listed = json.loads(source["metadata_json"] or "{}").get("tracks", []) if source["platform"] == "youtube" else []
    preferred = next((t["language"] for t in listed if t["kind"] == "uploader_provided"
                      and t["language"].startswith("zh")), "zh-Hant")
    return render(request, "source_fetch.html", status=status, error=error, **source_heading(source),
                  listed_tracks=listed, preferred_language=preferred,
                  has_original=bool(stored_audio(s, sid, "original_audio")))


async def source_tracks_page(request: Request, error: str | None = None, status: int = 200):
    s, sid, source = source_scope(request)
    return render(request, "source_tracks.html", status=status, error=error, **source_heading(source),
                  tracks=list_tracks(s, sid))


async def source_audio_page(request: Request, error: str | None = None, status: int = 200):
    """Playback file (MP3) and word-level alignment, per subtitle track."""
    s, sid, source = source_scope(request)
    tracks = list_tracks(s, sid)
    states = {t["id"]: {"aligned": len(align.track_words(s, t["id"])),
                        "can": align.supported(t["language"]) and t["kind"] != "translated"
                        and align.base_language(t["language"]) == align.base_language(source["audio_language"])}
              for t in tracks}
    playable = bool(stored_audio(s, sid, "playback_audio") or normalized_audio(s, sid))
    return render(request, "source_audio.html", status=status, error=error, **source_heading(source),
                  tracks=tracks, states=states, playable=playable,
                  has_mp3=bool(stored_audio(s, sid, "playback_audio")),
                  has_original=bool(stored_audio(s, sid, "original_audio")),
                  model_cached=align.weights_cached(s), model_gb=align.WEIGHTS_BYTES / 1e9)


async def source_edit_post(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    async with request.form(max_files=0, max_fields=20) as form:
        label, evidence = text(form, "license_label"), text(form, "license_evidence")
        fields = {k: text(form, k) for k in ("audio_language", "speaker_count", "taiwan_basis")}
        title = text(form, "title", 300)
    try:
        L.pick(label, L.LICENSES, "授權標示")
        if label != "unknown" and not evidence:
            raise ValueError("非「未知」的授權標示必須附上證據")
        changes = dict(license_label=label, license_evidence=evidence,
                       audio_language=L.pick(fields["audio_language"], L.LANGUAGES, "音訊語言"),
                       speaker_count=L.pick(fields["speaker_count"], L.SPEAKER_COUNT, "語者數"),
                       taiwan_basis=L.pick(fields["taiwan_basis"], L.TAIWAN_BASIS, "台灣語者證據"),
                       domain=L.pick(text(form, "domain") or "unknown", L.DOMAINS, "領域"))
        if title:
            changes["title"] = title
        update_source(s, sid, **changes)
        return redirect(f"/sources/{sid}", "saved")
    except Exception as exc:
        return await source_edit_page(request, str(exc), 400)


async def source_acquire_post(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    async with request.form(max_files=0, max_fields=12) as form:
        language, kind = chosen_track(form)
    try:
        source = get_source(s, sid)
        if not source or source["platform"] != "youtube":
            raise ValueError("來源不是 YouTube 影片")
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,20}", language):
            raise ValueError("字幕語言代碼格式無效")
        jid = enqueue(s, "youtube_acquire", sid, {"subtitle_language": language,
                                                  "subtitle_kind": L.pick(kind, L.DOWNLOAD_KINDS, "字幕類型"),
                                                  "rights_confirmed": True})
        return redirect(f"/sources/{sid}/fetch", "queued", jid, jobs="open")
    except Exception as exc:
        return await source_fetch_page(request, str(exc), 400)


def chosen_track(form) -> tuple[str, str]:
    """(language, kind) from either the caption-list select ("kind|language") or the manual fields."""
    picked = text(form, "track", 60)
    if "|" in picked:
        kind, language = picked.split("|", 1)
    else:
        language, kind = text(form, "subtitle_language", 20), text(form, "subtitle_kind")
    return language[:20], kind


async def source_refresh_tracks_post(request: Request):
    """Re-read the video's caption list from YouTube (like `yt-dlp --list-subs`); no download."""
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    source = get_source(s, sid)
    if not source:
        raise HTTPException(404)
    try:
        if source["platform"] != "youtube" or not source["url"]:
            raise ValueError("來源不是 YouTube 影片")
        jid = enqueue(s, "youtube_inspect", sid, {"urls": [source["url"]]})
        return redirect(f"/sources/{sid}/fetch", "queued", jid, jobs="open")
    except Exception as exc:
        return await source_fetch_page(request, str(exc), 400)


async def source_fetch_subtitles_post(request: Request):
    """Caption track only (no audio download), straight from YouTube."""
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    async with request.form(max_files=0, max_fields=12) as form:
        language, kind = chosen_track(form)
    try:
        source = get_source(s, sid)
        if not source or source["platform"] != "youtube":
            raise ValueError("來源不是 YouTube 影片")
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,20}", language):
            raise ValueError("字幕語言代碼格式無效")
        jid = enqueue(s, "youtube_subtitles", sid, {"subtitle_language": language,
                                                    "subtitle_kind": L.pick(kind, L.DOWNLOAD_KINDS, "字幕類型"),
                                                    "rights_confirmed": True})
        return redirect(f"/sources/{sid}/fetch", "queued", jid, jobs="open")
    except Exception as exc:
        return await source_fetch_page(request, str(exc), 400)


async def source_reprocess_post(request: Request):
    """Run the whole pipeline again for a stored source (YouTube: re-fetch audio and SRT first)."""
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    async with request.form(max_files=0, max_fields=12) as form:
        language, kind = chosen_track(form)
    try:
        source = get_source(s, sid)
        if not source:
            raise HTTPException(404)
        payload = {}
        if source["platform"] == "youtube":
            if not language:  # no pick on the form: repeat what was fetched last time
                last = (list_tracks(s, sid) or [None])[0]
                if not last:
                    raise ValueError("請先選擇字幕軌")
                language, kind = last["language"], last["kind"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{2,20}", language):
                raise ValueError("字幕語言代碼格式無效")
            payload = {"subtitle_language": language, "subtitle_kind": L.pick(kind, L.DOWNLOAD_KINDS, "字幕類型"),
                       "rights_confirmed": True}
        elif not stored_audio(s, sid, "original_audio"):
            raise ValueError("尚無原始音訊可重新處理")
        jid = enqueue(s, "reprocess", sid, payload)
        return redirect(f"/sources/{sid}/fetch", "queued", jid, jobs="open")
    except HTTPException:
        raise
    except Exception as exc:
        return await source_fetch_page(request, str(exc), 400)


async def source_playback_post(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    try:
        if not get_source(s, sid):
            raise HTTPException(404)
        if not stored_audio(s, sid, "original_audio"):
            raise ValueError("尚無原始音訊可轉檔")
        jid = enqueue(s, "playback", sid)
        return redirect(f"/sources/{sid}/audio", "queued", jid, jobs="open")
    except HTTPException:
        raise
    except Exception as exc:
        return await source_audio_page(request, str(exc), 400)


async def source_subtitles_post(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    path = None
    try:
        async with request.form(max_files=1, max_fields=10) as form:
            path = await save_upload(s, form.get("subtitle"), SUBTITLE_EXT)
            language = L.pick(text(form, "language"), L.LANGUAGES, "字幕語言")
            kind = L.pick(text(form, "kind"), L.SUBTITLE_KINDS, "字幕身分")
        if path is None:
            raise ValueError("請選擇 SRT 或 VTT 檔")
        # Parsing is quick and its errors belong on this page; conversion and slicing go to the worker.
        await run_in_threadpool(add_subtitles, s, sid, path, language, kind)
        jid = enqueue(s, "segment", sid)
        return redirect(f"/sources/{sid}/tracks", "subtitles", jid, jobs="open")
    except Exception as exc:
        return await source_tracks_page(request, str(exc), 400)
    finally:
        if path:
            path.unlink(missing_ok=True)


async def source_align_post(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    async with request.form(max_files=0, max_fields=5) as form:
        track_id = text(form, "track_id", 40)
    try:
        source = get_source(s, sid)
        track = next((t for t in list_tracks(s, sid) if t["id"] == track_id), None) if source else None
        if not track:
            raise ValueError("找不到字幕軌")
        if not align.supported(track["language"]):
            raise ValueError("字詞對齊目前只支援英文與中文字幕")
        if track["kind"] == "translated" or align.base_language(track["language"]) != align.base_language(source["audio_language"]):
            raise ValueError("字幕不是音訊同語言轉寫，不做字詞對齊")
        jid = enqueue(s, "alignment", sid, {"track_id": track_id})
        return redirect(f"/sources/{sid}/audio", "queued", jid, jobs="open")
    except Exception as exc:
        return await source_audio_page(request, str(exc), 400)


async def source_delete_post(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    async with request.form(max_files=0, max_fields=5) as form:
        from_list = text(form, "next", 10) == "list"
    try:
        await run_in_threadpool(delete_source, s, sid)
        return redirect("/sources", "deleted")
    except Exception as exc:
        return await (sources_page(request, str(exc), 400) if from_list else source_manage_page(request, str(exc), 400))


async def track_subtitle_file(request: Request):
    """The stored caption file of a track, as downloaded (SRT for YouTube fetches)."""
    s = cfg(request)
    tid = ident(request.path_params["tid"])
    with connect(s.database) as db:
        row = db.execute("SELECT a.path, t.format, t.source_id FROM tracks t JOIN assets a ON t.asset_id=a.id "
                         "WHERE t.id=?", (tid,)).fetchone()
    path = asset_path(s, row["path"]) if row else None
    if not path or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=f"{row['source_id'][:12]}-{tid[:8]}.{row['format']}")


async def source_audio(request: Request):
    # The MP3 playback copy when it exists; otherwise the normalized 16 kHz PCM (sample-exact seeking).
    s, sid = cfg(request), ident(request.path_params["sid"])
    mp3 = stored_audio(s, sid, "playback_audio")
    path = mp3 or normalized_audio(s, sid)
    if not path:
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg" if mp3 else "audio/wav",
                        headers={"cache-control": "private, max-age=3600"})


# ---------- jobs (shown in the side panel on every page) ----------

def wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


async def jobs_redirect(request: Request):
    return redirect("/", jobs="open")


async def job_action_post(request: Request):
    s = cfg(request)
    jid, action = ident(request.path_params["jid"]), request.path_params["action"]
    if action not in ("retry", "cancel"):
        raise HTTPException(404)
    try:
        retry(s, jid) if action == "retry" else request_cancel(s, jid)
    except ValueError as exc:
        if wants_json(request):
            return JSONResponse({"error": str(exc)}, status_code=400)
        return redirect("/", jobs="open")
    notice = "retried" if action == "retry" else "cancel"
    if wants_json(request):
        return JSONResponse({"ok": True, "notice": NOTICES[notice]})
    return redirect("/", notice, jobs="open")


async def embedding_post(request: Request):
    jid = enqueue(cfg(request), "embedding")
    return redirect("/clusters", "queued", jid, jobs="open")


# ---------- review ----------

def review_filters(params) -> dict:
    return {k: (params.get(k) or "").strip()[:200] for k in REVIEW_FILTERS}


async def review_view(request: Request, source, error: str | None = None, status: int = 200, params=None):
    """Segment review lives inside its source: the list is always scoped to that source."""
    s = cfg(request)
    params = params if params is not None else request.query_params
    f = review_filters(params)
    segments = list_segments(s, source_id=source["id"], search=f["q"],
                             review_status=f["status"] if f["status"] in L.REVIEW_STATUS else None,
                             language=f["language"] if f["language"] in L.LANGUAGES else None,
                             subtitle_kind=f["kind"] if f["kind"] in L.SUBTITLE_KINDS else None,
                             group_id=f["group"] or None, quality_flag=f["flag"] or None)
    chosen = params.get("segment", "")
    selected = next_id = None
    if HEX.fullmatch(chosen):
        index = next((i for i, x in enumerate(segments) if x["id"] == chosen), None)
        if index is not None:
            selected = segments[index]
            next_id = segments[index + 1]["id"] if index + 1 < len(segments) else None
        else:
            found = list_segments(s, segment_id=chosen, limit=1)
            selected = found[0] if found and found[0]["source_id"] == source["id"] else None
    if selected:
        selected = {**selected, "flags": json.loads(selected["quality_flags_json"]),
                    "labels": json.loads(selected["manual_labels_json"]),
                    "local_short": (selected["local_speaker_id"] or "").split(":", 1)[-1],
                    "link": youtube_link(selected["source_url"], selected["start_sec"])}
    return render(request, "source_review.html", status=status, error=error, **source_heading(source),
                  segments=segments, selected=selected, next_id=next_id, filters=f,
                  filtered=any(f.values()), query=urlencode({k: v for k, v in f.items() if v}),
                  flag_options={**L.QUALITY_FLAGS, **L.MANUAL_LABELS})


async def segments_page(request: Request):
    """Cross-source segment browser. Reviewing happens on the owning source's review page."""
    s = cfg(request)
    f = {k: (request.query_params.get(k) or "").strip()[:200] for k in ("source", *REVIEW_FILTERS)}
    segments = list_segments(s, source_id=f["source"] if HEX.fullmatch(f["source"]) else None, search=f["q"],
                             review_status=f["status"] if f["status"] in L.REVIEW_STATUS else None,
                             language=f["language"] if f["language"] in L.LANGUAGES else None,
                             subtitle_kind=f["kind"] if f["kind"] in L.SUBTITLE_KINDS else None,
                             group_id=f["group"] or None, quality_flag=f["flag"] or None)
    return render(request, "segments.html", active="segments", title="音段", segments=segments, filters=f,
                  filtered=any(f.values()), sources=list_sources(s), flag_options={**L.QUALITY_FLAGS, **L.MANUAL_LABELS})


async def source_review_page(request: Request):
    s, sid, source = source_scope(request)
    return await review_view(request, source)


async def review_redirect(request: Request):
    """Old global review URL: send segment links to the owning source, everything else to the segment list."""
    chosen = request.query_params.get("segment", "")
    found = list_segments(cfg(request), segment_id=chosen, limit=1) if HEX.fullmatch(chosen) else []
    if found:
        return redirect(f"/sources/{found[0]['source_id']}/review", segment=chosen)
    return redirect("/segments")


async def review_post(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    found = list_segments(s, segment_id=sid, limit=1)
    source = get_source(s, found[0]["source_id"]) if found else None
    if not source:
        raise HTTPException(404)
    async with request.form(max_files=0, max_fields=60) as form:
        f = review_filters(form)
        go_next = text(form, "next")
        corrected, note, local = text(form, "corrected_text", 20000), text(form, "note"), text(form, "local_speaker_id", 120)
        values = {k: text(form, k) for k in ("start_sec", "end_sec", "review_status", "review_level", "language", "speaker_state")}
        marked = [x for x in form.getlist("manual_labels") if x in L.MANUAL_LABELS]
    try:
        if local and ":" not in local:
            local = f"{source['id']}:{local}"
        await run_in_threadpool(lambda: review_segment(
            s, sid, corrected_text=corrected or None, note=note, local_speaker_id=local,
            start_sec=float(values["start_sec"]), end_sec=float(values["end_sec"]),
            review_status=L.pick(values["review_status"], L.REVIEW_STATUS, "審查狀態"),
            review_level=L.pick(values["review_level"], L.REVIEW_LEVEL, "校對程度"),
            language=L.pick(values["language"], L.LANGUAGES, "音段語言"),
            speaker_state=L.pick(values["speaker_state"], L.SPEAKER_STATE, "語者狀態"), manual_labels=marked))
        target = go_next if HEX.fullmatch(go_next) else sid
        return redirect(f"/sources/{source['id']}/review", "saved", segment=target, **f)
    except Exception as exc:
        return await review_view(request, source, str(exc), 400, {**f, "segment": sid})


async def segment_audio(request: Request):
    s = cfg(request)
    found = list_segments(s, segment_id=ident(request.path_params["sid"]), limit=1)
    if not found:
        raise HTTPException(404)
    path = asset_path(s, found[0]["audio_path"])
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav", headers={"cache-control": "private, max-age=3600"})


# ---------- clusters ----------

async def clusters_page(request: Request, error: str | None = None, status: int = 200):
    s = cfg(request)
    segments = list_segments(s, limit=10000)
    by_id = {x["id"]: x for x in segments}
    ready = [x for x in segments if x["embedding_status"] == "ready"]
    groups: dict[str, int] = {}
    for x in segments:
        name = x["manual_group"] or x["candidate_group"]
        if name:
            groups[name] = groups.get(name, 0) + 1
    group = request.query_params.get("group", "")[:80]
    members = [x for x in segments if (x["manual_group"] or x["candidate_group"]) == group] if group else []
    query = request.query_params.get("query", "")
    matches, query_segment = [], None
    if HEX.fullmatch(query) and query in by_id:
        query_segment = by_id[query]
        try:
            matches = [(by_id[i], score) for i, score in await run_in_threadpool(similar, s, query, 10) if i in by_id]
        except ValueError as exc:
            error = error or str(exc)
    return render(request, "clusters.html", status=status, error=error, active="clusters", title="聲音相似群",
                  ready=ready, total=len(segments), groups=sorted(groups.items(), key=lambda g: (-g[1], g[0])),
                  group=group, members=members[:50], member_count=len(members), query_segment=query_segment,
                  matches=matches, threshold=request.query_params.get("threshold", "0.35"))


async def clusters_run_post(request: Request):
    s = cfg(request)
    async with request.form(max_files=0, max_fields=5) as form:
        raw = text(form, "threshold")
    try:
        threshold = float(raw)
        if not 0.05 <= threshold <= 1.0:
            raise ValueError("門檻須介於 0.05 與 1.0")
        rid = await run_in_threadpool(run_clustering, s, threshold)
        return redirect("/clusters", "clustered", rid, threshold=raw)
    except Exception as exc:
        return await clusters_page(request, str(exc), 400)


async def group_post(request: Request):
    s = cfg(request)
    sid = ident(request.path_params["sid"])
    async with request.form(max_files=0, max_fields=5) as form:
        name, note = text(form, "group", 80), text(form, "note", 500)
    try:
        override_group(s, sid, name, note)
        return redirect("/clusters", "grouped", group=name)
    except Exception as exc:
        return await clusters_page(request, str(exc), 400)


# ---------- statistics & export ----------

async def export_page(request: Request, error: str | None = None, status: int = 200):
    s = cfg(request)
    sources = list_sources(s)
    stats = statistics(s)
    titles = {x["id"]: x["title"] for x in sources}
    exports = [{**e, "filters": {k: v for k, v in json.loads(e["filters_json"]).items() if v}}
               for e in list_exports(s)]
    return render(request, "export.html", status=status, error=error, active="export", title="統計與匯出",
                  stats=stats, sources=sources, titles=titles, exports=exports)


async def export_post(request: Request):
    s = cfg(request)
    async with request.form(max_files=0, max_fields=10) as form:
        f = {k: text(form, k, 200) for k in ("source", "language", "status", "kind", "group")}
        corrected = form.get("corrected_only") == "yes"
    try:
        target = await run_in_threadpool(lambda: export_corpus(
            s, source_id=f["source"] if HEX.fullmatch(f["source"]) else None,
            language=L.pick(f["language"], L.LANGUAGES, "語言", allow_empty=True),
            review_status=L.pick(f["status"], L.REVIEW_STATUS, "審查狀態", allow_empty=True),
            subtitle_kind=L.pick(f["kind"], L.SUBTITLE_KINDS, "字幕類型", allow_empty=True),
            group_id=f["group"] or None, corrected_only=corrected))
        return redirect("/export", "exported", target.name)
    except Exception as exc:
        return await export_page(request, str(exc), 400)


async def export_file(request: Request):
    s = cfg(request)
    name = request.path_params["name"]
    if name not in ("manifest.jsonl", "manifest.csv", "README.txt"):
        raise HTTPException(404)
    path = asset_path(s, f"exports/{ident(request.path_params['eid'])}/{name}")
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=name)


async def health(request: Request):
    return JSONResponse({"status": "ok", "version": __version__})


async def stats_json(request: Request):
    s = cfg(request)
    return JSONResponse({"statistics": await run_in_threadpool(statistics, s), "worker_age_sec": worker_seen(s)})


async def jobs_json(request: Request):
    """Recent jobs for the side panel. The ETA is a linear extrapolation of the job's own measured progress."""
    s = cfg(request)
    keys = ("id", "kind", "source_id", "status", "phase", "progress_current", "progress_total", "attempts",
            "error_code", "error_message", "created_at", "updated_at")

    def load():
        jobs = list_jobs(s, 30)
        titles = {x["id"]: x["title"] for x in list_sources(s)}
        found = []
        for j in jobs:
            timing = estimate(j)
            found.append({**{k: j.get(k) for k in keys},
                          "kind_label": L.JOB_KIND.get(j["kind"], j["kind"]),
                          "status_label": L.JOB_STATUS.get(j["status"], j["status"]),
                          "phase_label": L.JOB_PHASE.get(j["phase"], j["phase"]),
                          "tone": L.TONES.get(j["status"], "muted"),
                          "cancel_requested": bool(j["cancel_requested"]),
                          "source_title": titles.get(j["source_id"]) if j["source_id"] else None,
                          "percent": round(timing["fraction"] * 100, 1) if timing["fraction"] is not None else None,
                          "elapsed_sec": timing["elapsed_sec"], "eta_sec": timing["eta_sec"]})
        return found
    jobs = await run_in_threadpool(load)
    return JSONResponse({"jobs": jobs, "active": sum(j["status"] in ("queued", "running") for j in jobs),
                         "worker_age_sec": worker_seen(s)},
                        headers={"cache-control": "no-store"})


# ---------- app ----------

@lru_cache(maxsize=None)
def asset(name: str) -> str:
    """Static URL with a content hash, so a rebuilt image never serves stale CSS/JS from the browser cache."""
    import hashlib
    digest = hashlib.sha256((HERE / "static" / name).read_bytes()).hexdigest()[:12]
    return f"/static/{name}?v={digest}"


def create_app(settings: Settings | None = None, allowed_hosts: list[str] | None = None) -> Starlette:
    settings = settings or load_settings()
    init_db(settings.database)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(L=L, version=__version__, asset=asset)
    templates.env.filters.update(duration=duration, clock=clock, local_time=local_time, fromjson=json.loads,
                                 mb=lambda n: f"{(n or 0) / 1e6:,.1f} MB", gb=lambda n: f"{(n or 0) / 1e9:,.1f} GB",
                                 label=lambda code, options: options.get(code, code or "—"),
                                 tone=lambda code: L.TONES.get(code, "muted"))
    # Pages are plain GETs at the top level; every write, media/file download and JSON read lives under /api.
    api = [
        Route("/healthz", health), Route("/stats", stats_json), Route("/jobs", jobs_json),
        Route("/sources/local", import_local_post, methods=["POST"]),
        Route("/sources/inspect", inspect_urls_post, methods=["POST"]),
        Route("/sources/candidates", candidates_post, methods=["POST"]),
        Route("/sources/{sid}/edit", source_edit_post, methods=["POST"]),
        Route("/sources/{sid}/acquire", source_acquire_post, methods=["POST"]),
        Route("/sources/{sid}/subtitles", source_subtitles_post, methods=["POST"]),
        Route("/sources/{sid}/align", source_align_post, methods=["POST"]),
        Route("/sources/{sid}/refresh-tracks", source_refresh_tracks_post, methods=["POST"]),
        Route("/sources/{sid}/fetch-subtitles", source_fetch_subtitles_post, methods=["POST"]),
        Route("/sources/{sid}/playback", source_playback_post, methods=["POST"]),
        Route("/sources/{sid}/reprocess", source_reprocess_post, methods=["POST"]),
        Route("/sources/{sid}/delete", source_delete_post, methods=["POST"]),
        Route("/jobs/embedding", embedding_post, methods=["POST"]),
        Route("/jobs/{jid}/{action}", job_action_post, methods=["POST"]),
        Route("/review/{sid}", review_post, methods=["POST"]),
        Route("/clusters/run", clusters_run_post, methods=["POST"]),
        Route("/clusters/{sid}/group", group_post, methods=["POST"]),
        Route("/export", export_post, methods=["POST"]),
        Route("/media/segments/{sid}", segment_audio),
        Route("/media/sources/{sid}", source_audio),
        Route("/media/tracks/{tid}", track_subtitle_file),
        Route("/exports/{eid}/{name}", export_file),
    ]
    routes = [
        Route("/", overview), Route("/sources", sources_page), Route("/sources/{sid}", source_page),
        Route("/sources/{sid}/manage", source_manage_page), Route("/sources/{sid}/edit", source_edit_page),
        Route("/sources/{sid}/fetch", source_fetch_page), Route("/sources/{sid}/tracks", source_tracks_page),
        Route("/sources/{sid}/audio", source_audio_page),
        Route("/sources/{sid}/review", source_review_page),
        Route("/jobs", jobs_redirect), Route("/review", review_redirect), Route("/segments", segments_page), Route("/clusters", clusters_page),
        Route("/export", export_page),
        Mount("/api", routes=api),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    hosts = allowed_hosts or [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
                              if h.strip()]
    max_mb = int(os.environ.get("MAX_UPLOAD_MB", "2048"))
    app = Starlette(routes=routes, max_body_size=max_mb * 1024 * 1024,
                    middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts), Middleware(GuardMiddleware)])
    app.state.settings = settings
    app.state.templates = templates
    templates.env.globals["filter_url"] = filter_url
    return app
