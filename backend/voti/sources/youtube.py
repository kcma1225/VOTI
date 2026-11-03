"""yt-dlp wrapper for YouTube: URL classification, listing, per-video detail, audio + caption download.

No database access here (see voti.sources.handlers). Network functions take a `FetchOptions` built by the
handler from the `youtube` settings row; they raise `YouTubeError(code, message, blocked)` on classified
failures.

Caption vocabulary
  manual = info["subtitles"]          uploader-provided tracks ("native CC"); `live_chat` is not a track
  auto   = original-language YouTube ASR only. In info["automatic_captions"] that is the `<lang>-orig` key
           (timedtext URL with kind=asr and no tlang). Machine translations (tlang=…) and the HLS
           timedtext manifests some clients list for every track are NOT counted.
  A listed manual track may be empty (no speech text / 404): verify_captions, caption_state and download() check the
  content (docs/ARCHITECTURE.md "Caption verification").
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import subtitles as subs_mod

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
HANDLE_RE = re.compile(r"^@[\w.\-·]{1,100}$", re.UNICODE)

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com",
            "www.youtube-nocookie.com"}
SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
CHANNEL_TABS = {"", "videos", "shorts", "streams", "playlists", "featured", "home"}
LISTED_TABS = ("videos", "shorts")  # live/streams intentionally excluded

# Primary caption preference (see caption_rank): Traditional / Taiwan Chinese first, then generic Chinese (script
# decided by content after download), then Simplified (converted to Traditional by voti.textconv), then
# Taiwanese/Hakka, then anything else. Taiwanese/Hakka manual tracks are always taken in addition.
DEFAULT_CAPTION_PREFS = ("zh-TW", "zh-Hant", "zh-Hant-TW", "zh-HK", "zh-Hant-HK", "zh")  # kept for callers
EXTRA_DEFAULT_LANG_PREFIXES = ("nan", "hak", "tw")  # nan-TW (台語), hak-TW (客語)
SIMPLIFIED_TAGS = ("zh-hans", "zh-cn", "zh-sg", "zh-my", "cmn-hans")
SUBTITLE_FORMAT_PREFS = ("json3", "vtt", "srt")


# ───────────────────────────── URL classification (pure) ─────────────────────────────

def classify_url(url: str) -> dict:
    """Classify a YouTube URL without network access.

    → {"type": "video"|"channel"|"playlist", "canonical_url": str, and some of
       "video_id", "channel_id" (UC…), "handle" (@x), "custom_name" (/c/ or /user/ name, needs resolving),
       "playlist_id", "tab" (channel tab in the URL), "kind_hint" ("short"|"live")}
    Also accepts bare "@handle" and bare "UC…" channel ids. Raises ValueError otherwise.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("empty URL")
    if HANDLE_RE.match(raw):
        return _channel(handle=raw)
    if CHANNEL_ID_RE.match(raw):
        return _channel(channel_id=raw)
    if "://" not in raw:
        raw = "https://" + raw
    p = urllib.parse.urlsplit(raw)
    if p.scheme not in ("http", "https") or p.username or p.password:
        raise ValueError("not an http(s) URL")
    host = (p.hostname or "").lower()
    q = urllib.parse.parse_qs(p.query)
    path = urllib.parse.unquote(p.path)
    parts = [x for x in path.split("/") if x]
    list_id = (q.get("list") or [None])[0]
    if list_id is not None and not PLAYLIST_ID_RE.match(list_id):
        list_id = None

    if host in SHORT_HOSTS:
        if len(parts) == 1 and VIDEO_ID_RE.match(parts[0]):
            return _video(parts[0], playlist_id=list_id)
        raise ValueError("invalid youtu.be URL")
    if host not in YT_HOSTS:
        raise ValueError("not a YouTube URL")

    if path in ("/watch", "/watch/"):
        vid = (q.get("v") or [""])[0]
        if VIDEO_ID_RE.match(vid):
            return _video(vid, playlist_id=list_id)
        if list_id:
            return _playlist(list_id)
        raise ValueError("watch URL without a valid video id")
    if parts and parts[0] in ("shorts", "live", "embed", "v", "e") and len(parts) >= 2:
        if VIDEO_ID_RE.match(parts[1]):
            hint = {"shorts": "short", "live": "live"}.get(parts[0])
            return _video(parts[1], playlist_id=list_id, kind_hint=hint)
        raise ValueError("invalid video id")
    if parts and parts[0] == "playlist":
        if list_id:
            return _playlist(list_id)
        raise ValueError("playlist URL without list=")

    def tab_of(rest: list[str]) -> str:
        tab = rest[0].lower() if rest else ""
        if tab not in CHANNEL_TABS or len(rest) > 1:
            raise ValueError(f"unsupported channel path {path!r}")
        return tab

    if parts and parts[0].startswith("@"):
        if not HANDLE_RE.match(parts[0]):
            raise ValueError("invalid handle")
        return _channel(handle=parts[0], tab=tab_of(parts[1:]))
    if len(parts) >= 2 and parts[0] == "channel":
        if not CHANNEL_ID_RE.match(parts[1]):
            raise ValueError("invalid channel id")
        return _channel(channel_id=parts[1], tab=tab_of(parts[2:]))
    if len(parts) >= 2 and parts[0] in ("c", "user"):
        name = parts[1]
        if not re.match(r"^[\w.\-]{1,100}$", name, re.UNICODE):
            raise ValueError("invalid channel name")
        tab = tab_of(parts[2:])
        return {"type": "channel", "custom_name": name,
                "canonical_url": f"https://www.youtube.com/{parts[0]}/{urllib.parse.quote(name)}",
                **({"tab": tab} if tab else {})}
    raise ValueError("unrecognized YouTube URL")


def _video(vid: str, playlist_id: str | None = None, kind_hint: str | None = None) -> dict:
    d = {"type": "video", "video_id": vid, "canonical_url": f"https://www.youtube.com/watch?v={vid}"}
    if playlist_id:
        d["playlist_id"] = playlist_id
    if kind_hint:
        d["kind_hint"] = kind_hint
    return d


def _playlist(pid: str) -> dict:
    return {"type": "playlist", "playlist_id": pid, "canonical_url": f"https://www.youtube.com/playlist?list={pid}"}


def _channel(channel_id: str | None = None, handle: str | None = None, tab: str = "") -> dict:
    d: dict = {"type": "channel"}
    if channel_id:
        d["channel_id"] = channel_id
        d["canonical_url"] = f"https://www.youtube.com/channel/{channel_id}"
    if handle:
        d["handle"] = handle
        d.setdefault("canonical_url", f"https://www.youtube.com/{urllib.parse.quote(handle, safe='@')}")
    if tab:
        d["tab"] = tab
    return d


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def channel_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


# ───────────────────────────── errors ─────────────────────────────

class YouTubeError(Exception):
    def __init__(self, code: str, message: str, blocked: bool = False, retryable: bool = True):
        super().__init__(message)
        self.code, self.message, self.blocked, self.retryable = code, message, blocked, retryable
        self.extra: dict = {}


# Members-only (paid channel membership) / premium videos can never be fetched by this system.
MEMBERS_ONLY_AVAILABILITY = frozenset({"subscriber_only", "premium_only"})
MEMBERS_ONLY_MESSAGE = "會員限定影片，本系統不支援下載。"


def is_members_only(availability: str | None) -> bool:
    return (availability or "") in MEMBERS_ONLY_AVAILABILITY


# (pattern, code, blocked, retryable, message). First match wins; patterns match lower-cased yt-dlp text.
_ERROR_RULES: list[tuple[str, str, bool, bool, str]] = [
    (r"confirm you.?re not a bot|not a robot", "bot_check", True, True,
     "YouTube 要求登入確認非機器人（伺服器 IP 被判定為機器人）。"
     "請在設定上傳 cookies.txt 並啟用 cookies，或降低速率稍後重試。"),
    (r"http error 429|too many requests", "rate_limited", True, True,
     "YouTube 回應 429（請求過多）。請降低速率或稍後再試；必要時上傳 cookies。"),
    (r"confirm your age|age.?restrict|inappropriate for some users|age-gated", "age_restricted", True, False,
     "影片有年齡限制，需要登入帳號（cookies）才能取得。"),
    (r"members.?only|join this channel|available to this channel.?s members", "members_only", True, False,
     MEMBERS_ONLY_MESSAGE),
    (r"private video|this video is private|this playlist is private",
     "access_restricted", True, False, "影片／播放清單為私人或需要權限。"),
    (r"available in your (?:country|region)|geo.?restrict|blocked it in your country|geo-?blocked", "region_restricted",
     True, False, "影片受地區限制，無法從伺服器所在地區取得。"),
    (r"drm protected|this video is drm", "drm", True, False, "影片受 DRM 保護，無法下載。"),
    (r"premieres in|live event will begin|this live event will|is_upcoming", "upcoming", False, True,
     "影片尚未首播／直播尚未開始。"),
    (r"this live event has ended|live stream recording is not available|is currently live|is_live",
     "live_in_progress", False, True,
     "直播進行中或剛結束，尚無可下載的錄影。"),
    (r"copyright claim|removed by the uploader|account associated with this video has been terminated|"
     r"video has been removed|video unavailable|this video is unavailable|does not exist|"
     r"requested entity was not found|http error 404|this channel does not exist|has been terminated",
     "unavailable", False, False, "影片／頻道不存在或已被移除。"),
    (r"requested format is not available|no video formats found", "format_unavailable", False, True,
     "找不到可下載的音訊格式（可能需要更新 yt-dlp 或 JS runtime）。"),
]


def classify_error(exc: BaseException | str) -> YouTubeError:
    if isinstance(exc, YouTubeError):
        return exc
    text = str(exc)
    low = text.lower()
    for pattern, code, blocked, retryable, message in _ERROR_RULES:
        if re.search(pattern, low):
            return YouTubeError(code, f"{message}\n[{_short(text)}]", blocked=blocked, retryable=retryable)
    return YouTubeError("network_or_extractor", f"來源取得失敗（網路或 yt-dlp 擷取錯誤）：{_short(text)}")


def _short(text: str, n: int = 400) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
    return text if len(text) <= n else text[:n] + "…"


# ───────────────────────────── yt-dlp options ─────────────────────────────

@dataclass
class FetchOptions:
    rate_limit_kbps: int = 0                 # 0 = unlimited (download bandwidth)
    sleep_requests_sec: float = 0.75         # between HTTP requests inside one extraction (listing pages, detail)
    sleep_subtitles_sec: float = 1.0         # before each subtitle download
    cookiefile: str | None = None
    socket_timeout: int = 30
    extra: dict = field(default_factory=dict)


def js_runtimes() -> dict:
    """yt-dlp needs a JS runtime for YouTube's player challenges; enable whichever is installed."""
    import sysconfig
    scripts = Path(sysconfig.get_path("scripts"))
    found = {}
    for name, exe in (("deno", "deno"), ("node", "node"), ("bun", "bun"), ("quickjs", "qjs")):
        local = scripts / exe  # pip-installed `deno` wheel (yt-dlp[deno]) lives next to python
        path = str(local) if local.is_file() else shutil.which(exe)
        if path:
            found[name] = {"path": path}
    return found


def _ydl_class():
    import yt_dlp
    return yt_dlp.YoutubeDL


class _Logger:
    """Collect yt-dlp warnings (PO-token / missing-subtitle notes are useful in job results)."""

    def __init__(self):
        self.warnings: list[str] = []

    def debug(self, msg):  # noqa: D401
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        if len(self.warnings) < 50:
            self.warnings.append(_short(msg, 300))

    def error(self, msg):
        pass


def base_opts(fo: FetchOptions, logger: _Logger | None = None) -> dict:
    opts = {
        "quiet": True, "no_warnings": False, "noprogress": True, "noplaylist": True,
        "socket_timeout": fo.socket_timeout, "retries": 3, "extractor_retries": 2, "fragment_retries": 5,
        "skip_unavailable_fragments": False,
        "sleep_interval_requests": fo.sleep_requests_sec or None,
        "sleep_interval_subtitles": fo.sleep_subtitles_sec or None,
        "check_formats": False, "cachedir": False, "color": {"stdout": "never", "stderr": "never"},
    }
    rt = js_runtimes()
    if rt:
        opts["js_runtimes"] = rt
    if fo.cookiefile:
        opts["cookiefile"] = fo.cookiefile
    if fo.rate_limit_kbps and fo.rate_limit_kbps > 0:
        opts["ratelimit"] = int(fo.rate_limit_kbps * 1024)
    if logger is not None:
        opts["logger"] = logger
    opts.update(fo.extra)
    return {k: v for k, v in opts.items() if v is not None}


def _extract(url: str, opts: dict) -> dict:
    YDL = _ydl_class()
    try:
        with YDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return ydl.sanitize_info(info) if info else {}
    except YouTubeError:
        raise
    except Exception as e:  # noqa: BLE001  (yt-dlp raises DownloadError/ExtractorError with the cause text)
        raise classify_error(e) from e


# ───────────────────────────── helpers ─────────────────────────────

def ts_to_dt(ts) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def date_to_dt(d: str | None) -> datetime | None:
    if not d or not re.fullmatch(r"\d{8}", str(d)):
        return None
    return datetime.strptime(str(d), "%Y%m%d").replace(tzinfo=timezone.utc)


def best_thumbnail(thumbs: list | None, fallback: str | None = None, prefer_id: str | None = None) -> str | None:
    thumbs = [t for t in (thumbs or []) if isinstance(t, dict) and t.get("url")]
    if prefer_id:
        for t in thumbs:
            if t.get("id") == prefer_id:
                return t["url"]
    if not thumbs:
        return fallback
    def size(t):
        return ((t.get("width") or 0) * (t.get("height") or 0), t.get("preference") or 0)
    return max(thumbs, key=size)["url"] if any(t.get("width") for t in thumbs) else thumbs[-1]["url"]


def map_license(value: str | None) -> str:
    return "creative_commons" if value and "creative commons" in value.lower() else "youtube"


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ───────────────────────────── channel / playlist listing ─────────────────────────────

def _channel_meta(info: dict) -> dict:
    thumbs = info.get("thumbnails") or []
    avatar = best_thumbnail([t for t in thumbs if str(t.get("id", "")).startswith("avatar")] or
                            [t for t in thumbs if (t.get("width") or 0) and t.get("width") == t.get("height")]
                            or None)
    banner = best_thumbnail([t for t in thumbs if str(t.get("id", "")).startswith("banner")] or None)
    uploader_id = info.get("uploader_id")
    return {
        "platform_channel_id": info.get("channel_id"),
        "handle": uploader_id if uploader_id and str(uploader_id).startswith("@") else None,
        "title": info.get("channel") or info.get("uploader") or _strip_tab_suffix(info.get("title") or ""),
        "description": info.get("description") or "",
        "url": info.get("channel_url") or (channel_url(info["channel_id"]) if info.get("channel_id") else ""),
        "thumbnail_url": avatar,
        "banner_url": banner,
        "subscriber_count": _int(info.get("channel_follower_count")),
        "tags": list(info.get("tags") or []),
        "metadata": {k: info.get(k) for k in ("uploader", "uploader_id", "uploader_url", "channel_url",
                                              "channel_is_verified", "availability", "modified_date",
                                              "view_count") if info.get(k) is not None},
    }


def _strip_tab_suffix(title: str) -> str:
    return re.sub(r"\s+-\s+(Videos|Shorts|Live|Playlists|Home)$", "", title)


def _flat_entry(e: dict, kind: str) -> dict | None:
    vid = e.get("id")
    if not vid or not VIDEO_ID_RE.match(str(vid)):
        return None
    if e.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        return None
    url = e.get("url") or ""
    if kind == "video" and "/shorts/" in url:
        kind = "short"
    ts = e.get("timestamp") or e.get("release_timestamp")
    return {
        "platform_media_id": vid,
        "kind": kind,
        "url": video_url(vid),
        "title": e.get("title") or "",
        "description": e.get("description") or "",
        "duration_sec": float(e["duration"]) if e.get("duration") is not None else None,
        "view_count": _int(e.get("view_count")),
        "thumbnail_url": best_thumbnail(e.get("thumbnails"), f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"),
        "published_at": ts_to_dt(ts) or date_to_dt(e.get("upload_date")),
        # tab listings only give "3 weeks ago" → yt-dlp approximate_date; detail replaces it
        "published_approximate": bool(ts or e.get("upload_date")),
        "channel_id": e.get("channel_id"),
        "live_status": e.get("live_status"),
        "availability": e.get("availability"),
    }


def _flat_opts(fo: FetchOptions, limit: int | None, logger: _Logger) -> dict:
    opts = base_opts(fo, logger)
    opts.update({"extract_flat": "in_playlist", "noplaylist": False, "skip_download": True,
                 "extractor_args": {"youtubetab": {"approximate_date": [""]}}, "lazy_playlist": False})
    if limit:
        opts["playlistend"] = int(limit)
    return opts


def resolve_channel(target: str, fo: FetchOptions) -> dict:
    """Channel root (cheap: tab list only) → {"channel": meta, "tabs": [...]}. target: URL, @handle or UC id."""
    c = classify_url(target)
    if c["type"] != "channel":
        raise ValueError("not a channel")
    url = channel_url(c["channel_id"]) if c.get("channel_id") else c["canonical_url"]
    logger = _Logger()
    info = _extract(url, _flat_opts(fo, None, logger))
    if not info.get("channel_id"):
        raise YouTubeError("unavailable", "無法解析頻道 ID", retryable=False)
    tabs = []
    for e in info.get("entries") or []:
        t = (e.get("title") or "").rsplit(" - ", 1)[-1].strip().lower()
        if t:
            tabs.append(t)
    return {"channel": _channel_meta(info), "tabs": tabs, "warnings": logger.warnings}


def list_channel(channel_id: str, fo: FetchOptions, tabs: tuple[str, ...] = LISTED_TABS,
                 limit_per_tab: int | None = None, progress: Callable[[str, int], None] | None = None) -> dict:
    """Flat-list the given tabs of a channel. → {"channel": meta, "entries": [...], "tabs": {tab: count}}"""
    entries: list[dict] = []
    counts: dict[str, int] = {}
    meta: dict | None = None
    seen: set[str] = set()
    logger = _Logger()
    for tab in tabs:
        url = f"{channel_url(channel_id)}/{tab}"
        try:
            info = _extract(url, _flat_opts(fo, limit_per_tab, logger))
        except YouTubeError as e:
            # a channel without shorts (or videos) has no such tab
            if re.search(r"does not have a .*tab", e.message.lower()):
                counts[tab] = 0
                continue
            raise
        if meta is None:
            meta = _channel_meta(info)
        kind = "short" if tab == "shorts" else "video"
        n = 0
        for e in info.get("entries") or []:
            row = _flat_entry(e or {}, kind)
            if row is None or row["platform_media_id"] in seen:
                continue
            seen.add(row["platform_media_id"])
            entries.append(row)
            n += 1
        counts[tab] = n
        if progress:
            progress(tab, n)
    return {"channel": meta or {}, "entries": entries, "tabs": counts, "warnings": logger.warnings}


def list_playlist(playlist_id: str, fo: FetchOptions, limit: int | None = None) -> dict:
    logger = _Logger()
    info = _extract(playlist_url(playlist_id), _flat_opts(fo, limit, logger))
    entries = []
    for pos, e in enumerate(info.get("entries") or []):
        row = _flat_entry(e or {}, "video")
        if row:
            row["position"] = pos
            entries.append(row)
    meta = {
        "platform_playlist_id": info.get("id") or playlist_id,
        "title": info.get("title") or "",
        "description": info.get("description") or "",
        "url": playlist_url(playlist_id),
        "channel_platform_id": info.get("channel_id"),
        "channel_title": info.get("channel") or info.get("uploader"),
        "channel_handle": info.get("uploader_id") if str(info.get("uploader_id") or "").startswith("@") else None,
        "metadata": {k: info.get(k) for k in ("playlist_count", "view_count", "modified_date", "availability",
                                              "uploader", "uploader_id", "channel_url", "tags")
                     if info.get(k) not in (None, [], "")},
    }
    meta["metadata"]["thumbnail_url"] = best_thumbnail(info.get("thumbnails"))
    return {"playlist": meta, "entries": entries, "warnings": logger.warnings}


# ───────────────────────────── per-video detail ─────────────────────────────

def _caption_kind_of(fmts: list[dict]) -> tuple[str | None, str | None]:
    """→ (asr original lang | None, tlang | None) from the timedtext URLs of one automatic_captions key."""
    for f in fmts or []:
        u = f.get("url") or ""
        if "hls_timedtext" in u:
            continue
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
        tl = (q.get("tlang") or [None])[0]
        if (q.get("kind") or [None])[0] == "asr" and not tl:
            return (q.get("lang") or [None])[0], None
        if tl:
            return None, tl
    return None, None


def caption_tracks(info: dict) -> dict:
    """→ {"manual": {key: name}, "auto": {base_lang: {"key": yt-dlp key, "name": name}}}"""
    manual = {}
    for key, fmts in (info.get("subtitles") or {}).items():
        if key in ("live_chat", "rechat") or not fmts:
            continue
        manual[key] = (fmts[0] or {}).get("name") or key
    auto: dict[str, dict] = {}
    ac = info.get("automatic_captions") or {}
    for key, fmts in ac.items():
        if key.endswith("-orig"):
            base = key[: -len("-orig")]
            auto[base] = {"key": key, "name": (fmts[0] or {}).get("name") or base}
    if not auto:  # untranslatable ASR has no -orig twin; detect it from the URL
        for key, fmts in ac.items():
            lang, _ = _caption_kind_of(fmts)
            if lang and key not in auto:
                auto[key] = {"key": key, "name": (fmts[0] or {}).get("name") or key}
    return {"manual": manual, "auto": auto}


_AUDIO_FMT_KEYS = ("format_id", "ext", "acodec", "asr", "abr", "tbr", "audio_channels", "filesize",
                   "filesize_approx", "language", "format_note", "protocol", "container", "dynamic_range")


def summarize_detail(info: dict) -> dict:
    """Map a full yt-dlp info dict to Media column values (+ `metadata` extras, `captions`)."""
    vid = info.get("id")
    caps = caption_tracks(info)
    media_type = info.get("media_type")
    live_status = info.get("live_status")
    kind = "short" if media_type == "short" else "live" if (media_type == "livestream" or info.get("was_live")) \
        else "video"
    published = ts_to_dt(info.get("timestamp")) or ts_to_dt(info.get("release_timestamp")) \
        or date_to_dt(info.get("upload_date"))
    audio_formats = [{k: f.get(k) for k in _AUDIO_FMT_KEYS if f.get(k) is not None}
                     for f in info.get("formats") or []
                     if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    audio_lang = next((f.get("language") for f in audio_formats if f.get("language")), None)
    chapters = [{"start_sec": c.get("start_time"), "end_sec": c.get("end_time"), "title": c.get("title")}
                for c in info.get("chapters") or []]
    metadata = {
        "uploader": info.get("uploader"), "uploader_id": info.get("uploader_id"),
        "uploader_url": info.get("uploader_url"), "channel": info.get("channel"),
        "channel_url": info.get("channel_url"), "channel_platform_id": info.get("channel_id"),
        "channel_follower_count": info.get("channel_follower_count"),
        "channel_is_verified": info.get("channel_is_verified"),
        "timestamp": info.get("timestamp"), "release_timestamp": info.get("release_timestamp"),
        "upload_date": info.get("upload_date"), "release_date": info.get("release_date"),
        "live_status": live_status, "media_type": media_type, "license_raw": info.get("license"),
        "playable_in_embed": info.get("playable_in_embed"), "average_rating": info.get("average_rating"),
        "audio_language": audio_lang, "aspect_ratio": info.get("aspect_ratio"),
        "creators": info.get("creators"), "artists": info.get("artists"), "track": info.get("track"),
        "album": info.get("album"), "location": info.get("location"),
        "has_drm": info.get("_has_drm") or None,
        "caption_tracks": caps,
        "audio_formats": audio_formats,
        "published_approximate": False,
    }
    return {
        "platform_media_id": vid,
        "url": video_url(vid) if vid else info.get("webpage_url") or "",
        "kind": kind,
        "title": info.get("title") or "",
        "description": info.get("description") or "",
        "thumbnail_url": info.get("thumbnail") or best_thumbnail(info.get("thumbnails")),
        "duration_sec": float(info["duration"]) if info.get("duration") is not None else None,
        "published_at": published,
        "view_count": _int(info.get("view_count")),
        "like_count": _int(info.get("like_count")),
        "comment_count": _int(info.get("comment_count")),
        "tags": list(info.get("tags") or []),
        "categories": list(info.get("categories") or []),
        "chapters": chapters,
        "language": (info.get("language") or audio_lang or None),
        "license": map_license(info.get("license")),
        "availability": info.get("availability"),
        "age_limit": _int(info.get("age_limit")),
        "was_live": bool(info.get("was_live")) if info.get("was_live") is not None else
        (live_status == "was_live" if live_status else None),
        "has_manual_captions": bool(caps["manual"]),
        "has_auto_captions": bool(caps["auto"]),
        "manual_caption_langs": sorted(caps["manual"]),
        "auto_caption_langs": sorted(caps["auto"]),
        "channel_platform_id": info.get("channel_id"),
        "live_status": live_status,
        "metadata": {k: v for k, v in metadata.items() if v is not None},
    }


def fetch_detail(video_id: str, fo: FetchOptions) -> tuple[dict, list[str]]:
    """Full extraction (no download). → (sanitized info dict, warnings)"""
    logger = _Logger()
    opts = base_opts(fo, logger)
    opts.update({"skip_download": True})
    info = _extract(video_url(video_id), opts)
    if not info or info.get("id") != video_id:
        raise YouTubeError("metadata_mismatch", f"取得的影片 ID 與要求不同（{info.get('id')} ≠ {video_id}）")
    return info, logger.warnings


def info_json_bytes(info: dict) -> bytes:
    return json.dumps(info, ensure_ascii=False, default=str).encode("utf-8")


# ───────────────────────────── caption choice ─────────────────────────────

def _match(keys: list[str], want: str) -> str | None:
    wl = want.lower()
    for k in keys:
        if k.lower() == wl:
            return k
    for k in keys:  # named tracks: "zh-TW-xyz"
        if k.lower().startswith(wl + "-") and not (wl == "zh" and k.lower().startswith(("zh-cn", "zh-hans"))):
            return k
    return None


def _tag_is(lang: str, *tags: str) -> bool:
    return any(lang == t or lang.startswith(t + "-") for t in tags)


def caption_rank(lang: str) -> int:
    """Preference of a caption language tag for the primary track (lower = better).

    0 zh-TW, zh-Hant-TW            Traditional, Taiwan
    1 zh-Hant, zh-Hant-*           Traditional
    2 zh-HK, zh-MO                 Traditional (Hong Kong / Macau)
    3 zh, cmn, other zh-*          script unknown: decided by content after download (repick_primary_by_script)
    4 zh-Hans*, zh-CN, zh-SG, zh-MY  Simplified → converted to Traditional (OpenCC s2twp) on ingestion
    5 nan*, hak*                   Taiwanese / Hakka (Han characters; downloaded anyway)
    6 anything else
    """
    lang = (lang or "").lower().replace("_", "-")
    if _tag_is(lang, "zh-tw", "zh-hant-tw"):
        return 0
    if _tag_is(lang, "zh-hant"):
        return 1
    if _tag_is(lang, "zh-hk", "zh-mo"):
        return 2
    if _tag_is(lang, *SIMPLIFIED_TAGS):
        return 4
    if _tag_is(lang, "zh", "cmn"):
        return 3
    if lang.split("-")[0] in EXTRA_DEFAULT_LANG_PREFIXES:
        return 5
    return 6


def _ranked(keys) -> list[str]:  # noqa: ANN001
    return sorted(keys, key=lambda k: (caption_rank(k), len(k), k))


def choose_tracks(caps: dict, caption_langs: list[str] | None = None, include_auto: bool = False) -> list[dict]:
    """Pick subtitle tracks to download. → [{"key": yt-dlp key, "language": lang, "kind": "manual"|"auto",
    "primary": bool, "rank": caption_rank}]. The first item is the primary track (preferred text source).

    Default (no caption_langs): the best manual track by caption_rank, plus every other manual track of the same
    rank when that rank is 3 (`zh` / `cmn`: the script is only known from the content, see
    sources.handlers.repick_primary_by_script), plus all Taiwanese/Hakka manual tracks. include_auto adds YouTube
    ASR tracks (original language only) ranked the same way; a manual track is always primary when present.
    Non-Chinese tracks are never machine-translated.
    """
    manual_keys = list(caps.get("manual") or {})
    auto = caps.get("auto") or {}
    chosen: list[dict] = []

    def add(key, lang, kind):
        if not any(c["key"] == key and c["kind"] == kind for c in chosen):
            chosen.append({"key": key, "language": lang, "kind": kind, "primary": False,
                           "rank": caption_rank(lang)})

    if caption_langs:
        for want in caption_langs:
            k = _match(manual_keys, want)
            if k:
                add(k, k, "manual")
        # A Chinese variant was asked for but the video only has another one (e.g. zh-TW asked, zh-Hans present):
        # take the best Chinese manual track instead; Simplified text is converted to Traditional on ingestion.
        if not chosen and any(caption_rank(w) <= 4 for w in caption_langs):
            zh = [k for k in _ranked(manual_keys) if caption_rank(k) <= 4]
            if zh:
                add(zh[0], zh[0], "manual")
    else:
        ranked = _ranked(manual_keys)
        if ranked:
            best = ranked[0]
            add(best, best, "manual")
            if caption_rank(best) == 3:
                for k in ranked[1:]:
                    if caption_rank(k) == 3:
                        add(k, k, "manual")
        for k in manual_keys:  # Taiwanese / Hakka text is what the corpus is about: always keep it
            if k.lower().split("-")[0] in EXTRA_DEFAULT_LANG_PREFIXES:
                add(k, k, "manual")
    if include_auto:
        for base in _ranked(auto):
            a = auto[base]
            if caption_langs and not any(base.lower() == w.lower() or base.lower().startswith(w.lower() + "-")
                                         or w.lower().startswith(base.lower() + "-") for w in caption_langs):
                continue
            add(a["key"], base, "auto")
    if chosen:
        chosen[0]["primary"] = True
    return chosen


def _pick_sub_format(fmts: list[dict]) -> dict | None:
    usable = [f for f in fmts or [] if f.get("url") and "hls_timedtext" not in f["url"]]
    for ext in SUBTITLE_FORMAT_PREFS:
        for f in usable:
            if f.get("ext") == ext:
                return f
    return None


# ───────────────────────────── caption verification ─────────────────────────────
# YouTube metadata sometimes lists an uploader caption track whose content is empty (or that 404s). A video is
# only "has manual captions" when a listed track actually contains speech text (voti.subtitles.track_content).

CAPTION_VERIFY_MAX_FETCHES = 3
EMPTY_CAPTIONS_MESSAGE = "YouTube 標示有字幕，但字幕內容是空的"
_NOT_FOUND_RE = re.compile(r"http error (?:404|410)\b|\b(?:404|410)\b.*not found")


def fetch_caption_bytes(fmt: dict, fo: FetchOptions, headers: dict | None = None) -> bytes:
    """GET one subtitle format URL through yt-dlp's networking (cookies / proxy / impersonation as configured).

    Raises YouTubeError: `caption_not_found` (HTTP 404/410: the listed track does not exist), `rate_limited` /
    `bot_check` (classified, blocked), anything else `network_or_extractor`.
    """
    from yt_dlp.networking import Request
    from yt_dlp.networking.exceptions import HTTPError

    YDL = _ydl_class()
    try:
        with YDL(base_opts(fo, _Logger())) as ydl:
            resp = ydl.urlopen(Request(fmt["url"], headers=dict(fmt.get("http_headers") or headers or {})))
            try:
                return resp.read()
            finally:
                resp.close()
    except HTTPError as e:
        if e.status in (404, 410):
            raise YouTubeError("caption_not_found", f"字幕軌不存在（HTTP {e.status}）", retryable=False) from e
        raise classify_error(f"HTTP Error {e.status}: {e.reason}") from e
    except YouTubeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise classify_error(e) from e


def verify_caption_track(info: dict, key: str, fo: FetchOptions) -> dict:
    """Fetch and classify one manual caption track (no media download).

    → {"lang", "status": "ok"|"empty"|"error", "cues", "speech_cues", "chars", "reason"?, "format"?}
    empty: no downloadable format, HTTP 404/410, zero bytes, no cues, or no speech text (only [音樂] / ♪ …).
    error: transient / network failure or unparseable content — never treated as empty.
    bot_check / rate_limited are raised (they block every further request, see the handlers' error rules).
    """
    base = {"lang": key, "cues": 0, "speech_cues": 0, "chars": 0}
    fmt = _pick_sub_format((info.get("subtitles") or {}).get(key) or [])
    if fmt is None:
        return {**base, "status": "empty", "reason": "no_formats"}
    try:
        data = fetch_caption_bytes(fmt, fo, info.get("http_headers"))
    except YouTubeError as e:
        if e.code == "caption_not_found":
            return {**base, "status": "empty", "reason": "not_found", "format": fmt.get("ext")}
        if e.code in ("bot_check", "rate_limited"):
            raise
        return {**base, "status": "error", "reason": _short(f"{e.code}: {e.message}", 200), "format": fmt.get("ext")}
    return {**base, **subs_mod.track_content(data, fmt.get("ext")), "format": fmt.get("ext")}


def verify_captions(info: dict, fo: FetchOptions, max_fetches: int = CAPTION_VERIFY_MAX_FETCHES,
                    sleep: Callable[[float], None] | None = None) -> dict | None:
    """Verify the manual caption tracks of a video in download preference order (caption_rank): the preferred
    track first; while it is empty, the next one — at most `max_fetches` tracks. Stops at the first usable
    track and at the first `error` (do not hammer YouTube while the network misbehaves).

    → None when the video lists no manual tracks, else the `caption_check` dict stored in Media.metadata:
      {"checked_at", "source": "detail", "tracks": {lang: {"status","cues","speech_cues","chars","reason"?}},
       "usable_langs": [verified-ok langs]}
    """
    import time

    manual = list(caption_tracks(info)["manual"])
    if not manual:
        return None
    sleep = sleep or time.sleep
    tracks: dict[str, dict] = {}
    for i, key in enumerate(_ranked(manual)[:max(1, max_fetches)]):
        if i and fo.sleep_subtitles_sec:
            sleep(fo.sleep_subtitles_sec)  # same spacing yt-dlp uses between subtitle downloads
        r = verify_caption_track(info, key, fo)
        tracks[key] = {k: v for k, v in r.items() if k != "lang"}
        if r["status"] in ("ok", "error"):
            break
    return new_caption_check(tracks, "detail")


def new_caption_check(tracks: dict[str, dict], source: str) -> dict:
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "source": source, "tracks": tracks,
            "usable_langs": _ranked([k for k, t in tracks.items() if t.get("status") == "ok"])}


def merge_caption_check(old: dict | None, tracks: dict[str, dict], source: str) -> dict:
    """Download-time results update the stored check per language (other languages keep their status)."""
    merged = {**((old or {}).get("tracks") or {}), **tracks}
    return new_caption_check(merged, source)


def caption_state(manual_keys, check: dict | None) -> dict:  # noqa: ANN001
    """Media caption columns from the listed manual tracks + the stored verification.

    → {"has_manual_captions", "manual_caption_langs", "captions_empty"}
      No verification of any listed track: trust the metadata (has_manual = any listed).
      Otherwise: has_manual = a track verified ok, or a verification errored (benefit of the doubt: unverified
      tracks remain claimed). manual_caption_langs = verified-ok langs (preference order), then the unverified
      ones; empty tracks are never listed, and the list is [] when has_manual is False.
      captions_empty = tracks are listed, none is usable, and every checked one was empty.
    """
    manual = _ranked(list(manual_keys or []))
    tracks = {k: v for k, v in ((check or {}).get("tracks") or {}).items() if k in manual}
    if not tracks:
        return {"has_manual_captions": bool(manual), "manual_caption_langs": sorted(manual), "captions_empty": False}
    ok = [k for k in manual if tracks.get(k, {}).get("status") == "ok"]
    errored = [k for k in manual if tracks.get(k, {}).get("status") == "error"]
    unverified = sorted(k for k in manual if tracks.get(k, {}).get("status") not in ("ok", "empty"))
    has = bool(ok) or bool(errored)
    return {"has_manual_captions": has, "manual_caption_langs": (ok + unverified) if has else [],
            "captions_empty": bool(manual) and not has}


# ───────────────────────────── download ─────────────────────────────

AUDIO_FORMAT = "bestaudio[vcodec=none][protocol^=http][format_id!*=-drc]/bestaudio[vcodec=none]/bestaudio"
# video for hard-subtitle OCR (voti.pipeline.ocr): ≤ 720p, video-only, H.264 preferred (any ffmpeg decodes it)
VIDEO_FORMAT = ("bestvideo[height<=720][vcodec^=avc1][protocol^=http]/bestvideo[height<=720][vcodec^=avc1]/"
                "bestvideo[height<=720]/best[height<=720]")


@dataclass
class DownloadResult:
    info: dict
    audio_path: Path | None
    audio_format: dict
    subtitles: list[dict]           # {"key","language","kind","primary","path","format"}
    subtitle_errors: list[dict]     # {"key","kind","error"}
    warnings: list[str]
    caption_check: dict = field(default_factory=dict)  # {manual key: track_content result} of fetched tracks
    video_path: Path | None = None  # video-only stream for OCR (video="always" / "if_no_captions" / video_only)
    video_format: dict = field(default_factory=dict)


def download(video_id: str, workdir: Path, fo: FetchOptions, caption_langs: list[str] | None = None,
             include_auto: bool = False, require_captions: bool = True,
             progress: Callable[[float, str], None] | None = None,
             should_cancel: Callable[[], bool] | None = None, video: str | None = None,
             video_only: bool = False) -> DownloadResult:
    """Best audio-only stream as served (no re-encode) + the chosen subtitle tracks.

    video (hard-subtitle OCR): "always" also downloads a ≤ 720p video-only stream (VIDEO_FORMAT) after the audio;
    "if_no_captions" only when no usable subtitle track was fetched. video_only=True downloads just that video
    (no subtitles, no audio; result.audio_path is None) — re-running OCR after the video was deleted.

    Order: extract info → choose tracks (raise no_usable_captions before downloading anything when
    require_captions and nothing fits) → subtitles → audio.
    Every fetched subtitle file is classified (voti.subtitles.track_content): empty tracks (404, zero bytes, no
    speech text) are dropped; when the preferred manual tracks are empty (default languages only) the next manual
    tracks are tried (CAPTION_VERIFY_MAX_FETCHES in all). When nothing usable is left and only emptiness (no
    network error) is to blame, YouTubeError("empty_captions") is raised before any audio is downloaded
    (`.extra` = {"caption_tracks", "checked"}).
    progress(fraction, phase) is called with the audio download fraction.
    """
    import yt_dlp

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    logger = _Logger()
    opts = base_opts(fo, logger)
    opts.update({
        "format": AUDIO_FORMAT,
        "outtmpl": {"default": str(workdir / "original.%(ext)s")},
        "postprocessors": [], "keepvideo": True, "writesubtitles": False, "writeautomaticsub": False,
        "overwrites": True, "continuedl": False, "nopart": False, "updatetime": False,
        "concurrent_fragment_downloads": 1,
    })

    def hook(d):
        if should_cancel and should_cancel():
            raise yt_dlp.utils.DownloadCancelled("cancelled")
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(min(1.0, (d.get("downloaded_bytes") or 0) / total), "audio")
    opts["progress_hooks"] = [hook]

    YDL = yt_dlp.YoutubeDL
    try:
        with YDL(opts) as ydl:
            info = ydl.extract_info(video_url(video_id), download=False)
            if not info or info.get("id") != video_id:
                raise YouTubeError("metadata_mismatch", "下載回傳的影片 ID 不一致")
            if info.get("live_status") in ("is_live", "is_upcoming"):
                raise classify_error(str(info.get("live_status")))
            raw_info = copy.deepcopy(info)
            if video_only:
                vpath, vfmt = _download_video(raw_info, workdir, opts, progress, should_cancel)
                return DownloadResult(info=ydl.sanitize_info(info), audio_path=None, audio_format={}, subtitles=[],
                                      subtitle_errors=[], warnings=logger.warnings, video_path=vpath,
                                      video_format=vfmt)
            caps = caption_tracks(info)
            chosen = choose_tracks(caps, caption_langs, include_auto)
            if require_captions and not chosen:
                if caps["manual"] or caps["auto"]:
                    msg = (f"沒有符合條件的字幕軌（要求 {caption_langs or '預設繁中'}"
                           f"{'，含自動字幕' if include_auto else '，不含自動字幕'}）；可用：手動 "
                           f"{sorted(caps['manual']) or '無'}，自動 {sorted(caps['auto']) or '無'}")
                else:
                    msg = "影片沒有任何字幕軌"
                raise YouTubeError("no_usable_captions", msg, retryable=False)

            # subtitles first: cheap, and a missing / empty track should not cost an audio download
            subs, sub_errors, checked, auto_empty = [], [], {}, []

            def fetch(c: dict) -> None:
                pool = (info.get("subtitles") if c["kind"] == "manual" else info.get("automatic_captions")) or {}
                fmt = _pick_sub_format(pool.get(c["key"]) or [])
                if fmt is None:
                    if c["kind"] == "manual":
                        checked[c["key"]] = {"status": "empty", "cues": 0, "speech_cues": 0, "chars": 0,
                                             "reason": "no_formats"}
                    else:
                        sub_errors.append({"key": c["key"], "kind": c["kind"], "error": "no downloadable format"})
                    return
                safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", c["key"])
                path = workdir / f"sub.{safe_key}.{c['kind']}.{fmt['ext']}"
                try:
                    sub = {**fmt}
                    sub.setdefault("http_headers", info.get("http_headers"))
                    ydl.dl(str(path), sub, subtitle=True)
                except yt_dlp.utils.DownloadCancelled:
                    raise
                except Exception as e:  # noqa: BLE001
                    err = classify_error(e)
                    if err.code in ("bot_check", "rate_limited"):
                        raise err from e
                    if c["kind"] == "manual" and _NOT_FOUND_RE.search(str(e).lower()):
                        checked[c["key"]] = {"status": "empty", "cues": 0, "speech_cues": 0, "chars": 0,
                                             "reason": "not_found", "format": fmt["ext"]}
                        return
                    sub_errors.append({"key": c["key"], "kind": c["kind"], "error": _short(str(e), 300)})
                    return
                data = path.read_bytes() if path.exists() else b""
                content = subs_mod.track_content(data, fmt["ext"])
                if c["kind"] == "manual":
                    checked[c["key"]] = {**content, "format": fmt["ext"]}
                if content["status"] == "empty":
                    if c["kind"] == "auto":
                        auto_empty.append(f"{c['key']} (auto)")
                    path.unlink(missing_ok=True)  # YouTube listed it, but there is nothing to use
                    return
                subs.append({**c, "path": path, "format": fmt["ext"]})

            for c in chosen:
                fetch(c)
            first = chosen[0] if chosen else None
            if not caption_langs and first and first["kind"] == "manual" and \
                    checked.get(first["key"], {}).get("status") == "empty" and \
                    not any(s_["kind"] == "manual" and s_["rank"] < 5 for s_ in subs):
                # the preferred manual track was empty: try the next ones in preference order
                budget = max(0, CAPTION_VERIFY_MAX_FETCHES - len(checked))
                for k in _ranked(caps["manual"]):
                    if budget <= 0:
                        break
                    if k in checked or any(c["key"] == k and c["kind"] == "manual" for c in chosen):
                        continue
                    fetch({"key": k, "language": k, "kind": "manual", "primary": False, "rank": caption_rank(k)})
                    budget -= 1
                    if checked.get(k, {}).get("status") == "ok":
                        break
            empty = sorted(k for k, v in checked.items() if v["status"] == "empty") + auto_empty
            if chosen and not subs and empty and not sub_errors and require_captions:
                err = YouTubeError("empty_captions", f"{EMPTY_CAPTIONS_MESSAGE}（{', '.join(empty)}）",
                                   retryable=False)
                err.extra = {"caption_tracks": caps, "checked": checked}
                raise err
            if chosen and not subs and require_captions:
                raise YouTubeError("subtitle_unavailable",
                                   "字幕軌列在影片資訊中但下載失敗：" +
                                   "; ".join(f"{e['key']}: {e['error']}" for e in sub_errors), retryable=True)
            if subs and not any(s_["primary"] for s_ in subs):
                manual_subs = [s_ for s_ in subs if s_["kind"] == "manual"]
                min(manual_subs or subs, key=lambda s_: s_.get("rank", 6))["primary"] = True

            if progress:
                progress(0.0, "audio")
            result = ydl.process_ie_result(info, download=True)
            info = ydl.sanitize_info(result)
            vpath, vfmt = None, {}
            if video == "always" or (video == "if_no_captions" and not subs):
                vpath, vfmt = _download_video(raw_info, workdir, opts, progress, should_cancel)
    except YouTubeError:
        raise
    except yt_dlp.utils.DownloadCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        raise classify_error(e) from e

    audio = _find_audio(workdir, info)
    if audio is None:
        raise YouTubeError("audio_unavailable", "音訊未成功下載")
    fmt = {k: info.get(k) for k in _AUDIO_FMT_KEYS if info.get(k) is not None}
    for f in info.get("requested_downloads") or []:
        fmt.update({k: f.get(k) for k in ("filesize", "ext", "format_id") if f.get(k) is not None})
    return DownloadResult(info=info, audio_path=audio, audio_format=fmt, subtitles=subs,
                          subtitle_errors=sub_errors, warnings=logger.warnings, caption_check=checked,
                          video_path=vpath, video_format=vfmt)


def _download_video(raw_info: dict, workdir: Path, opts: dict, progress, should_cancel) -> tuple[Path, dict]:  # noqa: ANN001
    """Download the VIDEO_FORMAT stream of an already extracted video into workdir/video.<ext>."""
    import yt_dlp

    def hook(d):  # noqa: ANN001, ANN202
        if should_cancel and should_cancel():
            raise yt_dlp.utils.DownloadCancelled("cancelled")
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(min(1.0, (d.get("downloaded_bytes") or 0) / total), "video")
    vopts = {**opts, "format": VIDEO_FORMAT, "outtmpl": {"default": str(Path(workdir) / "video.%(ext)s")},
             "progress_hooks": [hook]}
    if progress:
        progress(0.0, "video")
    with yt_dlp.YoutubeDL(vopts) as ydl:
        res = ydl.process_ie_result(copy.deepcopy(raw_info), download=True)
    path = None
    for f in res.get("requested_downloads") or []:
        p = Path(f.get("filepath") or "")
        if p.is_file():
            path = p
    if path is None:
        path = next((p for p in sorted(Path(workdir).glob("video.*")) if not p.name.endswith((".part", ".ytdl"))),
                    None)
    if path is None:
        raise YouTubeError("video_unavailable", "影片（OCR 用）未成功下載")
    fmt = {k: res.get(k) for k in ("format_id", "ext", "width", "height", "vcodec", "fps", "filesize", "tbr")
           if res.get(k) is not None}
    return path, fmt


def _find_audio(workdir: Path, info: dict) -> Path | None:
    for f in info.get("requested_downloads") or []:
        p = Path(f.get("filepath") or "")
        if p.is_file():
            return p
    for p in sorted(workdir.glob("original.*")):
        if p.is_file() and not p.name.endswith((".part", ".ytdl")):
            return p
    return None


AUDIO_CONTENT_TYPES = {"webm": "audio/webm", "m4a": "audio/mp4", "mp4": "audio/mp4", "opus": "audio/ogg",
                       "ogg": "audio/ogg", "mp3": "audio/mpeg", "aac": "audio/aac", "wav": "audio/wav",
                       "flac": "audio/flac"}
SUBTITLE_CONTENT_TYPES = {"json3": "application/json", "vtt": "text/vtt", "srt": "application/x-subrip"}


def new_tempdir(base: str | None) -> tempfile.TemporaryDirectory:
    if base:
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
            return tempfile.TemporaryDirectory(dir=base, prefix="yt-")
        except OSError:
            pass
    return tempfile.TemporaryDirectory(prefix="voti-yt-")
