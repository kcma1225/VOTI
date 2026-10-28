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
  downloads are not stuck behind thousands of queued detail jobs in the FIFO detail queue.
  payload {"force": true, "verify": true} (caption re-check, POST /media/{id}/recheck-captions and
  /channels/{id}/recheck-captions): re-fetch metadata and re-verify captions even when detailed before.
  "verify" overrides settings.youtube.verify_captions. A re-detail never moves downloaded/processed media:
  status only changes for listed / failed / blocked → detailed and skipped(empty_captions) → detailed.

YouTube Data API fast pass (media.api_detail, voti.sources.ytapi; scan queue, no yt-dlp throttle)
  When an API key is usable, listings (channel.list incl. scheduled refreshes, playlist.list) and a resolved
  single video queue media.api_detail instead of the yt-dlp sweep. One videos.list call fills ≤ 50 media
  (metadata, exact date, license, counts, contentDetails.caption):
    caption false → has_manual_captions = False (has_auto_captions stays null: the API does not say), detailed
                    (`metadata.detail_source = "ytapi"`), no yt-dlp detail at all (queued single-media detail
                    jobs for it are cancelled; batch / sweep jobs skip it);
    caption true  → has_manual_captions = True, manual_caption_langs [] until the yt-dlp media.detail (still
                    queued per channel, normal pace) lists the tracks, auto captions, members-only, verification;
    missing       → failed / `unavailable` like the yt-dlp path.
  While a key is usable the yt-dlp channel sweep only takes media the API has seen (api_checked_at); without a
  usable key (none configured, all over quota / invalid) everything falls back to the yt-dlp path unchanged.

Caption verification (settings.youtube.verify_captions, default on)
  YouTube metadata sometimes lists an uploader caption track that is empty or 404s. media.detail fetches the
  preferred manual track (yt.verify_captions: caption_rank order, next track while empty, ≤3 fetches,
  `sleep_subtitles_sec` apart) and stores Media.metadata["caption_check"]. has_manual_captions /
  manual_caption_langs / captions_empty follow yt.caption_state. media.download classifies every fetched track
  again: empty ones are dropped; when nothing usable is left it stops before the audio download with
  failure_code `empty_captions` (media `skipped`).
"""
from __future__ import annotations

import logging
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import delete, func, select, update
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
from . import ytapi

log = logging.getLogger("voti.sources")

COOKIES_KEY = "system/cookies.txt"
YT_DEFAULTS = {"rate_limit_kbps": 0, "sleep_between_downloads_sec": 5, "max_concurrent_downloads": 2,
               "cookies_enabled": False,
               # re-list every channel this often to find new videos (voti.scheduler); 0 disables
               "auto_refresh_hours": 24,
               # optional (not in the admin form yet): spacing between listing/detail job starts
               "sleep_between_requests_sec": 2.0,
               # media.detail fetches the preferred manual caption track (≤3 tracks) to confirm it is not empty
               "verify_captions": True}
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
                mark_if_members_only(m)
        elif e["kind"] == "short" and m.kind == "video":
            m.kind = "short"
        if e.get("view_count") is not None:
            m.view_count = e["view_count"]
        rows.append(m)
    s.flush()
    return rows, new


def mark_if_members_only(m: Media) -> bool:
    """Members-only videos are unsupported: mark them skipped (never downloaded) as soon as we know."""
    if not yt.is_members_only(m.availability):
        return False
    if m.status in ("listed", "detailed", "failed", "blocked", "skipped"):
        m.status, m.failure_code, m.failure_message = "skipped", "members_only", yt.MEMBERS_ONLY_MESSAGE
    return True


def apply_caption_state(m: Media) -> dict:
    """has_manual_captions / manual_caption_langs / captions_empty from the listed manual tracks
    (metadata.caption_tracks) and the stored verification (metadata.caption_check)."""
    meta = m.metadata_ or {}
    manual = list(((meta.get("caption_tracks") or {}).get("manual") or {}))
    st = yt.caption_state(manual, meta.get("caption_check"))
    m.has_manual_captions = st["has_manual_captions"]
    m.manual_caption_langs = [str(x)[:16] for x in st["manual_caption_langs"]]
    m.captions_empty = st["captions_empty"]
    return st


def apply_detail(s, m: Media, d: dict, ctx: JobContext | None = None, caption_check: dict | None = None) -> None:
    """Copy summarize_detail() values. caption_check: a fresh verification (replaces the stored one); without
    it the stored check still applies to the tracks it covers."""
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
    m.metadata_ = {**(m.metadata_ or {}), **(d.get("metadata") or {}), "detail_source": "yt-dlp"}
    if caption_check is not None:
        m.metadata_ = {**m.metadata_, "caption_check": caption_check}
    apply_caption_state(m)
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
    """A queued/running channel sweep (not an explicit media_ids batch such as a playlist or a caption re-check,
    which never picks up newly listed media)."""
    return s.scalar(select(Job.id).where(Job.kind == "media.detail", Job.media_id.is_(None),
                                         Job.channel_id == channel_id, ~Job.payload.has_key("media_ids"),
                                         Job.status.in_(("queued", "running"))).limit(1)) is not None


def _detail_pending(channel_id: uuid.UUID | None, api_only: bool) -> list:
    """WHERE clauses of the yt-dlp channel sweep. api_only (a YouTube API key is usable): only media the API pass
    has seen — the others get their caption info from the API first (it decides whether yt-dlp is needed)."""
    c = [Media.channel_id == channel_id, Media.detailed_at.is_(None), Media.status == "listed"]
    if api_only:
        c.append(Media.api_checked_at.isnot(None))
    return c


def api_pending() -> list:
    """WHERE clauses: media the YouTube API pass has not seen yet and that still need caption info."""
    return [Media.platform == "youtube", Media.detailed_at.is_(None), Media.api_checked_at.is_(None),
            Media.status.in_(("listed", "failed", "blocked")),
            func.coalesce(Media.failure_code, "") != "members_only"]


def active_api_job(s, channel_id: uuid.UUID | None = None) -> Job | None:  # noqa: ANN001
    """The queued/running media.api_detail sweep of a channel (no media_ids), or with channel_id None the global
    backfill. A channel's own sweep is queued even while the backfill runs (a backfill run that already
    collected its media would miss a listing that finishes meanwhile); both skip media the other did."""
    q = select(Job).where(Job.kind == "media.api_detail", Job.status.in_(("queued", "running")),
                          ~Job.payload.has_key("media_ids"))
    q = q.where(Job.channel_id.is_(None) if channel_id is None else Job.channel_id == channel_id)
    return s.scalars(q.order_by(Job.created_at).limit(1)).first()


def queue_caption_info(s, channel_id: uuid.UUID, *, parent_id: uuid.UUID | None = None,  # noqa: ANN001
                       created_by: uuid.UUID | None = None, use_api: bool | None = None) -> str | None:
    """After a listing: get caption info for the channel's undetailed media. With a usable YouTube API key the
    API pass (media.api_detail channel sweep) for media it has not seen, plus the yt-dlp sweep for those it found
    uploader captions on; otherwise the yt-dlp sweep for every undetailed media. → "api" | "detail" | None."""
    if use_api is None:
        use_api = ytapi.has_usable_key(s)
    queued = None
    if use_api and s.scalar(select(Media.id).where(Media.channel_id == channel_id, *api_pending()).limit(1)):
        if active_api_job(s, channel_id) is None:
            enqueue(s, "media.api_detail", channel_id=channel_id, parent_id=parent_id, created_by=created_by,
                    payload={})
            queued = "api"
    pending = s.scalar(select(Media.id).where(*_detail_pending(channel_id, use_api)).limit(1)) is not None
    if pending and not _active_detail_sweep(s, channel_id):
        enqueue(s, "media.detail", channel_id=channel_id, payload={"batch_size": DETAIL_BATCH},
                parent_id=parent_id, created_by=created_by)
        queued = queued or "detail"
    return queued


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
        if ytapi.has_usable_key():
            ctx.then("media.api_detail", payload={"media_ids": [str(media_id)]})
        else:
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
        queued = queue_caption_info(s, channel_id, parent_id=ctx.job_id, created_by=ctx.created_by) \
            if ctx.payload.get("detail", True) else None
        result = {"tabs": res["tabs"], "total": len(rows), "new": len(new), "detail_queued": queued is not None,
                  "caption_info": queued,
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
            if ytapi.has_usable_key(s):
                todo_api = [str(m.id) for m in rows if m.detailed_at is None and m.api_checked_at is None
                            and m.status == "listed"]
                if todo_api:
                    enqueue(s, "media.api_detail", channel_id=p.channel_id,
                            payload={"media_ids": todo_api, "playlist_id": str(p.id)},
                            parent_id=ctx.job_id, created_by=ctx.created_by)
                todo = [str(m.id) for m in rows if m.detailed_at is None and m.api_checked_at is not None
                        and m.status == "listed"]
            if todo:
                enqueue(s, "media.detail", channel_id=p.channel_id,
                        payload={"media_ids": todo, "batch_size": DETAIL_BATCH, "playlist_id": str(p.id)},
                        parent_id=ctx.job_id, created_by=ctx.created_by)
            detail_queued = True
        result = {"playlist_id": str(p.id), "total": len(rows), "new": len(new), "detail_queued": detail_queued}
    ctx.progress(1.0, "done", f"共 {len(rows)} 支（新增 {len(new)}）")
    return result


# ───────────────────────────── media.detail ─────────────────────────────

def _verify_enabled(ctx: JobContext, settings: dict) -> bool:
    v = ctx.payload.get("verify")
    return bool(settings.get("verify_captions", True)) if v is None else bool(v)


def _detail_one(ctx: JobContext, media_id: uuid.UUID, fo: yt.FetchOptions, verify: bool = False) -> dict:
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
    check = None
    if verify and d["manual_caption_langs"] and not yt.is_members_only(d.get("availability")):
        ctx.progress(phase="caption_check", message="檢查字幕內容")
        try:
            check = yt.verify_captions(info, fo)
        except yt.YouTubeError as e:  # bot_check / rate_limited only (other failures are recorded as `error`)
            raise _job_error(e, _cookies_used(fo)) from e
    with session_scope() as s:
        m = s.get(Media, media_id)
        apply_detail(s, m, d, ctx, caption_check=check)
        store_info_json(s, m, info)
        if not mark_if_members_only(m):
            if m.status in ("listed", "failed", "blocked") or (
                    m.status == "skipped" and m.failure_code == "empty_captions" and not m.captions_empty):
                m.status = "detailed"
                m.failure_code = m.failure_message = None
        return {"media_id": str(media_id), "video_id": vid, "manual": list(m.manual_caption_langs or []),
                "listed_manual": d["manual_caption_langs"], "auto": d["auto_caption_langs"],
                "captions_empty": bool(m.captions_empty), "license": d["license"], "duration": d["duration_sec"],
                **({"caption_check": {k: v["status"] for k, v in check["tracks"].items()}} if check else {}),
                **({"warnings": warnings[:5]} if warnings else {})}


@handler("media.detail")
def media_detail(ctx: JobContext) -> dict:
    settings = youtube_settings()
    gap = float(settings["sleep_between_requests_sec"])
    if ctx.media_id is not None:
        throttle(ctx, "request", gap)
        ctx.progress(0.1, "detail", force=True)
        with fetch_options(settings) as fo:
            return _detail_one(ctx, ctx.media_id, fo, verify=_verify_enabled(ctx, settings))

    # batch / channel sweep
    batch_size = int(ctx.payload.get("batch_size") or DETAIL_BATCH)
    ids: list[str] = list(ctx.payload.get("media_ids") or [])
    sweep = not ids
    force = bool(ctx.payload.get("force"))
    api_only = False
    if sweep:
        if ctx.channel_id is None:
            raise JobError("bad_payload", "media.detail 需要 media_id、media_ids 或 channel_id", retryable=False)
        api_only = ytapi.has_usable_key()
        with session_scope() as s:
            ids = [str(x) for x in s.scalars(
                select(Media.id).where(*_detail_pending(ctx.channel_id, api_only))
                .order_by(Media.published_at.desc().nulls_last(), Media.created_at).limit(batch_size))]
    batch, rest = ids[:batch_size], ids[batch_size:]
    done, failed, results, skipped = 0, [], [], 0
    with fetch_options(settings) as fo:
        for i, mid in enumerate(batch):
            if not force and _known_from_api(uuid.UUID(mid)):
                skipped += 1  # the YouTube API found no uploader captions: nothing left for yt-dlp to tell
                continue
            throttle(ctx, "request", gap)
            ctx.progress(i / max(1, len(batch)), "detail", f"{i + 1}/{len(batch)}" +
                         (f"（剩 {len(rest)}）" if rest else ""), force=True)
            try:
                r = _detail_one(ctx, uuid.UUID(mid), fo, verify=_verify_enabled(ctx, settings))
                results.append({k: r[k] for k in ("video_id", "manual", "auto", "captions_empty")})
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
                    # status leaves "listed", so the sweep does not loop on it; a re-detail (caption re-check)
                    # never pushes downloaded / processed / skipped media backwards
                    if m is not None and m.status in ("listed", "detailed"):
                        m.status = "blocked" if je.blocked else "failed"
                        m.failure_code, m.failure_message = je.code, je.message[:2000]
    more = bool(rest)
    if sweep and done + len(failed) > 0:
        with session_scope() as s:
            more = s.scalar(select(Media.id).where(*_detail_pending(ctx.channel_id, api_only)).limit(1)) is not None
    if sweep and api_only and not more:
        with session_scope() as s:  # media the API pass has not reached yet: make sure one will
            if s.scalar(select(Media.id).where(Media.channel_id == ctx.channel_id, *api_pending()).limit(1)) \
                    and active_api_job(s, ctx.channel_id) is None:
                enqueue(s, "media.api_detail", channel_id=ctx.channel_id, payload={}, parent_id=ctx.job_id,
                        created_by=ctx.created_by)
    if more:
        payload = {k: v for k, v in ctx.payload.items() if k != "media_ids"}
        if rest:
            payload["media_ids"] = rest
        ctx.then("media.detail", payload=payload, channel_id=ctx.channel_id)
    return {"detailed": done, "failed": failed, "remaining": len(rest) if not sweep else None, "more": more,
            "items": results, **({"skipped_api": skipped} if skipped else {})}


def _known_from_api(media_id: uuid.UUID) -> bool:
    """Detailed by the YouTube API pass (no uploader captions): a batch / sweep has nothing to add."""
    with session_scope() as s:
        m = s.get(Media, media_id)
        return m is not None and m.detailed_at is not None and \
            (m.metadata_ or {}).get("detail_source") == "ytapi"


# ───────────────────────────── media.api_detail ─────────────────────────────

API_CALLS_PER_RUN = 10   # ≤ 500 videos per job run, then the job re-queues itself (round-robin on the scan queue)
REPLACED_MESSAGE = "已由 YouTube Data API 取得資訊（沒有上傳者字幕），不需要 yt-dlp 檢查"


def apply_api_item(s, m: Media, it: dict, ctx: JobContext | None = None) -> str:  # noqa: ANN001
    """Copy one videos.list item onto a media. → "no_captions" | "captions" | "pending" (live / upcoming /
    still processing: caption info not final, left to yt-dlp)."""
    d = ytapi.summarize_item(it)
    now = utcnow()
    for f in ("title", "thumbnail_url"):
        if d[f]:
            setattr(m, f, d[f])
    m.description = d["description"]
    for f in ("duration_sec", "published_at", "view_count", "like_count", "comment_count", "age_limit"):
        if d[f] is not None:
            setattr(m, f, d[f])
    m.tags, m.categories = d["tags"], d["categories"]
    m.license, m.language, m.availability, m.was_live = d["license"], d["language"], d["availability"], d["was_live"]
    if d["was_live"] and m.kind == "video":
        m.kind = "live"
    meta = dict(m.metadata_ or {})
    meta.pop("published_approximate", None)
    meta.update(published_approximate=False, live_status=d["live_status"],
                ytapi={"fetched_at": now.isoformat(), "caption": d["caption"], **d["extras"], "item": it})
    if d["channel_title"]:
        meta.setdefault("channel", d["channel_title"])
    m.metadata_ = meta
    if d["channel_platform_id"]:
        ch = _minimal_channel(s, d["channel_platform_id"], d["channel_title"],
                              created_by=ctx.created_by if ctx else None, parent_id=ctx.job_id if ctx else None,
                              source={"media_id": str(m.id), "video_id": m.platform_media_id})
        if ch is not None and m.channel_id is None:
            m.channel_id = ch.id
    m.api_checked_at = now
    if d["live_status"] in ("is_live", "is_upcoming") or (d["upload_status"] or "processed") != "processed":
        return "pending"
    if d["caption"]:
        m.has_manual_captions = True   # languages follow from the yt-dlp media.detail (manual_caption_langs)
        m.captions_empty = False
        return "captions"
    m.has_manual_captions, m.manual_caption_langs, m.captions_empty = False, [], False
    # has_auto_captions stays unknown (null): the API does not report YouTube ASR; the download finds it
    m.metadata_ = {**m.metadata_, "detail_source": "ytapi"}
    m.detailed_at = now
    if not mark_if_members_only(m) and m.status in ("listed", "failed", "blocked"):
        m.status, m.failure_code, m.failure_message = "detailed", None, None
    return "no_captions"


def _api_unavailable(m: Media) -> None:
    now = utcnow()
    m.api_checked_at = now
    m.metadata_ = {**(m.metadata_ or {}), "ytapi": {"fetched_at": now.isoformat(), "missing": True}}
    if m.status in ("listed", "detailed", "failed", "blocked"):
        m.status, m.failure_code, m.failure_message = "failed", "unavailable", ytapi.UNAVAILABLE_MESSAGE


def cancel_replaced_detail_jobs(s, media_ids: list[uuid.UUID]) -> int:  # noqa: ANN001
    """Queued single-media yt-dlp media.detail jobs of media the API pass fully detailed are no longer needed
    (caption re-checks — payload force/verify — are kept)."""
    if not media_ids:
        return 0
    n = 0
    for j in s.scalars(select(Job).where(Job.kind == "media.detail", Job.status == "queued",
                                         Job.media_id.in_(media_ids))):
        p = j.payload or {}
        if p.get("force") or p.get("verify") or p.get("recheck"):
            continue
        j.status, j.finished_at, j.error_code, j.error_message = "cancelled", utcnow(), "replaced", REPLACED_MESSAGE
        n += 1
    return n


def _api_fallback(s, ctx: JobContext, ids: list[str] | None) -> None:  # noqa: ANN001
    """No usable key any more: the yt-dlp path takes over (unchanged)."""
    if ids:
        enqueue(s, "media.detail", channel_id=ctx.channel_id, parent_id=ctx.job_id, created_by=ctx.created_by,
                payload={"media_ids": ids, "batch_size": DETAIL_BATCH})
        return
    chs = [ctx.channel_id] if ctx.channel_id else list(s.scalars(
        select(Media.channel_id).where(Media.channel_id.isnot(None), Media.detailed_at.is_(None),
                                       Media.status == "listed").distinct()))
    for cid in chs:
        if not _active_detail_sweep(s, cid):
            enqueue(s, "media.detail", channel_id=cid, payload={"batch_size": DETAIL_BATCH}, parent_id=ctx.job_id,
                    created_by=ctx.created_by)


@handler("media.api_detail")
def media_api_detail(ctx: JobContext) -> dict:
    """YouTube Data API fast pass. payload {media_ids: [...]} (explicit: playlist, resolved video), channel_id
    without media_ids (the channel's media the API has not seen), or neither ({all: true}: every such media —
    the backfill). ≤ API_CALLS_PER_RUN calls of ≤ 50 videos per run; re-queues itself while more remain."""
    explicit: list[str] = [str(x) for x in ctx.payload.get("media_ids") or []]
    limit = ytapi.MAX_IDS * API_CALLS_PER_RUN
    with session_scope() as s:
        q = select(Media.id, Media.platform_media_id).where(*api_pending())
        if explicit:
            q = q.where(Media.id.in_([uuid.UUID(x) for x in explicit]))
        elif ctx.channel_id is not None:
            q = q.where(Media.channel_id == ctx.channel_id)
        rows = [(str(a), b) for a, b in s.execute(
            q.order_by(Media.published_at.desc().nulls_last(), Media.id).limit(limit + 1)).all()]
    batch, more = rows[:limit], len(rows) > limit
    counts = {"no_captions": 0, "captions": 0, "pending": 0, "unavailable": 0}
    caption_media: list[tuple[str, uuid.UUID | None]] = []
    replaced = 0
    calls = 0
    for i in range(0, len(batch), ytapi.MAX_IDS):
        chunk = batch[i:i + ytapi.MAX_IDS]
        ctx.progress(i / max(1, len(batch)), "ytapi", f"YouTube API {i + len(chunk)}/{len(batch)}" +
                     ("（還有更多）" if more else ""), force=True)
        try:
            items, _kid = ytapi.videos_list([vid for _, vid in chunk])
        except ytapi.NoUsableKey:
            with session_scope() as s:
                left = [mid for mid, _ in batch[i:]] if explicit else None
                _api_fallback(s, ctx, left)
            log.warning("media.api_detail: no usable YouTube API key; falling back to yt-dlp detail")
            return {**counts, "calls": calls, "fallback": True, "replaced": replaced}
        except ytapi.ApiUnavailable as e:
            if calls:  # keep what was done; the next run continues with the rest
                ctx.then("media.api_detail", payload=dict(ctx.payload), channel_id=ctx.channel_id)
                return {**counts, "calls": calls, "more": True, "error": str(e)[:300], "replaced": replaced}
            raise JobError("ytapi_unavailable", f"YouTube Data API 暫時無法使用：{e}", retryable=True) from None
        calls += 1
        done_ids: list[uuid.UUID] = []
        with session_scope() as s:
            for mid, vid in chunk:
                m = s.get(Media, uuid.UUID(mid))
                if m is None:
                    continue
                it = items.get(vid)
                if it is None:
                    _api_unavailable(m)
                    counts["unavailable"] += 1
                    continue
                r = apply_api_item(s, m, it, ctx)
                counts[r] += 1
                if r == "no_captions":
                    done_ids.append(m.id)
                elif m.status == "listed":
                    caption_media.append((mid, m.channel_id))
            s.flush()
            replaced += cancel_replaced_detail_jobs(s, done_ids)
    # uploader captions (or live / processing): the yt-dlp detail still gives languages, ASR, members-only, …
    with session_scope() as s:
        if explicit and len(caption_media) <= 5:
            for mid, cid in caption_media:
                enqueue(s, "media.detail", media_id=uuid.UUID(mid), channel_id=cid, parent_id=ctx.job_id,
                        created_by=ctx.created_by)
        else:
            for cid in sorted({c for _, c in caption_media if c is not None}, key=str):
                if not _active_detail_sweep(s, cid):
                    enqueue(s, "media.detail", channel_id=cid, payload={"batch_size": DETAIL_BATCH},
                            parent_id=ctx.job_id, created_by=ctx.created_by)
            orphans = [mid for mid, c in caption_media if c is None]
            if orphans:
                enqueue(s, "media.detail", payload={"media_ids": orphans, "batch_size": DETAIL_BATCH},
                        parent_id=ctx.job_id, created_by=ctx.created_by)
    if more:
        payload = dict(ctx.payload)
        if explicit:
            seen = {mid for mid, _ in batch}
            payload["media_ids"] = [x for x in explicit if x not in seen]
        ctx.then("media.api_detail", payload=payload, channel_id=ctx.channel_id)
    ctx.progress(1.0, "done", f"YouTube API：{len(batch)} 支（無上傳者字幕 {counts['no_captions']}、"
                              f"有字幕 {counts['captions']}、無法取得 {counts['unavailable']}）")
    return {**counts, "calls": calls, "more": more, "replaced": replaced}


# ───────────────────────────── media.download ─────────────────────────────

@handler("media.download")
def media_download(ctx: JobContext) -> dict:
    if ctx.media_id is None:
        raise JobError("bad_payload", "media.download 需要 media_id", retryable=False)
    caption_langs = [str(x) for x in (ctx.payload.get("caption_langs") or []) if x] or None
    include_auto = bool(ctx.payload.get("include_auto", False))
    require_captions = not bool(ctx.payload.get("allow_no_captions", False))
    video_only = bool(ctx.payload.get("video_only"))
    with session_scope() as s:
        m = s.get(Media, ctx.media_id)
        if m is None:
            raise JobError("not_found", "影片不存在", retryable=False)
        if m.platform != "youtube":
            raise JobError("unsupported_platform", f"不支援的平台 {m.platform}", retryable=False)
        vid = m.platform_media_id
        video = ocr_video_mode(s, ctx.payload)
        if not video_only:
            m.status, m.stage, m.failure_code, m.failure_message = "downloading", "download", None, None
    if video == "if_no_captions" or video == "always":
        require_captions = False  # burned-in subtitles are read by OCR when no CC track is usable
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
        if video_only:
            return _download_video_only(ctx, vid, settings, progress, cancelled, yt_dlp)
        return _download(ctx, vid, settings, caption_langs, include_auto, require_captions, progress, cancelled,
                         yt_dlp, video)
    except Cancelled:
        with session_scope() as s:
            m = s.get(Media, ctx.media_id)
            if m is not None and m.status == "downloading" and not video_only:
                m.status, m.stage = ("detailed" if m.detailed_at else "listed"), None
        raise


def ocr_video_mode(s, payload: dict) -> str | None:  # noqa: ANN001
    """Video download for hard-subtitle OCR: "always" (payload ocr: true), "if_no_captions" (payload ocr_auto,
    default settings.ocr.auto_when_no_captions), or None. Nothing when settings.ocr.enabled is false."""
    from ..pipeline.common import get_setting
    cfg = get_setting(s, "ocr")
    if not cfg.get("enabled", True) or payload.get("ocr") is False:
        return None
    if payload.get("ocr") is True:
        return "always"
    auto = payload.get("ocr_auto")
    if auto is None:
        auto = cfg.get("auto_when_no_captions", True)
    return "if_no_captions" if auto else None


def _store_ocr_video(s, m: Media, path: Path, fmt: dict, requested: str) -> Asset:  # noqa: ANN001
    ext = path.suffix.lstrip(".").lower() or "mp4"
    for old in s.scalars(select(Asset).where(Asset.media_id == m.id, Asset.kind == "ocr_video")):
        s.delete(old)
    s.flush()
    asset = storage.store_asset(s, path, storage.media_key(m.id, f"ocr/video.{ext}"), "ocr_video", media_id=m.id,
                                content_type=f"video/{'mp4' if ext == 'mp4' else ext}",
                                params={"format": fmt, "temporary": True, "requested": requested})
    prev = dict((m.metadata_ or {}).get("ocr") or {})
    prev.update(status="pending", requested=requested, video_bytes=asset.bytes, video_format=fmt,
                queued_at=utcnow().isoformat())
    m.metadata_ = {**(m.metadata_ or {}), "ocr": prev}
    return asset


def _download_video_only(ctx, vid, settings, progress, cancelled, yt_dlp):  # noqa: ANN001
    """POST /media/{id}/ocr after the OCR video was deleted: fetch only the video, then run media.ocr {rerun}."""
    with download_slot(ctx, settings.get("max_concurrent_downloads", 1)):
        throttle(ctx, "download", float(settings.get("sleep_between_downloads_sec") or 0))
        ctx.progress(0.05, "download", "取得影片資訊", force=True)
        with fetch_options(settings) as fo, yt.new_tempdir(get_config().work_dir) as td:
            try:
                res = yt.download(vid, Path(td), fo, progress=progress, should_cancel=lambda: cancelled["flag"],
                                  video_only=True)
            except yt_dlp.utils.DownloadCancelled as e:
                raise Cancelled() from e
            except yt.YouTubeError as e:
                raise _job_error(e, _cookies_used(fo)) from e
            if cancelled["flag"]:
                raise Cancelled()
            with session_scope() as s:
                m = s.get(Media, ctx.media_id)
                asset = _store_ocr_video(s, m, res.video_path, res.video_format, "rerun")
                out = {"video_key": asset.key, "video_bytes": asset.bytes, "video": res.video_format}
                channel_id = m.channel_id
    ctx.then("media.ocr", {"rerun": True}, media_id=ctx.media_id, channel_id=channel_id)
    return out


def _download(ctx, vid, settings, caption_langs, include_auto, require_captions, progress, cancelled, yt_dlp,
              video=None):
    with download_slot(ctx, settings.get("max_concurrent_downloads", 1)):
        throttle(ctx, "download", float(settings.get("sleep_between_downloads_sec") or 0))
        ctx.progress(0.05, "download", "取得影片資訊", force=True)
        with fetch_options(settings) as fo, yt.new_tempdir(get_config().work_dir) as td:
            try:
                res = yt.download(vid, Path(td), fo, caption_langs=caption_langs, include_auto=include_auto,
                                  require_captions=require_captions, progress=progress,
                                  should_cancel=lambda: cancelled["flag"], video=video)
            except yt_dlp.utils.DownloadCancelled as e:
                raise Cancelled() from e
            except yt.YouTubeError as e:
                if e.code == "empty_captions":
                    _mark_empty_captions(ctx.media_id, e.extra)
                raise _job_error(e, _cookies_used(fo)) from e
            if cancelled["flag"]:
                raise Cancelled()
            ctx.progress(0.92, "store", "上傳音訊與字幕", force=True)
            return _store_download(ctx, res)


def _mark_empty_captions(media_id: uuid.UUID, extra: dict) -> None:
    """Download found only empty caption tracks: record them (status skipped / failure_code empty_captions follow
    from the job failure, queue.SKIP_CODES)."""
    with session_scope() as s:
        m = s.get(Media, media_id)
        if m is None:
            return
        meta = dict(m.metadata_ or {})
        if extra.get("caption_tracks"):
            meta["caption_tracks"] = extra["caption_tracks"]
        meta["caption_check"] = yt.merge_caption_check(meta.get("caption_check"), extra.get("checked") or {},
                                                       "download")
        m.metadata_ = meta
        apply_caption_state(m)


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
        check = yt.merge_caption_check((m.metadata_ or {}).get("caption_check"), res.caption_check, "download") \
            if res.caption_check else None
        apply_detail(s, m, d, ctx, caption_check=check)
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
        if res.video_path is not None:  # hard-subtitle OCR follows in the pipeline (media.ocr, deletes the video)
            requested = "manual" if ctx.payload.get("ocr") is True else "auto_no_captions"
            v = _store_ocr_video(s, m, res.video_path, res.video_format, requested)
            result.update(video_key=v.key, video_bytes=v.bytes, video=res.video_format)
    for k in stale_keys:
        try:
            storage.delete(k)
        except Exception:  # noqa: BLE001
            log.warning("could not delete stale %s", k)
    ctx.then("media.normalize", media_id=media_id)
    return result
