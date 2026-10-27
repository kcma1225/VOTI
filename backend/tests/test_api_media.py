"""Media filters, facets, download (explicit + select-all), detail, retry/reprocess, timeline, sources/resolve."""
from __future__ import annotations

import io
import sys
import types
from datetime import datetime, timezone

import numpy as np
import pytest
from sqlalchemy import select


@pytest.fixture
def corpus(factory):
    ch1 = factory.channel(title="台語頻道")
    ch2 = factory.channel(title="other")
    m = {}
    m["manual"] = factory.media(channel_id=ch1.id, title="阿公講古", kind="video", has_manual_captions=True,
                                has_auto_captions=True, manual_caption_langs=["zh-TW"], auto_caption_langs=["zh"],
                                license="creative_commons", detailed_at=datetime.now(timezone.utc),
                                status="detailed", duration_sec=1200,
                                published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    m["manual_en"] = factory.media(channel_id=ch1.id, title="English", kind="video", has_manual_captions=True,
                                   has_auto_captions=False, manual_caption_langs=["en"], license="youtube",
                                   detailed_at=datetime.now(timezone.utc), status="detailed", duration_sec=300,
                                   published_at=datetime(2024, 6, 1, tzinfo=timezone.utc))
    m["auto_only"] = factory.media(channel_id=ch1.id, title="auto only", kind="short", has_manual_captions=False,
                                   has_auto_captions=True, auto_caption_langs=["zh-TW"], license="youtube",
                                   detailed_at=datetime.now(timezone.utc), status="detailed", duration_sec=50)
    m["none"] = factory.media(channel_id=ch1.id, title="no captions", kind="video", has_manual_captions=False,
                              has_auto_captions=False, license="youtube",
                              detailed_at=datetime.now(timezone.utc), status="detailed", duration_sec=100)
    m["unknown"] = factory.media(channel_id=ch1.id, title="listed only", kind="video", status="listed",
                                 duration_sec=200)
    m["ready"] = factory.media(channel_id=ch1.id, title="done", kind="video", has_manual_captions=True,
                               manual_caption_langs=["zh-TW"], status="ready", duration_sec=400,
                               detailed_at=datetime.now(timezone.utc))
    m["other"] = factory.media(channel_id=ch2.id, title="other channel", kind="live", has_manual_captions=True,
                               manual_caption_langs=["zh-TW"], status="detailed", duration_sec=900,
                               detailed_at=datetime.now(timezone.utc))
    return ch1, ch2, m


def _ids(r):
    return {x["id"] for x in r.json()["items"]}


def test_media_filters(annotator_client, corpus):
    ch1, ch2, m = corpus
    c = annotator_client
    r = c.get("/api/media", params={"channel_id": str(ch1.id)})
    assert r.status_code == 200, r.text
    # auto-only hidden by default
    assert _ids(r) == {str(m[k].id) for k in ("manual", "manual_en", "none", "unknown", "ready")}
    assert r.json()["total"] == 5
    r = c.get("/api/media", params={"channel_id": str(ch1.id), "include_auto": "true"})
    assert str(m["auto_only"].id) in _ids(r)
    r = c.get("/api/media", params={"captions": "auto"})
    assert _ids(r) == {str(m["auto_only"].id)}
    r = c.get("/api/media", params={"captions": "manual", "caption_lang": "zh-TW"})
    assert _ids(r) == {str(m[k].id) for k in ("manual", "ready", "other")}
    r = c.get("/api/media", params={"captions": "none"})
    assert _ids(r) == {str(m["none"].id)}
    r = c.get("/api/media", params={"kind": "short,live", "include_auto": "true"})
    assert _ids(r) == {str(m["auto_only"].id), str(m["other"].id)}
    r = c.get("/api/media", params={"license": "creative_commons"})
    assert _ids(r) == {str(m["manual"].id)}
    r = c.get("/api/media", params={"min_duration": 250, "max_duration": 1000})
    assert _ids(r) == {str(m[k].id) for k in ("manual_en", "ready", "other")}
    r = c.get("/api/media", params={"q": "阿公"})
    assert _ids(r) == {str(m["manual"].id)}
    r = c.get("/api/media", params={"detailed": "false"})
    assert _ids(r) == {str(m["unknown"].id)}
    r = c.get("/api/media", params={"status": "ready,listed"})
    assert _ids(r) == {str(m["ready"].id), str(m["unknown"].id)}
    r = c.get("/api/media", params={"published_after": "2024-03-01T00:00:00Z"})
    assert _ids(r) == {str(m["manual_en"].id)}
    r = c.get("/api/media", params={"sort": "-duration", "limit": 2})
    assert [x["id"] for x in r.json()["items"]] == [str(m["manual"].id), str(m["other"].id)]
    assert r.json()["total"] == 6
    item = r.json()["items"][0]
    assert item["channel"]["title"] == "台語頻道" and item["manual_caption_langs"] == ["zh-TW"]
    assert c.get("/api/media", params={"sort": "bogus"}).status_code == 422
    assert c.get("/api/media", params={"captions": "bogus"}).status_code == 422


def test_media_facets(annotator_client, corpus):
    ch1, _, m = corpus
    r = annotator_client.get("/api/media", params={"channel_id": str(ch1.id), "kind": "video"})
    f = r.json()["facets"]
    # kind facet ignores the kind filter itself (auto-only short is hidden by default)
    assert f["kind"] == {"video": 5}
    # captions facet ignores captions/include_auto so auto-only media are counted
    assert f["captions"] == {"manual": 3, "auto_only": 0, "none": 1, "unknown": 1, "empty": 0}
    r = annotator_client.get("/api/media", params={"channel_id": str(ch1.id)})
    f = r.json()["facets"]
    assert f["captions"] == {"manual": 3, "auto_only": 1, "none": 1, "unknown": 1, "empty": 0}
    assert f["status"] == {"detailed": 3, "listed": 1, "ready": 1}
    assert f["license"] == {"creative_commons": 1, "youtube": 2, "unknown": 2}
    r = annotator_client.get("/api/media", params={"channel_id": str(ch1.id), "status": "ready"})
    assert r.json()["total"] == 1
    assert r.json()["facets"]["status"] == {"detailed": 3, "listed": 1, "ready": 1}


def test_download_explicit_ids(annotator_client, corpus, db):
    from voti.models import Job, Media
    _, _, m = corpus
    ids = [str(m[k].id) for k in ("manual", "auto_only", "ready", "unknown")]
    r = annotator_client.post("/api/media/download", json={"media_ids": ids, "caption_langs": ["zh-TW"]})
    assert r.status_code == 200, r.text
    body = r.json()
    # manual: ok; unknown captions: queued (worker decides); auto_only: skipped; ready: already done
    assert body["queued"] == 2 and body["skipped"] == 2
    assert body["skipped_reasons"] == {"no_usable_captions": 1, "already_downloaded": 1}
    db.expire_all()
    assert db.get(Media, m["manual"].id).status == "queued"
    assert db.get(Media, m["auto_only"].id).status == "detailed"
    jobs = db.scalars(select(Job).where(Job.kind == "media.download")).all()
    assert {j.media_id for j in jobs} == {m["manual"].id, m["unknown"].id}
    assert jobs[0].payload == {"caption_langs": ["zh-TW"], "include_auto": False}
    assert jobs[0].queue == "download"
    # include_auto lets auto-only through
    r = annotator_client.post("/api/media/download", json={"media_ids": [str(m["auto_only"].id)],
                                                           "include_auto": True})
    assert r.json()["queued"] == 1


def test_download_select_all_uses_media_filter(annotator_client, corpus, db):
    from voti.models import Job
    ch1, _, m = corpus
    flt = {"channel_id": str(ch1.id), "kind": "video", "captions": "manual"}
    listed = _ids(annotator_client.get("/api/media", params=flt))
    assert listed == {str(m[k].id) for k in ("manual", "manual_en", "ready")}
    r = annotator_client.post("/api/media/download", json={"filter": flt, "caption_langs": ["zh-TW"]})
    assert r.status_code == 200, r.text
    # manual_en lacks zh-TW manual captions, ready is already done
    assert r.json() == {"queued": 1, "skipped": 2,
                        "skipped_reasons": {"no_usable_captions": 1, "already_downloaded": 1}}
    jobs = db.scalars(select(Job.media_id).where(Job.kind == "media.download")).all()
    assert jobs == [m["manual"].id]
    # list form of kind is accepted in the body filter too
    r = annotator_client.post("/api/media/download", json={"filter": {"channel_id": str(ch1.id), "kind": ["video"]}})
    assert r.status_code == 200
    assert annotator_client.post("/api/media/download", json={}).status_code == 400


def test_media_detail_retry_reprocess(annotator_client, factory, db):
    from voti.models import Job, Track
    from voti.queue import enqueue
    media = factory.media(status="failed", failure_code="exception", detailed_at=datetime.now(timezone.utc))
    db.add(Track(media_id=media.id, language="zh-TW", kind="manual", format="vtt"))
    job = enqueue(db, "media.download", media_id=media.id, payload={})
    job.status, job.error_code = "failed", "http_403"
    db.commit()
    r = annotator_client.get(f"/api/media/{media.id}")
    assert r.status_code == 200
    d = r.json()
    assert d["jobs"][0]["status"] == "failed" and d["tracks"][0]["language"] == "zh-TW"
    r = annotator_client.post(f"/api/media/{media.id}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert annotator_client.post(f"/api/media/{media.id}/retry").status_code == 409
    db.expire_all()
    assert db.get(Job, job.id).status == "queued"
    r = annotator_client.post(f"/api/media/{media.id}/reprocess", json={"from_step": "media.quality"})
    assert r.status_code == 409  # not downloaded yet (status queued)
    from voti.models import Media
    db.get(Media, media.id).status = "ready"
    db.commit()
    r = annotator_client.post(f"/api/media/{media.id}/reprocess", json={"from_step": "media.quality"})
    assert r.status_code == 200 and r.json()["kind"] == "media.quality" and r.json()["queue"] == "ml"
    assert annotator_client.post(f"/api/media/{media.id}/reprocess", json={"from_step": "rm -rf"}).status_code == 400
    assert annotator_client.get("/api/media/00000000-0000-0000-0000-000000000000").status_code == 404


def test_timeline(annotator_client, factory, db):
    from voti import storage
    from voti.models import Cue, DiarizationTurn, SpeechRegion, Track
    media = factory.media(duration_sec=100.0, status="ready")
    n = 10000
    buf = io.BytesIO()
    np.savez(buf, hop_sec=np.float32(0.01), brouhaha_vad=np.linspace(0, 1, n, dtype=np.float32),
             snr=np.full(n, 20.0, dtype=np.float32), c50=np.zeros(n, np.float32),
             silero_vad=np.zeros(n, np.float32), dnsmos_t=np.zeros(1, np.float32),
             dnsmos=np.zeros((1, 4), np.float32))
    storage.put_bytes(buf.getvalue(), storage.media_key(media.id, "frames_quality.npz"))
    t = factory.add(Track(media_id=media.id, language="zh-TW", kind="manual", format="vtt"))
    db.add_all([Cue(track_id=t.id, idx=0, start_sec=1, end_sec=2, raw_text="a", text="a"),
                SpeechRegion(media_id=media.id, source="brouhaha", start_sec=1, end_sec=5),
                SpeechRegion(media_id=media.id, source="silero", start_sec=1, end_sec=6),
                DiarizationTurn(media_id=media.id, local_speaker="SPEAKER_00", start_sec=1, end_sec=5,
                                model_id="pyannote")])
    db.commit()
    factory.segment(media, 1.0, 2.0)
    r = annotator_client.get(f"/api/media/{media.id}/timeline")
    assert r.status_code == 200, r.text
    tl = r.json()
    assert len(tl["vad"]) <= 2000 and len(tl["snr"]) == len(tl["vad"])
    assert tl["hop_sec"] == pytest.approx(0.05)
    assert tl["snr"][0] == pytest.approx(20.0)
    assert tl["speech_regions"] == [[1.0, 5.0]]
    assert tl["diarization"][0]["speaker"] == "SPEAKER_00"
    assert tl["cues"] == [{"start": 1.0, "end": 2.0, "text": "a"}]
    assert len(tl["segments"]) == 1
    # media without analysis still returns an empty timeline
    other = factory.media()
    r = annotator_client.get(f"/api/media/{other.id}/timeline")
    assert r.status_code == 200 and r.json()["vad"] == []
    assert annotator_client.get(f"/api/media/{other.id}/quality").status_code == 404


def _fake_youtube(monkeypatch, result=None, error=None):
    mod = types.ModuleType("voti.sources.youtube")

    def classify_url(url):
        if error:
            raise ValueError(error)
        return dict(result)
    mod.classify_url = classify_url
    monkeypatch.setitem(sys.modules, "voti.sources.youtube", mod)
    import voti.sources
    monkeypatch.setattr(voti.sources, "youtube", mod, raising=False)


def test_resolve_video_channel_playlist_handle(annotator_client, db, monkeypatch):
    from voti.models import Channel, Job, Media, Playlist
    _fake_youtube(monkeypatch, {"type": "video", "video_id": "abcdefghijk",
                                "canonical_url": "https://www.youtube.com/watch?v=abcdefghijk"})
    r = annotator_client.post("/api/sources/resolve", json={"url": "https://youtu.be/abcdefghijk"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "video" and body["media"]["platform_media_id"] == "abcdefghijk"
    assert body["job"]["kind"] == "source.resolve" and body["job"]["payload"] == {"url": "https://youtu.be/abcdefghijk"}
    # repeat: no duplicate media row, job deduplicated while active
    r2 = annotator_client.post("/api/sources/resolve", json={"url": "https://youtu.be/abcdefghijk"})
    assert r2.json()["job"]["id"] == body["job"]["id"]
    assert len(db.scalars(select(Media)).all()) == 1

    _fake_youtube(monkeypatch, {"type": "channel", "channel_id": "UC1234567890123456789012", "handle": "@taigi",
                                "canonical_url": "https://www.youtube.com/channel/UC1234567890123456789012"})
    r = annotator_client.post("/api/sources/resolve", json={"url": "https://www.youtube.com/@taigi/videos"})
    assert r.json()["channel"]["handle"] == "@taigi"
    assert r.json()["job"]["channel_id"] == r.json()["channel"]["id"]

    _fake_youtube(monkeypatch, {"type": "playlist", "playlist_id": "PL123",
                                "canonical_url": "https://www.youtube.com/playlist?list=PL123"})
    r = annotator_client.post("/api/sources/resolve", json={"url": "https://www.youtube.com/playlist?list=PL123"})
    assert r.json()["playlist"]["platform_playlist_id"] == "PL123"
    pid = r.json()["playlist"]["id"]
    assert annotator_client.get(f"/api/playlists/{pid}").json()["media_count"] == 0

    _fake_youtube(monkeypatch, {"type": "channel", "handle": "@onlyhandle",
                                "canonical_url": "https://www.youtube.com/@onlyhandle"})
    n_channels = len(db.scalars(select(Channel)).all())
    r = annotator_client.post("/api/sources/resolve", json={"url": "https://www.youtube.com/@onlyhandle"})
    assert r.status_code == 200 and r.json()["channel"] is None
    assert len(db.scalars(select(Channel)).all()) == n_channels
    assert len(db.scalars(select(Playlist)).all()) == 1
    assert db.scalar(select(Job).where(Job.payload["url"].astext == "https://www.youtube.com/@onlyhandle"))

    _fake_youtube(monkeypatch, error="not youtube")
    r = annotator_client.post("/api/sources/resolve", json={"url": "https://example.com"})
    assert r.status_code == 400 and r.json()["code"] == "unsupported_url"


def test_channels(annotator_client, corpus, db):
    from voti.models import Job
    ch1, _, m = corpus
    r = annotator_client.get("/api/channels")
    assert r.json()["total"] == 2
    r = annotator_client.get("/api/channels", params={"q": "台語"})
    item = r.json()["items"][0]
    assert item["counts"]["listed"] == 6 and item["counts"]["ready"] == 1 and item["counts"]["detailed"] == 5
    assert item["counts"]["with_manual_captions"] == 3
    assert item["hours"]["listed"] == pytest.approx((1200 + 300 + 50 + 100 + 200 + 400) / 3600, abs=1e-3)
    d = annotator_client.get(f"/api/channels/{ch1.id}").json()
    assert d["speakers"] == {"candidates": 0, "total_sec": 0.0}
    r = annotator_client.patch(f"/api/channels/{ch1.id}", json={"notes": "多位講者"})
    assert r.json()["notes"] == "多位講者"
    j1 = annotator_client.post(f"/api/channels/{ch1.id}/refresh").json()
    j2 = annotator_client.post(f"/api/channels/{ch1.id}/refresh").json()
    assert j1["kind"] == "channel.list" and j1["id"] == j2["id"]
    r = annotator_client.post(f"/api/channels/{ch1.id}/detail", json={})
    assert r.json() == {"queued": 1}  # only the un-detailed one
    r = annotator_client.post(f"/api/channels/{ch1.id}/detail", json={"media_ids": [str(m["manual"].id)]})
    assert r.json() == {"queued": 1}
    assert len(db.scalars(select(Job).where(Job.kind == "media.detail")).all()) == 2
    assert annotator_client.post(f"/api/channels/{ch1.id}/dedup").json()["kind"] == "channel.dedup"
