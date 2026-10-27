"""Shared helpers for routers: pagination, audit log, and batch builders that turn ORM rows into
response models without N+1 queries."""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from fastapi import Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from ..models import (AuditLog, Channel, ClusterRun, Job, Media, MediaQuality, Segment, Speaker, SpeakerAssignment,
                      SpeakerOverride, User, VoiceProfile, utcnow)
from . import schemas as S
from .errors import ApiError

MAX_LIMIT = 500


@dataclass
class Paging:
    limit: int
    offset: int


def paging(limit: int = Query(50, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0)) -> Paging:
    return Paging(limit, offset)


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_uuid(value: str | uuid.UUID | None, what: str = "id") -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ApiError(400, "invalid_id", f"{what} 格式錯誤") from None


def get_or_404(s: Session, model, ident, what: str = "資料"):  # noqa: ANN001, ANN201
    obj = s.get(model, ident)
    if obj is None:
        raise ApiError(404, "not_found", f"找不到{what}")
    return obj


def audit(s: Session, user: User | None, action: str, entity_type: str, entity_id, before: dict | None = None,
          after: dict | None = None) -> None:  # noqa: ANN001
    s.add(AuditLog(user_id=user.id if user else None, action=action, entity_type=entity_type,
                   entity_id=str(entity_id), before=_jsonable(before), after=_jsonable(after)))


def _jsonable(d):  # noqa: ANN001, ANN202
    if d is None:
        return None
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(d)


def current_run_id(s: Session) -> uuid.UUID | None:
    return s.scalar(select(ClusterRun.id).where(ClusterRun.is_current.is_(True))
                    .order_by(ClusterRun.created_at.desc()).limit(1))


# ───────────────────────────── users / jobs ─────────────────────────────

def user_out(u: User) -> S.UserOut:
    return S.UserOut.model_validate(u)


def job_eta(job: Job) -> float | None:
    if job.status != "running" or not job.started_at or not job.progress or job.progress <= 0 or job.progress >= 1:
        return None
    elapsed = (utcnow() - job.started_at).total_seconds()
    if elapsed <= 0:
        return None
    return round(elapsed * (1 - job.progress) / job.progress, 1)


def job_outs(s: Session, jobs: Sequence[Job]) -> list[S.JobOut]:
    media_ids = {j.media_id for j in jobs if j.media_id}
    media = {r.id: r for r in s.execute(select(Media.id, Media.title, Media.channel_id)
                                        .where(Media.id.in_(media_ids))).all()} if media_ids else {}
    titles = {k: r.title for k, r in media.items()}
    # channel of the job, else the channel of its media
    job_channel = {j.id: j.channel_id or (media[j.media_id].channel_id if j.media_id in media else None) for j in jobs}
    ch_ids = {c for c in job_channel.values() if c}
    ch_titles = dict(s.execute(select(Channel.id, Channel.title).where(Channel.id.in_(ch_ids))).all()) \
        if ch_ids else {}
    return [S.JobOut(id=j.id, kind=j.kind, queue=j.queue, status=j.status, progress=j.progress or 0.0,
                     phase=j.phase or "", message=j.message or "", error_code=j.error_code,
                     error_message=j.error_message, attempts=j.attempts or 0, media_id=j.media_id,
                     channel_id=j.channel_id, media_title=titles.get(j.media_id),
                     channel_title=ch_titles.get(job_channel[j.id]), payload=j.payload or {},
                     result=j.result or {}, created_at=j.created_at, started_at=j.started_at,
                     finished_at=j.finished_at, eta_sec=job_eta(j), run_after=j.run_after) for j in jobs]


def job_out(s: Session, job: Job) -> S.JobOut:
    return job_outs(s, [job])[0]


# ───────────────────────────── channels ─────────────────────────────

DOWNLOADED_STATES = ("downloaded", "processing", "ready")
FAILED_STATES = ("failed", "blocked")


def channel_summaries(s: Session, channels: Sequence[Channel]) -> list[S.ChannelSummary]:
    ids = [c.id for c in channels]
    stats: dict = {}
    if ids:
        dur = func.coalesce(Media.duration_sec, 0.0)
        rows = s.execute(
            select(Media.channel_id,
                   func.count(),
                   func.count(Media.detailed_at),
                   func.count().filter(Media.has_manual_captions.is_(True)),
                   func.count().filter(Media.status.in_(DOWNLOADED_STATES)),
                   func.count().filter(Media.status == "ready"),
                   func.count().filter(Media.status.in_(FAILED_STATES)),
                   func.sum(dur),
                   func.sum(case((Media.status == "ready", dur), else_=0.0)),
                   func.sum(func.coalesce(MediaQuality.speech_sec, 0.0)))
            .select_from(Media).outerjoin(MediaQuality, MediaQuality.media_id == Media.id)
            .where(Media.channel_id.in_(ids)).group_by(Media.channel_id)).all()
        for r in rows:
            stats[r[0]] = r[1:]
    out = []
    for c in channels:
        st = stats.get(c.id)
        counts = S.ChannelCounts(**dict(zip(("listed", "detailed", "with_manual_captions", "downloaded", "ready",
                                             "failed"), st[:6]))) if st else S.ChannelCounts()
        hours = S.ChannelHours(listed=round((st[6] or 0) / 3600, 3), ready=round((st[7] or 0) / 3600, 3),
                               speech=round((st[8] or 0) / 3600, 3)) if st else S.ChannelHours()
        out.append(S.ChannelSummary(id=c.id, platform=c.platform, platform_channel_id=c.platform_channel_id,
                                    title=c.title, handle=c.handle, url=c.url, thumbnail_url=c.thumbnail_url,
                                    subscriber_count=c.subscriber_count, video_count=c.video_count,
                                    list_status=c.list_status, last_listed_at=c.last_listed_at, counts=counts,
                                    hours=hours))
    return out


# ───────────────────────────── media ─────────────────────────────

def media_summaries(s: Session, media: Sequence[Media]) -> list[S.MediaSummary]:
    ids = [m.id for m in media]
    ch_ids = {m.channel_id for m in media if m.channel_id}
    channels = dict(s.execute(select(Channel.id, Channel.title).where(Channel.id.in_(ch_ids))).all()) if ch_ids else {}
    quality = {q.media_id: q for q in s.scalars(select(MediaQuality).where(MediaQuality.media_id.in_(ids)))} if ids else {}
    seg_counts = dict(s.execute(select(Segment.media_id, func.count()).where(Segment.media_id.in_(ids),
                                                                            Segment.active.is_(True))
                                .group_by(Segment.media_id)).all()) if ids else {}
    out = []
    for m in media:
        q = quality.get(m.id)
        out.append(S.MediaSummary(
            **_media_base(m), channel=S.ChannelRef(id=m.channel_id, title=channels.get(m.channel_id, ""))
            if m.channel_id else None,
            quality=S.MediaQualityBrief(snr_mean=q.snr_mean, dnsmos_ovrl=q.dnsmos_ovrl,
                                        real_bandwidth_hz=q.real_bandwidth_hz, speech_ratio=q.speech_ratio,
                                        sample_rate=q.sample_rate, upsampled_suspect=q.upsampled_suspect)
            if q else None,
            segment_count=seg_counts.get(m.id, 0)))
    return out


def _media_base(m: Media) -> dict:
    return dict(id=m.id, platform=m.platform, platform_media_id=m.platform_media_id, url=m.url, kind=m.kind,
                title=m.title, thumbnail_url=m.thumbnail_url, duration_sec=m.duration_sec,
                published_at=m.published_at, view_count=m.view_count, license=m.license,
                has_manual_captions=m.has_manual_captions, has_auto_captions=m.has_auto_captions,
                manual_caption_langs=list(m.manual_caption_langs or []),
                auto_caption_langs=list(m.auto_caption_langs or []), status=m.status, stage=m.stage,
                failure_code=m.failure_code, detailed_at=m.detailed_at)


# ───────────────────────────── segments ─────────────────────────────

def segment_summaries(s: Session, segs: Sequence[Segment], run_id: uuid.UUID | None = None,
                      run_known: bool = False) -> list[S.SegmentSummary]:
    if not segs:
        return []
    ids = [g.id for g in segs]
    media_ids = {g.media_id for g in segs}
    media = {r.id: r for r in s.execute(select(Media.id, Media.title, Media.channel_id, Channel.title.label("ct"))
                                        .outerjoin(Channel, Channel.id == Media.channel_id)
                                        .where(Media.id.in_(media_ids))).all()}
    if not run_known:
        run_id = current_run_id(s)
    speakers: dict = {}
    if run_id:
        for seg_id, sp_id, label, dn in s.execute(
                select(SpeakerAssignment.segment_id, Speaker.id, Speaker.label, Speaker.display_name)
                .join(Speaker, Speaker.id == SpeakerAssignment.speaker_id)
                .where(SpeakerAssignment.run_id == run_id, SpeakerAssignment.segment_id.in_(ids))).all():
            speakers[seg_id] = S.SpeakerRef(id=sp_id, label=label, display_name=dn)
    overrides = dict(s.execute(select(SpeakerOverride.segment_id, SpeakerOverride.speaker_name)
                               .where(SpeakerOverride.segment_id.in_(ids))).all())
    profiles: dict = {}
    for vp in s.scalars(select(VoiceProfile).join(Segment, and_(Segment.id == VoiceProfile.segment_id,
                                                               Segment.revision == VoiceProfile.revision))
                        .where(VoiceProfile.segment_id.in_(ids)).order_by(VoiceProfile.created_at)):
        profiles[vp.segment_id] = vp  # latest wins
    out = []
    for g in segs:
        m = media.get(g.media_id)
        vp = profiles.get(g.id)
        out.append(S.SegmentSummary(**segment_base(g), media_title=m.title if m else "",
                                    channel_id=m.channel_id if m else None, channel_title=m.ct if m else None,
                                    speaker=speakers.get(g.id), speaker_override=overrides.get(g.id),
                                    profile=S.ProfileBrief(age=vp.age, sex=vp.sex,
                                                           emotion_label=(vp.emotion or {}).get("label"))
                                    if vp else None))
    return out


def segment_base(g: Segment) -> dict:
    return dict(id=g.id, media_id=g.media_id, start_sec=g.start_sec, end_sec=g.end_sec,
                duration=round(g.end_sec - g.start_sec, 3),
                text=g.corrected_text if g.corrected_text is not None else g.original_text,
                original_text=g.original_text, corrected_text=g.corrected_text, text_source=g.text_source,
                language=g.language, local_speaker=g.local_speaker, speaker_state=g.speaker_state,
                speech_ratio=g.speech_ratio, snr=g.snr, c50=g.c50, dnsmos_ovrl=g.dnsmos_ovrl,
                dnsmos_p808=g.dnsmos_p808, repeated_overlap=g.repeated_overlap or 0.0, flags=list(g.flags or []),
                review_status=g.review_status, revision=g.revision)


def audit_outs(s: Session, rows: Iterable[AuditLog]) -> list[S.AuditOut]:
    rows = list(rows)
    refs = user_refs(s, {r.user_id for r in rows})
    return [S.AuditOut(id=r.id, at=r.created_at, user=refs.get(r.user_id), user_id=r.user_id,
                       username=refs[r.user_id].username if r.user_id in refs else None, action=r.action,
                       entity_type=r.entity_type, entity_id=r.entity_id, before=r.before, after=r.after,
                       created_at=r.created_at) for r in rows]


class _Ref(S.UserRef):
    username: str = ""


def user_refs(s: Session, ids) -> dict:  # noqa: ANN001
    """{user_id: UserRef(id, display_name)} (display_name falls back to username)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: _Ref(id=u.id, display_name=u.display_name or u.username, username=u.username)
            for u in s.execute(select(User.id, User.display_name, User.username).where(User.id.in_(ids))).all()}


def user_ref(s: Session, user_id) -> S.UserRef | None:  # noqa: ANN001
    r = user_refs(s, {user_id}).get(user_id)
    return S.UserRef(id=r.id, display_name=r.display_name) if r else None
