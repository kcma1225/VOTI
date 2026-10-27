"""Segment review + audit, speaker override, similar (pgvector), audio cut/range, speakers, repeated, jobs, stats,
settings, quality."""
from __future__ import annotations

import io
import struct
import wave

import numpy as np
import pytest
from sqlalchemy import select

SR = 16000


def make_wav(n_samples: int, extra_chunk: bytes = b"voti test", sr: int = SR) -> tuple[bytes, np.ndarray]:
    """PCM16 mono WAV with a LIST chunk (odd size → pad byte) before `data`, so data does not start at 44."""
    pcm = (np.arange(n_samples) % 20000).astype("<i2")
    fmt = struct.pack("<HHIIHH", 1, 1, sr, sr * 2, 2, 16)
    lst = b"INFO" + extra_chunk
    chunks = b"fmt " + struct.pack("<I", 16) + fmt
    chunks += b"LIST" + struct.pack("<I", len(lst)) + lst + (b"\0" if len(lst) % 2 else b"")
    chunks += b"data" + struct.pack("<I", pcm.nbytes) + pcm.tobytes()
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks, pcm


def upload_wav(media_id, seconds: float = 10.0) -> np.ndarray:
    from voti import storage
    data, pcm = make_wav(int(seconds * SR))
    storage.put_bytes(data, storage.media_key(media_id, "normalized.wav"), "audio/wav")
    return pcm


# ───────────────────────────── review ─────────────────────────────

def test_segment_list_filters(annotator_client, factory):
    ch = factory.channel()
    m = factory.media(channel_id=ch.id)
    a = factory.segment(m, 0, 2, snr=20, dnsmos_ovrl=3.5, flags=["low_speech"], review_status="passed")
    b = factory.segment(m, 2, 5, snr=5, dnsmos_ovrl=2.0, flags=["clipped"], text_source="auto_caption")
    factory.segment(m, 5, 6, active=False)
    c = annotator_client
    r = c.get("/api/segments", params={"media_id": str(m.id)})
    assert r.json()["total"] == 2
    assert [x["id"] for x in r.json()["items"]] == [str(a.id), str(b.id)]
    assert r.json()["items"][0]["channel_title"] == ch.title and r.json()["items"][0]["duration"] == 2.0
    assert {x["id"] for x in c.get("/api/segments", params={"min_snr": 10}).json()["items"]} == {str(a.id)}
    assert {x["id"] for x in c.get("/api/segments", params={"flags": "clipped,foo"}).json()["items"]} == {str(b.id)}
    assert {x["id"] for x in c.get("/api/segments", params={"exclude_flags": "clipped"}).json()["items"]} == {str(a.id)}
    assert {x["id"] for x in c.get("/api/segments", params={"text_source": "auto_caption"}).json()["items"]} == \
        {str(b.id)}
    assert {x["id"] for x in c.get("/api/segments", params={"review_status": "passed"}).json()["items"]} == {str(a.id)}
    assert {x["id"] for x in c.get("/api/segments", params={"channel_id": str(ch.id), "sort": "-duration"})
            .json()["items"]} == {str(a.id), str(b.id)}
    assert c.get("/api/segments", params={"has_profile": "true"}).json()["total"] == 0


def test_segment_review_edit_and_audit(annotator_client, factory, db):
    from voti.models import AuditLog, Job, Segment
    m = factory.media(duration_sec=60)
    g = factory.segment(m, 1.0, 3.0, original_text="原本")
    c = annotator_client
    r = c.patch(f"/api/segments/{g.id}", json={"corrected_text": "改過", "review_status": "passed",
                                                "review_note": "ok"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["text"] == "改過" and d["original_text"] == "原本" and d["review_status"] == "passed"
    assert d["revision"] == 1 and d["reviewed_by"] is not None
    assert d["history"][0]["action"] == "segment.review" and d["history"][0]["username"] == "anno"
    assert d["history"][0]["before"] == {"corrected_text": None, "review_status": "unchecked", "review_note": ""}
    # boundary change bumps revision and re-enqueues embed/profile
    r = c.patch(f"/api/segments/{g.id}", json={"start_sec": 1.2, "end_sec": 3.4})
    assert r.status_code == 200
    d = r.json()
    assert d["revision"] == 2 and "boundary_adjusted" in d["flags"] and d["start_sec"] == 1.2
    kinds = set(db.scalars(select(Job.kind).where(Job.media_id == m.id)).all())
    assert kinds == {"media.embed", "media.profile", "media.align"}  # align: queued by the text edit already
    align = db.scalar(select(Job).where(Job.media_id == m.id, Job.kind == "media.align"))
    assert align.payload["chain"] is False and align.payload["segment_ids"] == [str(g.id)]
    # no-op patch writes nothing
    n_audit = len(db.scalars(select(AuditLog)).all())
    assert c.patch(f"/api/segments/{g.id}", json={"review_status": "passed"}).status_code == 200
    assert len(db.scalars(select(AuditLog)).all()) == n_audit
    # validation
    assert c.patch(f"/api/segments/{g.id}", json={"end_sec": 1.0}).status_code == 422
    assert c.patch(f"/api/segments/{g.id}", json={"end_sec": 100}).status_code == 422
    assert c.patch(f"/api/segments/{g.id}", json={"review_status": "bogus"}).status_code == 422
    db.expire_all()
    assert db.get(Segment, g.id).original_text == "原本"
    detail = c.get(f"/api/segments/{g.id}").json()
    assert [h["action"] for h in detail["history"]] == ["segment.review", "segment.review"]


def test_speaker_override(annotator_client, factory, db):
    from voti.models import AuditLog, SpeakerOverride
    m = factory.media()
    g = factory.segment(m, 0, 2)
    r = annotator_client.put(f"/api/segments/{g.id}/speaker", json={"speaker_name": "阿明"})
    assert r.status_code == 200 and r.json()["speaker_override"] == "阿明"
    assert db.get(SpeakerOverride, g.id).speaker_name == "阿明"
    assert annotator_client.delete(f"/api/segments/{g.id}/speaker").status_code == 204
    db.expire_all()
    assert db.get(SpeakerOverride, g.id) is None
    actions = db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all()
    assert actions == ["segment.speaker_override", "segment.speaker_override_delete"]


def test_similar(annotator_client, factory, db):
    from voti.models import EMBEDDING_DIM, Embedding
    m = factory.media()
    base = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    base[0] = 1
    near = base.copy()
    near[1] = 0.1
    far = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    far[2] = 1
    segs = [factory.segment(m, i, i + 1) for i in range(4)]
    for g, v in zip(segs, [base, near, far]):
        db.add(Embedding(segment_id=g.id, revision=1, model_id="ecapa", vector=v.tolist()))
    # stale revision embedding must be ignored
    db.add(Embedding(segment_id=segs[3].id, revision=0, model_id="ecapa", vector=base.tolist()))
    db.commit()
    r = annotator_client.get(f"/api/segments/{segs[0].id}/similar", params={"k": 5})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [x["segment"]["id"] for x in out] == [str(segs[1].id), str(segs[2].id)]
    assert out[0]["similarity"] > 0.99 and abs(out[1]["similarity"]) < 1e-6
    assert annotator_client.get(f"/api/segments/{segs[3].id}/similar").status_code == 409


# ───────────────────────────── audio ─────────────────────────────

def test_wav_header_parser():
    from voti.api.audio import parse_wav_header
    data, pcm = make_wav(SR)
    info = parse_wav_header(data, len(data))
    assert info.sample_rate == SR and info.channels == 1 and info.bits_per_sample == 16
    assert info.data_offset != 44 and data[info.data_offset:info.data_offset + 4] == pcm[:2].tobytes()
    assert info.data_size == pcm.nbytes
    assert parse_wav_header(data[:30], len(data)) is None
    with pytest.raises(ValueError):
        parse_wav_header(b"OggS" + b"\0" * 40)


def test_segment_audio_cut(annotator_client, factory):
    m = factory.media(duration_sec=10)
    pcm = upload_wav(m.id, 10.0)
    g = factory.segment(m, 2.0, 3.5)
    r = annotator_client.get(f"/api/audio/segments/{g.id}", params={"pad": 0.25})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    a, b = int(1.75 * SR), int(3.75 * SR)
    assert np.array_equal(frames, pcm[a:b])
    assert int(r.headers["content-length"]) == len(r.content)
    h = annotator_client.head(f"/api/audio/segments/{g.id}", params={"pad": 0.25})
    assert h.status_code == 200 and int(h.headers["content-length"]) == len(r.content)
    # padding is clipped at the start of the file
    g0 = factory.segment(m, 0.1, 0.5)
    r = annotator_client.get(f"/api/audio/segments/{g0.id}")
    with wave.open(io.BytesIO(r.content)) as w:
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert np.array_equal(frames, pcm[0:int(0.8 * SR)])
    missing = factory.segment(factory.media(), 0, 1)
    assert annotator_client.get(f"/api/audio/segments/{missing.id}").status_code == 404


def test_media_audio_range(annotator_client, factory):
    from voti import storage
    m = factory.media()
    upload_wav(m.id, 1.0)
    whole = storage.get_bytes(storage.media_key(m.id, "normalized.wav"))
    r = annotator_client.get(f"/api/audio/media/{m.id}")
    assert r.status_code == 200 and r.content == whole and r.headers["accept-ranges"] == "bytes"
    r = annotator_client.get(f"/api/audio/media/{m.id}", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == whole[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(whole)}"
    r = annotator_client.get(f"/api/audio/media/{m.id}", headers={"Range": "bytes=-10"})
    assert r.status_code == 206 and r.content == whole[-10:]
    r = annotator_client.get(f"/api/audio/media/{m.id}", headers={"Range": f"bytes={len(whole) + 5}-"})
    assert r.status_code == 416
    assert annotator_client.head(f"/api/audio/media/{m.id}").headers["content-length"] == str(len(whole))


# ───────────────────────────── speakers ─────────────────────────────

def test_speakers_and_diversity(annotator_client, factory, db):
    from voti.models import AuditLog, ClusterRun, Speaker, SpeakerAssignment
    ch1, ch2 = factory.channel(title="A"), factory.channel(title="B")
    m1, m2 = factory.media(channel_id=ch1.id), factory.media(channel_id=ch2.id)
    run = factory.add(ClusterRun(model_id="ecapa", algorithm="agglomerative", threshold=0.5, is_current=True))
    s1 = factory.add(Speaker(run_id=run.id, label="cand_001", total_sec=30, segment_count=2, media_count=1,
                             channel_ids=[str(ch1.id)],
                             profile={"sex": {"male": 0.8, "female": 0.2}, "age_group": {"adult": 1.0},
                                      "emotion": {"neutral": 0.5, "happy": 0.5}}))
    s2 = factory.add(Speaker(run_id=run.id, label="cand_002", total_sec=10, segment_count=1, media_count=1,
                             channel_ids=[str(ch1.id), str(ch2.id)], profile={}))
    g1, g2 = factory.segment(m1, 0, 20), factory.segment(m1, 20, 30)
    g3 = factory.segment(m2, 0, 10)
    db.add_all([SpeakerAssignment(run_id=run.id, segment_id=g1.id, speaker_id=s1.id, similarity=0.9),
                SpeakerAssignment(run_id=run.id, segment_id=g2.id, speaker_id=s1.id, similarity=0.8),
                SpeakerAssignment(run_id=run.id, segment_id=g3.id, speaker_id=s2.id, similarity=0.7)])
    db.commit()
    c = annotator_client
    r = c.get("/api/speakers")
    assert [x["label"] for x in r.json()["items"]] == ["cand_001", "cand_002"]
    assert c.get("/api/speakers", params={"channel_id": str(ch2.id)}).json()["total"] == 1
    assert len(c.get("/api/speakers/runs").json()) == 1
    d = c.get(f"/api/speakers/{s1.id}").json()
    assert [x["id"] for x in d["segments"]] == [str(g1.id), str(g2.id)]
    assert d["segments"][0]["speaker"]["label"] == "cand_001"
    assert d["channels"] == [{"id": str(ch1.id), "title": "A", "total_sec": 30.0}]
    dv = c.get("/api/speakers/diversity").json()
    assert dv["by_sex"] == {"male": 24.0, "female": 6.0, "unknown": 10.0}
    assert dv["by_age_group"] == {"adult": 30.0, "unknown": 10.0}
    assert dv["by_emotion"]["happy"] == 15.0
    assert dv["gini"] == pytest.approx(0.25)
    assert [x["label"] for x in dv["speaker_hours"]] == ["cand_001", "cand_002"]
    by_ch = {x["title"]: x for x in dv["by_channel"]}
    assert by_ch["A"]["speakers"] == 1 and by_ch["A"]["top_speaker_share"] == 1.0 and by_ch["B"]["total_sec"] == 10
    dv2 = c.get("/api/speakers/diversity", params={"channel_id": str(ch2.id)}).json()
    assert dv2["by_sex"]["unknown"] == 10.0 and dv2["by_sex"]["male"] == 0.0
    r = c.patch(f"/api/speakers/{s1.id}", json={"display_name": "阿明", "verified": True})
    assert r.json()["display_name"] == "阿明" and r.json()["verified"] is True
    assert db.scalar(select(AuditLog).where(AuditLog.action == "speaker.update")).after["display_name"] == "阿明"
    r = c.post("/api/speakers/cluster", json={"threshold": 0.6, "scope": {"channel_ids": [str(ch1.id)]}})
    assert r.status_code == 200 and r.json()["kind"] == "speakers.cluster"
    assert c.post("/api/speakers/cluster", json={"threshold": 0.6}).status_code == 409
    seg = c.get(f"/api/segments/{g3.id}").json()
    assert seg["speaker"]["label"] == "cand_002"


def test_gini():
    from voti.api.speakers import gini
    assert gini([]) == 0 and gini([5, 5, 5]) == 0
    assert gini([0, 0, 10]) == 0  # zero-time speakers are ignored
    assert gini([1, 3]) == pytest.approx(0.25)


# ───────────────────────────── repeated ─────────────────────────────

def test_repeated_groups(annotator_client, factory, db):
    import uuid

    from voti.models import RepeatedRegion, Segment
    ch = factory.channel()
    m1, m2 = factory.media(channel_id=ch.id), factory.media(channel_id=ch.id)
    gid = uuid.uuid4()
    db.add_all([RepeatedRegion(channel_id=ch.id, group_id=gid, media_id=m1.id, start_sec=0, end_sec=5,
                               position="intro", match_count=1, excluded=False),
                RepeatedRegion(channel_id=ch.id, group_id=gid, media_id=m2.id, start_sec=0, end_sec=5,
                               position="intro", match_count=1, excluded=False)])
    db.commit()
    g = factory.segment(m1, 3, 7)
    r = annotator_client.get(f"/api/channels/{ch.id}/repeated")
    assert r.json()[0]["region_count"] == 2 and r.json()[0]["excluded"] is False
    r = annotator_client.patch(f"/api/repeated/{gid}", json={"excluded": True})
    assert r.status_code == 200 and r.json()["segments_updated"] == 1
    db.expire_all()
    seg = db.get(Segment, g.id)
    assert seg.repeated_overlap == pytest.approx(0.5) and "repeated" in seg.flags
    annotator_client.patch(f"/api/repeated/{gid}", json={"excluded": False})
    db.expire_all()
    seg = db.get(Segment, g.id)
    assert seg.repeated_overlap == 0 and "repeated" not in seg.flags


# ───────────────────────────── jobs / stats / quality / settings ─────────────────────────────

def test_jobs_eta_cancel_retry(annotator_client, factory, db):
    from datetime import timedelta

    from voti.models import Job, utcnow
    from voti.queue import enqueue
    m = factory.media(title="影片")
    running = enqueue(db, "media.normalize", media_id=m.id)
    running.status, running.progress, running.started_at = "running", 0.25, utcnow() - timedelta(seconds=30)
    queued = enqueue(db, "channel.list")
    failed = enqueue(db, "media.detail", media_id=m.id)
    failed.status = "failed"
    db.commit()
    r = annotator_client.get("/api/jobs")
    body = r.json()
    assert body["total"] == 3 and body["counts"]["running"] == 1 and body["counts"]["failed"] == 1
    item = next(x for x in body["items"] if x["id"] == str(running.id))
    assert item["eta_sec"] == pytest.approx(90, abs=3) and item["media_title"] == "影片"
    assert annotator_client.get("/api/jobs", params={"status": "failed,queued"}).json()["total"] == 2
    assert annotator_client.post(f"/api/jobs/{queued.id}/cancel").json()["status"] == "cancelled"
    r = annotator_client.post(f"/api/jobs/{running.id}/cancel")
    assert r.json()["status"] == "running"
    db.expire_all()
    assert db.get(Job, running.id).cancel_requested is True
    assert annotator_client.post(f"/api/jobs/{failed.id}/retry").json()["status"] == "queued"
    assert annotator_client.post(f"/api/jobs/{failed.id}/retry").status_code == 409


def test_stats_and_quality(annotator_client, factory, db):
    from voti.models import MediaQuality
    m = factory.media(status="ready", duration_sec=3600)
    factory.media(status="failed")
    db.add(MediaQuality(media_id=m.id, sample_rate=48000, codec="opus", snr_mean=22.0, dnsmos_ovrl=3.1,
                        speech_sec=1800, real_bandwidth_hz=8000, upsampled_suspect=True))
    db.commit()
    factory.segment(m, 0, 36, review_status="passed")
    r = annotator_client.get("/api/stats/overview")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["media"]["ready"] == 1 and st["media"]["failed"] == 1 and st["media"]["listed"] == 2
    assert st["hours"]["speech"] == 0.5 and st["hours"]["reviewed"] == 0.01
    assert st["segments"] == {"total": 1, "passed": 1, "needs_fix": 0}
    assert set(st["queue_depth"]) == {"fetch", "audio", "ml", "export"}
    q = annotator_client.get("/api/quality/summary").json()
    assert q["sample_rates"] == {"48000": 1} and q["codecs"] == {"opus": 1} and q["upsampled_suspect"] == 1
    assert sum(b["count"] for b in q["histograms"]["snr"]) == 1
    snr_bin = next(b for b in q["histograms"]["snr"] if b["count"])
    assert snr_bin["bin"] == 20.0


def test_settings(admin_client, db):
    from voti import storage
    from voti.models import AuditLog
    try:
        storage.delete("system/cookies.txt")
    except Exception:  # noqa: BLE001
        pass
    r = admin_client.get("/api/settings")
    assert r.status_code == 200
    s = r.json()
    assert s["youtube"]["sleep_between_downloads_sec"] == 5 and s["cookies"]["present"] is False
    r = admin_client.put("/api/settings/youtube", json={"rate_limit_kbps": 500})
    assert r.status_code == 200 and r.json()["rate_limit_kbps"] == 500 and r.json()["max_concurrent_downloads"] == 1
    assert admin_client.get("/api/settings").json()["youtube"]["rate_limit_kbps"] == 500
    assert admin_client.put("/api/settings/youtube", json={"rate_limit_kbps": -1}).status_code == 422
    assert admin_client.put("/api/settings/youtube", json={"unknown_key": 1}).status_code == 422
    assert admin_client.put("/api/settings/nope", json={}).status_code == 404
    r = admin_client.put("/api/settings/pipeline", json={"voxprofile_backbone": "wavlm-large"})
    assert r.json()["voxprofile_backbone"] == "wavlm-large"
    cookies = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tx\n"
    r = admin_client.put("/api/settings/cookies", files={"file": ("cookies.txt", cookies, "text/plain")})
    assert r.status_code == 200, r.text
    assert r.json()["present"] is True
    assert storage.get_bytes("system/cookies.txt") == cookies
    r = admin_client.put("/api/settings/cookies", files={"file": ("x.txt", b"\x00\x01garbage", "text/plain")})
    assert r.status_code == 400
    assert admin_client.delete("/api/settings/cookies").status_code == 204
    assert admin_client.get("/api/settings").json()["cookies"]["present"] is False
    actions = db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all()
    assert actions == ["settings.update", "settings.update", "settings.cookies_upload", "settings.cookies_delete"]
