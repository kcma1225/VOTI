"""/api/jobs and /api/stats/overview, /api/healthz."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import (Asset, Channel, ClusterRun, Job, Media, MediaQuality, Segment, Speaker, User, utcnow)
from ..queue import KINDS, redis_client, requeue
from . import schemas as S
from .errors import ApiError
from .views import DOWNLOADED_STATES, FAILED_STATES, Paging, get_or_404, job_out, job_outs, paging, split_csv

router = APIRouter(tags=["jobs"])

JOB_STATUSES = ("queued", "running", "completed", "failed", "blocked", "cancelled", "interrupted")
RETRYABLE = ("failed", "blocked", "interrupted", "cancelled")


@router.get("/jobs", response_model=S.JobList)
def list_jobs(status: str | None = None, kind: str | None = None, media_id: uuid.UUID | None = None,
              channel_id: uuid.UUID | None = None, page: Paging = Depends(paging), _: User = Depends(current_user),
              s: Session = Depends(get_session)):
    base: list = []
    if kind:
        base.append(Job.kind.in_(split_csv(kind)))
    if media_id:
        base.append(Job.media_id == media_id)
    if channel_id:
        base.append(Job.channel_id == channel_id)
    cond = list(base)
    if status:
        cond.append(Job.status.in_(split_csv(status)))
    total = s.scalar(select(func.count()).select_from(Job).where(*cond)) or 0
    rows = s.scalars(select(Job).where(*cond).order_by(Job.created_at.desc(), Job.id)
                     .limit(page.limit).offset(page.offset)).all()
    counts = {st: 0 for st in JOB_STATUSES}
    counts.update(dict(s.execute(select(Job.status, func.count()).where(*base).group_by(Job.status)).all()))
    return S.JobList(items=job_outs(s, rows), total=total, counts=counts)


@router.get("/jobs/{job_id}", response_model=S.JobOut)
def get_job(job_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    return job_out(s, get_or_404(s, Job, job_id, "工作"))


@router.post("/jobs/{job_id}/retry", response_model=S.JobOut)
def retry_job(job_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    job = get_or_404(s, Job, job_id, "工作")
    if job.status not in RETRYABLE:
        raise ApiError(409, "not_retryable", "只有失敗、受阻、中斷或已取消的工作可以重試")
    if job.media_id and s.scalar(select(Job.id).where(Job.kind == job.kind, Job.media_id == job.media_id,
                                                      Job.status.in_(("queued", "running")), Job.id != job.id)):
        raise ApiError(409, "job_active", "同類型的工作已在執行中")
    requeue(s, job)
    if job.media_id and job.kind.startswith("media."):
        m = s.get(Media, job.media_id)
        if m is not None and m.status in FAILED_STATES:
            m.status = "queued" if job.kind == "media.download" else "processing"
            m.failure_code = m.failure_message = None
    s.commit()
    return job_out(s, job)


@router.post("/jobs/{job_id}/cancel", response_model=S.JobOut)
def cancel_job(job_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise ApiError(404, "not_found", "找不到工作")
    if job.status == "queued":
        job.status, job.finished_at, job.error_code, job.error_message = "cancelled", utcnow(), "cancelled", "使用者取消"
        if job.kind == "media.download" and job.media_id:
            m = s.get(Media, job.media_id)
            if m is not None and m.status == "queued":
                m.status = "detailed" if m.detailed_at else "listed"
    elif job.status == "running":
        job.cancel_requested = True
    else:
        raise ApiError(409, "not_cancellable", "這個工作已經結束")
    s.commit()
    return job_out(s, job)


# ───────────────────────────── stats ─────────────────────────────

WORKER_KEY_TTL = 120  # voti.queue.worker_loop refreshes voti:worker:<name> with ex=120 about every 60 s


def _workers(s: Session) -> list[S.WorkerOut]:
    """Workers announced in Redis (alive) plus workers that still own running jobs but stopped announcing."""
    now = utcnow()
    seen: dict[str, S.WorkerOut] = {}
    try:
        r = redis_client()
        for key in r.scan_iter(match="voti:worker:*", count=200):
            pipe = r.pipeline()
            pipe.get(key)
            pipe.ttl(key)
            val, ttl = pipe.execute()
            if val is None:
                continue
            name = key.decode()[len("voti:worker:"):]
            last = now - timedelta(seconds=max(0, WORKER_KEY_TTL - ttl)) if ttl and ttl > 0 else now
            seen[name] = S.WorkerOut(name=name, queues=[q for q in val.decode().split(",") if q], alive=True,
                                     last_seen_at=last)
    except Exception:  # noqa: BLE001  redis down: fall back to the job table only
        pass
    running = s.execute(select(Job.id, Job.kind, Job.queue, Job.worker, Job.heartbeat_at, Media.title)
                        .outerjoin(Media, Media.id == Job.media_id)
                        .where(Job.status == "running", Job.worker.isnot(None))
                        .order_by(Job.started_at)).all()
    for j in running:
        w = seen.get(j.worker)
        if w is None:
            w = seen[j.worker] = S.WorkerOut(name=j.worker, queues=[j.queue], alive=False, last_seen_at=j.heartbeat_at)
        if w.current_job is None:
            w.current_job = S.WorkerJob(id=j.id, kind=j.kind, media_title=j.title)
    return sorted(seen.values(), key=lambda w: w.name)


@router.get("/stats/overview", response_model=S.StatsOverview)
def overview(_: User = Depends(current_user), s: Session = Depends(get_session)):
    dur = func.coalesce(Media.duration_sec, 0.0)
    m = s.execute(select(func.count(), func.count(Media.detailed_at),
                         func.count().filter(Media.status.in_(DOWNLOADED_STATES)),
                         func.count().filter(Media.status == "ready"),
                         func.count().filter(Media.status.in_(FAILED_STATES)),
                         func.coalesce(func.sum(dur), 0.0),
                         func.coalesce(func.sum(dur).filter(Media.status.in_(DOWNLOADED_STATES)), 0.0))).one()
    speech = s.scalar(select(func.coalesce(func.sum(MediaQuality.speech_sec), 0.0))) or 0.0
    sd = Segment.end_sec - Segment.start_sec
    g = s.execute(select(func.count(), func.count().filter(Segment.review_status == "passed"),
                         func.count().filter(Segment.review_status == "needs_fix"),
                         func.coalesce(func.sum(sd), 0.0),
                         func.coalesce(func.sum(sd).filter(Segment.review_status == "passed"), 0.0))
                  .where(Segment.active.is_(True))).one()
    run = s.scalar(select(ClusterRun.id).where(ClusterRun.is_current.is_(True)).order_by(ClusterRun.created_at.desc())
                   .limit(1))
    sp = (0, 0)
    if run:
        sp = s.execute(select(func.count(), func.count().filter(Speaker.verified.is_(True)))
                       .where(Speaker.run_id == run)).one()
    depth = {q: 0 for q in sorted(set(KINDS.values()))}
    depth.update(dict(s.execute(select(Job.queue, func.count()).where(Job.status == "queued")
                                .group_by(Job.queue)).all()))
    return S.StatsOverview(
        channels=s.scalar(select(func.count()).select_from(Channel)) or 0,
        media={"listed": m[0], "detailed": m[1], "downloaded": m[2], "ready": m[3], "failed": m[4]},
        hours={"listed": round(m[5] / 3600, 3), "downloaded": round(m[6] / 3600, 3), "speech": round(speech / 3600, 3),
               "segments": round(g[3] / 3600, 3), "reviewed": round(g[4] / 3600, 3)},
        segments={"total": g[0], "passed": g[1], "needs_fix": g[2]},
        speakers={"candidates": sp[0], "verified": sp[1]},
        storage_bytes=int(s.scalar(select(func.coalesce(func.sum(Asset.bytes), 0))) or 0),
        workers=_workers(s), queue_depth=depth)


@router.get("/healthz", response_model=S.Ok, tags=["health"])
def healthz():
    return S.Ok()
