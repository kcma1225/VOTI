"""Job queue recovery: graceful shutdown, orphan recovery, delayed retries, Redis loss, no double execution.

Run e.g.: VOTI_TEST_DB=voti_test_recovery VOTI_TEST_REDIS_DB=5 pytest tests/test_queue_recovery.py
"""
from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from voti import queue
from voti.db import session_scope
from voti.models import Channel, DatasetExport, Dataset, Job, Media, utcnow

KIND = "speakers.cluster"  # a kind without media side effects; its handler is replaced per test


@pytest.fixture
def q(db, monkeypatch):  # noqa: ANN001
    """Fresh queue state; handlers dict restored after the test."""
    monkeypatch.setattr(queue, "HANDLERS", dict(queue.HANDLERS))
    return queue


def _job(db, kind: str = KIND, **kw) -> uuid.UUID:  # noqa: ANN001
    job = queue.enqueue(db, kind, **kw)
    db.commit()
    return job.id


def _get(job_id: uuid.UUID) -> Job:
    with session_scope() as s:
        j = s.get(Job, job_id)
        s.expunge(j)
        return j


def _list(queue_name: str) -> list[str]:
    return [x.decode() for x in queue.redis_client().lrange(f"voti:q:{queue_name}", 0, -1)]


def _delayed(queue_name: str) -> list[str]:
    return [x.decode() for x in queue.redis_client().zrange(f"voti:delayed:{queue_name}", 0, -1)]


def _set(job_id: uuid.UUID, **values) -> None:
    with session_scope() as s:
        s.execute(update(Job).where(Job.id == job_id).values(**values))


def _media(db, status: str = "processing") -> Media:  # noqa: ANN001
    m = Media(platform="youtube", platform_media_id=uuid.uuid4().hex[:11], status=status,
              detailed_at=utcnow())
    db.add(m)
    db.commit()
    return m


# ───────────────────────────── graceful shutdown ─────────────────────────────

def test_graceful_shutdown_requeues_without_counting_attempt(q, db):  # noqa: ANN001
    started = threading.Event()

    def h(ctx):  # noqa: ANN001
        started.set()
        for _ in range(200):
            ctx.progress(0.5, "work")
            time.sleep(0.05)
        return {"finished": True}
    q.HANDLERS[KIND] = h
    jid = _job(db)
    queue.redis_client().delete("voti:q:ml")
    w = q.Worker(["ml"], name="testhost:1", exit_on_force=False)
    threading.Timer(0.3, w.request_stop).start()
    t0 = time.monotonic()
    q.run_one(str(jid), w.name, _owner=w)
    assert time.monotonic() - t0 < 5
    j = _get(jid)
    assert started.is_set()
    assert j.status == "queued" and j.attempts == 0 and j.worker is None and j.phase == "requeued"
    assert j.run_after is None and j.error_code is None
    assert _list("ml")[-1] == str(jid)  # front of the queue (BRPOP side)


def test_shutdown_requeues_download_media_to_queued(q, db):  # noqa: ANN001
    m = _media(db, status="downloading")

    def h(ctx):  # noqa: ANN001
        while True:
            ctx.progress(0.2, "download")
            time.sleep(0.02)
    q.HANDLERS["media.download"] = h
    jid = _job(db, "media.download", media_id=m.id)
    w = q.Worker(["fetch"], name="testhost:2", exit_on_force=False)
    threading.Timer(0.2, w.request_stop).start()
    q.run_one(str(jid), w.name, _owner=w)
    assert _get(jid).status == "queued"
    db.expire_all()
    assert db.get(Media, m.id).status == "queued"


def test_forced_shutdown_requeues_blocking_handler(q, db):  # noqa: ANN001
    """A handler stuck in a blocking call (no progress) is requeued by the watchdog after the grace period;
    its late result is discarded."""
    def h(ctx):  # noqa: ANN001
        time.sleep(1.5)
        return {"late": True}
    q.HANDLERS[KIND] = h
    jid = _job(db)
    w = q.Worker(["ml"], name="testhost:3", shutdown_grace=0.3, heartbeat_sec=0.1, exit_on_force=False)
    w._thread = threading.Thread(target=w._watch, daemon=True)
    w._thread.start()
    threading.Timer(0.1, w.request_stop).start()
    try:
        q.run_one(str(jid), w.name, _owner=w)
    finally:
        w._done.set()
    j = _get(jid)
    assert j.status == "queued" and j.attempts == 0 and j.result == {}


def test_worker_loop_stops_and_next_worker_finishes(q, db):  # noqa: ANN001
    runs = []

    def h(ctx):  # noqa: ANN001
        runs.append(ctx.worker)
        if ctx.worker == "testhost:10":
            while True:
                ctx.progress(0.1)
                time.sleep(0.02)
        return {"ok": True}
    q.HANDLERS[KIND] = h
    jid = _job(db)
    w1 = q.Worker(["ml"], name="testhost:10", exit_on_force=False)
    t = threading.Thread(target=w1.run, kwargs={"install_signals": False})
    t.start()
    for _ in range(100):
        if runs:
            break
        time.sleep(0.05)
    w1.request_stop()
    t.join(timeout=10)
    assert not t.is_alive()
    assert _get(jid).status == "queued"
    assert not queue.redis_client().exists(queue.worker_key("testhost:10"))  # deregistered
    w2 = q.Worker(["ml"], name="testhost:11")
    t2 = threading.Thread(target=w2.run, kwargs={"install_signals": False, "max_jobs": 1})
    t2.start()
    t2.join(timeout=10)
    j = _get(jid)
    assert j.status == "completed" and j.attempts == 1 and runs == ["testhost:10", "testhost:11"]


def test_heartbeat_thread_beats_without_progress(q, db):  # noqa: ANN001
    seen = {}

    def h(ctx):  # noqa: ANN001
        time.sleep(0.2)
        seen["first"] = _get(ctx.job_id).heartbeat_at
        time.sleep(0.6)
        seen["second"] = _get(ctx.job_id).heartbeat_at
        seen["key"] = queue.redis_client().ttl(queue.worker_key("testhost:4"))
        return {}
    q.HANDLERS[KIND] = h
    jid = _job(db)
    w = q.Worker(["ml"], name="testhost:4", heartbeat_sec=0.2)
    t = threading.Thread(target=w.run, kwargs={"install_signals": False, "max_jobs": 1})
    t.start()
    t.join(timeout=10)
    assert _get(jid).status == "completed"
    assert seen["second"] > seen["first"]
    assert 0 < seen["key"] <= queue.WORKER_KEY_TTL


# ───────────────────────────── orphan recovery ─────────────────────────────

def _running(jid: uuid.UUID, worker: str, age: timedelta, attempts: int = 1, max_attempts: int = 4) -> None:
    _set(jid, status="running", worker=worker, started_at=utcnow() - age, heartbeat_at=utcnow() - age,
         attempts=attempts, max_attempts=max_attempts)


def test_orphan_with_dead_worker_key_is_requeued(q, db):  # noqa: ANN001
    jid = _job(db)
    queue.redis_client().delete("voti:q:ml")
    _running(jid, "ghost:1", timedelta(seconds=60))
    stats = q.recover_orphans()
    assert stats["requeued"] == 1
    j = _get(jid)
    assert j.status == "queued" and j.phase == "recovered" and "已自動恢復" in j.message
    assert j.attempts == 1 and j.worker is None
    assert _list("ml") == [str(jid)]


def test_live_worker_job_is_left_alone(q, db):  # noqa: ANN001
    jid = _job(db)
    _running(jid, "alive:1", timedelta(seconds=60))
    queue.redis_client().set(queue.worker_key("alive:1"), "ml", ex=60)
    assert q.recover_orphans() == {"requeued": 0, "interrupted": 0, "cancelled": 0}
    assert _get(jid).status == "running"
    # worker key gone but heartbeat recent (Redis just restarted): still left alone
    jid2 = _job(db, "channel.dedup")
    _running(jid2, "ghost:2", timedelta(seconds=5))
    q.recover_orphans()
    assert _get(jid2).status == "running"


def test_stale_heartbeat_requeued_even_if_key_present(q, db):  # noqa: ANN001
    jid = _job(db)
    _running(jid, "zombie:1", timedelta(minutes=3))
    queue.redis_client().set(queue.worker_key("zombie:1"), "ml", ex=60)
    q.recover_orphans()
    assert _get(jid).status == "queued"


def test_orphan_out_of_attempts_is_interrupted_and_media_consistent(q, db):  # noqa: ANN001
    m = _media(db, status="processing")
    jid = _job(db, "media.normalize", media_id=m.id)
    _running(jid, "ghost:3", timedelta(minutes=5), attempts=4, max_attempts=4)
    q.recover_orphans()
    j = _get(jid)
    assert j.status == "interrupted" and j.error_code == "interrupted" and "上限" in j.error_message
    db.expire_all()
    mm = db.get(Media, m.id)
    assert mm.status == "failed" and mm.failure_code == "interrupted"


def test_orphan_download_requeued_media_back_to_queued(q, db):  # noqa: ANN001
    m = _media(db, status="downloading")
    jid = _job(db, "media.download", media_id=m.id)
    _running(jid, "ghost:4", timedelta(minutes=5))
    q.recover_orphans()
    assert _get(jid).status == "queued"
    db.expire_all()
    assert db.get(Media, m.id).status == "queued"


def test_orphan_channel_list_failure_resets_listing(q, db):  # noqa: ANN001
    ch = Channel(platform="youtube", platform_channel_id="UC" + "x" * 22, list_status="listing")
    db.add(ch)
    db.commit()
    jid = _job(db, "channel.list", channel_id=ch.id)
    _running(jid, "ghost:5", timedelta(minutes=5), attempts=4, max_attempts=4)
    q.recover_orphans()
    db.expire_all()
    assert db.get(Channel, ch.id).list_status == "failed"


def test_orphan_cancel_requested_becomes_cancelled(q, db):  # noqa: ANN001
    jid = _job(db)
    _running(jid, "ghost:6", timedelta(minutes=5))
    _set(jid, cancel_requested=True)
    q.recover_orphans()
    assert _get(jid).status == "cancelled"


def test_own_hostname_restart_recovers_immediately(q, db):  # noqa: ANN001
    me = f"{socket.gethostname()}:{os.getpid()}"
    jid = _job(db)
    _running(jid, me, timedelta(seconds=1))  # fresh heartbeat; key may even still exist
    queue.redis_client().set(queue.worker_key(me), "ml", ex=60)
    # another live process on this host (the test runner's parent) is not touched
    other = _job(db, "channel.dedup")
    _running(other, f"{socket.gethostname()}:{os.getppid()}", timedelta(seconds=1))
    queue.redis_client().set(queue.worker_key(f"{socket.gethostname()}:{os.getppid()}"), "ml", ex=60)
    # a dead pid on this host is recovered
    dead = _job(db, "dataset.export", payload={"export_id": str(uuid.uuid4())})
    _running(dead, f"{socket.gethostname()}:{2 ** 22 + 12345}", timedelta(seconds=1))
    q.recover_orphans(startup_name=me)
    assert _get(jid).status == "queued" and _get(jid).phase == "recovered"
    assert _get(other).status == "running"
    assert _get(dead).status == "queued"


def test_worker_startup_recovers_previous_life(q, db):  # noqa: ANN001
    done = []
    q.HANDLERS[KIND] = lambda ctx: done.append(ctx.attempts) or {}
    jid = _job(db)
    me = "box:7"
    _running(jid, me, timedelta(seconds=1))
    queue.redis_client().delete("voti:q:ml")  # its Redis entry is gone too
    w = q.Worker(["ml"], name=me)
    t = threading.Thread(target=w.run, kwargs={"install_signals": False, "max_jobs": 1})
    t.start()
    t.join(timeout=10)
    assert _get(jid).status == "completed" and done == [2]


# ───────────────────────────── retries ─────────────────────────────

def test_retryable_error_backoff_then_failure(q, db):  # noqa: ANN001
    calls = []

    def h(ctx):  # noqa: ANN001
        calls.append(ctx.attempts)
        raise httpx.ConnectError("connection refused")
    q.HANDLERS[KIND] = h
    jid = _job(db)
    queue.redis_client().delete("voti:q:ml")
    for n, delay in enumerate(queue.RETRY_BACKOFF_SEC, start=1):
        _set(jid, run_after=None)
        t0 = utcnow()
        q.run_one(str(jid), "w:1")
        j = _get(jid)
        assert j.status == "queued" and j.attempts == n and j.phase == "retry_wait"
        assert j.error_code == "network" and "自動重試" in j.message
        assert abs((j.run_after - t0).total_seconds() - delay) < 5
        assert str(jid) in _delayed("ml")
    _set(jid, run_after=None)
    q.run_one(str(jid), "w:1")
    j = _get(jid)
    assert j.status == "failed" and j.attempts == 4 and j.error_code == "network" and j.run_after is None
    assert calls == [1, 2, 3, 4]


def test_retryable_blocked_error_ends_blocked(q, db):  # noqa: ANN001
    m = _media(db)

    def h(ctx):  # noqa: ANN001
        raise queue.JobError("legacy_ml_unavailable", "down", blocked=True, retryable=True)
    q.HANDLERS["media.quality"] = h
    jid = _job(db, "media.quality", media_id=m.id)
    q.run_one(str(jid), "w:1")
    assert _get(jid).status == "queued"
    db.expire_all()
    assert db.get(Media, m.id).status == "processing"  # still in the pipeline while waiting
    _set(jid, run_after=None, attempts=3)
    q.run_one(str(jid), "w:1")
    assert _get(jid).status == "blocked"
    db.expire_all()
    assert db.get(Media, m.id).status == "blocked"


def test_blocked_error_is_not_retried(q, db):  # noqa: ANN001
    def h(ctx):  # noqa: ANN001
        raise queue.JobError("bot_check", "cookies needed", blocked=True)
    q.HANDLERS[KIND] = h
    jid = _job(db)
    q.run_one(str(jid), "w:1")
    j = _get(jid)
    assert j.status == "blocked" and j.attempts == 1 and j.run_after is None


def test_non_transient_error_fails_immediately(q, db):  # noqa: ANN001
    def h(ctx):  # noqa: ANN001
        raise ValueError("bug")
    q.HANDLERS[KIND] = h
    jid = _job(db)
    q.run_one(str(jid), "w:1")
    j = _get(jid)
    assert j.status == "failed" and j.error_code == "exception" and j.attempts == 1


def test_run_after_respected(q, db):  # noqa: ANN001
    calls = []
    q.HANDLERS[KIND] = lambda ctx: calls.append(1) or {}
    jid = _job(db)
    queue.redis_client().delete("voti:q:ml")
    _set(jid, run_after=utcnow() + timedelta(seconds=60))
    q.run_one(str(jid), "w:1")  # popped too early
    assert calls == [] and _get(jid).status == "queued"
    assert _delayed("ml") == [str(jid)]  # parked in the delayed set
    assert q.promote_due(["ml"]) == 0
    _set(jid, run_after=utcnow() - timedelta(seconds=1))
    queue.redis_client().zadd("voti:delayed:ml", {str(jid): time.time() - 1})
    assert q.promote_due(["ml"]) == 1 and _list("ml") == [str(jid)] and _delayed("ml") == []
    q.run_one(str(jid), "w:1")
    assert calls == [1] and _get(jid).status == "completed"


def test_manual_retry_resets_attempts_and_run_after(q, db):  # noqa: ANN001
    jid = _job(db)
    _set(jid, status="failed", attempts=4, run_after=utcnow() + timedelta(minutes=5))
    j = db.get(Job, jid)
    db.refresh(j)
    queue.requeue(db, j)
    db.commit()
    j = _get(jid)
    assert j.status == "queued" and j.attempts == 0 and j.run_after is None


def test_classify_transient_errors():
    from botocore.exceptions import ClientError, EndpointConnectionError
    from sqlalchemy.exc import OperationalError

    assert queue.classify(EndpointConnectionError(endpoint_url="http://s3"))[0] == "s3_unavailable"
    assert queue.classify(ClientError({"Error": {"Code": "SlowDown"}, "ResponseMetadata": {"HTTPStatusCode": 503}},
                                      "PutObject"))[3] is True
    assert queue.classify(ClientError({"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                                      "GetObject"))[3] is False
    assert queue.classify(OperationalError("select 1", {}, Exception("server closed")))[0] == "db_unavailable"
    assert queue.classify(ConnectionResetError())[3] is True
    assert queue.classify(TimeoutError())[0] == "timeout"
    req = httpx.Request("GET", "http://x")
    assert queue.classify(httpx.HTTPStatusError("boom", request=req,
                                                response=httpx.Response(502, request=req)))[3] is True
    try:
        try:
            raise ConnectionRefusedError()
        except ConnectionRefusedError as e:
            raise RuntimeError("wrapped") from e
    except RuntimeError as e:
        assert queue.classify(e)[3] is True  # cause chain
    assert queue.classify(queue.JobError("x", "y"))[3] is False  # JobError default: not retryable


def test_youtube_transient_vs_blocked_mapping():
    from voti.sources import handlers as h
    from voti.sources import youtube as yt

    assert h._job_error(yt.classify_error("Unable to download webpage: <urlopen error timed out>")).retryable
    bot = h._job_error(yt.classify_error("Sign in to confirm you're not a bot"))
    assert bot.blocked and not bot.retryable
    assert not h._job_error(yt.classify_error("Video unavailable")).retryable


def test_export_waiting_for_retry_is_queued_not_failed(q, db, monkeypatch):  # noqa: ANN001
    from voti import datasets

    ds = Dataset(name="d", filters={})
    db.add(ds)
    db.commit()
    exp = DatasetExport(dataset_id=ds.id, status="queued", filters={})
    db.add(exp)
    db.commit()

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise ConnectionResetError("s3 reset")
    monkeypatch.setattr(datasets, "_run_export", boom)
    q.HANDLERS["dataset.export"] = datasets.export_handler
    jid = _job(db, "dataset.export", payload={"export_id": str(exp.id)})
    q.run_one(str(jid), "w:1")
    assert _get(jid).status == "queued"
    db.expire_all()
    assert db.get(DatasetExport, exp.id).status == "queued"


# ───────────────────────────── Redis loss / concurrency ─────────────────────────────

def test_redis_loss_repush(q, db):  # noqa: ANN001
    a, b = _job(db), _job(db, "channel.dedup")
    later = _job(db, "dataset.export", payload={"export_id": str(uuid.uuid4())})
    _set(later, run_after=utcnow() + timedelta(minutes=5))
    queue.redis_client().flushdb()  # Redis restarted without persistence
    assert q.repush_lost(["ml", "export"], min_age=None) == 3
    assert sorted(_list("ml")) == sorted([str(a), str(b)])
    assert _delayed("export") == [str(later)] and _list("export") == []
    assert q.repush_lost(["ml", "export"], min_age=None) == 0  # no duplicates on the next sweep
    # the periodic sweep ignores very young jobs (the producer is about to push them)
    queue.redis_client().flushdb()
    assert q.repush_lost(["ml"]) == 0


def test_two_workers_never_run_a_job_twice(q, db):  # noqa: ANN001
    lock = threading.Lock()
    counts: dict[str, int] = {}

    def h(ctx):  # noqa: ANN001
        with lock:
            counts[str(ctx.job_id)] = counts.get(str(ctx.job_id), 0) + 1
        time.sleep(0.01)
        return {}
    q.HANDLERS[KIND] = h
    ids = [str(_job(db)) for _ in range(20)]
    r = queue.redis_client()
    for jid in ids:  # duplicate ids in Redis (re-push after a Redis hiccup)
        r.lpush("voti:q:ml", jid, jid)
    workers = [q.Worker(["ml"], name=f"cc:{i}") for i in range(2)]
    threads = [threading.Thread(target=w.run, kwargs={"install_signals": False}) for w in workers]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with session_scope() as s:
            left = s.scalar(select(Job.id).where(Job.status != "completed").limit(1))
        if left is None:
            break
        time.sleep(0.1)
    for w in workers:
        w.request_stop()
    for t in threads:
        t.join(timeout=10)
    assert counts == {jid: 1 for jid in ids}
    with session_scope() as s:
        assert {j.attempts for j in s.scalars(select(Job))} == {1}


def test_init_db_adds_run_after_to_existing_table(db, db_engine):  # noqa: ANN001
    """Databases created before run_after existed get the column from init_db (create_all never alters)."""
    from sqlalchemy import inspect, text

    from voti.db import init_db
    with db_engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS jobs_running_heartbeat"))
        conn.execute(text("ALTER TABLE jobs DROP COLUMN run_after"))
    assert "run_after" not in {c["name"] for c in inspect(db_engine).get_columns("jobs")}
    init_db()
    init_db()  # idempotent
    assert "run_after" in {c["name"] for c in inspect(db_engine).get_columns("jobs")}
    assert "jobs_running_heartbeat" in {i["name"] for i in inspect(db_engine).get_indexes("jobs")}
