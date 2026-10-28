"""API extensions the frontend relies on (fields marked `// ext` in frontend/src/api/types.ts)."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from test_api_datasets import _run_job, ds_corpus  # noqa: F401  (fixture)
from test_api_segments import upload_wav


def test_playlists_list_and_channel_counts(annotator_client, factory, db):
    from voti.models import Playlist, PlaylistItem
    ch, other = factory.channel(), factory.channel()
    v1, v2 = factory.media(channel_id=ch.id, kind="video"), factory.media(channel_id=ch.id, kind="video")
    factory.media(channel_id=ch.id, kind="short")
    p1 = factory.add(Playlist(platform_playlist_id="PL1", channel_id=ch.id, title="合輯", url="u",
                              metadata_={"thumbnails": [{"url": "a"}, {"url": "https://i.ytimg.com/x.jpg"}]}))
    factory.add(Playlist(platform_playlist_id="PL2", channel_id=other.id, title="x", url="u"))
    db.add_all([PlaylistItem(playlist_id=p1.id, media_id=v1.id), PlaylistItem(playlist_id=p1.id, media_id=v2.id)])
    db.commit()
    r = annotator_client.get("/api/playlists", params={"channel_id": str(ch.id)})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    item = r.json()["items"][0]
    assert item["media_count"] == 2 and item["thumbnail_url"] == "https://i.ytimg.com/x.jpg"
    assert annotator_client.get("/api/playlists").json()["total"] == 2
    d = annotator_client.get(f"/api/channels/{ch.id}").json()
    assert d["kind_counts"] == {"video": 2, "short": 1} and d["playlist_count"] == 1


def test_upsampled_filter_and_quality_fields(annotator_client, factory, db):
    from voti.models import MediaQuality
    a, b, c = factory.media(), factory.media(), factory.media()
    spectrum = [{"hz": 100.0, "db": -20.0}, {"hz": 8000.0, "db": -80.0}]
    db.add_all([MediaQuality(media_id=a.id, sample_rate=48000, upsampled_suspect=True,
                             details={"spectrum": spectrum, "model_versions": {"x": "1"}}),
                MediaQuality(media_id=b.id, sample_rate=44100, upsampled_suspect=False)])
    db.commit()
    r = annotator_client.get("/api/media", params={"upsampled_suspect": "true"})
    assert [x["id"] for x in r.json()["items"]] == [str(a.id)]
    assert r.json()["items"][0]["quality"]["sample_rate"] == 48000
    assert r.json()["items"][0]["quality"]["upsampled_suspect"] is True
    r = annotator_client.get("/api/media", params={"upsampled_suspect": "false"})
    assert {x["id"] for x in r.json()["items"]} == {str(b.id), str(c.id)}
    q = annotator_client.get(f"/api/media/{a.id}/quality").json()
    assert q["details"]["spectrum"] == spectrum
    assert annotator_client.get(f"/api/media/{a.id}").json()["quality"]["details"]["spectrum"] == spectrum
    assert annotator_client.get("/api/quality/summary").json()["media_count"] == 2


def test_job_channel_title(annotator_client, factory, db):
    from voti.queue import enqueue
    ch = factory.channel(title="頻道甲")
    m = factory.media(channel_id=ch.id)
    enqueue(db, "media.detail", media_id=m.id)          # channel via the media
    enqueue(db, "channel.list", channel_id=ch.id)       # channel directly
    db.commit()
    items = annotator_client.get("/api/jobs").json()["items"]
    assert [j["channel_title"] for j in items] == ["頻道甲", "頻道甲"]


def test_repeated_examples_title_and_patch_shape(annotator_client, factory, db):
    from voti.models import RepeatedRegion
    ch = factory.channel()
    m = factory.media(channel_id=ch.id, title="片頭影片")
    gid = uuid.uuid4()
    db.add(RepeatedRegion(channel_id=ch.id, group_id=gid, media_id=m.id, start_sec=0, end_sec=4, position="intro",
                          excluded=True))
    db.commit()
    g = annotator_client.get(f"/api/channels/{ch.id}/repeated").json()[0]
    assert g["examples"][0]["media_title"] == "片頭影片"
    r = annotator_client.patch(f"/api/repeated/{gid}", json={"excluded": False}).json()
    assert r["group_id"] == str(gid) and r["excluded"] is False and r["position"] == "intro"
    assert r["examples"][0]["media_title"] == "片頭影片" and r["segments_updated"] == 0


def test_segment_audio_rev_cache(annotator_client, factory):
    m = factory.media(duration_sec=10)
    upload_wav(m.id, 10.0)
    g = factory.segment(m, 1, 2)
    r = annotator_client.get(f"/api/audio/segments/{g.id}", params={"rev": 1})
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    assert r.headers["x-segment-revision"] == "1"
    r = annotator_client.get(f"/api/audio/segments/{g.id}")
    assert r.headers["cache-control"] == "private, no-cache"
    etag = r.headers["etag"]
    annotator_client.patch(f"/api/segments/{g.id}", json={"end_sec": 2.5})
    r = annotator_client.get(f"/api/audio/segments/{g.id}", params={"rev": 1})  # stale rev → not cacheable
    assert r.headers["cache-control"] == "private, no-cache" and r.headers["etag"] != etag
    assert r.headers["x-segment-revision"] == "2"


def test_review_history_and_reviewed_by_shape(annotator_client, factory):
    g = factory.segment(factory.media(), 0, 2)
    d = annotator_client.patch(f"/api/segments/{g.id}", json={"review_status": "passed"}).json()
    assert d["reviewed_by"]["display_name"] == "anno" and "id" in d["reviewed_by"]
    h = d["history"][0]
    assert h["user"]["display_name"] == "anno" and h["at"] == h["created_at"]


def test_youtube_sleep_between_requests(admin_client):
    assert admin_client.get("/api/settings").json()["youtube"]["sleep_between_requests_sec"] == 0.75
    full = {"rate_limit_kbps": 0, "sleep_between_downloads_sec": 5, "sleep_between_requests_sec": 3.5,
            "max_concurrent_downloads": 1, "cookies_enabled": False}
    r = admin_client.put("/api/settings/youtube", json=full)
    assert r.status_code == 200 and r.json()["sleep_between_requests_sec"] == 3.5


def test_speakers_q(annotator_client, factory):
    from voti.models import ClusterRun, Speaker
    run = factory.add(ClusterRun(model_id="m", algorithm="a", threshold=0.5, is_current=True))
    factory.add(Speaker(run_id=run.id, label="cand_001", display_name="阿明"))
    factory.add(Speaker(run_id=run.id, label="cand_002"))
    assert [x["label"] for x in annotator_client.get("/api/speakers", params={"q": "阿明"}).json()["items"]] == \
        ["cand_001"]
    assert annotator_client.get("/api/speakers", params={"q": "cand"}).json()["total"] == 2


def test_workers_overview(annotator_client, factory, db):
    from voti.models import utcnow
    from voti.queue import enqueue, redis_client
    redis_client().set("voti:worker:host:1", "fetch", ex=120)
    m = factory.media(title="下載中")
    j = enqueue(db, "media.download", media_id=m.id)
    j.status, j.worker, j.started_at, j.heartbeat_at = "running", "host:1", utcnow(), utcnow()
    j2 = enqueue(db, "media.quality", media_id=m.id)
    j2.status, j2.worker, j2.heartbeat_at = "running", "gone:2", utcnow() - timedelta(minutes=3)
    db.commit()
    workers = {w["name"]: w for w in annotator_client.get("/api/stats/overview").json()["workers"]}
    w = workers["host:1"]
    assert w["alive"] is True and w["queues"] == ["fetch"] and w["last_seen_at"]
    assert w["current_job"] == {"id": str(j.id), "kind": "media.download", "media_title": "下載中"}
    assert workers["gone:2"]["alive"] is False and workers["gone:2"]["current_job"]["kind"] == "media.quality"


def test_export_and_dataset_extensions(annotator_client, ds_corpus, db):  # noqa: F811
    _, m1, m2, _, _ = ds_corpus
    upload_wav(m1.id, 10.0)
    upload_wav(m2.id, 10.0)
    d = annotator_client.post("/api/datasets", json={"name": "ext", "filters": {}}).json()
    assert d["created_by"]["display_name"] == "anno" and d["last_export"] is None and d["segment_count"] is None
    e = annotator_client.post(f"/api/datasets/{d['id']}/export", json={"format": "csv", "include_audio": True}).json()
    assert e["format"] == "csv" and e["include_audio"] is True and e["progress"] == 0 and e["files"] == []
    assert e["size_bytes"] is None and e["finished_at"] is None
    _run_job(e["job"]["id"])
    e = annotator_client.get(f"/api/exports/{e['id']}").json()
    assert e["progress"] == 1.0 and e["finished_at"]
    assert e["files"] == ["manifest.jsonl", "manifest.csv", "README.md", "dataset.tar"]
    sizes = {f["name"]: f["size_bytes"] for f in e["file_details"]}
    assert all(v > 0 for v in sizes.values()) and e["size_bytes"] == sum(sizes.values())
    tar = next(f for f in e["file_details"] if f["name"] == "dataset.tar")
    r = annotator_client.get(tar["url"])
    assert r.status_code == 200 and len(r.content) == tar["size_bytes"]
    ds = annotator_client.get(f"/api/datasets/{d['id']}").json()
    assert ds["segment_count"] == 3 and ds["total_sec"] == pytest.approx(6.0)
    assert ds["last_export"]["id"] == e["id"] and ds["last_export"]["status"] == "completed"
    listed = annotator_client.get("/api/datasets").json()["items"][0]
    assert listed["last_export"]["format"] == "csv"
