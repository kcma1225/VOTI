"""media.ocr end to end with a stubbed OCR engine: a real (synthetic) mp4 in S3, frames_quality speech, the job
handler, stored track / cues / events / crops, video deletion, the OCR API, download options and dataset rules."""
from __future__ import annotations

import subprocess
import uuid

import numpy as np
import pytest
from sqlalchemy import select

from test_ocr import CFG, SUBS, Source, StubEngine

SPEECH = [(0.4, 9.9), (14.8, 17.0)]   # subtitles at 10.1–14.6 s have no speech


def _mp4(path, fps: int = 10) -> None:  # noqa: ANN001
    src = Source()
    w, h = src.size
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                          "-crf", "18", str(path)], stdin=subprocess.PIPE)
    for _, fr in src.frames(fps):
        p.stdin.write(np.ascontiguousarray(fr).tobytes())
    p.stdin.close()
    assert p.wait() == 0


@pytest.fixture
def ocr_media(db, factory, tmp_path):  # noqa: ANN001
    from voti import storage
    from voti.models import Setting
    from voti.pipeline.common import Frames
    m = factory.media(status="processing", has_manual_captions=False, has_auto_captions=False, duration_sec=17.0)
    hop = 0.016875
    n = int(17.0 / hop) + 1
    vad = np.full(n, 0.05, np.float32)
    for a, b in SPEECH:
        vad[int(a / hop):int(b / hop)] = 0.95
    fq = Frames(hop_sec=hop, brouhaha_vad=vad, snr=np.full(n, 20, np.float32), c50=np.full(n, 30, np.float32),
                silero_vad=vad.copy(), dnsmos_t=np.zeros(0, np.float32), dnsmos=np.zeros((0, 4), np.float32))
    storage.store_bytes_asset(db, fq.to_npz_bytes(), storage.media_key(m.id, "frames_quality.npz"), "frames_quality",
                              media_id=m.id)
    video = tmp_path / "video.mp4"
    _mp4(video)
    storage.store_asset(db, video, storage.media_key(m.id, "ocr/video.mp4"), "ocr_video", media_id=m.id,
                        content_type="video/mp4")
    db.add(Setting(key="ocr", value={"discovery_step_sec": CFG["discovery_step_sec"]}))
    db.commit()
    return m


def test_ocr_job_stores_track_and_deletes_video(db, ocr_media, monkeypatch):  # noqa: ANN001
    from voti import storage
    from voti.models import Asset, Cue, Job, Media, Track
    from voti.pipeline import ocr
    from voti.queue import enqueue, run_one
    import voti.pipeline.handlers  # noqa: F401

    monkeypatch.setattr(ocr, "load_engine", lambda kind, device: StubEngine())
    m = ocr_media
    assert ocr.needed(db, m.id)
    job = enqueue(db, "media.ocr", media_id=m.id)
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.status == "completed", job.error_message
    assert job.result["events"] == len(SUBS) and job.result["paired"] == len(SUBS) - 2
    # chained to segmentation
    assert db.scalar(select(Job).where(Job.parent_id == job.id)).kind == "media.segment"
    tr = db.scalar(select(Track).where(Track.media_id == m.id, Track.kind == "ocr"))
    assert tr is not None and tr.language == "zh-TW" and tr.format == "json"
    cues = db.scalars(select(Cue).where(Cue.track_id == tr.id).order_by(Cue.idx)).all()
    assert [c.raw_text for c in cues] == [t for _, _, t in SUBS]
    assert sum("ocr_paired" in c.flags for c in cues) == len(SUBS) - 2
    assert all("ocr" in c.flags for c in cues)
    # video gone (row and object), results + crops kept
    assert db.scalar(select(Asset).where(Asset.media_id == m.id, Asset.kind == "ocr_video")) is None
    assert storage.head(storage.media_key(m.id, "ocr/video.mp4")) is None
    assert not ocr.needed(db, m.id)
    assert storage.head(ocr.events_key(m.id)) is not None
    assert storage.head(ocr.crop_key(m.id, 0)) is not None
    meta = db.get(Media, m.id).metadata_["ocr"]
    assert meta["status"] == "done" and meta["video_deleted"] and meta["paired"] == len(SUBS) - 2
    assert sum(meta["rejected"].values()) == 2 and set(meta["rejected"]) <= {"no_speech", "little_speech"}


def test_ocr_api(db, ocr_media, admin_client, monkeypatch):  # noqa: ANN001
    from voti import storage
    from voti.models import Job
    from voti.pipeline import ocr
    ocr.run(ocr_media.id, engine=StubEngine())
    m = ocr_media
    r = admin_client.get(f"/api/media/{m.id}/ocr")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "done" and d["total"] == len(SUBS) and not d["video_available"]
    ev = d["events"][0]
    assert ev["text"] == SUBS[0][2] and ev["status"] == "paired" and ev["cue_id"]
    assert ev["bbox"][1] > 280 and ev["crop_url"]
    assert {e["reason"] for e in d["events"] if e["status"] == "rejected"} <= {"no_speech", "little_speech"}
    img = admin_client.get(ev["crop_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp" and img.content[:4] == b"RIFF"
    assert admin_client.get(f"/api/media/{m.id}/ocr/crops/999.webp").status_code == 404
    det = admin_client.get(f"/api/media/{m.id}").json()
    assert det["ocr"]["status"] == "done" and det["ocr_job_active"] is False
    assert any(t["kind"] == "ocr" for t in det["tracks"])
    # re-run: needs the audio download first, then force (the OCR track exists), then a video-only download
    assert admin_client.post(f"/api/media/{m.id}/ocr", json={}).json()["code"] == "not_downloaded"
    storage.store_bytes_asset(db, b"x", storage.media_key(m.id, "original.m4a"), "original_audio", media_id=m.id)
    db.commit()
    assert admin_client.post(f"/api/media/{m.id}/ocr", json={}).json()["code"] == "ocr_exists"
    r = admin_client.post(f"/api/media/{m.id}/ocr", json={"force": True})
    assert r.status_code == 200, r.text
    job = db.get(Job, uuid.UUID(r.json()["id"]))
    assert job.kind == "media.download" and job.payload == {"video_only": True, "ocr": True}
    assert admin_client.get(f"/api/media/{m.id}").json()["ocr_job_active"] is True
    assert admin_client.post(f"/api/media/{m.id}/ocr", json={"force": True}).json()["code"] == "job_active"


def test_download_ocr_options(db, factory, admin_client):  # noqa: ANN001
    from voti.models import Job, Setting
    none = factory.media(status="detailed", has_manual_captions=False, has_auto_captions=False)
    cc = factory.media(status="detailed", has_manual_captions=True, manual_caption_langs=["zh-TW"])
    r = admin_client.post("/api/media/download", json={"media_ids": [str(none.id), str(cc.id)]}).json()
    assert r["queued"] == 2 and r["ocr"] == 1
    p = {j.media_id: j.payload for j in db.scalars(select(Job).where(Job.kind == "media.download"))}
    assert "ocr" not in p[none.id] and "ocr" not in p[cc.id]   # the worker applies settings.ocr (auto)
    db.execute(Job.__table__.delete())
    for m in (none, cc):
        m.status = "detailed"
    db.commit()
    r = admin_client.post("/api/media/download", json={"media_ids": [str(none.id)], "ocr": False}).json()
    assert r["queued"] == 0 and r["skipped_reasons"] == {"no_usable_captions": 1}
    r = admin_client.post("/api/media/download", json={"media_ids": [str(cc.id)], "ocr": True}).json()
    assert r["queued"] == 1 and r["ocr"] == 1
    assert db.scalar(select(Job).where(Job.media_id == cc.id)).payload["ocr"] is True
    db.add(Setting(key="ocr", value={"auto_when_no_captions": False}))
    db.commit()
    r = admin_client.post("/api/media/download", json={"media_ids": [str(none.id)]}).json()
    assert r["queued"] == 0 and r["skipped_reasons"] == {"no_usable_captions": 1}


def test_download_handler_mode(db):  # noqa: ANN001
    from voti.models import Setting
    from voti.sources.handlers import ocr_video_mode
    assert ocr_video_mode(db, {}) == "if_no_captions"
    assert ocr_video_mode(db, {"ocr": True}) == "always"
    assert ocr_video_mode(db, {"ocr": False}) is None
    db.add(Setting(key="ocr", value={"enabled": False}))
    db.commit()
    assert ocr_video_mode(db, {"ocr": True}) is None


def test_ocr_settings_api(admin_client):  # noqa: ANN001
    d = admin_client.get("/api/settings").json()["ocr"]
    assert d["enabled"] is True and d["auto_when_no_captions"] is True and d["fps"] == 5.0 and d["model"] == "server"
    assert d["cpu_model"] == "mobile"
    assert admin_client.put("/api/settings/ocr", json={"fps": 40}).status_code == 422
    assert admin_client.put("/api/settings/ocr", json={"model": "huge"}).status_code == 422
    r = admin_client.put("/api/settings/ocr", json={"fps": 4, "keep_crops": False})
    assert r.status_code == 200 and r.json()["fps"] == 4 and r.json()["keep_crops"] is False


def test_chain_skips_ocr_without_video(db, factory):  # noqa: ANN001
    from voti.pipeline import handlers

    class Ctx:
        kind, payload, then_calls = "media.diarize", {}, []

        def then(self, kind, payload=None, **kw):  # noqa: ANN001
            self.then_calls.append(kind)
    m = factory.media(status="processing")
    ctx = Ctx()
    ctx.media_id = m.id
    handlers._chain(ctx)
    assert ctx.then_calls == ["media.segment"]


def test_dataset_takes_only_validated_ocr(db, factory):  # noqa: ANN001
    from voti.datasets import preview
    m = factory.media(status="ready")
    factory.segment(m, 0, 3, text_source="manual_caption", validity="valid")
    factory.segment(m, 3, 6, text_source="ocr_caption", validity="valid")
    factory.segment(m, 6, 9, text_source="ocr_caption", validity="unknown")
    factory.segment(m, 9, 12, text_source="ocr_caption", validity="invalid")
    loose = {"min_snr": None, "min_dnsmos_ovrl": None, "min_speech_ratio": None, "min_bandwidth_hz": None}
    p = preview(db, loose)
    assert p["segment_count"] == 1 and p["excluded"]["ocr_caption"] == 1
    p = preview(db, {**loose, "text_sources": ["manual_caption", "ocr_caption"]})
    assert p["segment_count"] == 2 and p["excluded"]["ocr_unverified"] == 1 and p["excluded"]["invalid"] == 1
