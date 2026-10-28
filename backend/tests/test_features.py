"""Channel auto-add + listing state, scheduled refresh / new videos, downloaded filter, review list (filters,
neighbors), media segments / words endpoints and re-alignment requests after edits.

yt-dlp is monkeypatched (no network). Run isolated, e.g.:
  VOTI_TEST_DB=voti_test_feat VOTI_TEST_BUCKET=voti-test-feat VOTI_TEST_REDIS_DB=8 pytest tests/test_features.py
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from voti.sources import youtube as yt

UC = "UChaNiPEqVcO-iE1NIg3wuHQ"
UC2 = "UCaaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture
def h(db):  # noqa: ANN001
    from voti.models import Setting
    from voti.sources import handlers
    db.add(Setting(key="youtube", value={"sleep_between_requests_sec": 0, "sleep_between_downloads_sec": 0}))
    db.commit()
    return handlers


def run(db, kind, **kw):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import enqueue, run_one
    job = enqueue(db, kind, **kw)
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    return db.get(Job, job.id)


def run_job(db, job):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import run_one
    run_one(str(job.id), "test")
    db.expire_all()
    return db.get(Job, job.id)


def _info(vid, channel_id=UC, channel="呆呆伙伴", **kw):
    info = {"id": vid, "title": f"title {vid}", "duration": 61, "timestamp": 1581912003, "channel_id": channel_id,
            "channel": channel, "uploader_id": "@DaiDai", "channel_url": yt.channel_url(channel_id),
            "channel_follower_count": 1234, "live_status": "not_live", "media_type": "video", "formats": [],
            "subtitles": {"zh-CN": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?lang=zh-CN"}]},
            "automatic_captions": {}}
    info.update(kw)
    return info


def _entry(vid, **kw):
    from datetime import datetime, timezone
    return {"platform_media_id": vid, "kind": "video", "url": yt.video_url(vid), "title": f"t {vid}",
            "description": "", "duration_sec": 60.0, "view_count": 5, "thumbnail_url": "thumb",
            "published_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "published_approximate": True,
            "channel_id": None, "live_status": None, "availability": None, **kw}


CHANNEL_META = {"platform_channel_id": UC, "handle": "@DaiDai", "title": "呆呆伙伴", "description": "d",
                "url": yt.channel_url(UC), "thumbnail_url": "avatar", "banner_url": "banner",
                "subscriber_count": 7680, "tags": [], "metadata": {}}


# ───────────────────────────── 1. single video → its channel ─────────────────────────────

def test_detail_auto_adds_channel_and_queues_listing(db, h, monkeypatch, annotator_client):  # noqa: ANN001
    from voti.models import Channel, Job, Media
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid), []))
    m1 = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    m2 = Media(platform_media_id="bbbbbbbbbbb", status="listed")
    db.add_all([m1, m2])
    db.commit()
    job = run(db, "media.detail", media_id=m1.id)
    assert job.status == "completed", job.error_message
    ch = db.scalar(select(Channel))
    assert (ch.title, ch.handle, ch.subscriber_count, ch.list_status) == ("呆呆伙伴", "@DaiDai", 1234, "queued")
    assert ch.metadata_["auto_added"]["video_id"] == "aaaaaaaaaaa"
    lists = db.scalars(select(Job).where(Job.kind == "channel.list")).all()
    assert len(lists) == 1 and lists[0].channel_id == ch.id and lists[0].status == "queued"

    # the channel list shows the name and the waiting listing job right away
    item = annotator_client.get("/api/channels").json()["items"][0]
    assert item["title"] == "呆呆伙伴" and item["list_status"] == "queued"
    assert item["listing_job"]["id"] == str(lists[0].id) and item["listing_job"]["status"] == "queued"

    # a second video of the same (now known) channel adds nothing
    job = run(db, "media.detail", media_id=m2.id)
    assert job.status == "completed"
    assert len(db.scalars(select(Channel)).all()) == 1
    assert len(db.scalars(select(Job).where(Job.kind == "channel.list")).all()) == 1


def test_existing_channel_is_not_relisted(db, h, monkeypatch, factory):  # noqa: ANN001
    from voti.models import Job, Media
    ch = factory.channel(platform_channel_id=UC, list_status="listed")
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid), []))
    m = Media(platform_media_id="ccccccccccc", status="listed")
    db.add(m)
    db.commit()
    assert run(db, "media.detail", media_id=m.id).status == "completed"
    db.refresh(m)
    assert m.channel_id == ch.id
    assert db.scalars(select(Job).where(Job.kind == "channel.list")).all() == []


def test_channel_listing_lifecycle_and_api(db, h, monkeypatch, factory, annotator_client):  # noqa: ANN001
    from voti import queue
    from voti.models import Channel, Media
    from voti.sources.handlers import queue_channel_listing
    ch = factory.channel(platform_channel_id=UC, title="")
    job = queue_channel_listing(db, ch)
    db.commit()
    assert db.get(Channel, ch.id).list_status == "queued"

    seen = {}

    def fake_list(channel_id, fo, tabs, limit_per_tab=None, progress=None):
        with queue.session_scope() as s:  # observed while the job runs
            seen["status"] = s.get(Channel, ch.id).list_status
        seen["api"] = annotator_client.get(f"/api/channels/{ch.id}").json()
        return {"channel": CHANNEL_META, "tabs": {"videos": 2}, "entries": [_entry("aaaaaaaaaaa"),
                                                                             _entry("bbbbbbbbbbb")], "warnings": []}
    monkeypatch.setattr(yt, "list_channel", fake_list)
    job = run_job(db, job)
    assert job.status == "completed" and job.result["first_listing"] is True
    assert seen["status"] == "listing" and seen["api"]["list_status"] == "listing"
    assert seen["api"]["listing_job"]["status"] == "running"
    d = annotator_client.get(f"/api/channels/{ch.id}").json()
    assert d["list_status"] == "listed" and d["listing_job"] is None and d["title"] == "呆呆伙伴"
    # listing done → videos visible; caption details continue in the background
    assert d["detail_progress"] == {"detailed": 0, "total": 2, "failed": 0, "active": True,
                                    "langs_pending": 0}
    assert d["next_refresh_at"] is not None
    # first listing: nothing is "new"
    assert all(m.discovered_at is None for m in db.scalars(select(Media)))
    assert d["counts"]["new"] == 0


def test_list_status_follows_recovery_failure_and_cancel(db, h, factory, annotator_client):  # noqa: ANN001
    from voti import queue
    from voti.models import Channel, Job
    from voti.sources.handlers import queue_channel_listing
    ch = factory.channel(platform_channel_id=UC)
    job = queue_channel_listing(db, ch)
    db.commit()
    # running on a worker that died → recovered → queued
    db.execute(update(Job).where(Job.id == job.id).values(status="running", worker="ghost:1", attempts=1,
                                                          heartbeat_at=queue.utcnow() - timedelta(minutes=5)))
    db.execute(update(Channel).where(Channel.id == ch.id).values(list_status="listing"))
    db.commit()
    queue.recover_orphans()
    db.expire_all()
    assert db.get(Job, job.id).status == "queued" and db.get(Channel, ch.id).list_status == "queued"
    # out of attempts → interrupted → failed
    db.execute(update(Job).where(Job.id == job.id).values(status="running", worker="ghost:1", attempts=4,
                                                          heartbeat_at=queue.utcnow() - timedelta(minutes=5)))
    db.commit()
    queue.recover_orphans()
    db.expire_all()
    assert db.get(Job, job.id).status == "interrupted" and db.get(Channel, ch.id).list_status == "failed"

    # cancel of a queued refresh of an already listed channel → listed again
    db.execute(update(Channel).where(Channel.id == ch.id).values(list_status="listed",
                                                                 last_listed_at=queue.utcnow()))
    db.commit()
    r = annotator_client.post(f"/api/channels/{ch.id}/refresh")
    assert r.status_code == 200 and r.json()["kind"] == "channel.list"
    db.expire_all()
    assert db.get(Channel, ch.id).list_status == "queued"
    assert annotator_client.post(f"/api/jobs/{r.json()['id']}/cancel").status_code == 200
    db.expire_all()
    assert db.get(Channel, ch.id).list_status == "listed"

    # a stale stored status (row says listing, no job) is never shown, and the scheduler repairs it
    db.execute(update(Channel).where(Channel.id == ch.id).values(list_status="listing", last_listed_at=None))
    db.commit()
    assert annotator_client.get(f"/api/channels/{ch.id}").json()["list_status"] == "failed"
    from voti import scheduler
    assert scheduler.tick(lock=False)["reconciled"] == 1
    db.expire_all()
    assert db.get(Channel, ch.id).list_status in ("failed", "queued")  # queued: the scheduler re-listed it


def test_handle_url_resolve_keeps_channel_queued(db, h, monkeypatch):  # noqa: ANN001
    from voti import scheduler
    from voti.models import Channel, Job
    monkeypatch.setattr(yt, "resolve_channel",
                        lambda target, fo: {"channel": CHANNEL_META, "tabs": ["videos"], "warnings": []})
    job = run(db, "source.resolve", payload={"url": "https://www.youtube.com/@DaiDai"})
    assert job.status == "completed"
    ch = db.scalar(select(Channel))
    assert ch.list_status == "queued" and job.channel_id == ch.id
    scheduler.reconcile_channels(db)
    assert ch.list_status == "queued"
    assert db.scalar(select(Job).where(Job.kind == "channel.list")).status == "queued"


# ───────────────────────────── 5. scheduled refresh / new videos ─────────────────────────────

def test_scheduled_refresh_marks_new_videos(db, h, monkeypatch, factory, annotator_client):  # noqa: ANN001
    from voti import scheduler
    from voti.models import Job, Media, Setting
    now = scheduler.utcnow()
    old = factory.channel(platform_channel_id=UC, list_status="listed", last_listed_at=now - timedelta(hours=30))
    fresh = factory.channel(platform_channel_id=UC2, list_status="listed", last_listed_at=now - timedelta(hours=2))
    factory.media(platform_media_id="aaaaaaaaaaa", channel_id=old.id, status="detailed")

    out = scheduler.tick(lock=False)
    assert out["refresh_queued"] == 1
    jobs = db.scalars(select(Job).where(Job.kind == "channel.list")).all()
    assert [(j.channel_id, j.payload.get("scheduled")) for j in jobs] == [(old.id, True)]
    assert scheduler.tick(lock=False)["refresh_queued"] == 0  # already queued: not twice
    d = annotator_client.get(f"/api/channels/{fresh.id}").json()
    assert d["next_refresh_at"] is not None and d["list_status"] == "listed"

    monkeypatch.setattr(yt, "list_channel", lambda *a, **k: {
        "channel": CHANNEL_META, "tabs": {"videos": 2}, "warnings": [],
        "entries": [_entry("aaaaaaaaaaa"), _entry("nnnnnnnnnnn")]})
    job = run_job(db, jobs[0])
    assert job.status == "completed" and job.result["new"] == 1 and job.result["first_listing"] is False
    new = db.scalar(select(Media).where(Media.platform_media_id == "nnnnnnnnnnn"))
    assert new.discovered_at is not None and new.status == "listed"
    # caption info is fetched automatically, nothing is downloaded
    kinds = {j.kind for j in db.scalars(select(Job).where(Job.parent_id == job.id))}
    assert kinds == {"media.detail"}
    c = annotator_client
    items = c.get("/api/media", params={"channel_id": str(old.id)}).json()
    assert {x["platform_media_id"]: x["is_new"] for x in items["items"]} == {"aaaaaaaaaaa": False, "nnnnnnnnnnn": True}
    assert items["facets"]["new"] == 1
    assert [x["platform_media_id"] for x in c.get("/api/media", params={"is_new": "true"}).json()["items"]] == \
        ["nnnnnnnnnnn"]
    assert c.get(f"/api/channels/{old.id}").json()["counts"]["new"] == 1

    # auto refresh off
    db.merge(Setting(key="youtube", value={"auto_refresh_hours": 0, "sleep_between_requests_sec": 0}))
    db.commit()
    db.execute(update(Job).where(Job.kind == "channel.list").values(created_at=now - timedelta(days=3)))
    db.commit()
    assert scheduler.tick(lock=False)["refresh_queued"] == 0
    assert c.get(f"/api/channels/{old.id}").json()["next_refresh_at"] is None


def test_scheduler_staggers_and_locks(db, h, factory):  # noqa: ANN001
    from voti import scheduler
    from voti.models import Job
    for i in range(6):
        factory.channel(platform_channel_id=f"UC{i:022d}", list_status="new")
    assert scheduler.tick(lock=False)["refresh_queued"] == scheduler.MAX_PER_TICK
    assert scheduler.tick(lock=False)["refresh_queued"] == scheduler.MAX_PER_TICK
    # never more than MAX_SCHEDULED_QUEUED waiting
    assert scheduler.tick(lock=False)["refresh_queued"] == 0
    assert len(db.scalars(select(Job).where(Job.kind == "channel.list")).all()) == scheduler.MAX_SCHEDULED_QUEUED
    # the Redis lock lets one worker act per period
    assert "skipped" not in scheduler.tick()
    assert scheduler.tick() == {"skipped": "locked"}


def test_settings_auto_refresh_hours(admin_client):  # noqa: ANN001
    assert admin_client.get("/api/settings").json()["youtube"]["auto_refresh_hours"] == 24
    r = admin_client.put("/api/settings/youtube", json={"auto_refresh_hours": 0})
    assert r.status_code == 200 and r.json()["auto_refresh_hours"] == 0
    assert admin_client.put("/api/settings/youtube", json={"auto_refresh_hours": -1}).status_code == 422


# ───────────────────────────── 3. downloaded filter ─────────────────────────────

def test_downloaded_filter_and_facets(annotator_client, factory, db):  # noqa: ANN001
    from voti.models import Asset
    a = factory.media(status="ready", has_manual_captions=True)
    b = factory.media(status="failed", has_manual_captions=True)  # audio stored, a later step failed
    db.add(Asset(media_id=b.id, kind="original_audio", key=f"media/{b.id}/original.webm", sha256="x", bytes=1))
    db.commit()
    c_ = factory.media(status="detailed", has_manual_captions=True)
    c = annotator_client
    yes = c.get("/api/media", params={"downloaded": "true"}).json()
    assert {x["id"] for x in yes["items"]} == {str(a.id), str(b.id)}
    assert all(x["downloaded"] for x in yes["items"])
    no = c.get("/api/media", params={"downloaded": "false"}).json()
    assert [x["id"] for x in no["items"]] == [str(c_.id)] and no["items"][0]["downloaded"] is False
    assert no["facets"]["downloaded"] == {"yes": 2, "no": 1}  # facet ignores its own dimension
    # select-all download with the same filter object
    r = c.post("/api/media/download", json={"filter": {"downloaded": False}})
    assert r.json()["queued"] == 1


# ───────────────────────────── 6/7. review list, neighbors, media transcript ─────────────────────────────

def test_review_list_and_neighbors(annotator_client, factory):  # noqa: ANN001
    from datetime import datetime, timezone
    ch = factory.channel()
    m1 = factory.media(channel_id=ch.id, published_at=datetime(2024, 5, 1, tzinfo=timezone.utc))
    m2 = factory.media(channel_id=ch.id, published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    a = factory.segment(m1, 0, 2, original_text="甲乙")
    b = factory.segment(m1, 3, 5, original_text="丙丁", review_status="passed")
    c_ = factory.segment(m1, 6, 8, original_text="戊己")
    d = factory.segment(m2, 0, 2, original_text="甲庚")
    c = annotator_client
    params = {"review_status": "unchecked", "channel_id": str(ch.id)}
    r = c.get("/api/segments", params=params).json()
    assert [x["id"] for x in r["items"]] == [str(a.id), str(c_.id), str(d.id)]  # newest media first, then time
    assert r["items"][0]["media_title"] == m1.title and r["items"][0]["channel_title"] == ch.title
    n = c.get(f"/api/segments/{c_.id}/neighbors", params=params).json()
    assert n == {"prev_id": str(a.id), "next_id": str(d.id), "index": 1, "total": 3, "in_filter": True}
    first = c.get(f"/api/segments/{a.id}/neighbors", params=params).json()
    assert first["prev_id"] is None and first["index"] == 0
    # a segment that no longer matches (just reviewed) keeps its place, so "next" still works
    n = c.get(f"/api/segments/{b.id}/neighbors", params=params).json()
    assert n["prev_id"] == str(a.id) and n["next_id"] == str(c_.id) and n["in_filter"] is False and n["total"] == 3
    # text search + other filters go through the same filter
    n = c.get(f"/api/segments/{d.id}/neighbors", params={"q": "甲"}).json()
    assert n["prev_id"] == str(a.id) and n["next_id"] is None and n["total"] == 2
    assert c.get("/api/segments", params={"q": "甲", "min_duration": 1.5}).json()["total"] == 2


def test_media_segments_words_and_realign_on_edit(annotator_client, factory, db):  # noqa: ANN001
    from voti.models import Job, WordAlignment
    from voti.pipeline.align import MODEL_ID, text_hash
    m = factory.media(duration_sec=60, status="ready")
    a = factory.segment(m, 0, 2, original_text="你好", language="zh-TW")
    b = factory.segment(m, 2, 4, original_text="再見", language="zh-TW")
    factory.segment(m, 5, 6, active=False)
    db.add(WordAlignment(segment_id=a.id, media_id=m.id, revision=1, text_hash=text_hash("你好"), status="aligned",
                         model_id=MODEL_ID, units=[["你", 0.1, 0.4, 0.9], ["好", 0.4, 0.8, 0.8]]))
    db.commit()
    c = annotator_client
    segs = c.get(f"/api/media/{m.id}/segments").json()
    assert segs["total"] == 2 and [x["id"] for x in segs["items"]] == [str(a.id), str(b.id)]
    assert segs["items"][0]["review_status"] == "unchecked" and segs["items"][0]["start_sec"] == 0
    w = c.get(f"/api/media/{m.id}/words").json()
    assert w["words"] == {str(a.id): [["你", 0.1, 0.4], ["好", 0.4, 0.8]]}
    assert w["status"] == {"aligned": 1, "none": 1} and w["job_active"] is False
    d = c.get(f"/api/segments/{a.id}").json()
    assert d["words"] == [{"t": "你", "s": 0.1, "e": 0.4}, {"t": "好", "s": 0.4, "e": 0.8}]
    assert d["alignment"]["status"] == "aligned"

    # inline review: pass + text fix in one call → timing invalid, re-alignment of just this segment queued
    r = c.patch(f"/api/segments/{a.id}", json={"review_status": "passed", "corrected_text": "你們好"})
    assert r.status_code == 200
    d = r.json()
    assert d["text"] == "你們好" and d["review_status"] == "passed" and d["words"] is None
    assert d["alignment"]["status"] == "pending"
    job = db.scalar(select(Job).where(Job.kind == "media.align", Job.media_id == m.id))
    assert job.payload == {"chain": False, "segment_ids": [str(a.id)], "reason": "edit"}
    # a second edit while that job is queued joins it
    c.patch(f"/api/segments/{b.id}", json={"corrected_text": "再會"})
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.payload["segment_ids"] == [str(a.id), str(b.id)]
    assert c.get(f"/api/media/{m.id}/words").json()["words"] == {}
    # only review status: no re-alignment needed
    c.patch(f"/api/segments/{b.id}", json={"review_status": "needs_fix"})
    assert len(db.scalars(select(Job).where(Job.kind == "media.align")).all()) == 1
    assert db.get(type(m), m.id).status == "ready"


def test_align_endpoints(annotator_client, factory, db):  # noqa: ANN001
    from voti.models import Job, WordAlignment
    from voti.pipeline.align import MODEL_ID, text_hash
    ch = factory.channel()
    m1 = factory.media(channel_id=ch.id, status="ready")
    m2 = factory.media(channel_id=ch.id, status="ready")
    factory.media(channel_id=ch.id, status="detailed")  # no segments
    g1 = factory.segment(m1, 0, 2, original_text="你好")
    factory.segment(m2, 0, 2, original_text="再見")
    db.add(WordAlignment(segment_id=g1.id, media_id=m1.id, revision=1, text_hash=text_hash("你好"),
                         status="aligned", model_id=MODEL_ID, units=[["你好", 0, 1, None]]))
    db.commit()
    c = annotator_client
    assert c.post(f"/api/channels/{ch.id}/align").json() == {"queued": 1}  # m1 is aligned already
    assert c.post(f"/api/channels/{ch.id}/align", json={"force": True}).json() == {"queued": 2}
    jobs = db.scalars(select(Job).where(Job.kind == "media.align")).all()
    assert {j.media_id for j in jobs} == {m1.id, m2.id} and all(j.payload["chain"] is False for j in jobs)
    assert c.post(f"/api/media/{m1.id}/align").json() == {"queued": 1}
