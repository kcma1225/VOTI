"""/api/speakers: clustering runs, candidate speakers, diversity overview."""
from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Channel, ClusterRun, Job, Media, Segment, Speaker, SpeakerAssignment, User
from ..queue import enqueue
from . import schemas as S
from .errors import ApiError
from .views import Paging, audit, current_run_id, get_or_404, job_out, paging, segment_summaries

router = APIRouter(prefix="/speakers", tags=["speakers"])

SPEAKER_SORTS = {
    "-total_sec": Speaker.total_sec.desc(), "total_sec": Speaker.total_sec.asc(),
    "-segment_count": Speaker.segment_count.desc(), "segment_count": Speaker.segment_count.asc(),
    "-media_count": Speaker.media_count.desc(), "media_count": Speaker.media_count.asc(),
    "label": Speaker.label.asc(), "-label": Speaker.label.desc(),
}


@router.post("/cluster", response_model=S.JobOut)
def cluster(body: S.ClusterIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    active = s.scalar(select(Job).where(Job.kind == "speakers.cluster", Job.status.in_(("queued", "running"))))
    if active:
        raise ApiError(409, "job_active", "已有分群工作在執行中")
    scope = {}
    if body.scope.get("channel_ids"):
        try:
            scope["channel_ids"] = [str(uuid.UUID(str(c))) for c in body.scope["channel_ids"]]
        except ValueError:
            raise ApiError(422, "invalid_scope", "channel_ids 格式錯誤") from None
    job = enqueue(s, "speakers.cluster", payload={"threshold": body.threshold, "scope": scope}, created_by=user.id)
    s.commit()
    return job_out(s, job)


@router.get("/runs", response_model=list[S.ClusterRunOut])
def runs(_: User = Depends(current_user), s: Session = Depends(get_session)):
    return [S.ClusterRunOut.model_validate(r)
            for r in s.scalars(select(ClusterRun).order_by(ClusterRun.created_at.desc()).limit(200))]


def _resolve_run(s: Session, run_id: uuid.UUID | None) -> uuid.UUID | None:
    if run_id:
        get_or_404(s, ClusterRun, run_id, "分群執行")
        return run_id
    return current_run_id(s)


@router.get("", response_model=S.Page[S.SpeakerOut])
def list_speakers(run_id: uuid.UUID | None = None, channel_id: uuid.UUID | None = None, q: str | None = None,
                  sort: str = "-total_sec",
                  page: Paging = Depends(paging), _: User = Depends(current_user), s: Session = Depends(get_session)):
    if sort not in SPEAKER_SORTS:
        raise ApiError(422, "invalid_sort", f"sort 必須是 {', '.join(SPEAKER_SORTS)} 之一")
    rid = _resolve_run(s, run_id)
    if rid is None:
        return S.Page[S.SpeakerOut](items=[], total=0)
    c = [Speaker.run_id == rid]
    if channel_id:
        c.append(Speaker.channel_ids.contains([str(channel_id)]))
    if q:
        pat = f"%{q.strip()}%"
        c.append(or_(Speaker.label.ilike(pat), Speaker.display_name.ilike(pat)))
    total = s.scalar(select(func.count()).select_from(Speaker).where(*c)) or 0
    rows = s.scalars(select(Speaker).where(*c).order_by(SPEAKER_SORTS[sort], Speaker.id)
                     .limit(page.limit).offset(page.offset)).all()
    return S.Page[S.SpeakerOut](items=[S.SpeakerOut.model_validate(r) for r in rows], total=total)


# ───────────────────────────── diversity ─────────────────────────────

def gini(values: list[float]) -> float:
    xs = sorted(v for v in values if v > 0)
    n = len(xs)
    if n == 0:
        return 0.0
    total = sum(xs)
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return round((2 * cum) / (n * total) - (n + 1) / n, 4)


def _dist(profile_part, keys_hint: tuple[str, ...] = ()) -> dict[str, float]:  # noqa: ANN001
    """Normalize a profile distribution: {label: p} or {"probs": {...}} or a bare label string."""
    if isinstance(profile_part, str):
        return {profile_part: 1.0}
    if not isinstance(profile_part, dict):
        return {}
    if isinstance(profile_part.get("probs"), dict):
        profile_part = profile_part["probs"]
    return {str(k): float(v) for k, v in profile_part.items() if isinstance(v, (int, float))}


def _spread(acc: dict[str, float], dist: dict[str, float], seconds: float) -> None:
    total_p = sum(p for p in dist.values() if p > 0)
    if total_p <= 0:
        acc["unknown"] = acc.get("unknown", 0.0) + seconds
        return
    scale = 1.0 / total_p if total_p > 1.0 + 1e-6 else 1.0
    for k, p in dist.items():
        if p > 0:
            acc[k] = acc.get(k, 0.0) + seconds * p * scale
    rest = 1.0 - min(1.0, total_p)
    if rest > 1e-6:
        acc["unknown"] = acc.get("unknown", 0.0) + seconds * rest


@router.get("/diversity", response_model=S.DiversityOut)
def diversity(channel_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None, _: User = Depends(current_user),
              s: Session = Depends(get_session)):
    rid = _resolve_run(s, run_id)
    empty = S.DiversityOut(run_id=rid, by_sex={"male": 0.0, "female": 0.0, "unknown": 0.0}, by_age_group={},
                           by_emotion={}, by_channel=[], speaker_hours=[], gini=0.0)
    if rid is None:
        return empty
    speakers = {sp.id: sp for sp in s.scalars(select(Speaker).where(Speaker.run_id == rid))}
    # seconds per (speaker, channel) from assignments of this run
    dur = Segment.end_sec - Segment.start_sec
    per: dict = defaultdict(float)
    q = (select(SpeakerAssignment.speaker_id, Media.channel_id, func.sum(dur))
         .join(Segment, Segment.id == SpeakerAssignment.segment_id).join(Media, Media.id == Segment.media_id)
         .where(SpeakerAssignment.run_id == rid, Segment.active.is_(True), Segment.validity != "invalid")
         .group_by(SpeakerAssignment.speaker_id, Media.channel_id))
    if channel_id:
        q = q.where(Media.channel_id == channel_id)
    for sp_id, ch_id, sec in s.execute(q).all():
        per[(sp_id, ch_id)] += float(sec or 0)
    # from the assignments (not Speaker.total_sec): segments marked invalid after clustering no longer count
    sec_by_speaker: dict = defaultdict(float)
    for (sp_id, _ch), sec in per.items():
        sec_by_speaker[sp_id] += sec
    by_sex: dict[str, float] = {"male": 0.0, "female": 0.0, "unknown": 0.0}
    by_age: dict[str, float] = {}
    by_emotion: dict[str, float] = {}
    for sp_id, sec in sec_by_speaker.items():
        sp = speakers.get(sp_id)
        if sp is None or sec <= 0:
            continue
        prof = sp.profile or {}
        _spread(by_sex, _dist(prof.get("sex")), sec)
        _spread(by_age, _dist(prof.get("age_group")), sec)
        _spread(by_emotion, _dist(prof.get("emotion")), sec)
    ch_ids = {ch for (_sp, ch) in per}
    titles = dict(s.execute(select(Channel.id, Channel.title).where(Channel.id.in_([c for c in ch_ids if c]))).all()) \
        if ch_ids else {}
    by_channel = []
    for ch in ch_ids:
        secs = [sec for (sp, c), sec in per.items() if c == ch and sec > 0]
        tot = sum(secs)
        by_channel.append(S.DiversityChannel(channel_id=ch, title=titles.get(ch, "") if ch else "（無頻道）",
                                             speakers=len(secs), total_sec=round(tot, 3),
                                             top_speaker_share=round(max(secs) / tot, 4) if tot > 0 else 0.0))
    by_channel.sort(key=lambda c: -c.total_sec)
    hours = sorted(((sp_id, sec) for sp_id, sec in sec_by_speaker.items() if sec > 0 and sp_id in speakers),
                   key=lambda x: -x[1])
    return S.DiversityOut(
        run_id=rid, by_sex={k: round(v, 3) for k, v in by_sex.items()},
        by_age_group={k: round(v, 3) for k, v in by_age.items()},
        by_emotion={k: round(v, 3) for k, v in by_emotion.items()}, by_channel=by_channel,
        speaker_hours=[S.SpeakerHours(speaker_id=sp_id, label=speakers[sp_id].label,
                                      display_name=speakers[sp_id].display_name, total_sec=round(sec, 3))
                       for sp_id, sec in hours],
        gini=gini([sec for _, sec in hours]))


# ───────────────────────────── single speaker ─────────────────────────────

@router.get("/{speaker_id}", response_model=S.SpeakerDetail)
def get_speaker(speaker_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    sp = get_or_404(s, Speaker, speaker_id, "語者")
    segs = s.scalars(select(Segment).join(SpeakerAssignment, SpeakerAssignment.segment_id == Segment.id)
                     .where(SpeakerAssignment.speaker_id == sp.id, Segment.active.is_(True))
                     .order_by(SpeakerAssignment.similarity.desc(), Segment.id).limit(50)).all()
    dur = Segment.end_sec - Segment.start_sec
    chans = s.execute(select(Media.channel_id, Channel.title, func.sum(dur))
                      .select_from(SpeakerAssignment).join(Segment, Segment.id == SpeakerAssignment.segment_id)
                      .join(Media, Media.id == Segment.media_id).outerjoin(Channel, Channel.id == Media.channel_id)
                      .where(SpeakerAssignment.speaker_id == sp.id, Segment.active.is_(True),
                             Segment.validity != "invalid")
                      .group_by(Media.channel_id, Channel.title).order_by(func.sum(dur).desc())).all()
    base = S.SpeakerOut.model_validate(sp).model_dump()
    return S.SpeakerDetail(**base, segments=segment_summaries(s, segs, run_id=sp.run_id, run_known=True),
                           channels=[S.SpeakerChannel(id=c, title=t or "", total_sec=round(float(sec or 0), 3))
                                     for c, t, sec in chans])


@router.patch("/{speaker_id}", response_model=S.SpeakerOut)
def patch_speaker(speaker_id: uuid.UUID, body: S.SpeakerPatchIn, user: User = Depends(current_user),
                  s: Session = Depends(get_session)):
    sp = get_or_404(s, Speaker, speaker_id, "語者")
    changes = body.model_dump(exclude_unset=True)
    if "verified" in changes and changes["verified"] is None:
        changes.pop("verified")
    before = {k: getattr(sp, k) for k in changes}
    for k, v in changes.items():
        setattr(sp, k, v.strip() if isinstance(v, str) else v)
    audit(s, user, "speaker.update", "speaker", sp.id, before=before, after={k: getattr(sp, k) for k in changes})
    s.commit()
    return S.SpeakerOut.model_validate(sp)
