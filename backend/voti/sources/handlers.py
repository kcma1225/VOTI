"""Fetch-queue job handlers (YouTube): source.resolve, channel.list, playlist.list, media.detail, media.download.

Rate limiting
  * Every YouTube extraction job first takes a global Redis "start slot" so job starts are spaced across all
    fetch workers: `sleep_between_requests_sec` (default 2) for listing/detail, `sleep_between_downloads_sec`
    for downloads. Inside one extraction yt-dlp sleeps between HTTP requests (sleep_interval_requests).
  * `max_concurrent_downloads` is enforced with Redis slots; `rate_limit_kbps` maps to yt-dlp `ratelimit`.
  * Cookies: only when settings.youtube.cookies_enabled and S3 `system/cookies.txt` exists (copied to a temp
    file per job). Browser cookies are never read.

media.detail modes (payload / ids)
  media_id set                         → that one video
  payload {"media_ids": [...]}         → a batch; processes `batch_size` (default 20) and chains the rest
  channel_id set, no media_id          → the next `batch_size` un-detailed media of the channel, then chains
                                         itself while more remain
  Listing enqueues ONE chained detail job per channel/playlist (not one per video) so user-triggered
  downloads are not stuck behind thousands of queued detail jobs in the FIFO fetch queue.
"""
from __future__ import annotations

import logging
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from .. import storage
from ..config import get_config
from ..db import session_scope
from ..models import Asset, Channel, Cue, Job, Media, Playlist, PlaylistItem, Setting, Track, utcnow
from ..queue import (Cancelled, JobContext, JobError, active_list_job, enqueue, handler, redis_client,
                     sync_channel_list_status)
from .. import subtitles as subs_mod
from .. import textconv
from . import youtube as yt

log = logging.getLogger("voti.sources")

COOKIES_KEY = "system/cookies.txt"
YT_DEFAULTS = {"rate_limit_kbps": 0, "sleep_between_downloads_sec": 5, "max_concurrent_downloads": 1,
               "cookies_enabled": False,
               # re-list every channel this often to find new videos (voti.scheduler); 0 disables
               "auto_refresh_hours": 24,
               # optional (not in the admin form yet): spacing between listing/detail job starts
               "sleep_between_requests_sec": 2.0}
DETAIL_BATCH = 20
PRE_DOWNLOAD_STATUSES = ("listed", "detailed", "failed", "blocked", "skipped")


# ───────────────────────────── settings / options ─────────────────────────────

def youtube_settings() -> dict:
    with session_scope() as s:
        row = s.get(Setting, "youtube")
        value = dict(row.value or {}) if row else {}
    out = dict(YT_DEFAULTS)
    out.update({k: v for k, v in value.items() if v is not None})
    return out


@contextmanager
def fetch_options(settings: dict) -> Iterator[yt.FetchOptions]:
    cookiefile = None
    tmp = None
    try:
        if settings.get("cookies_enabled") and storage.head(COOKIES_KEY):
            tmp = tempfile.NamedTemporaryFile(prefix="voti-cookies-", suffix=".txt", delete=False)
            tmp.close()
            storage.get_file(COOKIES_KEY, Path(tmp.name))
            cookiefile = tmp.name
        yield yt.FetchOptions(rate_limit_kbps=int(settings.get("rate_limit_kbps") or 0), cookiefile=cookiefile)
    finally:
        if tmp is not None:
            Path(tmp.name).unlink(missing_ok=True)


def _cookies_used(fo: yt.FetchOptions) -> bool:
    return bool(fo.cookiefile)


# YouTube failures the queue retries automatically (delayed backoff). Blocked codes (bot_check, rate_limited,
# age/members/region restrictions) need a human (cookies, slower settings) and are never auto-retried.
YT_TRANSIENT_CODES = {"network_or_extractor", "subtitle_unavailable", "audio_unavailable"}


def _job_error(e: yt.YouTubeError, cookies: bool = False) -> JobError:
    msg = e.message
    if e.code == "bot_check" and cookies:
        msg += "\n（已使用 cookies 仍被擋：cookies 可能已過期，請重新匯出上傳。）"
    return JobError(e.code, msg, blocked=e.blocked,
                    retryable=bool(e.retryable and not e.blocked and e.code in YT_TRANSIENT_CODES))


# ───────────────────────────── Redis spacing / slots ─────────────────────────────

def _wait(ctx: JobContext, seconds: float, phase: str) -> None:
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        ctx.progress(phase=phase, message=f"等待 {left:.0f} 秒（速率限制）")
        time.sleep(min(left, 2.0))


def throttle(ctx: JobContext, name: str, interval: float) -> None:
    """Global spacing: at most one job start per `interval` seconds per name across all workers."""
    if not interval or interval <= 0:
        return
    key = f"voti:yt:gap:{name}"
    try:
        r = redis_client()
        while not r.set(key, str(ctx.job_id), nx=True, px=int(interval * 1000)):
            ttl = r.pttl(key)
            _wait(ctx, max(0.2, (ttl if ttl and ttl > 0 else 500) / 1000.0), "rate_limit")
    except Cancelled:
        raise
    except Exception:  # noqa: BLE001  (redis down → local sleep only)
        log.warning("redis throttle unavailable; sleeping locally")
        _wait(ctx, interval, "rate_limit")


@contextmanager
def download_slot(ctx: JobContext, max_concurrent: int) -> Iterator[None]:
    n = max(1, int(max_concurrent or 1))
    r = None
    held = None
    try:
        r = redis_client()
        while held is None:
            for i in range(n):
                k = f"voti:yt:dlslot:{i}"
                if r.set(k, str(ctx.job_id), nx=True, ex=900):
                    held = k
                    break
                if _slot_abandoned(r.get(k), ctx.job_id):
                    # held by this job's previous (interrupted) attempt or by a job no longer running
                    r.set(k, str(ctx.job_id), ex=900)
                    held = k
                    break
            if held is None:
                _wait(ctx, 3.0, "waiting_slot")
    except Cancelled:
        raise
    except Exception:  # noqa: BLE001
        log.warning("redis slot unavailable; continuing without concurrency limit")
        r, held = None, None
    try:
        yield
    finally:
        if r is not None and held is not None:
            try:
                if (r.get(held) or b"").decode() == str(ctx.job_id):
                    r.delete(held)
            except Exception:  # noqa: BLE001
                pass


def _slot_abandoned(owner: bytes | None, job_id: uuid.UUID) -> bool:
    if not owner:
        return False
    owner_s = owner.decode()
    if owner_s == str(job_id):
        return True
    try:
        oid = uuid.UUID(owner_s)
    except ValueError:
        return True
    with session_scope() as s:
        status = s.scalar(select(Job.status).where(Job.id == oid))
    return status != "running"


def _refresh_slot(ctx: JobContext) -> None:
    try:
        r = redis_client()
        for k in r.scan_iter("voti:yt:dlslot:*"):
            if (r.get(k) or b"").decode() == str(ctx.job_id):
                r.expire(k, 900)
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────────── row helpers ─────────────────────────────

_CHANNEL_FIELDS = ("handle", "title", "description", "url", "thumbnail_url", "banner_url", "subscriber_count")


def upsert_channel(s, meta: dict, row: Channel | None = None) -> Channel:
    """Find/create a Channel by platform id and copy listing metadata. `row` = row pre-created by the API."""
    pcid = meta.get("platform_channel_id")
    existing = s.scalar(select(Channel).where(Channel.platform == "youtube", Channel.platform_channel_id == pcid)) \
        if pcid else None
    if row is not None and existing is not None and existing.id != row.id:
        # the API made a placeholder row (e.g. keyed by handle) but the channel already exists
        if not yt.CHANNEL_ID_RE.match(row.platform_channel_id or ""):
            s.delete(row)
            s.flush()
        row = existing
    row = row or existing
    if row is None:
        row = Channel(platform="youtube", platform_channel_id=pcid)
        s.add(row)
    if pcid:
        row.platform_channel_id = pcid
    for f in _CHANNEL_FIELDS:
        v = meta.get(f)
        if v not in (None, ""):
            setattr(row, f, v)
    if meta.get("tags"):
        row.tags = list(meta["tags"])
    if meta.get("metadata"):
        row.metadata_ = {**(row.metadata_ or {}), **meta["metadata"]}
    s.flush()
    return row


def queue_channel_listing(s, ch: Channel, *, created_by: uuid.UUID | None = None,
                          parent_id: uuid.UUID | None = None, payload: dict | None = None):  # noqa: ANN201
    """Enqueue channel.list for a channel unless one is already queued/running; list_status follows."""
    job = active_list_job(s, ch.id)
    if job is None or job.kind != "channel.list":
        job = enqueue(s, "channel.list", channel_id=ch.id, created_by=created_by, parent_id=parent_id,
                      payload=payload or {"tabs": list(yt.LISTED_TABS), "detail": True})
        s.flush()
    sync_channel_list_status(s, ch)
    return job


def _minimal_channel(s, pcid: str | None, title: str | None = None, handle: str | None = None,
                     url: str | None = None, *, auto_list: bool = True, subscriber_count: int | None = None,
                     created_by: uuid.UUID | None = None, parent_id: uuid.UUID | None = None,
                     source: dict | None = None) -> Channel | None:
    """Find or create the channel a video / playlist belongs to. A channel that is not in the library yet is
    created right away (title/handle from the video's info, so the channel list can show its name) and, with
    auto_list, gets list_status=queued and a channel.list job (adding one video adds its channel)."""
    if not pcid:
        return None
    ch = s.scalar(select(Channel).where(Channel.platform == "youtube", Channel.platform_channel_id == pcid))
    if ch is None:
        new = Channel(platform="youtube", platform_channel_id=pcid, title=title or "", handle=handle,
                      url=url or yt.channel_url(pcid), subscriber_count=subscriber_count,
                      metadata_={"auto_added": {"at": utcnow().isoformat(), **(source or {})}} if source else {})
        try:
            with s.begin_nested():
                s.add(new)
                s.flush()
            ch = new
        except IntegrityError:  # another job created it concurrently
            ch = s.scalar(select(Channel).where(Channel.platform == "youtube", Channel.platform_channel_id == pcid))
            if ch is None:
                raise
        else:
            if auto_list:
                queue_channel_listing(s, ch, created_by=created_by, parent_id=parent_id)
                log.info("auto-added channel %s (%s) from %s", pcid, title, source)
    else:
        if title and not ch.title:
            ch.title = title
        if handle and not ch.handle:
            ch.handle = handle
    return ch


def _get_or_create_media(s, video_id: str, row_id: uuid.UUID | None = None) -> tuple[Media, bool]:
    m = s.get(Media, row_id) if row_id else None
    if m is None:
        m = s.scalar(select(Media).where(Media.platform == "youtube", Media.platform_media_id == video_id))
    if m is not None:
        return m, False
    m = Media(platform="youtube", platform_media_id=video_id, url=yt.video_url(video_id), status="listed")
    s.add(m)
    s.flush()
    return m, True


def upsert_listed_media(s, entries: list[dict], channel: Channel | None) -> tuple[list[Media], list[Media]]:
    """Insert/update media from flat listing entries. → (all rows in entry order, newly created rows)"""
    rows: list[Media] = []
    new: list[Media] = []
    by_id: dict[str, Media] = {}
    ids = [e["platform_media_id"] for e in entries]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for m in s.scalars(select(Media).where(Media.platform == "youtube", Media.platform_media_id.in_(chunk))):
            by_id[m.platform_media_id] = m
    for e in entries:
        m = by_id.get(e["platform_media_id"])
        if m is None:
            m = Media(platform="youtube", platform_media_id=e["platform_media_id"], url=e["url"],
                      status="listed", kind=e["kind"], metadata_={})
            s.add(m)
            by_id[e["platform_media_id"]] = m
            new.append(m)
        undetailed = m.detailed_at is None
        if channel is not None and m.channel_id is None:
            m.channel_id = channel.id
        if undetailed:
            m.kind = e["kind"]
            for f in ("title", "description", "thumbnail_url"):
                if e.get(f):
                    setattr(m, f, e[f])
            if e.get("duration_sec") is not None:
                m.duration_sec = e["duration_sec"]
            if e.get("published_at") and m.published_at is None:
                m.published_at = e["published_at"]
                m.metadata_ = {**(m.metadata_ or {}), "published_approximate": e.get("published_approximate", True)}
            if e.get("availability"):
                m.availability = e["availability"]
        elif e["kind"] == "short" and m.kind == "video":
            m.kind = "short"
        if e.get("view_count") is not None:
            m.view_count = e["view_count"]
        rows.append(m)
    s.flush()
    return rows, new


def apply_detail(s, m: Media, d: dict, ctx: JobContext | None = None) -> None:
    for f in ("url", "kind", "title", "description", "thumbnail_url", "duration_sec", "published_at",
              "view_count", "like_count", "comment_count", "tags", "categories", "chapters", "language", "license",
              "availability", "age_limit", "was_live", "has_manual_captions", "has_auto_captions",
              "manual_caption_langs", "auto_caption_langs"):
        v = d.get(f)
        if f in ("tags", "categories", "chapters", "manual_caption_langs", "auto_caption_langs"):
            setattr(m, f, list(v or []))
        elif v is not None or f in ("language", "availability"):
            if f == "language" and v:
                v = str(v)[:16]
            setattr(m, f, v)
    m.metadata_ = {**(m.metadata_ or {}), **(d.get("metadata") or {})}
    ch = _minimal_channel(s, d.get("channel_platform_id"), d["metadata"].get("channel"),
                          d["metadata"].get("uploader_id") if str(d["metadata"].get("uploader_id") or "")
                          .startswith("@") else None, d["metadata"].get("channel_url"),
                          subscriber_count=yt._int(d["metadata"].get("channel_follower_count")),
                          created_by=ctx.created_by if ctx else None, parent_id=ctx.job_id if ctx else None,
                          source={"media_id": str(m.id), "video_id": m.platform_media_id})
    if ch is not None:
        m.channel_id = ch.id
    m.detailed_at = utcnow()


def store_info_json(s, m: Media, info: dict) -> Asset:
    asset = storage.store_bytes_asset(s, yt.info_json_bytes(info), storage.media_key(m.id, "info.json"),
                                      "info_json", media_id=m.id, content_type="application/json",
                                      params={"extractor": info.get("extractor"), "yt_dlp": _ytdlp_version(),
                                              "epoch": info.get("epoch")})
    m.info_json_asset_id = asset.id
    return asset


def _ytdlp_version() -> str:
    try:
        import yt_dlp.version
        return yt_dlp.version.__version__
    except Exception:  # noqa: BLE001
        return "?"


def _active_detail_sweep(s, channel_id: uuid.UUID | None) -> bool:
    return s.scalar(select(Job.id).where(Job.kind == "media.detail", Job.media_id.is_(None),
                                         Job.channel_id == channel_id,
                                         Job.status.in_(("queued", "running"))).limit(1)) is not None


# ───────────────────────────── source.resolve ─────────────────────────────

@handler("source.resolve")
def resolve(ctx: JobContext) -> dict:
    url = ctx.payload.get("url") or ""
    try:
        c = yt.classify_url(url)
    except ValueError as e:
        raise JobError("invalid_url", f"無法辨識的 YouTube 網址：{e}", retryable=False) from e
    settings = youtube_settings()
    ctx.progress(0.05, "resolve", c["type"], force=True)

    if c["type"] == "video":
        with session_scope() as s:
            m, created = _get_or_create_media(s, c["video_id"], ctx.media_id)
            if c.get("kind_hint") == "short" and m.detailed_at is None:
                m.kind = "short"
            media_id = m.id
        ctx.then("media.detail", media_id=media_id)
        return {"type": "video", "media_id": str(media_id), "created": created}

    if c["type"] == "playlist":
        with session_scope() as s:
            p = s.scalar(select(Playlist).where(Playlist.platform == "youtube",
                                                Playlist.platform_playlist_id == c["playlist_id"]))
            if p is None:
                p = Playlist(platform="youtube", platform_playlist_id=c["playlist_id"], url=c["canonical_url"])
                s.add(p)
                s.flush()
            pid = p.id
        ctx.then("playlist.list", payload={"playlist_id": str(pid), "detail": ctx.payload.get("detail", True)},
                 channel_id=ctx.channel_id)
        return {"type": "playlist", "playlist_id": str(pid)}

    # channel
    throttle(ctx, "request", float(settings["sleep_between_requests_sec"]))
    with fetch_options(settings) as fo:
        try:
            res = yt.resolve_channel(c.get("channel_id") or c["canonical_url"], fo)
        except yt.YouTubeError as e:
            if ctx.channel_id:
                with session_scope() as s:
                    ch = s.get(Channel, ctx.channel_id)
                    if ch is not None:
                        ch.list_status = "failed"
            raise _job_error(e, _cookies_used(fo)) from e
    with session_scope() as s:
        pre = s.get(Channel, ctx.channel_id) if ctx.channel_id else None
        ch = upsert_channel(s, res["channel"], pre)
        ch.metadata_ = {**(ch.metadata_ or {}), "tabs": res["tabs"]}
        channel_id = ch.id
        if ch.list_status not in ("listing",):
            ch.list_status = "queued"  # the chained channel.list commits with this job's completion
        # this job now stands for the channel's listing until channel.list takes over (status reconciliation)
        s.execute(update(Job).where(Job.id == ctx.job_id, Job.channel_id.is_(None)).values(channel_id=channel_id))
    tabs = [t for t in yt.LISTED_TABS if t in res["tabs"]] or list(yt.LISTED_TABS)
    ctx.then("channel.list", payload={"tabs": tabs, "detail": ctx.payload.get("detail", True)},
             channel_id=channel_id)
    return {"type": "channel", "channel_id": str(channel_id), "platform_channel_id": res["channel"]
            ["platform_channel_id"], "tabs": res["tabs"]}


# ───────────────────────────── channel.list ─────────────────────────────

@handler("channel.list")
def channel_list(ctx: JobContext) -> dict:
    channel_id = ctx.channel_id or (uuid.UUID(ctx.payload["channel_id"]) if ctx.payload.get("channel_id") else None)
    if channel_id is None:
        raise JobError("bad_payload", "channel.list 需要 channel_id", retryable=False)
    with session_scope() as s:
        ch = s.get(Channel, channel_id)
        if ch is None:
            raise JobError("not_found", "頻道不存在", retryable=False)
        pcid = ch.platform_channel_id
        ch.list_status = "listing"
        first_listing = ch.last_listed_at is None
    if not yt.CHANNEL_ID_RE.match(pcid or ""):
        raise JobError("bad_channel", f"頻道 ID 尚未解析（{pcid}）；請重新以網址加入", retryable=False)
    tabs = tuple(t for t in (ctx.payload.get("tabs") or yt.LISTED_TABS) if t in yt.LISTED_TABS)
    limit = ctx.payload.get("limit")
    settings = youtube_settings()
    throttle(ctx, "request", float(settings["sleep_between_requests_sec"]))
    ctx.progress(0.05, "listing", f"列出 {', '.join(tabs)}", force=True)

    done_tabs: list[str] = []

    def on_tab(tab: str, n: int) -> None:
        done_tabs.append(tab)
        ctx.progress(0.1 + 0.7 * len(done_tabs) / max(1, len(tabs)), "listing", f"{tab}: {n} 支")

    with fetch_options(settings) as fo:
        try:
            res = yt.list_channel(pcid, fo, tabs=tabs, limit_per_tab=limit, progress=on_tab)
        except yt.YouTubeError as e:
            with session_scope() as s:
                s.get(Channel, channel_id).list_status = "failed"
            raise _job_error(e, _cookies_used(fo)) from e
    with session_scope() as s:
        ch = s.get(Channel, channel_id)
        if res["channel"]:
            upsert_channel(s, res["channel"], ch)
        rows, new = upsert_listed_media(s, res["entries"], ch)
        now = utcnow()
        if not first_listing:
            # a re-listing (scheduled or manual refresh) found these: mark them new (MediaSummary.is_new)
            for m in new:
                m.discovered_at = now
        if not limit:
            ch.video_count = len(rows)
        ch.list_status = "listed"
        ch.last_listed_at = now
        ch.metadata_ = {**(ch.metadata_ or {}), "listed_tabs": res["tabs"]}
        pending = s.scalar(select(Media.id).where(Media.channel_id == channel_id, Media.detailed_at.is_(None),
                                                  Media.status == "listed").limit(1)) is not None
        detail_queued = False
        if ctx.payload.get("detail", True) and pending and not _active_detail_sweep(s, channel_id):
            enqueue(s, "media.detail", channel_id=channel_id, payload={"batch_size": DETAIL_BATCH},
                    parent_id=ctx.job_id, created_by=ctx.created_by)
            detail_queued = True
        result = {"tabs": res["tabs"], "total": len(rows), "new": len(new), "detail_queued": detail_queued,
                  "first_listing": first_listing, "scheduled": bool(ctx.payload.get("scheduled")),
                  "warnings": res.get("warnings", [])[:10]}
    ctx.progress(1.0, "done", f"共 {len(rows)} 支（新增 {len(new)}）")
    return result


# ───────────────────────────── playlist.list ─────────────────────────────

def _playlist_row(s, ref: str | None, ctx: JobContext) -> Playlist:
    if not ref:
        raise JobError("bad_payload", "playlist.list 需要 playlist_id", retryable=False)
    p = None
    try:
        p = s.get(Playlist, uuid.UUID(str(ref)))
    except ValueError:
        pass
    if p is None:
        p = s.scalar(select(Playlist).where(Playlist.platform == "youtube", Playlist.platform_playlist_id == ref))
    if p is None and yt.PLAYLIST_ID_RE.match(str(ref)):
        p = Playlist(platform="youtube", platform_playlist_id=str(ref), url=yt.playlist_url(str(ref)))
        s.add(p)
        s.flush()
    if p is None:
        raise JobError("not_found", "播放清單不存在", retryable=False)
    return p


@handler("playlist.list")
def playlist_list(ctx: JobContext) -> dict:
    with session_scope() as s:
        p = _playlist_row(s, ctx.payload.get("playlist_id"), ctx)
        p.list_status = "listing"
        playlist_row_id, ppid = p.id, p.platform_playlist_id
    settings = youtube_settings()
    throttle(ctx, "request", float(settings["sleep_between_requests_sec"]))
    ctx.progress(0.05, "listing", "列出播放清單", force=True)
    with fetch_options(settings) as fo:
        try:
            res = yt.list_playlist(ppid, fo, limit=ctx.payload.get("limit"))
        except yt.YouTubeError as e:
            with session_scope() as s:
                s.get(Playlist, playlist_row_id).list_status = "failed"
            raise _job_error(e, _cookies_used(fo)) from e
    meta = res["playlist"]
    with session_scope() as s:
        p = s.get(Playlist, playlist_row_id)
        p.title = meta["title"] or p.title
        p.description = meta["description"] or p.description
        p.url = meta["url"]
        p.metadata_ = {**(p.metadata_ or {}), **meta["metadata"]}
        owner = _minimal_channel(s, meta.get("channel_platform_id"), meta.get("channel_title"),
                                 meta.get("channel_handle"), created_by=ctx.created_by, parent_id=ctx.job_id,
                                 source={"playlist_id": str(p.id)})
        if owner is not None:
            p.channel_id = owner.id
        rows, new = upsert_listed_media(s, res["entries"], None)
        s.execute(delete(PlaylistItem).where(PlaylistItem.playlist_id == p.id))
        seen = set()
        for e, m in zip(res["entries"], rows):
            if m.id in seen:
                continue
            seen.add(m.id)
            s.add(PlaylistItem(playlist_id=p.id, media_id=m.id, position=e.get("position", 0)))
        p.list_status = "listed"
        p.last_listed_at = utcnow()
        todo = [str(m.id) for m in rows if m.detailed_at is None and m.status == "listed"]
        detail_queued = False
        if ctx.payload.get("detail", True) and todo:
            enqueue(s, "media.detail", channel_id=p.channel_id,
                    payload={"media_ids": todo, "batch_size": DETAIL_BATCH, "playlist_id": str(p.id)},
                    parent_id=ctx.job_id, created_by=ctx.created_by)
            detail_queued = True
        result = {"playlist_id": str(p.id), "total": len(rows), "new": len(new), "detail_queued": detail_queued}
    ctx.progress(1.0, "done", f"共 {len(rows)} 支（新增 {len(new)}）")
    return result


# ───────────────────────────── media.detail ─────────────────────────────

def _detail_one(ctx: JobContext, media_id: uuid.UUID, fo: yt.FetchOptions) -> dict:
    with session_scope() as s:
        m = s.get(Media, media_id)
        if m is None:
            raise JobError("not_found", "影片不存在", retryable=False)
        if m.platform != "youtube":
            raise JobError("unsupported_platform", f"不支援的平台 {m.platform}", retryable=False)
        vid = m.platform_media_id
    try:
        info, warnings = yt.fetch_detail(vid, fo)
    except yt.YouTubeError as e:
        raise _job_error(e, _cookies_used(fo)) from e
    d = yt.summarize_detail(info)
    with session_scope() as s:
        m = s.get(Media, media_id)
        apply_detail(s, m, d, ctx)
        store_info_json(s, m, info)
        if m.status in ("listed", "failed", "blocked"):
            m.status = "detailed"
            m.failure_code = m.failure_message = None
        return {"media_id": str(media_id), "video_id": vid, "manual": d["manual_caption_langs"],
                "auto": d["auto_caption_langs"], "license": d["license"], "duration": d["duration_sec"],
                **({"warnings": warnings[:5]} if warnings else {})}


@handler("media.detail")
def media_detail(ctx: JobContext) -> dict:
    settings = youtube_settings()
    gap = float(settings["sleep_between_requests_sec"])
    if ctx.media_id is not None:
        throttle(ctx, "request", gap)
        ctx.progress(0.1, "detail", force=True)
        with fetch_options(settings) as fo:
            return _detail_one(ctx, ctx.media_id, fo)

    # batch / channel sweep
    batch_size = int(ctx.payload.get("batch_size") or DETAIL_BATCH)
    ids: list[str] = list(ctx.payload.get("media_ids") or [])
    sweep = not ids
    if sweep:
        if ctx.channel_id is None:
            raise JobError("bad_payload", "media.detail 需要 media_id、media_ids 或 channel_id", retryable=False)
        with session_scope() as s:
            ids = [str(x) for x in s.scalars(
                select(Media.id).where(Media.channel_id == ctx.channel_id, Media.detailed_at.is_(None),
                                       Media.status == "listed")
                .order_by(Media.published_at.desc().nulls_last(), Media.created_at).limit(batch_size))]
    batch, rest = ids[:batch_size], ids[batch_size:]
    done, failed, results = 0, [], []
    with fetch_options(settings) as fo:
        for i, mid in enumerate(batch):
            throttle(ctx, "request", gap)
            ctx.progress(i / max(1, len(batch)), "detail", f"{i + 1}/{len(batch)}" +
                         (f"（剩 {len(rest)}）" if rest else ""), force=True)
            try:
                r = _detail_one(ctx, uuid.UUID(mid), fo)
                results.append({k: r[k] for k in ("video_id", "manual", "auto")})
                done += 1
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                if isinstance(e, JobError) and e.code in ("bot_check", "rate_limited"):
                    raise  # stop the whole sweep; re-running it picks up where we left
                je = e if isinstance(e, JobError) else JobError("exception", f"{type(e).__name__}: {e}")
                log.warning("detail %s failed: %s %s", mid, je.code, je.message[:200])
                failed.append({"media_id": mid, "code": je.code})
                with session_scope() as s:
                    m = s.get(Media, uuid.UUID(mid))
                    if m is not None:  # status leaves "listed", so the sweep does not loop on it
                        m.status = "blocked" if je.blocked else "failed"
                        m.failure_code, m.failure_message = je.code, je.message[:2000]
    more = bool(rest)
    if sweep and done + len(failed) > 0:
        with session_scope() as s:
            more = s.scalar(select(Media.id).where(Media.channel_id == ctx.channel_id, Media.detailed_at.is_(None),
                                                   Media.status == "listed").limit(1)) is not None
    if more:
        payload = {k: v for k, v in ctx.payload.items() if k != "media_ids"}
        if rest:
            payload["media_ids"] = rest
        ctx.then("media.detail", payload=payload, channel_id=ctx.channel_id)
    return {"detailed": done, "failed": failed, "remaining": len(rest) if not sweep else None, "more": more,
            "items": results}


# ───────────────────────────── media.download ─────────────────────────────

@handler("media.download")
def media_download(ctx: JobContext) -> dict:
    if ctx.media_id is None:
        raise JobError("bad_payload", "media.download 需要 media_id", retryable=False)
    caption_langs = [str(x) for x in (ctx.payload.get("caption_langs") or []) if x] or None
    include_auto = bool(ctx.payload.get("include_auto", False))
    require_captions = not bool(ctx.payload.get("allow_no_captions", False))
    with session_scope() as s:
        m = s.get(Media, ctx.media_id)
        if m is None:
            raise JobError("not_found", "影片不存在", retryable=False)
        if m.platform != "youtube":
            raise JobError("unsupported_platform", f"不支援的平台 {m.platform}", retryable=False)
        vid = m.platform_media_id
        m.status, m.stage, m.failure_code, m.failure_message = "downloading", "download", None, None
    settings = youtube_settings()

    cancelled = {"flag": False}

    def progress(frac: float, phase: str) -> None:
        try:
            ctx.progress(0.1 + 0.8 * frac, phase, f"下載音訊 {frac * 100:.0f}%")
            _refresh_slot(ctx)
        except Cancelled:
            cancelled["flag"] = True

    import yt_dlp

    try:
        return _download(ctx, vid, settings, caption_langs, include_auto, require_captions, progress, cancelled,
                         yt_dlp)
    except Cancelled:
        with session_scope() as s:
            m = s.get(Media, ctx.media_id)
            if m is not None and m.status == "downloading":
                m.status, m.stage = ("detailed" if m.detailed_at else "listed"), None
        raise


def _download(ctx, vid, settings, caption_langs, include_auto, require_captions, progress, cancelled, yt_dlp):
    with download_slot(ctx, settings.get("max_concurrent_downloads", 1)):
        throttle(ctx, "download", float(settings.get("sleep_between_downloads_sec") or 0))
        ctx.progress(0.05, "download", "取得影片資訊", force=True)
        with fetch_options(settings) as fo, yt.new_tempdir(get_config().work_dir) as td:
            try:
                res = yt.download(vid, Path(td), fo, caption_langs=caption_langs, include_auto=include_auto,
                                  require_captions=require_captions, progress=progress,
                                  should_cancel=lambda: cancelled["flag"])
            except yt_dlp.utils.DownloadCancelled as e:
                raise Cancelled() from e
            except yt.YouTubeError as e:
                raise _job_error(e, _cookies_used(fo)) from e
            if cancelled["flag"]:
                raise Cancelled()
            ctx.progress(0.92, "store", "上傳音訊與字幕", force=True)
            return _store_download(ctx, res)


_TRADITIONAL_SCRIPTS = ("traditional", "neutral")


def repick_primary_by_script(tracks: list[dict]) -> None:
    """Content detection for language tags that do not say which script they use (`zh`, `cmn`, …): when the
    primary track turned out to be Simplified while another downloaded track of the same kind and the same
    preference tier is Traditional, that one becomes primary (Simplified would only be converted)."""
    cur = next((t for t in tracks if t.get("primary")), None)
    if cur is None or cur.get("script") != "simplified" or cur.get("rank") is None:
        return
    for t in tracks:
        if t is not cur and t["kind"] == cur["kind"] and t.get("rank") == cur["rank"] and \
                t.get("script") in _TRADITIONAL_SCRIPTS:
            cur["primary"], t["primary"] = False, True
            return


def _store_download(ctx: JobContext, res: yt.DownloadResult) -> dict:
    info = res.info
    ext = res.audio_path.suffix.lstrip(".").lower() or "bin"
    d = yt.summarize_detail(info)
    with session_scope() as s:
        m = s.get(Media, ctx.media_id)
        apply_detail(s, m, d, ctx)
        store_info_json(s, m, info)
        key = storage.media_key(m.id, f"original.{ext}")
        audio = storage.store_asset(s, res.audio_path, key, "original_audio", media_id=m.id,
                                    content_type=yt.AUDIO_CONTENT_TYPES.get(ext, "application/octet-stream"),
                                    params={"format": res.audio_format, "yt_dlp": _ytdlp_version(),
                                            "reencoded": False})
        # a re-download with a different container leaves a stale original.<old ext>; its object is deleted
        # only after this transaction commits (an interrupted run must not leave a row without its object)
        stale_keys = []
        for old in s.scalars(select(Asset).where(Asset.media_id == m.id, Asset.kind == "original_audio",
                                                 Asset.id != audio.id)):
            stale_keys.append(old.key)
            s.delete(old)

        tracks_out = []
        conversions = []
        text_cfg = textconv.text_settings(s)
        for sub in res.subtitles:
            lang = str(sub["language"])[:16]
            fmt = sub["format"]
            skey = storage.media_key(m.id, f"subtitles/{lang}.{sub['kind']}.{fmt}")
            data = Path(sub["path"]).read_bytes()
            cues = subs_mod.parse_subtitle(data, fmt, auto=sub["kind"] == "auto", duration_sec=d["duration_sec"])
            # Simplified → Traditional (Taiwan): Cue.text converted, raw_text and the stored file stay original
            s2t = textconv.convert_parsed_cues(cues, lang, text_cfg)
            stats = subs_mod.summary(cues)
            asset = storage.store_bytes_asset(s, data, skey, "subtitle", media_id=m.id,
                                              content_type=yt.SUBTITLE_CONTENT_TYPES.get(fmt, "text/plain"),
                                              params={"language": lang, "kind": sub["kind"], "yt_key": sub["key"],
                                                      "primary": sub["primary"], **stats, "s2t": s2t})
            tr = s.scalar(select(Track).where(Track.media_id == m.id, Track.language == lang,
                                              Track.kind == sub["kind"]))
            if tr is None:
                tr = Track(media_id=m.id, language=lang, kind=sub["kind"], format=fmt)
                s.add(tr)
                s.flush()
            else:
                s.execute(delete(Cue).where(Cue.track_id == tr.id))
            tr.format, tr.asset_id = fmt, asset.id
            s.add_all([Cue(track_id=tr.id, idx=c.idx, start_sec=c.start_sec, end_sec=c.end_sec,
                           raw_text=c.raw_text, text=c.text, flags=c.flags) for c in cues])
            tracks_out.append({"track_id": str(tr.id), "language": lang, "kind": sub["kind"], "format": fmt,
                               "primary": sub["primary"], "cues": stats["cues"], "usable": stats["usable"],
                               "s2t_converted": bool(s2t.get("converted")), "script": s2t.get("script"),
                               "rank": sub.get("rank")})
            conversions.append({"track_id": str(tr.id), "language": lang, "kind": sub["kind"], **s2t})
        repick_primary_by_script(tracks_out)
        primary = next((t for t in tracks_out if t["primary"]), None)
        m.metadata_ = {**(m.metadata_ or {}), "text_conversion": textconv.media_summary(conversions), "download": {
            "downloaded_at": utcnow().isoformat(), "audio": res.audio_format, "tracks": tracks_out,
            "primary_track": {"language": primary["language"], "kind": primary["kind"],
                              "track_id": primary["track_id"]} if primary else None,
            "subtitle_errors": res.subtitle_errors}}
        m.status, m.stage = "downloaded", None
        media_id = m.id
        result = {"audio_key": key, "audio_bytes": audio.bytes, "audio": res.audio_format, "tracks": tracks_out,
                  "subtitle_errors": res.subtitle_errors, **({"warnings": res.warnings[:5]} if res.warnings else {})}
    for k in stale_keys:
        try:
            storage.delete(k)
        except Exception:  # noqa: BLE001
            log.warning("could not delete stale %s", k)
    ctx.then("media.normalize", media_id=media_id)
    return result
