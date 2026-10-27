"""/api/media: listing with filters + facets, detail, download (incl. select-all), retry/reprocess,
timeline, quality."""
from __future__ import annotations

import io
import logging
import math
import uuid
from datetime import datetime

import numpy as np
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, exists, func, or_, select, true
from sqlalchemy.orm import Session

from .. import storage
from ..auth import current_user
from ..db import get_session
from ..models import (Cue, DiarizationTurn, Job, Media, MediaQuality, PlaylistItem, RepeatedRegion, Segment,
                      SpeechRegion, Track, User)
from ..queue import PIPELINE, enqueue, requeue
from . import schemas as S
from .errors import ApiError
from .views import Paging, _media_base, get_or_404, job_out, job_outs, media_summaries, paging, segment_summaries

log = logging.getLogger("voti.api.media")
router = APIRouter(prefix="/media", tags=["media"])

MEDIA_SORTS = {
    "published_at": Media.published_at.asc().nulls_last(), "-published_at": Media.published_at.desc().nulls_last(),
    "duration": Media.duration_sec.asc().nulls_last(), "-duration": Media.duration_sec.desc().nulls_last(),
    "views": Media.view_count.asc().nulls_last(), "-views": Media.view_count.desc().nulls_last(),
    "title": Media.title.asc(), "-title": Media.title.desc(),
    "created_at": Media.created_at.asc(), "-created_at": Media.created_at.desc(),
}
CAPTION_CHOICES = ("manual", "auto", "any", "none")
TIMELINE_MAX_POINTS = 2000


class MediaFilter(BaseModel):
    """The single media filter used by GET /media and by POST /media/download select-all."""
    channel_id: uuid.UUID | None = None
    playlist_id: uuid.UUID | None = None
    q: str | None = None
    kind: list[str] = []
    status: list[str] = []
    captions: str | None = None
    include_auto: bool = False
    caption_lang: str | None = None
    license: str | None = None
    min_duration: float | None = None
    max_duration: float | None = None
    published_after: datetime | None = None
    published_before: datetime | None = None
    detailed: bool | None = None
    upsampled_suspect: bool | None = None
    sort: str | None = None

    @field_validator("kind", "status", mode="before")
    @classmethod
    def _csv(cls, v):  # noqa: ANN001, ANN206
        if v is None:
            return []
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator("captions")
    @classmethod
    def _captions(cls, v):  # noqa: ANN001, ANN206
        if v in (None, ""):
            return None
        if v not in CAPTION_CHOICES:
            raise ValueError(f"captions must be one of {CAPTION_CHOICES}")
        return v

    @field_validator("sort")
    @classmethod
    def _sort(cls, v):  # noqa: ANN001, ANN206
        if v in (None, ""):
            return None
        if v not in MEDIA_SORTS:
            raise ValueError(f"sort must be one of {tuple(MEDIA_SORTS)}")
        return v


def media_filter_query(
        channel_id: uuid.UUID | None = None, playlist_id: uuid.UUID | None = None, q: str | None = None,
        kind: str | None = Query(None, description="comma list: video,short,live"),
        status: str | None = Query(None, description="comma list"),
        captions: str | None = Query(None, description="manual|auto|any|none"), include_auto: bool = False,
        caption_lang: str | None = None, license: str | None = None,  # noqa: A002
        min_duration: float | None = None, max_duration: float | None = None,
        published_after: datetime | None = None, published_before: datetime | None = None,
        detailed: bool | None = None, upsampled_suspect: bool | None = None,
        sort: str | None = None) -> MediaFilter:
    try:
        return MediaFilter(channel_id=channel_id, playlist_id=playlist_id, q=q, kind=kind, status=status,
                           captions=captions, include_auto=include_auto, caption_lang=caption_lang, license=license,
                           min_duration=min_duration, max_duration=max_duration, published_after=published_after,
                           published_before=published_before, detailed=detailed,
                           upsampled_suspect=upsampled_suspect, sort=sort)
    except ValueError as e:
        raise ApiError(422, "invalid_filter", f"篩選條件不正確：{e}") from None


_manual = Media.has_manual_captions.is_(True)
_auto_only = and_(Media.has_manual_captions.is_(False), Media.has_auto_captions.is_(True))
_no_captions = and_(Media.has_manual_captions.is_(False), func.coalesce(Media.has_auto_captions, False).is_(False))
_unknown_captions = Media.has_manual_captions.is_(None)


def media_conditions(f: MediaFilter, skip: frozenset[str] | set[str] = frozenset()) -> list:
    """WHERE clauses for a MediaFilter. `skip` drops dimensions (used for facets)."""
    c: list = []
    if f.channel_id:
        c.append(Media.channel_id == f.channel_id)
    if f.playlist_id:
        c.append(exists().where(PlaylistItem.media_id == Media.id, PlaylistItem.playlist_id == f.playlist_id))
    if f.q:
        pat = f"%{f.q.strip()}%"
        c.append(or_(Media.title.ilike(pat), Media.description.ilike(pat)))
    if f.kind and "kind" not in skip:
        c.append(Media.kind.in_(f.kind))
    if f.status and "status" not in skip:
        c.append(Media.status.in_(f.status))
    if "captions" not in skip:
        if f.captions == "manual":
            c.append(_manual)
        elif f.captions == "auto":
            c.append(_auto_only)
        elif f.captions == "any":
            c.append(or_(_manual, Media.has_auto_captions.is_(True)))
        elif f.captions == "none":
            c.append(_no_captions)
        # auto-caption-only media are hidden by default
        if not f.include_auto and f.captions not in ("auto", "any"):
            c.append(~_auto_only)
        if f.caption_lang:
            lang = f.caption_lang
            if f.captions == "manual":
                c.append(Media.manual_caption_langs.any(lang))
            elif f.captions == "auto":
                c.append(Media.auto_caption_langs.any(lang))
            elif f.include_auto or f.captions == "any":
                c.append(or_(Media.manual_caption_langs.any(lang), Media.auto_caption_langs.any(lang)))
            else:
                c.append(Media.manual_caption_langs.any(lang))
    if f.license and "license" not in skip:
        c.append(Media.license == f.license)
    if f.min_duration is not None:
        c.append(Media.duration_sec >= f.min_duration)
    if f.max_duration is not None:
        c.append(Media.duration_sec <= f.max_duration)
    if f.published_after:
        c.append(Media.published_at >= f.published_after)
    if f.published_before:
        c.append(Media.published_at <= f.published_before)
    if f.detailed is True:
        c.append(Media.detailed_at.isnot(None))
    elif f.detailed is False:
        c.append(Media.detailed_at.is_(None))
    if f.upsampled_suspect is not None:
        suspect = exists().where(MediaQuality.media_id == Media.id, MediaQuality.upsampled_suspect.is_(True))
        c.append(suspect if f.upsampled_suspect else ~suspect)
    return c


def filtered_media(f: MediaFilter):  # noqa: ANN201
    """SELECT Media matching the filter (sorted)."""
    q = select(Media).where(*media_conditions(f))
    order = MEDIA_SORTS.get(f.sort or "-published_at")
    return q.order_by(order, Media.id)


def _where(conds: list):  # noqa: ANN202
    return and_(true(), *conds)


def media_facets(s: Session, f: MediaFilter) -> S.MediaFacets:
    def grouped(col, skip):  # noqa: ANN001, ANN202
        rows = s.execute(select(col, func.count()).where(_where(media_conditions(f, skip))).group_by(col)).all()
        return {str(k) if k is not None else "unknown": n for k, n in rows}

    cap_where = _where(media_conditions(f, {"captions"}))
    row = s.execute(select(func.count().filter(_manual), func.count().filter(_auto_only),
                           func.count().filter(_no_captions), func.count().filter(_unknown_captions))
                    .where(cap_where)).one()
    return S.MediaFacets(kind=grouped(Media.kind, {"kind"}),
                         captions={"manual": row[0], "auto_only": row[1], "none": row[2], "unknown": row[3]},
                         license=grouped(Media.license, {"license"}), status=grouped(Media.status, {"status"}))


@router.get("", response_model=S.MediaList)
def list_media(f: MediaFilter = Depends(media_filter_query), page: Paging = Depends(paging),
               _: User = Depends(current_user), s: Session = Depends(get_session)):
    total = s.scalar(select(func.count()).select_from(Media).where(_where(media_conditions(f))))
    rows = s.scalars(filtered_media(f).limit(page.limit).offset(page.offset)).all()
    return S.MediaList(items=media_summaries(s, rows), total=total or 0, facets=media_facets(s, f))


# ───────────────────────────── download ─────────────────────────────

class DownloadIn(BaseModel):
    media_ids: list[uuid.UUID] | None = None
    filter: MediaFilter | None = None
    caption_langs: list[str] = []
    include_auto: bool = False


# media already in flight or done are not re-queued
NOT_DOWNLOADABLE = ("downloading", "downloaded", "processing", "ready")


def _usable_captions(m: Media, langs: list[str], include_auto: bool) -> bool | None:
    """True/False when caption availability is known, None when the media has not been detailed yet."""
    if m.has_manual_captions is None:
        return None

    def ok(available: list[str] | None) -> bool:
        available = list(available or [])
        if not available:
            return False
        if not langs:
            return True
        return any(a == lang or a.startswith(lang + "-") or lang.startswith(a + "-") for lang in langs
                   for a in available)
    if m.has_manual_captions and ok(m.manual_caption_langs or (["*"] if not langs else [])):
        return True
    if include_auto and m.has_auto_captions and ok(m.auto_caption_langs or (["*"] if not langs else [])):
        return True
    return False


@router.post("/download", response_model=S.DownloadOut)
def download(body: DownloadIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    if body.media_ids is None and body.filter is None:
        raise ApiError(400, "no_selection", "請指定 media_ids 或 filter")
    if body.media_ids is not None:
        rows = s.scalars(select(Media).where(Media.id.in_(body.media_ids))).all()
    else:
        rows = s.scalars(filtered_media(body.filter)).all()
    queued = 0
    reasons: dict[str, int] = {}
    payload = {"caption_langs": body.caption_langs, "include_auto": body.include_auto}
    for m in rows:
        if m.status in NOT_DOWNLOADABLE:
            reasons["already_downloaded"] = reasons.get("already_downloaded", 0) + 1
            continue
        if _usable_captions(m, body.caption_langs, body.include_auto) is False:
            reasons["no_usable_captions"] = reasons.get("no_usable_captions", 0) + 1
            continue
        m.status, m.failure_code, m.failure_message = "queued", None, None
        enqueue(s, "media.download", media_id=m.id, channel_id=m.channel_id, payload=payload, created_by=user.id)
        queued += 1
    if body.media_ids is not None:
        missing = len(set(body.media_ids)) - len(rows)
        if missing:
            reasons["not_found"] = missing
    s.commit()
    return S.DownloadOut(queued=queued, skipped=sum(reasons.values()), skipped_reasons=reasons)


# ───────────────────────────── detail ─────────────────────────────

@router.get("/{media_id}", response_model=S.MediaDetailOut)
def media_detail(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    m = get_or_404(s, Media, media_id, "影片")
    summary = media_summaries(s, [m])[0]
    jobs = s.scalars(select(Job).where(Job.media_id == m.id).order_by(Job.created_at.desc()).limit(50)).all()
    tracks = s.scalars(select(Track).where(Track.media_id == m.id).order_by(Track.created_at)).all()
    playlists = s.scalars(select(PlaylistItem.playlist_id).where(PlaylistItem.media_id == m.id)).all()
    q = s.get(MediaQuality, m.id)
    return S.MediaDetailOut(
        **_media_base(m), channel=summary.channel, segment_count=summary.segment_count, description=m.description,
        tags=m.tags or [], categories=m.categories or [], chapters=m.chapters or [], language=m.language,
        like_count=m.like_count, comment_count=m.comment_count, availability=m.availability, age_limit=m.age_limit,
        metadata=m.metadata_ or {}, failure_message=m.failure_message, jobs=job_outs(s, jobs),
        tracks=[S.TrackOut(id=t.id, language=t.language, kind=t.kind, format=t.format) for t in tracks],
        playlists=list(playlists), quality=S.MediaQualityOut.model_validate(q) if q else None)


RETRYABLE = ("failed", "blocked", "interrupted", "cancelled")


@router.post("/{media_id}/retry", response_model=S.JobOut)
def retry_media(media_id: uuid.UUID, user: User = Depends(current_user), s: Session = Depends(get_session)):
    m = get_or_404(s, Media, media_id, "影片")
    job = s.scalar(select(Job).where(Job.media_id == m.id).order_by(Job.created_at.desc()).limit(1))
    if job is None or job.status not in RETRYABLE:
        raise ApiError(409, "nothing_to_retry", "這部影片沒有可重試的失敗步驟")
    _requeue_job(s, job)
    m.status = "queued" if job.kind in ("media.download", "media.detail") else "processing"
    if job.kind == "media.detail":
        m.status = "listed" if m.detailed_at is None else "detailed"
    m.failure_code = m.failure_message = None
    m.stage = job.kind
    s.commit()
    return job_out(s, job)


def _requeue_job(s: Session, job: Job) -> None:
    if job.media_id and s.scalar(select(Job.id).where(Job.kind == job.kind, Job.media_id == job.media_id,
                                                      Job.status.in_(("queued", "running")), Job.id != job.id)):
        raise ApiError(409, "job_active", "同類型的工作已在執行中")
    requeue(s, job)


@router.post("/{media_id}/reprocess", response_model=S.JobOut)
def reprocess(media_id: uuid.UUID, body: S.ReprocessIn, user: User = Depends(current_user),
              s: Session = Depends(get_session)):
    m = get_or_404(s, Media, media_id, "影片")
    if body.from_step not in PIPELINE:
        raise ApiError(400, "invalid_step", f"from_step 必須是 {', '.join(PIPELINE)} 之一")
    if m.status not in ("downloaded", "processing", "ready", "failed", "blocked"):
        raise ApiError(409, "not_downloaded", "影片尚未下載，無法重新處理")
    job = enqueue(s, body.from_step, media_id=m.id, channel_id=m.channel_id, payload={"reprocess": True},
                  created_by=user.id)
    m.status, m.stage, m.failure_code, m.failure_message = "processing", body.from_step, None, None
    s.commit()
    return job_out(s, job)


# ───────────────────────────── timeline / quality ─────────────────────────────

def downsample(arr: np.ndarray, hop: float, max_points: int = TIMELINE_MAX_POINTS) -> tuple[list[float], float]:
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    n = arr.size
    if n <= max_points:
        return _clean(arr), hop
    factor = math.ceil(n / max_points)
    pad = (-n) % factor
    if pad:
        arr = np.concatenate([arr, np.full(pad, np.nan, dtype=np.float32)])
    with np.errstate(all="ignore"):
        out = np.nanmean(arr.reshape(-1, factor), axis=1)
    return _clean(out), hop * factor


def _clean(a: np.ndarray) -> list[float]:
    return [None if not np.isfinite(x) else round(float(x), 4) for x in a]  # type: ignore[misc]


def load_frames(media_id: uuid.UUID) -> dict | None:
    key = storage.media_key(media_id, "frames_quality.npz")
    if storage.head(key) is None:
        return None
    with np.load(io.BytesIO(storage.get_bytes(key)), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _timeline_track(s: Session, media_id: uuid.UUID) -> Track | None:
    used = s.execute(select(Segment.track_id, func.count()).where(Segment.media_id == media_id,
                                                                 Segment.active.is_(True),
                                                                 Segment.track_id.isnot(None))
                     .group_by(Segment.track_id).order_by(func.count().desc()).limit(1)).first()
    if used:
        return s.get(Track, used[0])
    tracks = s.scalars(select(Track).where(Track.media_id == media_id).order_by(Track.created_at)).all()
    for kind in ("manual", "uploaded", "auto", "translated"):
        for t in tracks:
            if t.kind == kind:
                return t
    return tracks[0] if tracks else None


@router.get("/{media_id}/timeline", response_model=S.Timeline)
def timeline(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    m = get_or_404(s, Media, media_id, "影片")
    out = S.Timeline(duration=m.duration_sec)
    try:
        frames = load_frames(m.id)
    except Exception:  # noqa: BLE001
        log.exception("cannot read frames_quality for %s", m.id)
        frames = None
    if frames is not None and "hop_sec" in frames:
        hop = float(np.asarray(frames["hop_sec"]).reshape(-1)[0])
        if "brouhaha_vad" in frames:
            out.vad, out.hop_sec = downsample(frames["brouhaha_vad"], hop)
        elif "silero_vad" in frames:
            out.vad, out.hop_sec = downsample(frames["silero_vad"], hop)
        if "snr" in frames:
            out.snr, hop2 = downsample(frames["snr"], hop)
            out.hop_sec = out.hop_sec or hop2
        if out.duration is None:
            n = max((np.asarray(v).shape[0] for k, v in frames.items() if k in ("brouhaha_vad", "snr")), default=0)
            out.duration = round(n * hop, 3) if n else None
    regions = s.execute(select(SpeechRegion.source, SpeechRegion.start_sec, SpeechRegion.end_sec)
                        .where(SpeechRegion.media_id == m.id).order_by(SpeechRegion.start_sec)).all()
    src = "brouhaha" if any(r.source == "brouhaha" for r in regions) else "silero"
    out.speech_regions = [[r.start_sec, r.end_sec] for r in regions if r.source == src]
    out.diarization = [S.DiarTurn(speaker=t.local_speaker, start=t.start_sec, end=t.end_sec, overlap=t.is_overlap)
                       for t in s.scalars(select(DiarizationTurn).where(DiarizationTurn.media_id == m.id)
                                          .order_by(DiarizationTurn.start_sec))]
    out.repeated = [S.RepeatedSpan(id=r.id, group_id=r.group_id, start=r.start_sec, end=r.end_sec,
                                   position=r.position, excluded=r.excluded)
                    for r in s.scalars(select(RepeatedRegion).where(RepeatedRegion.media_id == m.id)
                                       .order_by(RepeatedRegion.start_sec))]
    track = _timeline_track(s, m.id)
    if track:
        out.track_id = track.id
        out.cues = [S.CueOut(start=c.start_sec, end=c.end_sec, text=c.text)
                    for c in s.scalars(select(Cue).where(Cue.track_id == track.id).order_by(Cue.idx))]
    segs = s.scalars(select(Segment).where(Segment.media_id == m.id, Segment.active.is_(True))
                     .order_by(Segment.start_sec)).all()
    out.segments = segment_summaries(s, segs)
    return out


@router.get("/{media_id}/quality", response_model=S.MediaQualityOut)
def media_quality(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    get_or_404(s, Media, media_id, "影片")
    q = s.get(MediaQuality, media_id)
    if q is None:
        raise ApiError(404, "no_quality", "這部影片尚未完成品質分析")
    return S.MediaQualityOut.model_validate(q)
