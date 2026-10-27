"""Job queue: Postgres `jobs` is the source of truth, Redis lists carry job ids for fast wake-up.

Producer:  enqueue(s, kind, media_id=..., payload=...)  → Job (deduplicated per kind+media while active)
Worker:    python -m voti.workers.run --queues scan,detail
Handler:   @handler("media.download") def run(ctx: JobContext) -> dict | None

Inside a handler use ctx.progress(fraction, phase, message) — it also raises Cancelled when the user
cancelled the job (and ShutdownRequested, a Cancelled subclass, when the worker is stopping) — and raise
JobError(code, message, blocked=True/False, retryable=True/False) for classified failures.
A handler may call ctx.then(kind, payload) to enqueue follow-up jobs when it completes successfully.

Queues
  scan    : URL resolve, channel / playlist listing (several workers so one huge channel never blocks others)
  detail  : per-video metadata + caption availability (batched per channel, round-robin across channels)
  download: audio + caption downloads (own workers, so downloads run while channels are being scanned)
  audio : normalize, probe, bandwidth, loudness, silero VAD, segmentation, text conversion (CPU)
  ml    : brouhaha+dnsmos quality, diarization, alignment, embedding, vox-profile, fingerprint dedup, clustering
          (GPU if any)
  export: dataset exports

Recovery (docs/ARCHITECTURE.md "Job recovery")
  * Liveness: every worker runs a heartbeat thread that refreshes `voti:worker:<host>:<pid>` (ex=WORKER_KEY_TTL)
    and `jobs.heartbeat_at` of its running job every HEARTBEAT_SEC, independent of handler progress calls.
  * Graceful shutdown (SIGTERM/SIGINT): stop taking jobs; the running handler is interrupted at its next
    ctx.progress(); the job goes back to `queued` without counting the attempt. If the handler is stuck in a
    blocking call for SHUTDOWN_GRACE_SEC, the row is requeued anyway and the process exits.
  * Orphans: a `running` job whose heartbeat is older than STALE_AFTER, or whose worker key vanished and whose
    heartbeat is older than DEAD_WORKER_GRACE, is requeued (phase "recovered") while attempts < max_attempts,
    else marked `interrupted`. A worker starting on the same host recovers its previous life's jobs at once.
  * Transient errors (JobError.retryable, network / timeout / 5xx / S3 / DB connection errors) are retried
    after RETRY_BACKOFF_SEC delays via `jobs.run_after`; claims never take a job before its run_after.
  * Redis loss: the sweeper (and every worker start) re-pushes queued jobs that are in neither the Redis list
    nor the delayed set. Claiming is an atomic UPDATE ... WHERE status='queued', so duplicates are harmless.
"""
from __future__ import annotations

import errno
import logging
import os
import signal
import socket
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache

import redis
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_config
from .db import session_scope
from .models import Job, utcnow

log = logging.getLogger("voti.queue")

# kind → queue. Every job kind used anywhere must be registered here.
KINDS: dict[str, str] = {
    "source.resolve": "scan",        # payload {url}: classify URL; create channel/playlist/media rows; chain listing
    "channel.list": "scan",          # channel_id: list videos + shorts tabs (flat), upsert media rows
    "playlist.list": "scan",         # payload {playlist_id}
    "media.detail": "detail",          # media_id: full metadata + caption availability (+ info.json asset)
    "media.download": "download",        # media_id, payload {caption_langs:[...], include_auto:bool}
    "media.normalize": "audio",       # → normalized.wav, ffprobe, bandwidth, loudness, clipping
    "media.quality": "ml",            # brouhaha (legacy-ml) + silero + dnsmos → media_quality, speech_regions
    "media.diarize": "ml",            # pyannote → diarization_turns
    "media.ocr": "ml",                # burned-in subtitles (PP-OCR on the ocr_video asset) → Track(kind="ocr") + cues
    "media.segment": "audio",         # cues + VAD + diarization → segments with quality snapshot
    "media.align": "ml",              # MMS_FA CTC forced alignment → per-character times (word_alignments)
    "media.validate": "ml",           # caption ↔ audio check: VoxLingua107 language ID + alignment confidence
    "media.embed": "ml",              # ECAPA → embeddings
    "media.profile": "ml",            # Vox-Profile (legacy-ml) → voice_profiles
    "media.fingerprint": "ml",        # per-media fingerprint asset; then channel.dedup
    "media.convert_text": "audio",    # Simplified → Traditional (OpenCC) of existing cues/segments (voti.textconv)
    "channel.dedup": "ml",            # cross-media repeated regions within a channel
    "speakers.cluster": "ml",         # payload {threshold, scope}
    "dataset.export": "export",       # payload {export_id}
}

# Pipeline order after download; each step enqueues the next on success (see pipeline.chain).
# media.ocr runs only for media with an `ocr_video` asset (pipeline.handlers._chain skips it otherwise).
PIPELINE = ["media.normalize", "media.quality", "media.diarize", "media.ocr", "media.segment", "media.align",
            "media.validate", "media.embed", "media.profile", "media.fingerprint"]

# media.* kinds that are not pipeline steps: their failure must not change Media.status
SIDE_KINDS = {"media.convert_text"}


def is_side_job(kind: str, payload: dict | None) -> bool:
    """Side jobs never change Media.status. media.align / media.validate run outside the pipeline chain
    (re-alignment / re-validation after an edit, backfill: payload chain=false) are side jobs too, and so is a
    media.download that only re-fetches the video for OCR (payload video_only=true, POST /media/{id}/ocr)."""
    return kind in SIDE_KINDS or (kind in ("media.align", "media.validate") and (payload or {}).get("chain") is False) \
        or (kind == "media.download" and bool((payload or {}).get("video_only")))

# Failure codes that mean "not eligible" rather than "broken": media becomes `skipped`.
SKIP_CODES = {"no_usable_captions", "members_only", "empty_captions"}

# ── recovery timings ──
HEARTBEAT_SEC = 15.0                          # worker key + jobs.heartbeat_at refresh
WORKER_KEY_TTL = 45                           # voti:worker:<name> expiry (s)
STALE_AFTER = timedelta(minutes=2)            # running job without heartbeat this long → orphan
DEAD_WORKER_GRACE = timedelta(seconds=45)     # worker key gone + heartbeat older than this → orphan
SWEEP_SEC = 30.0                              # orphan recovery + Redis re-push interval
REPUSH_MIN_AGE = timedelta(seconds=10)        # periodic re-push skips jobs younger than this (producer race)
RETRY_BACKOFF_SEC = (30, 120, 600)            # delays before automatic retry 1, 2, 3+
DEFAULT_MAX_ATTEMPTS = 4                      # first run + 3 automatic retries
SHUTDOWN_GRACE_SEC = float(os.environ.get("VOTI_SHUTDOWN_GRACE_SEC", "8"))
POP_TIMEOUT_SEC = 2                           # BRPOP timeout: bounds shutdown latency while idle


class JobError(Exception):
    """Classified failure. blocked: needs a human (login, cookies, model terms) → job/media `blocked`.
    retryable: transient (network, timeout, upstream 5xx, service restarting) → automatic delayed retry while
    attempts remain; a blocked+retryable error ends `blocked` when the retries are exhausted."""

    def __init__(self, code: str, message: str, blocked: bool = False, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.blocked, self.retryable = code, message, blocked, retryable


class Cancelled(Exception):
    pass


class ShutdownRequested(Cancelled):
    """The worker is stopping: the job is put back to `queued` (attempt not counted)."""


class JobLost(Cancelled):
    """This worker no longer owns the job (recovered elsewhere / deleted): stop without touching the row."""


@lru_cache
def redis_client() -> redis.Redis:
    return redis.Redis.from_url(get_config().redis_url, socket_timeout=10, socket_connect_timeout=5,
                                health_check_interval=30)


def _qkey(queue: str) -> str:
    return f"voti:q:{queue}"


def _dkey(queue: str) -> str:
    """Sorted set of delayed job ids (score = run_after epoch seconds)."""
    return f"voti:delayed:{queue}"


def worker_key(name: str) -> str:
    return f"voti:worker:{name}"


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
              created_by=created_by, parent_id=parent_id, priority=priority, max_attempts=DEFAULT_MAX_ATTEMPTS)
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
        push(job_id, queue)


def push(job_id: str | uuid.UUID, queue: str, run_after: datetime | None = None, front: bool = False) -> bool:
    """Make a queued job visible to workers: the list when due, else the delayed set. Never raises."""
    try:
        r = redis_client()
        if run_after is not None and run_after > utcnow():
            r.zadd(_dkey(queue), {str(job_id): run_after.timestamp()})
        elif front:
            r.rpush(_qkey(queue), str(job_id))  # BRPOP pops from the right: next to run
        else:
            r.lpush(_qkey(queue), str(job_id))
        return True
    except redis.RedisError:
        log.warning("redis push failed for %s; the sweeper will pick it up", job_id)
        return False


def requeue(s: Session, job: Job) -> None:
    """Reset a finished/failed job back to queued (retry button). Attempts start over."""
    job.status, job.error_code, job.error_message = "queued", None, None
    job.progress, job.phase, job.message, job.cancel_requested = 0.0, "", "", False
    job.started_at = job.finished_at = job.heartbeat_at = job.run_after = None
    job.attempts, job.worker = 0, None
    job.max_attempts = max(job.max_attempts or 0, DEFAULT_MAX_ATTEMPTS)
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
    attempts: int = 1
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    worker: str | None = None
    queue: str = ""
    _followups: list[tuple[str, dict, dict]] = field(default_factory=list)
    _last_beat: float = 0.0
    _stop: threading.Event | None = None   # the owning worker's stop flag
    _lost: bool = False                    # set by the heartbeat thread when the row is no longer ours

    def check_interrupt(self) -> None:
        """Raise ShutdownRequested / JobLost without a DB round trip (cheap; call it in tight loops)."""
        if self._lost:
            raise JobLost()
        if self._stop is not None and self._stop.is_set():
            raise ShutdownRequested()

    def progress(self, fraction: float | None = None, phase: str | None = None, message: str | None = None,
                 force: bool = False) -> None:
        """Throttled (≤1/s) progress write + cancellation / shutdown check."""
        self.check_interrupt()
        now = time.monotonic()
        if not force and now - self._last_beat < 1.0 and fraction not in (0.0, 1.0):
            return
        self._last_beat = now
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is None or job.cancel_requested:  # None: its media/channel was deleted
                raise Cancelled()
            if self.worker is not None and (job.status != "running" or job.worker != self.worker):
                raise JobLost()
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

    def will_retry(self, exc: BaseException) -> bool:
        """Will the queue put this job back to `queued` after `exc` (shutdown or retryable error)?
        Handlers use it to keep their own entity state (e.g. DatasetExport.status) consistent."""
        if isinstance(exc, (ShutdownRequested, JobLost)) or (self._stop is not None and self._stop.is_set()):
            return True
        if isinstance(exc, Cancelled):
            return False
        _, _, _, retryable = classify(exc)
        return retryable and self.attempts < self.max_attempts


# ───────────────────────────── error classification ─────────────────────────────

_NET_ERRNOS = {errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED, errno.ETIMEDOUT, errno.EHOSTUNREACH,
               errno.ENETUNREACH, errno.ENETDOWN, errno.EPIPE, errno.EHOSTDOWN}
_S3_RETRY_CODES = {"SlowDown", "ServiceUnavailable", "InternalError", "RequestTimeout", "RequestTimeTooSkewed",
                   "Throttling", "ThrottlingException"}


def _transient_code(e: BaseException) -> str | None:
    """Code for a transient (auto-retryable) unclassified exception, or None. Walks the cause chain."""
    seen = 0
    cur: BaseException | None = e
    while cur is not None and seen < 8:
        seen += 1
        mod = type(cur).__module__ or ""
        if isinstance(cur, redis.RedisError) and isinstance(cur, (redis.ConnectionError, redis.TimeoutError)):
            return "redis_unavailable"
        if mod.startswith("sqlalchemy"):
            from sqlalchemy import exc as sa_exc
            if isinstance(cur, (sa_exc.OperationalError, sa_exc.DisconnectionError)) or (
                    isinstance(cur, sa_exc.DBAPIError) and cur.connection_invalidated):
                return "db_unavailable"
        if mod.startswith("psycopg"):
            import psycopg
            if isinstance(cur, psycopg.OperationalError):
                return "db_unavailable"
        if mod.startswith("botocore") or mod.startswith("boto3"):
            from botocore import exceptions as bexc
            if isinstance(cur, (bexc.EndpointConnectionError, bexc.ConnectionClosedError, bexc.ReadTimeoutError,
                                bexc.ConnectTimeoutError, bexc.ConnectionError)):
                return "s3_unavailable"
            if isinstance(cur, bexc.ClientError):
                err = cur.response.get("Error", {}) if hasattr(cur, "response") else {}
                status = (cur.response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode", 0) or 0
                if status >= 500 or err.get("Code") in _S3_RETRY_CODES:
                    return "s3_unavailable"
        if mod.startswith("httpx"):
            import httpx
            if isinstance(cur, httpx.TransportError):
                return "network"
            if isinstance(cur, httpx.HTTPStatusError) and cur.response.status_code >= 500:
                return "upstream_5xx"
        if mod.startswith("requests") or mod.startswith("urllib3"):
            name = type(cur).__name__
            if any(x in name for x in ("Connection", "Timeout", "Protocol", "ChunkedEncoding", "NewConnection")):
                return "network"
        if isinstance(cur, (TimeoutError, socket.timeout)):
            return "timeout"
        if isinstance(cur, ConnectionError):
            return "network"
        if isinstance(cur, OSError) and cur.errno in _NET_ERRNOS:
            return "network"
        cur = cur.__cause__ or cur.__context__
    return None


def is_transient(e: BaseException) -> bool:
    """Network / timeout / 5xx / S3 / DB connection error (anywhere in the cause chain)?"""
    return _transient_code(e) is not None


def classify(e: BaseException) -> tuple[str, str, bool, bool]:
    """→ (code, message, blocked, retryable)."""
    if isinstance(e, JobError):
        return e.code, e.message, e.blocked, e.retryable
    code = _transient_code(e)
    if code is not None:
        return code, f"暫時性錯誤 {type(e).__name__}: {e}"[:4000], False, True
    return "exception", f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}", False, False


def retry_delay(attempts: int) -> int:
    """Delay (s) before the next automatic retry after `attempts` runs."""
    return RETRY_BACKOFF_SEC[min(max(attempts, 1) - 1, len(RETRY_BACKOFF_SEC) - 1)]


def _fmt_delay(sec: float) -> str:
    return f"{int(sec)} 秒" if sec < 90 else f"{sec / 60:.0f} 分鐘"


# ───────────────────────────── claim / finish / requeue ─────────────────────────────

def _claim(job_id: str, worker: str) -> JobContext | None:
    now = utcnow()
    try:
        jid = uuid.UUID(str(job_id))
    except ValueError:
        return None
    with session_scope() as s:
        row = s.execute(update(Job).where(Job.id == jid, Job.status == "queued",
                                          or_(Job.run_after.is_(None), Job.run_after <= now))
                        .values(status="running", started_at=now, heartbeat_at=now, finished_at=None,
                                attempts=Job.attempts + 1, worker=worker, run_after=None)
                        .returning(Job.id, Job.kind, Job.queue, Job.media_id, Job.channel_id, Job.payload,
                                   Job.created_by, Job.attempts, Job.max_attempts)).first()
        if row is None:
            return None
        return JobContext(job_id=row.id, kind=row.kind, media_id=row.media_id, channel_id=row.channel_id,
                          payload=row.payload or {}, created_by=row.created_by, attempts=row.attempts,
                          max_attempts=row.max_attempts or DEFAULT_MAX_ATTEMPTS, worker=worker, queue=row.queue)


def _not_due_reschedule(job_id: str) -> None:
    """A popped id that could not be claimed: if it is queued but not yet due, park it in the delayed set."""
    try:
        jid = uuid.UUID(str(job_id))
    except ValueError:
        return
    with session_scope() as s:
        row = s.execute(select(Job.status, Job.queue, Job.run_after).where(Job.id == jid)).first()
    if row is not None and row.status == "queued" and row.run_after is not None and row.run_after > utcnow():
        push(job_id, row.queue, row.run_after)


def _owned(stmt, ctx: JobContext):  # noqa: ANN001, ANN202
    stmt = stmt.where(Job.id == ctx.job_id, Job.status == "running")
    return stmt.where(Job.worker == ctx.worker) if ctx.worker is not None else stmt


def _finish(ctx: JobContext, status: str, result: dict | None = None, code: str | None = None,
            message: str | None = None) -> bool:
    """Terminal state, only if this worker still owns the running job. Follow-ups commit atomically with it."""
    with session_scope() as s:
        job = s.scalar(_owned(select(Job), ctx).with_for_update())
        if job is None:  # deleted with its media/channel, or recovered/requeued elsewhere
            return False
        job.status, job.finished_at, job.run_after = status, utcnow(), None
        if result is not None:
            job.result = result
        if status == "completed":
            job.progress = 1.0
            for kind, payload, ids in ctx._followups:
                enqueue(s, kind, payload=payload, parent_id=ctx.job_id, created_by=ctx.created_by, **ids)
        job.error_code, job.error_message = code, message
        return True


def _requeue_running(ctx: JobContext, *, count_attempt: bool, phase: str, message: str,
                     run_after: datetime | None = None, error: tuple[str, str] | None = None,
                     front: bool = True) -> bool:
    """running → queued for a job this worker owns. count_attempt=False gives the attempt back (shutdown)."""
    values = dict(status="queued", worker=None, started_at=None, heartbeat_at=None, run_after=run_after,
                  phase=phase, message=message, progress=0.0)
    if not count_attempt:
        values["attempts"] = Job.attempts - 1
    if error is not None:
        values["error_code"], values["error_message"] = error[0], error[1][:4000]
    with session_scope() as s:
        row = s.execute(_owned(update(Job), ctx).values(**values).returning(Job.queue)).first()
    if row is None:
        return False
    _sync_entities(ctx.kind, ctx.media_id, ctx.channel_id, ctx.payload, "requeued")
    push(ctx.job_id, row.queue, run_after, front=front)
    return True


def _cancel_requested(job_id: uuid.UUID) -> bool:
    with session_scope() as s:
        return bool(s.scalar(select(Job.cancel_requested).where(Job.id == job_id)))


def _db_retry(fn: Callable[[], object], what: str, tries: int = 6) -> object:
    """Run a short DB operation, retrying while the database is unreachable (≈1 min total)."""
    delay = 1.0
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i + 1 == tries or _transient_code(e) is None:
                raise
            log.warning("%s failed (%s); retrying in %.0fs", what, e, delay)
            time.sleep(delay)
            delay = min(delay * 2, 15.0)
    return None


def run_one(job_id: str, worker: str, _owner: "Worker | None" = None) -> None:
    ctx = _claim(job_id, worker)
    if ctx is None:
        _not_due_reschedule(job_id)
        return
    if _owner is not None:
        ctx._stop = _owner.stop
        _owner.current = ctx
    try:
        _execute(ctx)
    finally:
        if _owner is not None:
            _owner.current = None


def _execute(ctx: JobContext) -> None:
    fn = HANDLERS.get(ctx.kind)
    if fn is None:
        _finish(ctx, "failed", code="no_handler", message=f"no handler for {ctx.kind} on this worker")
        return
    log.info("start %s %s media=%s attempt=%d/%d", ctx.kind, ctx.job_id, ctx.media_id, ctx.attempts,
             ctx.max_attempts)
    try:
        result = fn(ctx)
    except JobLost:
        log.warning("job %s is no longer owned by %s; dropping its result", ctx.job_id, ctx.worker)
        return
    except Cancelled as e:
        stopping = isinstance(e, ShutdownRequested) or (ctx._stop is not None and ctx._stop.is_set())
        if stopping and not _db_retry(lambda: _cancel_requested(ctx.job_id), "cancel check"):
            _shutdown_requeue(ctx)
            return
        if _db_retry(lambda: _finish(ctx, "cancelled", code="cancelled", message="使用者取消"), "finish"):
            _sync_entities(ctx.kind, ctx.media_id, ctx.channel_id, ctx.payload, "cancelled")
        return
    except Exception as e:  # noqa: BLE001
        err = e
        _db_retry(lambda: _on_error(ctx, err), "record failure")
        return
    if _db_retry(lambda: _finish(ctx, "completed", result=result or {}), "finish"):
        log.info("done %s %s", ctx.kind, ctx.job_id)


def _shutdown_requeue(ctx: JobContext) -> bool:
    ok = _requeue_running(ctx, count_attempt=False, phase="requeued",
                          message="worker 停止中：工作已放回佇列，重新啟動後自動繼續")
    if ok:
        log.info("requeued %s %s (worker shutdown)", ctx.kind, ctx.job_id)
    return ok


def _on_error(ctx: JobContext, e: Exception) -> None:
    if ctx._stop is not None and ctx._stop.is_set():
        # errors while stopping are usually caused by the shutdown (killed subprocess, closed pool)
        log.info("job %s raised %s during shutdown; requeueing", ctx.job_id, type(e).__name__)
        _shutdown_requeue(ctx)
        return
    code, message, blocked, retryable = classify(e)
    if not isinstance(e, JobError):
        log.error("job %s crashed: %s", ctx.job_id, message.splitlines()[0] if message else e,
                  exc_info=not retryable)
    if retryable and ctx.attempts < ctx.max_attempts:
        delay = retry_delay(ctx.attempts)
        run_after = utcnow() + timedelta(seconds=delay)
        msg = f"暫時性錯誤（{code}），{_fmt_delay(delay)}後自動重試（第 {ctx.attempts + 1}/{ctx.max_attempts} 次）"
        if _requeue_running(ctx, count_attempt=True, phase="retry_wait", message=msg, run_after=run_after,
                            error=(code, message), front=False):
            log.warning("job %s %s: %s → retry in %ss", ctx.kind, ctx.job_id, code, delay)
        return
    status = "blocked" if blocked else "failed"
    if _finish(ctx, status, code=code, message=message):
        _sync_entities(ctx.kind, ctx.media_id, ctx.channel_id, ctx.payload, status, code, message)


def _on_media_failure(ctx: JobContext, code: str, message: str, blocked: bool) -> None:
    """Kept for callers/tests of the old API."""
    _sync_entities(ctx.kind, ctx.media_id, ctx.channel_id, ctx.payload, "blocked" if blocked else "failed",
                   code, message)


def _sync_entities(kind: str, media_id: uuid.UUID | None, channel_id: uuid.UUID | None, payload: dict | None,
                   outcome: str, code: str | None = None, message: str | None = None) -> None:
    """Keep media / channel / playlist / export state consistent with the job outcome.

    outcome: requeued (will run again) | failed | blocked | interrupted (terminal) | cancelled."""
    from .models import Channel, DatasetExport, Media, Playlist
    payload = payload or {}
    try:
        with session_scope() as s:
            if kind in ("channel.list", "source.resolve"):
                ch_id = channel_id or _uuid(payload.get("channel_id"))
                ch = s.get(Channel, ch_id) if ch_id else None
                if ch is not None:
                    sync_channel_list_status(s, ch, kind, outcome)
            if kind.startswith("media.") and not is_side_job(kind, payload) and media_id is not None:
                m = s.get(Media, media_id)
                if m is not None and outcome == "cancelled":
                    pass  # handlers / cancel endpoints restore the pre-job state themselves
                elif m is not None:
                    if outcome == "requeued":
                        if kind == "media.download" and m.status in ("downloading", "listed", "detailed"):
                            m.status, m.stage = "queued", None
                    elif not (kind == "media.detail" and m.status not in ("listed", "detailed")):
                        # a failed re-detail must not push an already downloaded/ready media backwards
                        status = "skipped" if code in SKIP_CODES else ("blocked" if outcome == "blocked" else "failed")
                        m.status, m.failure_code = status, code
                        m.failure_message = (message or "")[:2000]
            elif kind == "playlist.list" and outcome not in ("requeued", "cancelled"):
                ref = _uuid(payload.get("playlist_id"))
                p = s.get(Playlist, ref) if ref else s.scalar(
                    select(Playlist).where(Playlist.platform_playlist_id == str(payload.get("playlist_id"))))
                if p is not None and p.list_status == "listing":
                    p.list_status = "failed"
            elif kind == "dataset.export" and outcome != "cancelled":
                exp = s.get(DatasetExport, _uuid(payload.get("export_id")))
                if exp is not None and exp.status != "completed":
                    exp.status = "queued" if outcome == "requeued" else "failed"
    except Exception:  # noqa: BLE001 - entity sync is best effort; the job row is authoritative
        log.exception("could not sync entities for %s (%s)", kind, outcome)


LIST_JOB_KINDS = ("channel.list", "source.resolve")


def active_list_job(s: Session, channel_id: uuid.UUID, exclude: uuid.UUID | None = None) -> Job | None:
    """The queued/running channel.list (else source.resolve) job of a channel."""
    q = select(Job).where(Job.channel_id == channel_id, Job.kind.in_(LIST_JOB_KINDS), Job.media_id.is_(None),
                          Job.status.in_(("queued", "running")))
    if exclude is not None:
        q = q.where(Job.id != exclude)
    rows = s.scalars(q).all()
    rows.sort(key=lambda j: (j.kind != "channel.list", j.status != "running", j.created_at))
    return rows[0] if rows else None


def sync_channel_list_status(s: Session, ch, kind: str | None = None, outcome: str | None = None) -> str:  # noqa: ANN001
    """Channel.list_status follows its listing job: an active job → queued / listing; otherwise a stale
    queued/listing (crash, cancel, final failure) becomes failed — or listed again when only a re-listing was
    cancelled. Returns the new status."""
    job = active_list_job(s, ch.id)
    if job is not None:
        ch.list_status = "listing" if (job.status == "running" and job.kind == "channel.list") else "queued"
    elif outcome == "cancelled" or (outcome is None and ch.list_status in ("queued", "listing")):
        if ch.list_status in ("queued", "listing"):
            ch.list_status = "listed" if ch.last_listed_at is not None else ("new" if outcome == "cancelled"
                                                                              else "failed")
    elif outcome in ("failed", "blocked", "interrupted") and ch.list_status in ("queued", "listing", "new"):
        ch.list_status = "failed"
    return ch.list_status


def _uuid(v) -> uuid.UUID | None:  # noqa: ANN001
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


# ───────────────────────────── recovery / sweeper ─────────────────────────────

def _host_pid(name: str | None) -> tuple[str, int | None]:
    if not name or ":" not in name:
        return name or "", None
    host, _, pid = name.rpartition(":")
    try:
        return host, int(pid)
    except ValueError:
        return host, None


def _previous_life(worker: str | None, me: str) -> bool:
    """Was `worker` an earlier process on this host (same container after a restart / crash)?"""
    host, pid = _host_pid(worker)
    my_host, my_pid = _host_pid(me)
    if not worker or host != my_host:
        return False
    if worker == me or pid is None:
        return True  # same name: we have not claimed anything yet, so it is a previous life
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False  # another live process on this host (several workers on one box)


def recover_orphans(startup_name: str | None = None) -> dict[str, int]:
    """Requeue (or mark interrupted) running jobs whose worker is gone. Safe to run from any worker."""
    now = utcnow()
    alive: dict[str, bool] = {}
    redis_ok = True
    try:
        r = redis_client()
    except Exception:  # noqa: BLE001
        redis_ok = False
    with session_scope() as s:
        rows = s.execute(select(Job.id, Job.kind, Job.queue, Job.worker, Job.heartbeat_at, Job.attempts,
                                Job.max_attempts, Job.cancel_requested, Job.media_id, Job.channel_id, Job.payload)
                         .where(Job.status == "running")).all()
    stats = {"requeued": 0, "interrupted": 0, "cancelled": 0}
    for j in rows:
        reason = None
        if startup_name is not None and _previous_life(j.worker, startup_name):
            reason = "restart"
        elif j.heartbeat_at is None or j.heartbeat_at < now - STALE_AFTER:
            reason = "stale_heartbeat"
        elif redis_ok and j.heartbeat_at < now - DEAD_WORKER_GRACE:
            if j.worker not in alive:
                try:
                    alive[j.worker] = bool(j.worker) and bool(r.exists(worker_key(j.worker)))
                except redis.RedisError:
                    redis_ok = False
                    continue
            if not alive[j.worker]:
                reason = "worker_gone"
        if reason is None:
            continue
        outcome = _recover(j, reason)
        if outcome:
            stats[outcome] += 1
    if any(stats.values()):
        log.warning("orphan recovery: %s", stats)
    return stats


_REASON_TEXT = {"restart": "worker 重新啟動", "stale_heartbeat": "worker 超過 2 分鐘未回應",
                "worker_gone": "worker 已離線"}


def _recover(j, reason: str) -> str | None:  # noqa: ANN001
    """Optimistic: only touches the row if worker and heartbeat are still what we saw."""
    guard = [Job.id == j.id, Job.status == "running",
             Job.worker == j.worker if j.worker is not None else Job.worker.is_(None),
             Job.heartbeat_at == j.heartbeat_at if j.heartbeat_at is not None else Job.heartbeat_at.is_(None)]
    why = _REASON_TEXT.get(reason, reason)
    now = utcnow()
    if j.cancel_requested:
        values, outcome = dict(status="cancelled", finished_at=now, error_code="cancelled",
                               error_message="使用者取消（worker 中斷時）"), "cancelled"
    elif (j.attempts or 0) < (j.max_attempts or DEFAULT_MAX_ATTEMPTS):
        values, outcome = dict(status="queued", worker=None, started_at=None, heartbeat_at=None, run_after=None,
                               progress=0.0, phase="recovered",
                               message=f"已自動恢復：{why}，工作已重新排隊（已執行 {j.attempts} 次）"), "requeued"
    else:
        values, outcome = dict(status="interrupted", finished_at=now, error_code="interrupted",
                               error_message=f"工作中斷（{why}），已達自動重試上限 {j.max_attempts} 次；"
                                             "請確認原因後手動重試"), "interrupted"
    with session_scope() as s:
        res = s.execute(update(Job).where(*guard).values(**values))
        if res.rowcount != 1:
            return None
    log.warning("recovered job %s %s (worker %s, %s) → %s", j.kind, j.id, j.worker, reason, outcome)
    if outcome == "requeued":
        _sync_entities(j.kind, j.media_id, j.channel_id, j.payload, "requeued")
        push(j.id, j.queue, front=True)
    elif outcome == "interrupted":
        _sync_entities(j.kind, j.media_id, j.channel_id, j.payload, "interrupted", "interrupted",
                       values["error_message"])
    else:  # cancelled
        _sync_entities(j.kind, j.media_id, j.channel_id, j.payload, "cancelled")
    if outcome == "cancelled" and j.kind == "media.download" and j.media_id is not None:  # pre-download state
        from .models import Media
        with session_scope() as s:
            m = s.get(Media, j.media_id)
            if m is not None and m.status in ("queued", "downloading"):
                m.status, m.stage = ("detailed" if m.detailed_at else "listed"), None
    return outcome


def promote_due(queues: list[str]) -> int:
    """Move due ids from the delayed sets to the front of their lists. Each id moves once (ZREM wins)."""
    r = redis_client()
    now = time.time()
    n = 0
    for q in queues:
        for jid in r.zrangebyscore(_dkey(q), "-inf", now, start=0, num=500):
            if r.zrem(_dkey(q), jid):
                r.rpush(_qkey(q), jid)
                n += 1
    return n


def repush_lost(queues: list[str], min_age: timedelta | None = REPUSH_MIN_AGE) -> int:
    """Queued jobs that are neither in the Redis list nor in the delayed set (Redis restarted / flushed /
    push failed) are pushed again. Duplicate ids are harmless: claiming is atomic."""
    now = utcnow()
    with session_scope() as s:
        q = select(Job.id, Job.queue, Job.run_after).where(Job.status == "queued", Job.queue.in_(queues))
        if min_age:
            q = q.where(Job.created_at < now - min_age)
        rows = s.execute(q.order_by(Job.priority.desc(), Job.created_at)).all()
    if not rows:
        return 0
    r = redis_client()
    pending: dict[str, set[str]] = {}
    for qn in {row.queue for row in rows}:
        pending[qn] = {x.decode() for x in r.lrange(_qkey(qn), 0, -1)} | \
                      {x.decode() for x in r.zrange(_dkey(qn), 0, -1)}
    n = 0
    for row in rows:
        if str(row.id) not in pending[row.queue]:
            push(row.id, row.queue, row.run_after)
            n += 1
    if n:
        log.warning("re-pushed %d queued job(s) missing from Redis", n)
    return n


def sweep(queues: list[str], startup_name: str | None = None) -> None:
    """Orphan recovery (all queues) + delayed-job promotion + Redis re-push (own queues)."""
    try:
        recover_orphans(startup_name)
    except Exception:  # noqa: BLE001
        log.exception("orphan recovery failed")
    try:
        promote_due(queues)
        repush_lost(queues, None if startup_name else REPUSH_MIN_AGE)
    except Exception:  # noqa: BLE001
        log.exception("re-push failed")


# ───────────────────────────── worker ─────────────────────────────

def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    """One job at a time on the main thread; a daemon thread does heartbeats and the shutdown watchdog."""

    def __init__(self, queues: list[str], name: str | None = None, *, shutdown_grace: float = SHUTDOWN_GRACE_SEC,
                 heartbeat_sec: float = HEARTBEAT_SEC, sweep_sec: float = SWEEP_SEC, exit_on_force: bool = True,
                 ticks: list[Callable[[], object]] | None = None):
        self.queues = list(queues)
        # periodic callables run on the main loop after every sweep (e.g. the channel refresh scheduler);
        # they must be short and safe to run on several workers at once (use a Redis lock)
        self.ticks = list(ticks or [])
        self.name = name or worker_name()
        self.stop = threading.Event()
        self.current: JobContext | None = None
        self.shutdown_grace = shutdown_grace
        self.heartbeat_sec = heartbeat_sec
        self.sweep_sec = sweep_sec
        self.exit_on_force = exit_on_force
        self._stop_at: float | None = None
        self._force = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    # ── control ──
    def request_stop(self, force: bool = False) -> None:
        if self._stop_at is None:
            self._stop_at = time.monotonic()
        self.stop.set()
        if force:
            self._force.set()

    def _on_signal(self, signum, _frame) -> None:  # noqa: ANN001
        first = not self.stop.is_set()
        log.warning("worker %s got %s: %s", self.name, signal.Signals(signum).name,
                    "finishing / requeueing the current job" if first else "forcing exit")
        self.request_stop(force=not first)

    def install_signal_handlers(self) -> None:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self._on_signal)
            signal.signal(signal.SIGINT, self._on_signal)

    # ── heartbeat / watchdog thread ──
    def announce(self) -> None:
        try:
            redis_client().set(worker_key(self.name), ",".join(self.queues), ex=WORKER_KEY_TTL)
        except redis.RedisError as e:
            log.warning("worker announce failed: %s", e)

    def beat(self) -> None:
        """Refresh the worker key and the running job's heartbeat (detects a lost job)."""
        self.announce()
        ctx = self.current
        if ctx is None:
            return
        try:
            with session_scope() as s:
                res = s.execute(update(Job).where(Job.id == ctx.job_id, Job.status == "running",
                                                  Job.worker == self.name).values(heartbeat_at=utcnow()))
                owned = res.rowcount == 1
            if not owned and self.current is ctx:
                log.warning("job %s is no longer owned by %s", ctx.job_id, self.name)
                ctx._lost = True
        except Exception as e:  # noqa: BLE001 - DB down: try again next beat
            log.warning("heartbeat for job %s failed: %s", ctx.job_id, e)

    def _watch(self) -> None:
        next_beat = 0.0
        while not self._done.is_set():
            now = time.monotonic()
            if now >= next_beat:
                self.beat()
                next_beat = now + self.heartbeat_sec
            if self.stop.is_set() and self.current is not None and (
                    self._force.is_set() or now - (self._stop_at or now) >= self.shutdown_grace):
                self._force_requeue()
                if self.exit_on_force:
                    return
                self._force.clear()
                self._stop_at = float("inf")  # once per job in tests
            self._done.wait(0.25)

    def _force_requeue(self) -> None:
        ctx = self.current
        if ctx is None:
            return
        log.warning("job %s did not stop within %.0fs; requeueing it and exiting", ctx.job_id, self.shutdown_grace)
        try:
            _shutdown_requeue(ctx)
        except Exception:  # noqa: BLE001
            log.exception("forced requeue failed; orphan recovery will pick the job up")
        ctx._lost = True
        if self.exit_on_force:
            try:
                redis_client().delete(worker_key(self.name))
            except Exception:  # noqa: BLE001
                pass
            logging.shutdown()
            os._exit(0)

    def run_ticks(self) -> None:
        for fn in self.ticks:
            try:
                fn()
            except Exception:  # noqa: BLE001 - a scheduler bug must never stop the worker
                log.exception("periodic task %s failed", getattr(fn, "__name__", fn))

    # ── main loop ──
    def run(self, install_signals: bool = True, max_jobs: int | None = None) -> None:
        if install_signals:
            self.install_signal_handlers()
        r = redis_client()
        keys = [_qkey(q) for q in self.queues]
        self.announce()
        self._thread = threading.Thread(target=self._watch, name=f"voti-heartbeat-{self.name}", daemon=True)
        self._thread.start()
        log.info("worker %s listening on %s", self.name, self.queues)
        sweep(self.queues, startup_name=self.name)  # own previous jobs + Redis loss, right away
        self.run_ticks()
        last_sweep = time.monotonic()
        jobs = 0
        try:
            while not self.stop.is_set():
                if time.monotonic() - last_sweep > self.sweep_sec:
                    sweep(self.queues)
                    self.run_ticks()
                    last_sweep = time.monotonic()
                try:
                    promote_due(self.queues)
                    item = r.brpop(keys, timeout=POP_TIMEOUT_SEC)
                except redis.RedisError as e:
                    log.warning("redis unavailable (%s); retrying", e)
                    self.stop.wait(2.0)
                    continue
                if item is None:
                    continue
                if self.stop.is_set():  # popped while stopping: put it back at the front
                    push(item[1].decode(), item[0].decode().rsplit(":", 1)[-1], front=True)
                    break
                try:
                    run_one(item[1].decode(), self.name, _owner=self)
                except Exception:  # noqa: BLE001 - DB down while claiming etc.; never kill the loop
                    log.exception("run_one(%s) failed", item[1])
                    push(item[1].decode(), item[0].decode().rsplit(":", 1)[-1], front=True)
                    self.stop.wait(2.0)
                jobs += 1
                if max_jobs is not None and jobs >= max_jobs:
                    break
        finally:
            self._done.set()
            if self._thread is not None:
                self._thread.join(timeout=2)
            try:
                r.delete(worker_key(self.name))
            except redis.RedisError:
                pass
            log.info("worker %s stopped", self.name)


def worker_loop(queues: list[str], ticks: list[Callable[[], object]] | None = None) -> None:
    Worker(queues, ticks=ticks).run()
