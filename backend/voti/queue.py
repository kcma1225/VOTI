"""Job queue: Postgres `jobs` is the source of truth, Redis lists carry job ids for fast wake-up.

Producer:  enqueue(s, kind, media_id=..., payload=...)  → Job (deduplicated per kind+media while active)
Worker:    python -m voti.workers.run --queues fetch,audio
Handler:   @handler("media.download") def run(ctx: JobContext) -> dict | None

Inside a handler use ctx.progress(fraction, phase, message) — it also raises Cancelled when the user
cancelled the job — and raise JobError(code, message, blocked=True/False) for classified failures.
A handler may call ctx.then(kind, payload) to enqueue follow-up jobs when it completes successfully.

Queues
  fetch : youtube listing / detail / download (rate limited, few workers)
  audio : normalize, probe, bandwidth, loudness, silero VAD, segmentation (CPU)
  ml    : brouhaha+dnsmos quality, diarization, embedding, vox-profile, fingerprint dedup, clustering (GPU if any)
  export: dataset exports
"""
from __future__ import annotations

import logging
import os
import socket
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_config
from .db import session_scope
from .models import Job, utcnow

log = logging.getLogger("voti.queue")

# kind → queue. Every job kind used anywhere must be registered here.
KINDS: dict[str, str] = {
    "source.resolve": "fetch",        # payload {url}: classify URL; create channel/playlist/media rows; chain listing
    "channel.list": "fetch",          # channel_id: list videos + shorts tabs (flat), upsert media rows
    "playlist.list": "fetch",         # payload {playlist_id}
    "media.detail": "fetch",          # media_id: full metadata + caption availability (+ info.json asset)
    "media.download": "fetch",        # media_id, payload {caption_langs:[...], include_auto:bool}
    "media.normalize": "audio",       # → normalized.wav, ffprobe, bandwidth, loudness, clipping
    "media.quality": "ml",            # brouhaha (legacy-ml) + silero + dnsmos → media_quality, speech_regions
    "media.diarize": "ml",            # pyannote → diarization_turns
    "media.segment": "audio",         # cues + VAD + diarization → segments with quality snapshot
    "media.embed": "ml",              # ECAPA → embeddings
    "media.profile": "ml",            # Vox-Profile (legacy-ml) → voice_profiles
    "media.fingerprint": "ml",        # per-media fingerprint asset; then channel.dedup
    "channel.dedup": "ml",            # cross-media repeated regions within a channel
    "speakers.cluster": "ml",         # payload {threshold, scope}
    "dataset.export": "export",       # payload {export_id}
}

# Pipeline order after download; each step enqueues the next on success (see pipeline.chain).
PIPELINE = ["media.normalize", "media.quality", "media.diarize", "media.segment", "media.embed",
            "media.profile", "media.fingerprint"]

STALE_AFTER = timedelta(minutes=10)  # running job without heartbeat → interrupted


class JobError(Exception):
    def __init__(self, code: str, message: str, blocked: bool = False, retryable: bool = True):
        super().__init__(message)
        self.code, self.message, self.blocked, self.retryable = code, message, blocked, retryable


class Cancelled(Exception):
    pass


@lru_cache
def redis_client() -> redis.Redis:
    return redis.Redis.from_url(get_config().redis_url)


def _qkey(queue: str) -> str:
    return f"voti:q:{queue}"


def enqueue(s: Session, kind: str, *, media_id: uuid.UUID | None = None, channel_id: uuid.UUID | None = None,
            payload: dict | None = None, created_by: uuid.UUID | None = None, parent_id: uuid.UUID | None = None,
            priority: int = 0) -> Job:
    """Insert a job (or return the active one for the same kind+media) and push its id after commit."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind}")
    if media_id is not None:
        existing = s.scalar(select(Job).where(Job.kind == kind, Job.media_id == media_id,
                                              Job.status.in_(("queued", "running"))))
        if existing:
            return existing
    job = Job(kind=kind, queue=KINDS[kind], media_id=media_id, channel_id=channel_id, payload=payload or {},
              created_by=created_by, parent_id=parent_id, priority=priority)
    try:
        with s.begin_nested():
            s.add(job)
            s.flush()
    except IntegrityError:  # raced with another producer
        return s.scalar(select(Job).where(Job.kind == kind, Job.media_id == media_id,
                                          Job.status.in_(("queued", "running"))))
    _push_after_commit(s, job)
    return job


def _push_after_commit(s: Session, job: Job) -> None:
    from sqlalchemy import event

    job_id, queue = str(job.id), job.queue

    @event.listens_for(s, "after_commit", once=True)
    def _push(_session):  # noqa: ANN001
        try:
            redis_client().lpush(_qkey(queue), job_id)
        except redis.RedisError:
            log.warning("redis push failed for %s; the sweeper will pick it up", job_id)


def requeue(s: Session, job: Job) -> None:
    """Reset a finished/failed job back to queued (retry button)."""
    job.status, job.error_code, job.error_message = "queued", None, None
    job.progress, job.phase, job.message, job.cancel_requested = 0.0, "", "", False
    job.started_at = job.finished_at = job.heartbeat_at = None
    _push_after_commit(s, job)


# ───────────────────────────── worker side ─────────────────────────────

HANDLERS: dict[str, Callable[["JobContext"], dict | None]] = {}


def handler(kind: str):
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind}")

    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


@dataclass
class JobContext:
    job_id: uuid.UUID
    kind: str
    media_id: uuid.UUID | None
    channel_id: uuid.UUID | None
    payload: dict
    created_by: uuid.UUID | None
    _followups: list[tuple[str, dict, dict]] = field(default_factory=list)
    _last_beat: float = 0.0

    def progress(self, fraction: float | None = None, phase: str | None = None, message: str | None = None,
                 force: bool = False) -> None:
        """Throttled (≤1/s) progress write + cancellation check."""
        now = time.monotonic()
        if not force and now - self._last_beat < 1.0 and fraction not in (0.0, 1.0):
            return
        self._last_beat = now
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job.cancel_requested:
                raise Cancelled()
            if fraction is not None:
                job.progress = max(0.0, min(1.0, fraction))
            if phase is not None:
                job.phase = phase
            if message is not None:
                job.message = message
            job.heartbeat_at = utcnow()

    def then(self, kind: str, payload: dict | None = None, **ids) -> None:
        """Enqueue a follow-up job after this one completes. ids: media_id=..., channel_id=..."""
        self._followups.append((kind, payload or {}, ids))


def _claim(job_id: str, worker: str) -> JobContext | None:
    with session_scope() as s:
        row = s.execute(update(Job).where(Job.id == uuid.UUID(job_id), Job.status == "queued")
                        .values(status="running", started_at=utcnow(), heartbeat_at=utcnow(),
                                attempts=Job.attempts + 1, worker=worker)
                        .returning(Job.id, Job.kind, Job.media_id, Job.channel_id, Job.payload, Job.created_by)).first()
        if row is None:
            return None
        return JobContext(job_id=row.id, kind=row.kind, media_id=row.media_id, channel_id=row.channel_id,
                          payload=row.payload or {}, created_by=row.created_by)


def _finish(ctx: JobContext, status: str, result: dict | None = None, code: str | None = None,
            message: str | None = None) -> None:
    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        job.status, job.finished_at = status, utcnow()
        if result is not None:
            job.result = result
        if status == "completed":
            job.progress = 1.0
            for kind, payload, ids in ctx._followups:
                enqueue(s, kind, payload=payload, parent_id=ctx.job_id, created_by=ctx.created_by, **ids)
        job.error_code, job.error_message = code, message


def run_one(job_id: str, worker: str) -> None:
    ctx = _claim(job_id, worker)
    if ctx is None:
        return
    fn = HANDLERS.get(ctx.kind)
    if fn is None:
        _finish(ctx, "failed", code="no_handler", message=f"no handler for {ctx.kind} on this worker")
        return
    log.info("start %s %s media=%s", ctx.kind, ctx.job_id, ctx.media_id)
    try:
        result = fn(ctx)
        _finish(ctx, "completed", result=result or {})
    except Cancelled:
        _finish(ctx, "cancelled", code="cancelled", message="使用者取消")
    except JobError as e:
        _finish(ctx, "blocked" if e.blocked else "failed", code=e.code, message=e.message)
        _on_media_failure(ctx, e.code, e.message, blocked=e.blocked)
    except Exception as e:  # noqa: BLE001
        log.exception("job %s crashed", ctx.job_id)
        _finish(ctx, "failed", code="exception", message=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}")
        _on_media_failure(ctx, "exception", f"{type(e).__name__}: {e}", blocked=False)


def _on_media_failure(ctx: JobContext, code: str, message: str, blocked: bool) -> None:
    if ctx.media_id is None or not ctx.kind.startswith("media."):
        return
    from .models import Media
    with session_scope() as s:
        m = s.get(Media, ctx.media_id)
        if m is not None:
            m.status, m.failure_code, m.failure_message = ("blocked" if blocked else "failed"), code, message[:2000]


def sweep(queues: list[str]) -> None:
    """Mark stale running jobs interrupted and re-push queued jobs Redis may have lost."""
    cutoff = utcnow() - STALE_AFTER
    with session_scope() as s:
        s.execute(update(Job).where(Job.status == "running", Job.queue.in_(queues), Job.heartbeat_at < cutoff)
                  .values(status="interrupted", error_code="interrupted", error_message="worker 中斷",
                          finished_at=utcnow()))
        old = utcnow() - timedelta(minutes=2)
        ids = s.scalars(select(Job.id).where(Job.status == "queued", Job.queue.in_(queues), Job.created_at < old)
                        .order_by(Job.priority.desc(), Job.created_at).limit(200)).all()
    r = redis_client()
    for q in queues:
        pending = {x.decode() for x in r.lrange(_qkey(q), 0, -1)}
        for jid in ids:
            if str(jid) not in pending:
                r.rpush(_qkey(q), str(jid))


def worker_loop(queues: list[str]) -> None:
    name = f"{socket.gethostname()}:{os.getpid()}"
    r = redis_client()
    keys = [_qkey(q) for q in queues]
    last_sweep = 0.0
    log.info("worker %s listening on %s", name, queues)
    while True:
        if time.monotonic() - last_sweep > 60:
            try:
                sweep(queues)
            except Exception:  # noqa: BLE001
                log.exception("sweep failed")
            last_sweep = time.monotonic()
            r.set(f"voti:worker:{name}", ",".join(queues), ex=120)
        item = r.brpop(keys, timeout=5)
        if item is None:
            continue
        run_one(item[1].decode(), name)
