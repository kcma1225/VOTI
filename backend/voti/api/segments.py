"""/api/segments: list/filter, detail, review edits (audited), speaker override, similar (pgvector)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from ..auth import current_user
from ..db import get_session
from ..models import (AuditLog, Embedding, Media, Segment, SpeakerAssignment, SpeakerOverride, User,
                      VoiceProfile, utcnow)
from ..queue import enqueue
from . import schemas as S
from .errors import ApiError
from .views import Paging, audit, audit_outs, get_or_404, paging, segment_summaries, split_csv, user_ref

router = APIRouter(prefix="/segments", tags=["segments"])

_duration = Segment.end_sec - Segment.start_sec
SEGMENT_SORTS = {
    "start": (Segment.media_id, Segment.start_sec), "media": (Segment.media_id, Segment.start_sec),
    "-created_at": (Segment.created_at.desc(),), "created_at": (Segment.created_at,),
    "-updated_at": (Segment.updated_at.desc(),),
    "snr": (Segment.snr.asc().nulls_last(),), "-snr": (Segment.snr.desc().nulls_last(),),
    "dnsmos": (Segment.dnsmos_ovrl.asc().nulls_last(),), "-dnsmos": (Segment.dnsmos_ovrl.desc().nulls_last(),),
    "duration": (_duration.asc(),), "-duration": (_duration.desc(),),
    "speech_ratio": (Segment.speech_ratio.asc().nulls_last(),),
    "-speech_ratio": (Segment.speech_ratio.desc().nulls_last(),),
}


def jsonb_has_any(col, values: list[str]):  # noqa: ANN001, ANN201
    return col.op("?|")(cast(values, ARRAY(Text)))


@router.get("", response_model=S.Page[S.SegmentSummary])
def list_segments(media_id: uuid.UUID | None = None, channel_id: uuid.UUID | None = None,
                  speaker_id: uuid.UUID | None = None, review_status: str | None = None,
                  text_source: str | None = None, q: str | None = None, min_snr: float | None = None,
                  min_dnsmos: float | None = None, min_speech_ratio: float | None = None,
                  flags: str | None = Query(None, description="comma list, any-of"),
                  exclude_flags: str | None = Query(None, description="comma list"),
                  has_profile: bool | None = None, sort: str = "start", page: Paging = Depends(paging),
                  _: User = Depends(current_user), s: Session = Depends(get_session)):
    if sort not in SEGMENT_SORTS:
        raise ApiError(422, "invalid_sort", f"sort 必須是 {', '.join(SEGMENT_SORTS)} 之一")
    c: list = [Segment.active.is_(True)]
    if media_id:
        c.append(Segment.media_id == media_id)
    if channel_id:
        c.append(Segment.media_id.in_(select(Media.id).where(Media.channel_id == channel_id)))
    if speaker_id:
        c.append(exists().where(SpeakerAssignment.segment_id == Segment.id,
                                SpeakerAssignment.speaker_id == speaker_id))
    if review_status:
        c.append(Segment.review_status.in_(split_csv(review_status)))
    if text_source:
        c.append(Segment.text_source.in_(split_csv(text_source)))
    if q:
        pat = f"%{q.strip()}%"
        c.append(or_(Segment.original_text.ilike(pat), Segment.corrected_text.ilike(pat)))
    if min_snr is not None:
        c.append(Segment.snr >= min_snr)
    if min_dnsmos is not None:
        c.append(Segment.dnsmos_ovrl >= min_dnsmos)
    if min_speech_ratio is not None:
        c.append(Segment.speech_ratio >= min_speech_ratio)
    if flags:
        c.append(jsonb_has_any(Segment.flags, split_csv(flags)))
    if exclude_flags:
        c.append(~jsonb_has_any(Segment.flags, split_csv(exclude_flags)))
    if has_profile is not None:
        e = exists().where(VoiceProfile.segment_id == Segment.id, VoiceProfile.revision == Segment.revision)
        c.append(e if has_profile else ~e)
    total = s.scalar(select(func.count()).select_from(Segment).where(*c)) or 0
    rows = s.scalars(select(Segment).where(*c).order_by(*SEGMENT_SORTS[sort], Segment.id)
                     .limit(page.limit).offset(page.offset)).all()
    return S.Page[S.SegmentSummary](items=segment_summaries(s, rows), total=total)


def segment_detail(s: Session, g: Segment) -> S.SegmentDetail:
    summary = segment_summaries(s, [g])[0]
    vp = s.scalar(select(VoiceProfile).where(VoiceProfile.segment_id == g.id, VoiceProfile.revision == g.revision)
                  .order_by(VoiceProfile.created_at.desc()).limit(1))
    history = s.scalars(select(AuditLog).where(AuditLog.entity_type == "segment", AuditLog.entity_id == str(g.id))
                        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(100)).all()
    data = summary.model_dump(exclude={"profile"})
    return S.SegmentDetail(**data, track_id=g.track_id, cue_ids=g.cue_ids or [], dnsmos_sig=g.dnsmos_sig,
                           dnsmos_bak=g.dnsmos_bak, clipping_ratio=g.clipping_ratio, lufs=g.lufs,
                           review_note=g.review_note or "", reviewed_by=user_ref(s, g.reviewed_by), reviewed_at=g.reviewed_at,
                           profile=S.VoiceProfileOut.model_validate(vp) if vp else None,
                           history=audit_outs(s, history))


@router.get("/{segment_id}", response_model=S.SegmentDetail)
def get_segment(segment_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    return segment_detail(s, get_or_404(s, Segment, segment_id, "音段"))


EDITABLE = ("corrected_text", "review_status", "review_note", "speaker_state", "start_sec", "end_sec")


@router.patch("/{segment_id}", response_model=S.SegmentDetail)
def patch_segment(segment_id: uuid.UUID, body: S.SegmentPatchIn, user: User = Depends(current_user),
                  s: Session = Depends(get_session)):
    g = s.scalar(select(Segment).where(Segment.id == segment_id).with_for_update())
    if g is None:
        raise ApiError(404, "not_found", "找不到音段")
    changes = body.model_dump(exclude_unset=True)
    if "start_sec" in changes and changes["start_sec"] is None or "end_sec" in changes and changes["end_sec"] is None:
        raise ApiError(422, "invalid_boundary", "邊界不可為空")
    if "review_status" in changes and changes["review_status"] is None:
        raise ApiError(422, "invalid_review_status", "review_status 不可為空")
    if "speaker_state" in changes and changes["speaker_state"] is None:
        raise ApiError(422, "invalid_speaker_state", "speaker_state 不可為空")
    if changes.get("review_note") is None:
        changes.pop("review_note", None)
    before = {k: getattr(g, k) for k in changes}
    changes = {k: v for k, v in changes.items() if before[k] != v}
    if not changes:
        return segment_detail(s, g)
    before = {k: before[k] for k in changes}

    boundary = "start_sec" in changes or "end_sec" in changes
    if boundary:
        start = changes.get("start_sec", g.start_sec)
        end = changes.get("end_sec", g.end_sec)
        duration = s.scalar(select(Media.duration_sec).where(Media.id == g.media_id))
        if end - start < 0.1:
            raise ApiError(422, "invalid_boundary", "結束時間必須晚於開始時間至少 0.1 秒")
        if duration and end > duration + 0.05:
            raise ApiError(422, "invalid_boundary", "結束時間超過影片長度")
        if end - start > 120:
            raise ApiError(422, "invalid_boundary", "音段不可超過 120 秒")
    for k, v in changes.items():
        setattr(g, k, v)
    after = dict(changes)
    if boundary:
        g.revision = (g.revision or 1) + 1
        if "boundary_adjusted" not in (g.flags or []):
            g.flags = list(g.flags or []) + ["boundary_adjusted"]
        after["revision"] = g.revision
        before["revision"] = g.revision - 1
        # embeddings/profiles of the old revision are no longer used; recompute lazily (missing ones only)
        lazy = {"only_missing": True, "chain": False, "reason": "boundary_edit"}
        enqueue(s, "media.embed", media_id=g.media_id, payload=lazy, created_by=user.id)
        enqueue(s, "media.profile", media_id=g.media_id, payload=lazy, created_by=user.id)
    if "review_status" in changes or "corrected_text" in changes:
        g.reviewed_by, g.reviewed_at = user.id, utcnow()
    audit(s, user, "segment.review", "segment", g.id, before=before, after=after)
    s.commit()
    return segment_detail(s, g)


@router.put("/{segment_id}/speaker", response_model=S.SegmentDetail)
def set_speaker_override(segment_id: uuid.UUID, body: S.SpeakerOverrideIn, user: User = Depends(current_user),
                         s: Session = Depends(get_session)):
    g = get_or_404(s, Segment, segment_id, "音段")
    ov = s.get(SpeakerOverride, g.id)
    before = {"speaker_name": ov.speaker_name} if ov else None
    name = body.speaker_name.strip()
    if ov is None:
        ov = SpeakerOverride(segment_id=g.id, speaker_name=name)
        s.add(ov)
    ov.speaker_name, ov.updated_by, ov.updated_at = name, user.id, utcnow()
    audit(s, user, "segment.speaker_override", "segment", g.id, before=before, after={"speaker_name": name})
    s.commit()
    return segment_detail(s, g)


@router.delete("/{segment_id}/speaker", status_code=204, response_class=Response)
def delete_speaker_override(segment_id: uuid.UUID, user: User = Depends(current_user),
                            s: Session = Depends(get_session)):
    g = get_or_404(s, Segment, segment_id, "音段")
    ov = s.get(SpeakerOverride, g.id)
    if ov is not None:
        audit(s, user, "segment.speaker_override_delete", "segment", g.id, before={"speaker_name": ov.speaker_name})
        s.delete(ov)
        s.commit()
    return Response(status_code=204)


@router.get("/{segment_id}/similar", response_model=list[S.SimilarOut])
def similar(segment_id: uuid.UUID, k: int = Query(10, ge=1, le=100), _: User = Depends(current_user),
            s: Session = Depends(get_session)):
    g = get_or_404(s, Segment, segment_id, "音段")
    emb = s.scalar(select(Embedding).where(Embedding.segment_id == g.id, Embedding.revision == g.revision)
                   .order_by(Embedding.created_at.desc()).limit(1))
    if emb is None:
        raise ApiError(409, "no_embedding", "這個音段尚未計算語者向量")
    dist = Embedding.vector.cosine_distance(emb.vector).label("dist")
    rows = s.execute(select(Segment, dist).join(Embedding, and_(Embedding.segment_id == Segment.id,
                                                                Embedding.revision == Segment.revision))
                     .where(Embedding.model_id == emb.model_id, Segment.id != g.id, Segment.active.is_(True))
                     .order_by(dist).limit(k)).all()
    summaries = segment_summaries(s, [r[0] for r in rows])
    return [S.SimilarOut(segment=sm, similarity=round(1.0 - float(r[1]), 4)) for sm, r in zip(summaries, rows)]

