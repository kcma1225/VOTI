"""Periodic maintenance run by the scan workers (voti.workers.run registers `tick` as a Worker tick).

Every tick (≈ every 30 s on each scan worker; a Redis lock makes only one worker act per TICK_LOCK_SEC):
  * scheduled channel refresh — channels whose last listing (or last listing attempt) is older than
    `youtube.auto_refresh_hours` (default 24; 0 disables) get a `channel.list` job with payload
    {"scheduled": true}. Staggered: at most MAX_PER_TICK new jobs per tick and never more than
    MAX_SCHEDULED_QUEUED scheduled listings waiting in the scan queue, oldest due first. A refresh marks the
    videos it finds as new (Media.discovered_at; voti.sources.handlers.channel_list) and the existing
    listing → detail chain fetches their caption info; they are never downloaded automatically.
  * list_status reconciliation — a channel left `queued` / `listing` without an active listing job (crash,
    lost row) is set from its jobs (voti.queue.sync_channel_list_status).
  * alignment safety net — segments whose re-alignment was requested (word_alignments.status = pending) more
    than ALIGN_PENDING_GRACE ago while no media.align job is active get one (closes the race between an edit and
    a finishing alignment job).
  * automatic speaker clustering — when embeddings newer than the current clustering run exist (or none ran yet),
    no media.embed / speakers.cluster job is active and the newest embedding is AUTO_CLUSTER_QUIET old (the batch
    has settled), a `speakers.cluster` job is queued with the current run's threshold and scope (payload
    {"auto": true}). During a long backlog that never settles, a run older than AUTO_CLUSTER_MAX_STALE is
    refreshed anyway.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import session_scope
from .models import Channel, Job, Setting, utcnow

log = logging.getLogger("voti.scheduler")

LOCK_KEY = "voti:sched:tick"
TICK_LOCK_SEC = 25
MAX_PER_TICK = 2
MAX_SCHEDULED_QUEUED = 4
DEFAULT_REFRESH_HOURS = 24.0
NEW_WINDOW = timedelta(days=7)  # MediaSummary.is_new: discovered by a refresh within this window
ALIGN_PENDING_GRACE = timedelta(minutes=2)
AUTO_CLUSTER_QUIET = timedelta(minutes=10)
AUTO_CLUSTER_MAX_STALE = timedelta(hours=6)


def refresh_hours(s: Session) -> float:
    row = s.get(Setting, "youtube")
    v = (row.value or {}).get("auto_refresh_hours") if row is not None else None
    try:
        return max(0.0, float(v)) if v is not None else DEFAULT_REFRESH_HOURS
    except (TypeError, ValueError):
        return DEFAULT_REFRESH_HOURS


def refresh_interval(s: Session) -> timedelta | None:
    h = refresh_hours(s)
    return timedelta(hours=h) if h > 0 else None


def last_list_attempts(s: Session, channel_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, datetime]:
    """channel id → created_at of its newest channel.list job."""
    q = select(Job.channel_id, func.max(Job.created_at)).where(Job.kind == "channel.list",
                                                                Job.channel_id.isnot(None))
    if channel_ids is not None:
        if not channel_ids:
            return {}
        q = q.where(Job.channel_id.in_(channel_ids))
    return dict(s.execute(q.group_by(Job.channel_id)).all())


def next_refresh_at(last_listed_at: datetime | None, last_attempt: datetime | None,
                    interval: timedelta | None, now: datetime | None = None) -> datetime | None:
    """When the scheduler will re-list the channel (None: auto refresh disabled). A past time means due."""
    if interval is None:
        return None
    base = max([t for t in (last_listed_at, last_attempt) if t is not None], default=None)
    return (base + interval) if base is not None else (now or utcnow())


def due_channels(s: Session, interval: timedelta, now: datetime, limit: int) -> list[Channel]:
    attempts = last_list_attempts(s)
    rows = s.execute(select(Channel.id, Channel.last_listed_at)
                     .where(Channel.list_status.notin_(("queued", "listing")))).all()
    due = []
    for cid, listed in rows:
        when = next_refresh_at(listed, attempts.get(cid), interval, now)
        if when is not None and when <= now:
            due.append((when, cid))
    due.sort()
    return [s.get(Channel, cid) for _, cid in due[:limit]]


def schedule_refreshes(s: Session, now: datetime | None = None) -> int:
    from .sources.handlers import queue_channel_listing
    from .sources.youtube import LISTED_TABS
    now = now or utcnow()
    interval = refresh_interval(s)
    if interval is None:
        return 0
    waiting = s.scalar(select(func.count()).select_from(Job).where(
        Job.kind == "channel.list", Job.status == "queued", Job.payload["scheduled"].as_boolean().is_(True))) or 0
    room = min(MAX_PER_TICK, MAX_SCHEDULED_QUEUED - waiting)
    if room <= 0:
        return 0
    n = 0
    for ch in due_channels(s, interval, now, room):
        if ch is None:
            continue
        queue_channel_listing(s, ch, payload={"tabs": list(LISTED_TABS), "detail": True, "scheduled": True})
        n += 1
    if n:
        log.info("scheduled %d channel refresh(es)", n)
    return n


def reconcile_channels(s: Session) -> int:
    from .queue import sync_channel_list_status
    n = 0
    for ch in s.scalars(select(Channel).where(Channel.list_status.in_(("queued", "listing")))):
        before = ch.list_status
        if sync_channel_list_status(s, ch) != before:
            log.warning("channel %s list_status %s → %s (reconciled with its jobs)", ch.id, before, ch.list_status)
            n += 1
    return n


def realign_pending(s: Session, now: datetime | None = None) -> int:
    from .models import WordAlignment
    from .queue import enqueue
    now = now or utcnow()
    media_ids = s.scalars(select(WordAlignment.media_id).where(WordAlignment.status == "pending",
                                                               WordAlignment.created_at < now - ALIGN_PENDING_GRACE)
                          .distinct().limit(20)).all()
    n = 0
    for mid in media_ids:
        active = s.scalar(select(Job.id).where(Job.kind == "media.align", Job.media_id == mid,
                                               Job.status.in_(("queued", "running"))))
        if active is None:
            enqueue(s, "media.align", media_id=mid, payload={"chain": False, "pending_only": True,
                                                             "reason": "pending_safety_net"})
            n += 1
    return n


def auto_cluster(s: Session, now: datetime | None = None) -> bool:
    from .models import ClusterRun, Embedding
    from .pipeline.cluster import DEFAULT_THRESHOLD
    from .queue import enqueue
    now = now or utcnow()
    newest = s.scalar(select(func.max(Embedding.created_at)))
    if newest is None:
        return False
    run = s.scalar(select(ClusterRun).where(ClusterRun.is_current.is_(True)).order_by(ClusterRun.created_at.desc()))
    if run is not None and newest <= run.created_at:
        return False
    if s.scalar(select(Job.id).where(Job.kind == "speakers.cluster", Job.status.in_(("queued", "running")))):
        return False
    settled = newest < now - AUTO_CLUSTER_QUIET and not s.scalar(
        select(Job.id).where(Job.kind == "media.embed", Job.status.in_(("queued", "running"))))
    stale = run is not None and run.created_at < now - AUTO_CLUSTER_MAX_STALE
    if not (settled or stale):
        return False
    payload = {"threshold": run.threshold if run else DEFAULT_THRESHOLD, "scope": dict(run.scope or {}) if run else {},
               "auto": True}
    enqueue(s, "speakers.cluster", payload=payload)
    log.info("auto speaker clustering queued (threshold %.2f)", payload["threshold"])
    return True


def tick(now: datetime | None = None, lock: bool = True) -> dict:
    if lock:
        from .queue import redis_client
        try:
            if not redis_client().set(LOCK_KEY, "1", nx=True, ex=TICK_LOCK_SEC):
                return {"skipped": "locked"}
        except Exception as e:  # noqa: BLE001 - no Redis: skip (another worker or the next tick will do it)
            log.warning("scheduler lock unavailable: %s", e)
            return {"skipped": "no_lock"}
    out = {}
    with session_scope() as s:
        out["reconciled"] = reconcile_channels(s)
    with session_scope() as s:
        out["refresh_queued"] = schedule_refreshes(s, now)
    with session_scope() as s:
        out["realign_queued"] = realign_pending(s, now)
    with session_scope() as s:
        out["cluster_queued"] = auto_cluster(s, now)
    return out
