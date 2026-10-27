"""Public YouTube metadata and opt-in yt-dlp retrieval. No cookies or access bypass."""
from __future__ import annotations

import json
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


class YouTubeError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def normalize_video_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or parsed.port:
        raise ValueError("須提供一般 YouTube 影片網址")
    query = urllib.parse.parse_qs(parsed.query)
    if host in ("www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            video_id = query.get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            video_id = parsed.path.split("/")[2]
        else:
            raise ValueError("網址不是單支影片；播放清單請先展開候選")
    elif host in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.lstrip("/")
    else:
        raise ValueError("僅接受 YouTube 影片網址")
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("影片 ID 格式無效")
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


# Same as `yt-dlp --convert-subs srt`: ffmpeg turns whatever YouTube serves (vtt, srv3, ttml) into SRT.
SRT_CONVERT = {"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"}


def _ydl():
    try:
        import yt_dlp
    except ImportError as exc:
        raise YouTubeError("missing_dependency", "缺少 yt-dlp；請安裝可選相依套件") from exc
    return yt_dlp


def _base_opts() -> dict:
    """Shared options. yt-dlp needs a local JS runtime for YouTube; use whichever is installed."""
    import shutil
    runtimes = {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}
    return {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "socket_timeout": 20,
            "retries": 2, "extractor_retries": 2, **({"js_runtimes": runtimes} if runtimes else {})}


def _classify(exc: Exception) -> YouTubeError:
    message = str(exc).lower()
    if "sign in" in message or "login" in message or "private" in message:
        return YouTubeError("access_restricted", "影片需要登入或無法公開取得；不會繞過限制")
    if "not available" in message or "unavailable" in message or "removed" in message:
        return YouTubeError("unavailable", "影片不存在或目前不可取得")
    if "geo" in message or "region" in message or "country" in message:
        return YouTubeError("region_restricted", "影片受地區限制；不會繞過限制")
    return YouTubeError("network_or_extractor", f"來源檢查／取得失敗：{str(exc)[:250]}")


def summarize(info: dict, video_id: str, canonical: str) -> dict:
    tracks = []
    for field, kind in (("subtitles", "uploader_provided"), ("automatic_captions", "auto_generated")):
        for language, variants in (info.get(field) or {}).items():
            tracks.append({"language": language, "kind": kind,
                           "formats": sorted({v.get("ext", "") for v in variants if v.get("ext")}),
                           "availability": "listed_not_verified_downloadable"})
    return {"id": video_id, "url": canonical, "title": info.get("title") or "",
            "description": info.get("description") or "", "channel_id": info.get("channel_id"),
            "channel_name": info.get("channel"), "published_date": info.get("upload_date"),
            "duration_sec": info.get("duration"), "language": info.get("language"),
            "license_label": info.get("license") or "unknown", "tracks": tracks,
            "categories": info.get("categories") or [], "tags": (info.get("tags") or [])[:30],
            "metadata": {k: info.get(k) for k in ("id", "title", "channel_id", "upload_date", "duration", "language",
                                                  "license", "categories", "tags")}}

def inspect_video(url: str) -> dict:
    video_id, canonical = normalize_video_url(url)
    yt_dlp = _ydl()
    try:
        with yt_dlp.YoutubeDL({**_base_opts(), "skip_download": True}) as ydl:
            info = ydl.extract_info(canonical, download=False)
    except Exception as exc:
        raise _classify(exc) from exc
    if not info or info.get("id") != video_id:
        raise YouTubeError("metadata_mismatch", "取得的影片 ID 與要求不同")
    return summarize(info, video_id, canonical)

def download_video(url: str, dest_dir: Path, subtitle_language: str,
                   subtitle_kind: str = "uploader_provided", progress=None) -> dict:
    """Caller confirms usage rights first. Returns files only after actual retrieval checks.

    progress(fraction) follows yt-dlp's byte counters for the audio download; unknown totals report nothing."""
    video_id, canonical = normalize_video_url(url)
    if subtitle_kind not in ("uploader_provided", "auto_generated"):
        raise ValueError("字幕來源類型不支援")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,20}", subtitle_language):
        raise ValueError("字幕語言代碼格式無效")
    yt_dlp = _ydl()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest_dir, prefix="retrieve-") as td:
        stage = Path(td)
        opts = {**_base_opts(), "format": "bestaudio/best", "outtmpl": str(stage / f"{video_id}.%(ext)s"),
                "writesubtitles": subtitle_kind == "uploader_provided",
                "writeautomaticsub": subtitle_kind == "auto_generated", "subtitleslangs": [subtitle_language],
                "subtitlesformat": "srt/best", "postprocessors": [SRT_CONVERT], "skip_download": False, "fragment_retries": 2, "overwrites": False}
        if progress:
            def hook(state):
                total = state.get("total_bytes") or state.get("total_bytes_estimate")
                if state.get("status") == "downloading" and total \
                        and not str(state.get("filename", "")).endswith((".vtt", ".srt")):
                    progress(min(1.0, (state.get("downloaded_bytes") or 0) / total))
            opts["progress_hooks"] = [hook]
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(canonical, download=True)
        except Exception as exc:
            raise _classify(exc) from exc
        if not info or info.get("id") != video_id:
            raise YouTubeError("metadata_mismatch", "下載回傳的影片 ID 不一致")
        subtitle_files = [f for f in stage.iterdir() if f.suffix in (".srt", ".vtt")]
        audio_files = [f for f in stage.iterdir() if f.is_file() and f not in subtitle_files and not f.name.endswith(".part")]
        if not audio_files:
            raise YouTubeError("audio_unavailable", "音訊未成功下載")
        if not subtitle_files:
            raise YouTubeError("subtitle_unavailable", "指定字幕軌不存在或無法取得；可手動補上 SRT/VTT")
        from .media import probe
        probe(audio_files[0])
        from .subtitles import parse_file
        if not parse_file(subtitle_files[0]):
            raise YouTubeError("subtitle_invalid", "字幕下載後無可解析內容")
        results = {}
        for kind, item in (("audio", audio_files[0]), ("subtitle", subtitle_files[0])):
            target = dest_dir / f"{video_id}-{kind}{item.suffix.lower()}"
            if target.exists():
                raise YouTubeError("file_conflict", f"目標檔案已存在：{target.name}")
            item.replace(target)
            results[kind] = target
        metadata = dest_dir / f"{video_id}-metadata.json"
        metadata.write_text(json.dumps({k: info.get(k) for k in
                          ("id", "title", "description", "channel_id", "channel", "upload_date", "duration", "language", "license")},
                         ensure_ascii=False), encoding="utf-8")
        results["metadata"] = metadata
        results["info"] = summarize(info, video_id, canonical)  # same fields as inspect_video, no second page fetch
        return results


def download_subtitles(url: str, dest_dir: Path, subtitle_language: str, subtitle_kind: str = "uploader_provided") -> Path:
    """Only the caption track (skip_download). Caller confirms usage rights first."""
    video_id, canonical = normalize_video_url(url)
    if subtitle_kind not in ("uploader_provided", "auto_generated"):
        raise ValueError("字幕來源類型不支援")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,20}", subtitle_language):
        raise ValueError("字幕語言代碼格式無效")
    yt_dlp = _ydl()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest_dir, prefix="subtitles-") as td:
        stage = Path(td)
        opts = {**_base_opts(), "skip_download": True, "outtmpl": str(stage / f"{video_id}.%(ext)s"),
                "writesubtitles": subtitle_kind == "uploader_provided",
                "writeautomaticsub": subtitle_kind == "auto_generated", "subtitleslangs": [subtitle_language],
                "subtitlesformat": "srt/best", "postprocessors": [SRT_CONVERT]}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(canonical, download=True)
        except Exception as exc:
            raise _classify(exc) from exc
        if not info or info.get("id") != video_id:
            raise YouTubeError("metadata_mismatch", "下載回傳的影片 ID 不一致")
        found = sorted(f for f in stage.iterdir() if f.suffix in (".srt", ".vtt"))
        if not found:
            raise YouTubeError("subtitle_unavailable", "指定字幕軌不存在或無法取得；可手動補上 SRT/VTT")
        from .subtitles import parse_file
        if not parse_file(found[0]):
            raise YouTubeError("subtitle_invalid", "字幕下載後無可解析內容")
        target = dest_dir / f"{video_id}-subtitle{found[0].suffix.lower()}"
        if target.exists():
            raise YouTubeError("file_conflict", f"目標檔案已存在：{target.name}")
        found[0].replace(target)
        return target

def expand_candidates(url: str, limit: int = 20) -> list[dict]:
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in ("https", "http") or (parsed.hostname or "").lower() not in (
        "youtube.com", "www.youtube.com", "m.youtube.com"):
        raise ValueError("候選網址須為 YouTube 頻道或播放清單")
    if not (parsed.path.startswith(("/playlist", "/channel/", "/@", "/c/")) or
            (parsed.path == "/watch" and "list=" in parsed.query)):
        raise ValueError("不是頻道或播放清單網址")
    if not 1 <= limit <= 50:
        raise ValueError("候選上限須為 1 至 50")
    yt_dlp = _ydl()
    try:
        with yt_dlp.YoutubeDL({**_base_opts(), "noplaylist": False, "extract_flat": "in_playlist",
                               "skip_download": True, "playlistend": limit}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise _classify(exc) from exc
    items = []
    for entry in (info or {}).get("entries") or []:
        vid = (entry or {}).get("id", "")
        if VIDEO_ID.fullmatch(vid):
            items.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                          "title": entry.get("title") or "", "candidate_origin": url})
    return items[:limit]


def search_candidates(api_key: str, query: str, limit: int = 10,
                      published_after: str | None = None, channel_id: str | None = None) -> list[dict]:
    if not api_key:
        raise YouTubeError("missing_api_key", "未設定 YOUTUBE_API_KEY；本地與手動 URL 匯入仍可使用")
    if not 1 <= limit <= 50:
        raise ValueError("搜尋上限須為 1 至 50")
    params = {"part": "snippet", "type": "video", "q": query, "maxResults": str(limit),
              "videoLicense": "creativeCommon", "videoCaption": "closedCaption", "key": api_key}
    if published_after:
        params["publishedAfter"] = published_after
    if channel_id:
        params["channelId"] = channel_id
    request = urllib.request.Request("https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except Exception as exc:
        # Avoid printing API key in the request URL or underlying exception.
        raise YouTubeError("api_error", "YouTube API 查詢失敗；請檢查金鑰、配額與連線") from exc
    return [{"id": i["id"]["videoId"], "title": i["snippet"]["title"],
             "url": f"https://www.youtube.com/watch?v={i['id']['videoId']}",
             "candidate_origin": "youtube_api_search", "search_query": query}
            for i in data.get("items", []) if i.get("id", {}).get("videoId")]
