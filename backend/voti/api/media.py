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
from ..auth import current_user, require_admin
from ..db import get_session
from ..models import (Cue, DiarizationTurn, Job, Media, MediaQuality, PlaylistItem, RepeatedRegion, Segment,
                      Speaker, SpeakerAssignment, SpeechRegion, Track, User, VoiceProfile, WordAlignment)
from .. import deletion, textconv
from ..sources.youtube import caption_rank
from ..queue import PIPELINE, enqueue, is_side_job, requeue
from . import schemas as S
from .errors import ApiError
from .settings import get_setting
from .views import (Paging, _media_base, audit, downloaded_condition, get_or_404, job_out, job_outs,
                    media_summaries, current_run_id, new_condition, paging, segment_summaries)

log = logging.getLogger("voti.api.media")
router = APIRouter(prefix="/media", tags=["media"])

MEDIA_SORTS = {
    "published_at": Media.published_at.asc().nulls_last(), "-published_at": Media.published_at.desc().nulls_last(),
    "duration": Media.duration_sec.asc().nulls_last(), "-duration": Media.duration_sec.desc().nulls_last(),
    "views": Media.view_count.asc().nulls_last(), "-views": Media.view_count.desc().nulls_last(),
    "title": Media.title.asc(), "-title": Media.title.desc(),
    "created_at": Media.created_at.asc(), "-created_at": Media.created_at.desc(),
}
CAPTION_CHOICES = ("manual", "auto", "any", "none", "empty")
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
    downloaded: bool | None = None   # true: original audio stored / downloaded|processing|ready; false: the rest
    is_new: bool | None = None       # found by a channel refresh within 7 days and not downloaded
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
        captions: str | None = Query(None, description="manual|auto|any|none|empty"), include_auto: bool = False,
        caption_lang: str | None = None, license: str | None = None,  # noqa: A002
        min_duration: float | None = None, max_duration: float | None = None,
        published_after: datetime | None = None, published_before: datetime | None = None,
        detailed: bool | None = None, upsampled_suspect: bool | None = None,
        downloaded: bool | None = None, is_new: bool | None = None,
        sort: str | None = None) -> MediaFilter:
    try:
        return MediaFilter(channel_id=channel_id, playlist_id=playlist_id, q=q, kind=kind, status=status,
                           captions=captions, include_auto=include_auto, caption_lang=caption_lang, license=license,
                           min_duration=min_duration, max_duration=max_duration, published_after=published_after,
                           published_before=published_before, detailed=detailed,
                           upsampled_suspect=upsampled_suspect, downloaded=downloaded, is_new=is_new, sort=sort)
    except ValueError as e:
        raise ApiError(422, "invalid_filter", f"篩選條件不正確：{e}") from None


# captions_empty (YouTube listed CC tracks that turned out empty) counts as "no captions", never as manual or
# auto-only, even when YouTube ASR exists; `empty` is the subset of `none` with captions_empty.
_empty = Media.captions_empty.is_(True)
_manual = and_(Media.has_manual_captions.is_(True), ~_empty)
_auto_only = and_(Media.has_manual_captions.is_(False), Media.has_auto_captions.is_(True), ~_empty)
_no_captions = or_(and_(Media.has_manual_captions.is_(False),
                        func.coalesce(Media.has_auto_captions, False).is_(False)), _empty)
_unknown_captions = and_(Media.has_manual_captions.is_(None), ~_empty)


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
        elif f.captions == "empty":
            c.append(_empty)
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
    if f.downloaded is not None and "downloaded" not in skip:
        c.append(downloaded_condition() if f.downloaded else ~downloaded_condition())
    if f.is_new is not None and "is_new" not in skip:
        c.append(new_condition() if f.is_new else ~new_condition())
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
                           func.count().filter(_no_captions), func.count().filter(_unknown_captions),
                           func.count().filter(_empty))
                    .where(cap_where)).one()
    dl = s.execute(select(func.count().filter(downloaded_condition()), func.count())
                   .where(_where(media_conditions(f, {"downloaded"})))).one()
    new = s.scalar(select(func.count()).where(_where(media_conditions(f, {"is_new"})), new_condition())) or 0
    return S.MediaFacets(kind=grouped(Media.kind, {"kind"}),
                         captions={"manual": row[0], "auto_only": row[1], "none": row[2], "unknown": row[3],
                                   "empty": row[4]},
                         license=grouped(Media.license, {"license"}), status=grouped(Media.status, {"status"}),
                         downloaded={"yes": dl[0], "no": dl[1] - dl[0]}, new=new)


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
    # hard-subtitle OCR: true = also download video and OCR every selected media; false = never; null (default) =
    # settings.ocr.auto_when_no_captions decides for media without usable captions
    ocr: bool | None = None


# media already in flight or done are not re-queued
NOT_DOWNLOADABLE = ("downloading", "downloaded", "processing", "ready")


def _usable_captions(m: Media, langs: list[str], include_auto: bool) -> bool | None:
    """True/False when caption availability is known, None when the media has not been detailed yet."""
    if m.has_manual_captions is None:
        return None
    if m.has_manual_captions and not m.manual_caption_langs and not m.captions_empty and langs:
        return None  # uploader captions known from the YouTube API, languages not fetched yet: the download decides

    def ok(available: list[str] | None) -> bool:
        available = list(available or [])
        if not available:
            return False
        if not langs:
            return True
        if any(a == lang or a.startswith(lang + "-") or lang.startswith(a + "-") for lang in langs
               for a in available):
            return True
        # any Chinese variant stands in for a requested one (Simplified is converted on ingestion)
        return any(caption_rank(lang) <= 4 for lang in langs) and any(caption_rank(a) <= 4 for a in available)
    if m.has_manual_captions and ok(m.manual_caption_langs or (["*"] if not langs else [])):
        return True
    if include_auto and m.has_auto_captions and ok(m.auto_caption_langs or (["*"] if not langs else [])):
        return True
    return False


class SelectionIn(BaseModel):
    media_ids: list[uuid.UUID] | None = None
    filter: MediaFilter | None = None


@router.post("/selection-captions", response_model=S.SelectionCaptionsOut)
def selection_captions(body: SelectionIn, _: User = Depends(current_user), s: Session = Depends(get_session)):
    """Uploader caption languages over a whole selection (explicit ids or every media matching a filter, across
    pages), for the download dialog: how many selected media have each language."""
    if body.media_ids is None and body.filter is None:
        raise ApiError(400, "no_selection", "請指定 media_ids 或 filter")
    cond = [Media.id.in_(body.media_ids)] if body.media_ids is not None else list(media_conditions(body.filter))
    rows = s.execute(select(Media.has_manual_captions, Media.captions_empty, Media.manual_caption_langs)
                     .where(*cond)).all()
    langs: dict[str, int] = {}
    with_manual = unknown = 0
    for has_manual, empty, codes in rows:
        if not has_manual or empty:
            continue
        with_manual += 1
        if not codes:
            unknown += 1  # YouTube API: has uploader captions, languages not fetched yet
        for c in set(codes or []):
            langs[c] = langs.get(c, 0) + 1
    return S.SelectionCaptionsOut(total=len(rows), with_manual=with_manual, langs_unknown=unknown,
                                  langs=dict(sorted(langs.items(), key=lambda kv: -kv[1])))


class MediaDeleteIn(SelectionIn):
    confirm: bool = False


def _selected_ids(s: Session, body: SelectionIn) -> list[uuid.UUID]:
    if body.media_ids is None and body.filter is None:
        raise ApiError(400, "no_selection", "請指定 media_ids 或 filter")
    cond = [Media.id.in_(body.media_ids)] if body.media_ids is not None else list(media_conditions(body.filter))
    return list(s.scalars(select(Media.id).where(*cond)))


@router.post("/delete-impact", response_model=S.MediaDeleteImpact)
def media_delete_impact(body: SelectionIn, _: User = Depends(require_admin), s: Session = Depends(get_session)):
    """Admin only: what deleting the selection (explicit ids or every media matching a filter) would remove."""
    return deletion.media_impact(s, _selected_ids(s, body))


@router.post("/delete", response_model=S.MediaDeleteOut)
def delete_media(body: MediaDeleteIn, admin: User = Depends(require_admin), s: Session = Depends(get_session)):
    """Admin only. Deletes the videos completely (audio, captions, segments, analysis, reviews); the channel stays
    and a later re-list shows them again as not downloaded. Downloaded / reviewed media need confirm=true."""
    ids = _selected_ids(s, body)
    impact = deletion.media_impact(s, ids)
    if impact["requires_confirmation"] and not body.confirm:
        raise ApiError(409, "confirmation_required",
                       f"選取的影片中有 {impact['downloaded_media']} 部已下載或處理過，刪除會永久刪除音檔與分析結果")
    titles = dict(s.execute(select(Media.id, Media.title).where(Media.id.in_(ids[:50]))).all())
    result = deletion.delete_media(s, ids)
    audit(s, admin, "media.delete", "media", ids[0] if len(ids) == 1 else f"{len(ids)} media",
          before={"count": len(ids), "titles": [titles.get(i, "") for i in ids[:50]], **impact}, after=None)
    s.commit()
    return result


@router.post("/download", response_model=S.DownloadOut)
def download(body: DownloadIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    if body.media_ids is None and body.filter is None:
        raise ApiError(400, "no_selection", "請指定 media_ids 或 filter")
    if body.media_ids is not None:
        rows = s.scalars(select(Media).where(Media.id.in_(body.media_ids))).all()
    else:
        rows = s.scalars(filtered_media(body.filter)).all()
    queued = ocr_n = 0
    reasons: dict[str, int] = {}
    payload = {"caption_langs": body.caption_langs, "include_auto": body.include_auto}
    ocr_cfg = get_setting(s, "ocr").model_dump()
    ocr_ok = ocr_cfg["enabled"] and body.ocr is not False
    ocr_auto = ocr_ok and (body.ocr is True or ocr_cfg["auto_when_no_captions"])
    for m in rows:
        if m.status in NOT_DOWNLOADABLE:
            reasons["already_downloaded"] = reasons.get("already_downloaded", 0) + 1
            continue
        if m.failure_code == "members_only" or (m.availability or "") in ("subscriber_only", "premium_only"):
            reasons["members_only"] = reasons.get("members_only", 0) + 1
            continue
        no_cc = (m.captions_empty and not (body.include_auto and m.has_auto_captions)) or \
            _usable_captions(m, body.caption_langs, body.include_auto) is False
        if no_cc and not ocr_auto:
            key = "empty_captions" if m.captions_empty else "no_usable_captions"
            reasons[key] = reasons.get(key, 0) + 1
            continue
        p = dict(payload)
        if body.ocr is not None:     # true: video always; false: never. null: settings.ocr decides in the worker
            p["ocr"] = body.ocr
        if (ocr_ok and body.ocr is True) or (no_cc and ocr_auto):
            ocr_n += 1
        m.status, m.failure_code, m.failure_message = "queued", None, None
        enqueue(s, "media.download", media_id=m.id, channel_id=m.channel_id, payload=p, created_by=user.id)
        queued += 1
    if body.media_ids is not None:
        missing = len(set(body.media_ids)) - len(rows)
        if missing:
            reasons["not_found"] = missing
    s.commit()
    return S.DownloadOut(queued=queued, skipped=sum(reasons.values()), skipped_reasons=reasons, ocr=ocr_n)


# ───────────────────────────── detail ─────────────────────────────

@router.get("/{media_id}", response_model=S.MediaDetailOut)
def media_detail(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    m = get_or_404(s, Media, media_id, "影片")
    summary = media_summaries(s, [m])[0]
    jobs = s.scalars(select(Job).where(Job.media_id == m.id).order_by(Job.created_at.desc()).limit(50)).all()
    tracks = s.scalars(select(Track).where(Track.media_id == m.id).order_by(Track.created_at)).all()
    playlists = s.scalars(select(PlaylistItem.playlist_id).where(PlaylistItem.media_id == m.id)).all()
    q = s.get(MediaQuality, m.id)
    s2t = textconv.track_infos(s, tracks)
    validity_counts = {"valid": 0, "invalid": 0, "unknown": 0}
    validity_counts.update(dict(s.execute(select(Segment.validity, func.count())
                                          .where(Segment.media_id == m.id, Segment.active.is_(True))
                                          .group_by(Segment.validity)).all()))
    validating = s.scalar(select(Job.id).where(Job.kind == "media.validate", Job.media_id == m.id,
                                               Job.status.in_(("queued", "running"))).limit(1)) is not None
    ocr_active = _ocr_job_active(s, m.id)
    track_outs = [S.TrackOut(id=t.id, language=t.language, kind=t.kind, format=t.format,
                             s2t_converted=bool(s2t.get(t.id, {}).get("converted")),
                             script=s2t.get(t.id, {}).get("script"),
                             cues_converted=s2t.get(t.id, {}).get("cues_converted")) for t in tracks]
    return S.MediaDetailOut(
        **_media_base(m), channel=summary.channel, segment_count=summary.segment_count,
        downloaded=summary.downloaded, invalid_count=summary.invalid_count, invalid_sec=summary.invalid_sec,
        description=m.description,
        tags=m.tags or [], categories=m.categories or [], chapters=m.chapters or [], language=m.language,
        like_count=m.like_count, comment_count=m.comment_count, availability=m.availability, age_limit=m.age_limit,
        metadata=m.metadata_ or {}, failure_message=m.failure_message, jobs=job_outs(s, jobs),
        tracks=track_outs, playlists=list(playlists), quality=S.MediaQualityOut.model_validate(q) if q else None,
        text_converted=any(tr.s2t_converted for tr in track_outs),
        text_convertible=any(textconv.convertible(t.language, s2t.get(t.id)) for t in tracks),
        text_conversion=(m.metadata_ or {}).get("text_conversion"), validity_counts=validity_counts,
        validation_job_active=validating, ocr=(m.metadata_ or {}).get("ocr"), ocr_job_active=ocr_active)


def _ocr_job_active(s: Session, media_id: uuid.UUID) -> bool:
    jobs = s.scalars(select(Job).where(Job.media_id == media_id, Job.kind.in_(("media.ocr", "media.download")),
                                       Job.status.in_(("queued", "running")))).all()
    return any(j.kind == "media.ocr" or (j.payload or {}).get("video_only") for j in jobs)


RECHECK_PAYLOAD = {"force": True, "verify": True}


@router.post("/{media_id}/recheck-captions", response_model=S.JobOut)
def recheck_captions(media_id: uuid.UUID, user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Re-fetch metadata and re-verify the caption tracks now (media.detail {force, verify}). Downloaded /
    processed media keep their status; a media skipped for empty_captions returns to `detailed` when a track is
    usable now."""
    m = get_or_404(s, Media, media_id, "影片")
    if m.platform != "youtube":
        raise ApiError(409, "unsupported_platform", "只有 YouTube 影片可以重新檢查字幕")
    job = enqueue(s, "media.detail", media_id=m.id, channel_id=m.channel_id, payload=dict(RECHECK_PAYLOAD),
                  created_by=user.id)
    if job.status == "queued" and not all((job.payload or {}).get(k) for k in RECHECK_PAYLOAD):
        job.payload = {**(job.payload or {}), **RECHECK_PAYLOAD}  # an already queued plain detail job
    s.commit()
    return job_out(s, job)


RETRYABLE = ("failed", "blocked", "interrupted", "cancelled")


@router.post("/{media_id}/retry", response_model=S.JobOut)
def retry_media(media_id: uuid.UUID, user: User = Depends(current_user), s: Session = Depends(get_session)):
    m = get_or_404(s, Media, media_id, "影片")
    job = next((j for j in s.scalars(select(Job).where(Job.media_id == m.id).order_by(Job.created_at.desc())
                                     .limit(50)) if not is_side_job(j.kind, j.payload)), None)
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
    if body.from_step == "media.ocr":
        from ..pipeline import ocr as ocr_mod
        if ocr_mod.video_asset(s, m.id) is None:
            raise ApiError(409, "ocr_video_missing", "OCR 用的影片已刪除；請用 POST /media/{id}/ocr 重新辨識")
    job = enqueue(s, body.from_step, media_id=m.id, channel_id=m.channel_id, payload={"reprocess": True},
                  created_by=user.id)
    m.status, m.stage, m.failure_code, m.failure_message = "processing", body.from_step, None, None
    s.commit()
    return job_out(s, job)


# ───────────────────────────── hard-subtitle OCR ─────────────────────────────

@router.get("/{media_id}/ocr", response_model=S.OcrOut)
def media_ocr(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    """Burned-in subtitle events of the media's OCR track (voti.pipeline.ocr), all of them, in time order."""
    import json as _json

    from ..models import Asset
    from ..pipeline import ocr as ocr_mod
    m = get_or_404(s, Media, media_id, "影片")
    summary = dict((m.metadata_ or {}).get("ocr") or {})
    out = S.OcrOut(status=summary.get("status") or "none", summary=summary, job_active=_ocr_job_active(s, m.id),
                   video_available=ocr_mod.video_asset(s, m.id) is not None)
    track = s.scalar(select(Track).where(Track.media_id == m.id, Track.kind == "ocr").order_by(Track.created_at.desc()))
    asset = s.get(Asset, track.asset_id) if track is not None and track.asset_id else None
    if asset is None:
        return out
    data = _json.loads(storage.get_bytes(asset.key))
    cues = {c.idx: c.id for c in s.scalars(select(Cue).where(Cue.track_id == track.id))}
    events = []
    for r in data.get("events", []):
        events.append(S.OcrEventOut(
            **{k: r.get(k) for k in ("idx", "start", "end", "raw_text", "text", "s2t_converted", "track", "style",
                                     "bbox", "lines", "conf", "n_frames", "agreement", "status", "reason",
                                     "speech_sec", "paired_start", "paired_end") if r.get(k) is not None},
            cue_id=cues.get(r["idx"]),
            crop_url=f"/api/media/{m.id}/ocr/crops/{r['idx']}.webp" if r.get("crop_key") else None))
    out.model, out.frame_size, out.fps = data.get("model"), data.get("frame_size"), summary.get("fps")
    out.styles, out.tracks, out.events, out.total = data.get("styles", []), data.get("tracks", []), events, len(events)
    return out


@router.get("/{media_id}/ocr/crops/{name}")
def media_ocr_crop(media_id: uuid.UUID, name: str, _: User = Depends(current_user), s: Session = Depends(get_session)):
    """The webp crop of one OCR event (`{idx}.webp`), proxied from storage; cacheable."""
    from fastapi import Response

    from ..pipeline import ocr as ocr_mod
    get_or_404(s, Media, media_id, "影片")
    stem = name[:-5] if name.endswith(".webp") else name
    if not stem.isdigit():
        raise ApiError(404, "not_found", "找不到截圖")
    try:
        data = storage.get_bytes(ocr_mod.crop_key(media_id, int(stem)))
    except Exception:  # noqa: BLE001
        raise ApiError(404, "not_found", "找不到截圖") from None
    return Response(content=data, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/{media_id}/ocr", response_model=S.JobOut)
def run_ocr(media_id: uuid.UUID, body: S.OcrRunIn | None = None, user: User = Depends(current_user),
            s: Session = Depends(get_session)):
    """(Re-)run hard-subtitle OCR: media.ocr {rerun} when the temporary video is still stored, otherwise a
    video-only media.download {video_only, ocr} that chains media.ocr {rerun}. Either way the media is re-segmented
    afterwards (media.segment {reprocess} → align → validate → …); reviewed / corrected segments are kept."""
    from ..pipeline import ocr as ocr_mod
    body = body or S.OcrRunIn()
    m = get_or_404(s, Media, media_id, "影片")
    if not get_setting(s, "ocr").enabled:
        raise ApiError(409, "ocr_disabled", "硬字幕 OCR 已在設定中停用")
    if m.platform != "youtube":
        raise ApiError(409, "unsupported_platform", "只有 YouTube 影片可以下載影片做 OCR")
    from ..models import Asset
    has_audio = s.scalar(select(Asset.id).where(Asset.media_id == m.id, Asset.kind == "original_audio")
                         .limit(1)) is not None
    if not has_audio or m.status in ("queued", "downloading"):
        raise ApiError(409, "not_downloaded", "影片尚未下載，請先下載（可在下載時選擇 OCR）")
    if _ocr_job_active(s, m.id):
        raise ApiError(409, "job_active", "OCR 工作已在執行中")
    has_track = s.scalar(select(Track.id).where(Track.media_id == m.id, Track.kind == "ocr").limit(1)) is not None
    if has_track and not body.force:
        raise ApiError(409, "ocr_exists", "已有 OCR 字幕；要重新辨識請帶 force: true")
    if ocr_mod.video_asset(s, m.id) is not None:
        job = enqueue(s, "media.ocr", media_id=m.id, channel_id=m.channel_id, payload={"rerun": True},
                      created_by=user.id)
        m.status, m.stage, m.failure_code, m.failure_message = "processing", "media.ocr", None, None
    else:
        job = enqueue(s, "media.download", media_id=m.id, channel_id=m.channel_id,
                      payload={"video_only": True, "ocr": True}, created_by=user.id)
    meta = dict(m.metadata_ or {})
    meta["ocr"] = {**dict(meta.get("ocr") or {}), "status": "pending", "requested": "rerun"}
    m.metadata_ = meta
    s.commit()
    return job_out(s, job)


@router.post("/{media_id}/convert-text", response_model=S.QueuedOut)
def convert_text(media_id: uuid.UUID, user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Queue Simplified → Traditional conversion of this media's existing cues/segments (voti.textconv)."""
    m = get_or_404(s, Media, media_id, "影片")
    if not textconv.text_settings(s)["convert_simplified"]:
        raise ApiError(409, "text_conversion_disabled", "簡轉繁已在設定中停用")
    if m.id not in textconv.convertible_media(s, Media.id == m.id):
        return S.QueuedOut(queued=0)
    enqueue(s, "media.convert_text", media_id=m.id, channel_id=m.channel_id, created_by=user.id)
    s.commit()
    return S.QueuedOut(queued=1)


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


@router.get("/{media_id}/segments", response_model=S.Page[S.SegmentSummary])
def media_segments(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    """Every active segment of one media in time order (no pagination) — the media page transcript."""
    m = get_or_404(s, Media, media_id, "影片")
    segs = s.scalars(select(Segment).where(Segment.media_id == m.id, Segment.active.is_(True))
                     .order_by(Segment.start_sec, Segment.id)).all()
    return S.Page[S.SegmentSummary](items=segment_summaries(s, segs), total=len(segs))


# ───────────────────────────── speakers of one media ─────────────────────────────

SAMPLE_SEGMENTS = 5


def _probs(d) -> dict[str, float]:  # noqa: ANN001
    """Vox-Profile trait {"label", "probs": {...}} (or a flat {label: p}) → {label: p}."""
    if not isinstance(d, dict) or not d:
        return {}
    src = d["probs"] if isinstance(d.get("probs"), dict) else d
    return {str(k): float(v) for k, v in src.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _norm(acc: dict[str, float]) -> dict[str, float]:
    tot = sum(v for v in acc.values() if v > 0)
    if tot <= 0:
        return {}
    return {k: round(v / tot, 4) for k, v in sorted(acc.items(), key=lambda kv: -kv[1]) if v > 0}


def aggregate_media_profile(items: list[tuple[float, VoiceProfile]]) -> S.MediaSpeakerProfile:
    """Duration-weighted aggregate of per-segment VoiceProfile rows (one per segment)."""
    sex: dict[str, float] = {}
    age_group: dict[str, float] = {}
    dists: dict[str, dict[str, float]] = {"emotion": {}, "speech_flow": {}, "accent_broad": {}}
    vq: dict[str, float] = {}
    vq_w: dict[str, float] = {}
    age_sum = age_w = 0.0
    for w, p in items:
        w = max(float(w), 1e-3)
        sp = (p.model_versions or {}).get("sex_probs")
        sp = _probs(sp) if isinstance(sp, dict) else {}
        if sp:
            tot = sum(sp.values()) or 1.0
            for k, v in sp.items():
                sex[k] = sex.get(k, 0.0) + w * v / tot
        elif p.sex:
            if p.sex_prob is not None and p.sex in ("male", "female"):
                other = "female" if p.sex == "male" else "male"
                sex[p.sex] = sex.get(p.sex, 0.0) + w * p.sex_prob
                sex[other] = sex.get(other, 0.0) + w * (1 - p.sex_prob)
            else:
                sex[p.sex] = sex.get(p.sex, 0.0) + w
        if p.age is not None:
            age_sum += w * p.age
            age_w += w
        if p.age_group:
            age_group[p.age_group] = age_group.get(p.age_group, 0.0) + w
        for f, acc in dists.items():
            probs = _probs(getattr(p, f))
            tot = sum(v for v in probs.values() if v > 0)
            for k, v in probs.items():
                if v > 0 and tot > 0:
                    acc[k] = acc.get(k, 0.0) + w * v / tot
        for k, v in _probs(p.voice_quality).items():
            vq[k] = vq.get(k, 0.0) + w * v
            vq_w[k] = vq_w.get(k, 0.0) + w
    sex_n = _norm(sex)
    top = next(iter(sex_n.items()), None)
    return S.MediaSpeakerProfile(
        sex=sex_n, sex_top=top[0] if top else None, sex_prob=top[1] if top else None,
        age_mean=round(age_sum / age_w, 2) if age_w else None, age_group=_norm(age_group),
        emotion=_norm(dists["emotion"]),
        voice_quality={k: round(vq[k] / vq_w[k], 4) for k in sorted(vq, key=lambda k: -(vq[k] / vq_w[k]))},
        speech_flow=_norm(dists["speech_flow"]), accent_broad=_norm(dists["accent_broad"]))


def media_speaker_labels(s: Session, media_id: uuid.UUID) -> list[str]:
    """Every diarization label of a media (turns ∪ segment local_speaker), most data first: usable (not invalid)
    segment seconds, then diarized seconds, then label. 語者 1 is the speaker with the most data; this is also
    the colour order of the timeline speaker lane, speaker cards and transcript highlights (the frontend's
    timelineSpeakers uses the same key)."""
    diar: dict[str, float] = {}
    for lab, sec in s.execute(select(DiarizationTurn.local_speaker,
                                     func.sum(DiarizationTurn.end_sec - DiarizationTurn.start_sec))
                              .where(DiarizationTurn.media_id == media_id).group_by(DiarizationTurn.local_speaker)):
        if lab:
            diar[lab] = float(sec or 0)
    usable: dict[str, float] = {}
    for lab, sec in s.execute(select(Segment.local_speaker, func.sum(Segment.end_sec - Segment.start_sec))
                              .where(Segment.media_id == media_id, Segment.active.is_(True),
                                     Segment.local_speaker.isnot(None),
                                     func.coalesce(Segment.validity, "unknown") != "invalid")
                              .group_by(Segment.local_speaker)):
        usable[lab] = float(sec or 0)
    labels = set(diar) | set(usable)
    labels |= {x for x in s.scalars(select(Segment.local_speaker).where(Segment.media_id == media_id,
                                                                       Segment.active.is_(True),
                                                                       Segment.local_speaker.isnot(None))
                                    .distinct()).all() if x}
    return sorted(labels, key=lambda x: (-round(usable.get(x, 0.0), 3), -round(diar.get(x, 0.0), 3), x))


@router.get("/{media_id}/speakers", response_model=list[S.MediaSpeaker])
def media_speakers(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    """The diarized speakers of one media, most data first (see media_speaker_labels), with speaking time, segment counts, aggregated
    Vox-Profile estimates, the cross-video candidate speaker (cluster) their segments are assigned to and a few
    representative segments. Invalid segments (validity = invalid) are excluded from every number."""
    from .settings import get_setting
    m = get_or_404(s, Media, media_id, "影片")
    labels = media_speaker_labels(s, m.id)
    if not labels:
        return []
    diar: dict[str, float] = {}
    for lab, sec in s.execute(select(DiarizationTurn.local_speaker,
                                     func.sum(DiarizationTurn.end_sec - DiarizationTurn.start_sec))
                              .where(DiarizationTurn.media_id == m.id).group_by(DiarizationTurn.local_speaker)):
        diar[lab] = float(sec or 0)
    segs = s.scalars(select(Segment).where(Segment.media_id == m.id, Segment.active.is_(True),
                                           Segment.local_speaker.isnot(None))
                     .order_by(Segment.start_sec, Segment.id)).all()
    valid = [g for g in segs if g.validity != "invalid"]
    ids = [g.id for g in valid]
    backbone = get_setting(s, "pipeline").voxprofile_backbone
    profiles: dict[uuid.UUID, VoiceProfile] = {}
    if ids:
        rev = {g.id: g.revision for g in valid}
        for vp in s.scalars(select(VoiceProfile).where(VoiceProfile.segment_id.in_(ids))
                            .order_by(VoiceProfile.created_at)):
            if vp.revision != rev.get(vp.segment_id):
                continue
            cur = profiles.get(vp.segment_id)
            if cur is None or vp.backbone == backbone or cur.backbone != backbone:
                profiles[vp.segment_id] = vp
    clusters: dict[uuid.UUID, tuple[uuid.UUID, str, str | None]] = {}
    run_id = current_run_id(s)
    if run_id and ids:
        for seg_id, sp_id, label, dn in s.execute(
                select(SpeakerAssignment.segment_id, Speaker.id, Speaker.label, Speaker.display_name)
                .join(Speaker, Speaker.id == SpeakerAssignment.speaker_id)
                .where(SpeakerAssignment.run_id == run_id, SpeakerAssignment.segment_id.in_(ids))):
            clusters[seg_id] = (sp_id, label, dn)
    out = []
    for i, lab in enumerate(labels):
        mine = [g for g in valid if g.local_speaker == lab]
        dur = {g.id: max(0.0, g.end_sec - g.start_sec) for g in mine}
        items = [(dur[g.id], profiles[g.id]) for g in mine if g.id in profiles]
        by_cluster: dict[uuid.UUID, float] = {}
        meta: dict[uuid.UUID, tuple[str, str | None]] = {}
        for g in mine:
            c = clusters.get(g.id)
            if c:
                by_cluster[c[0]] = by_cluster.get(c[0], 0.0) + dur[g.id]
                meta[c[0]] = (c[1], c[2])
        cluster = None
        if by_cluster:
            cid = max(by_cluster, key=lambda k: by_cluster[k])
            cluster = S.MediaSpeakerCluster(id=cid, label=meta[cid][0], display_name=meta[cid][1],
                                            share=round(by_cluster[cid] / (sum(by_cluster.values()) or 1), 4))

        # representative: single-speaker, 2–15 s, cleanest first; shown in time order
        def rank(g: Segment) -> tuple:
            d = dur[g.id]
            return (g.speaker_state != "single", not (2.0 <= d <= 15.0), g.id not in profiles,
                    -(g.snr if g.snr is not None else -99.0), -d)
        samples = sorted(sorted(mine, key=rank)[:SAMPLE_SEGMENTS], key=lambda g: g.start_sec)
        first = next((g.start_sec for g in segs if g.local_speaker == lab), None)
        out.append(S.MediaSpeaker(
            local_speaker=lab, color_index=i, segment_count=len(mine), total_sec=round(sum(dur.values()), 3),
            diarized_sec=round(diar.get(lab, 0.0), 3),
            invalid_count=sum(1 for g in segs if g.local_speaker == lab and g.validity == "invalid"),
            first_sec=first, profile=aggregate_media_profile(items), profiled_segments=len(items), cluster=cluster,
            sample_segment_ids=[g.id for g in samples]))
    return out


@router.get("/{media_id}/words", response_model=S.MediaWords)
def media_words(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    """Per-character timing of every aligned segment: {segment_id: [[unit, start|null, end|null], ...]}.
    Only alignments that still match the segment's current text and revision are returned."""
    from ..pipeline.align import segment_text, text_hash
    m = get_or_404(s, Media, media_id, "影片")
    rows = s.execute(select(Segment, WordAlignment).join(WordAlignment, WordAlignment.segment_id == Segment.id)
                     .where(Segment.media_id == m.id, Segment.active.is_(True))).all()
    words: dict[str, list] = {}
    status: dict[str, int] = {}
    for g, wa in rows:
        st = wa.status
        if st == "aligned" and (wa.revision != g.revision or wa.text_hash != text_hash(segment_text(g))):
            st = "stale"
        status[st] = status.get(st, 0) + 1
        if st == "aligned":
            words[str(g.id)] = [[u[0], u[1], u[2]] for u in (wa.units or [])]
    total = s.scalar(select(func.count()).select_from(Segment).where(Segment.media_id == m.id,
                                                                     Segment.active.is_(True))) or 0
    status["none"] = total - len(rows)
    active = s.scalar(select(Job.id).where(Job.kind == "media.align", Job.media_id == m.id,
                                           Job.status.in_(("queued", "running")))) is not None
    return S.MediaWords(words=words, segments=total, status=status, job_active=active)


@router.post("/{media_id}/align", response_model=S.QueuedOut)
def align_media(media_id: uuid.UUID, body: S.AlignIn | None = None, user: User = Depends(current_user),
                s: Session = Depends(get_session)):
    """(Re-)align the segment text of one media (media.align, outside the pipeline chain). force=true also
    re-aligns segments whose alignment is still valid. 0 when the media has no segments."""
    m = get_or_404(s, Media, media_id, "影片")
    if s.scalar(select(Segment.id).where(Segment.media_id == m.id, Segment.active.is_(True)).limit(1)) is None:
        return S.QueuedOut(queued=0)
    enqueue(s, "media.align", media_id=m.id, channel_id=m.channel_id, created_by=user.id,
            payload={"chain": False, "force": bool(body and body.force), "reason": "backfill"})
    s.commit()
    return S.QueuedOut(queued=1)


@router.post("/{media_id}/validate", response_model=S.QueuedOut)
def validate_media(media_id: uuid.UUID, body: S.ValidateIn | None = None, user: User = Depends(current_user),
                   s: Session = Depends(get_session)):
    """(Re-)check whether the captions match the audio (media.validate, outside the pipeline chain). force=true
    re-checks segments whose result is still current (manual decisions are always kept). 0 without segments."""
    m = get_or_404(s, Media, media_id, "影片")
    if s.scalar(select(Segment.id).where(Segment.media_id == m.id, Segment.active.is_(True)).limit(1)) is None:
        return S.QueuedOut(queued=0)
    enqueue(s, "media.validate", media_id=m.id, channel_id=m.channel_id, created_by=user.id,
            payload={"chain": False, "force": bool(body and body.force), "reason": "backfill"})
    s.commit()
    return S.QueuedOut(queued=1)


@router.get("/{media_id}/quality", response_model=S.MediaQualityOut)
def media_quality(media_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    get_or_404(s, Media, media_id, "影片")
    q = s.get(MediaQuality, media_id)
    if q is None:
        raise ApiError(404, "no_quality", "這部影片尚未完成品質分析")
    return S.MediaQualityOut.model_validate(q)
