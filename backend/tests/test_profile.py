"""media.profile handler with a mocked legacy-ml (Postgres required; uses the shared `db`/`factory` fixtures).

Run isolated from other suites, e.g.:
  VOTI_TEST_DB=voti_test_legacy VOTI_TEST_BUCKET=voti-test-legacy VOTI_TEST_REDIS_DB=10 pytest tests/test_profile.py
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

import voti.pipeline.profile as profile
from voti.models import Job, Media, Segment, Setting, VoiceProfile
from voti.queue import JobError, enqueue, run_one


def fake_result(dur: float) -> dict | None:
    if dur < 1.0:
        return None
    return {
        "duration_sec": dur, "n_chunks": 1, "low_confidence": dur < 3,
        "age": 31.5, "age_group": "thirties", "sex": "female", "sex_prob": 0.9,
        "sex_probs": {"female": 0.9, "male": 0.1},
        "voice_quality": {"label": "soft", "labels": ["soft"], "probs": {"soft": 0.7, "deep": 0.1}},
        "emotion": {"label": "Neutral", "probs": {"Neutral": 0.8, "Happiness": 0.2}},
        "emotion_dim": {"arousal": 0.4, "valence": 0.5, "dominance": 0.45},
        "speech_flow": {"label": "Fluent", "probs": {"Fluent": 0.9, "Disfluent": 0.1}},
        "accent_broad": {"label": "Other", "probs": {"Other": 1.0}},
        "accent_narrow": {"label": "East Asia", "probs": {"East Asia": 1.0}},
    }


@pytest.fixture
def fake_service(monkeypatch):  # noqa: ANN001
    calls: list[dict] = []

    def voxprofile(key, spans, backbone="whisper-large-v3"):  # noqa: ANN001, ANN202
        calls.append({"key": key, "spans": list(spans), "backbone": backbone})
        return {"results": [fake_result(b - a) for a, b in spans], "backbone": backbone,
                "model_versions": {"emotion": f"{backbone}-emotion@abc1234"}}
    monkeypatch.setattr(profile.legacy_client, "voxprofile", voxprofile)
    monkeypatch.setattr(profile, "BATCH_SPANS", 2)
    return calls


@pytest.fixture
def media_with_segments(factory):  # noqa: ANN001
    ch = factory.channel()
    m = factory.media(channel_id=ch.id, status="processing")
    segs = {
        "single": factory.segment(m, 0.0, 4.0, speaker_state="single"),
        "unknown": factory.segment(m, 5.0, 7.0, speaker_state="unknown"),
        "long": factory.segment(m, 8.0, 30.0, speaker_state="single"),
        "multiple": factory.segment(m, 31.0, 35.0, speaker_state="multiple"),
        "overlap": factory.segment(m, 36.0, 40.0, speaker_state="overlap"),
        "inactive": factory.segment(m, 41.0, 45.0, speaker_state="single", active=False),
        "short": factory.segment(m, 46.0, 46.6, speaker_state="single"),
    }
    return m, segs


def _run(db, media, payload=None):  # noqa: ANN001, ANN202
    job = enqueue(db, "media.profile", media_id=media.id, channel_id=media.channel_id, payload=payload or {})
    db.commit()
    run_one(str(job.id), "test-worker")
    db.expire_all()
    return db.get(Job, job.id)


def _followups(db, job):  # noqa: ANN001, ANN202
    return db.scalars(select(Job).where(Job.parent_id == job.id)).all()


def test_profiles_eligible_segments_and_chains(db, fake_service, media_with_segments):  # noqa: ANN001
    m, segs = media_with_segments
    job = _run(db, m)
    assert job.status == "completed", (job.error_code, job.error_message)
    rows = db.scalars(select(VoiceProfile)).all()
    assert {r.segment_id for r in rows} == {segs["single"].id, segs["unknown"].id, segs["long"].id}
    r = next(r for r in rows if r.segment_id == segs["single"].id)
    assert r.backbone == "whisper-large-v3" and r.revision == 1
    assert r.sex == "female" and r.sex_prob == pytest.approx(0.9) and r.age_group == "thirties"
    assert r.emotion["label"] == "Neutral" and r.emotion_dim["arousal"] == pytest.approx(0.4)
    assert r.model_versions["emotion"].startswith("whisper-large-v3") and r.model_versions["n_chunks"] == 1
    # batching (BATCH_SPANS=2) over 3 spans, key = normalized wav
    assert [len(c["spans"]) for c in fake_service] == [2, 1]
    assert fake_service[0]["key"] == f"media/{m.id}/normalized.wav"
    assert job.result["profiled"] == 3
    assert [j.kind for j in _followups(db, job)] == ["media.fingerprint"]


def test_only_missing_and_no_chain(db, fake_service, media_with_segments):  # noqa: ANN001
    m, segs = media_with_segments
    _run(db, m)
    fake_service.clear()
    # boundary edit on one segment → new revision lacks a profile
    seg = db.get(Segment, segs["unknown"].id)
    seg.revision, seg.end_sec = 2, 8.0
    db.commit()
    job = _run(db, m, {"only_missing": True, "chain": False, "reason": "boundary_edit"})
    assert job.status == "completed"
    assert len(fake_service) == 1 and fake_service[0]["spans"] == [(5.0, 8.0)]
    assert _followups(db, job) == []
    revs = sorted(r.revision for r in db.scalars(select(VoiceProfile).where(VoiceProfile.segment_id == seg.id)))
    assert revs == [1, 2]


def test_nothing_to_do_still_chains(db, fake_service, media_with_segments):  # noqa: ANN001
    m, _ = media_with_segments
    first = _run(db, m)
    for j in _followups(db, first):  # let the first fingerprint job finish so enqueue does not dedupe
        j.status = "completed"
    db.commit()
    fake_service.clear()
    job = _run(db, m)
    assert job.status == "completed" and job.result["candidates"] == 0
    assert fake_service == []
    assert [j.kind for j in _followups(db, job)] == ["media.fingerprint"]


def test_backbone_from_settings(db, fake_service, media_with_segments):  # noqa: ANN001
    m, _ = media_with_segments
    db.add(Setting(key="pipeline", value={"voxprofile_backbone": "wavlm-large"}))
    db.commit()
    _run(db, m)
    assert {c["backbone"] for c in fake_service} == {"wavlm-large"}
    assert {r.backbone for r in db.scalars(select(VoiceProfile))} == {"wavlm-large"}


def test_force_overwrites(db, fake_service, media_with_segments):  # noqa: ANN001
    m, _ = media_with_segments
    _run(db, m)
    fake_service.clear()
    job = _run(db, m, {"force": True, "chain": False})
    assert job.status == "completed" and job.result["profiled"] == 3
    assert len(db.scalars(select(VoiceProfile)).all()) == 3  # upserted, not duplicated


def test_unavailable_blocks_media(db, monkeypatch, media_with_segments):  # noqa: ANN001
    m, _ = media_with_segments

    def down(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise JobError("legacy_ml_unavailable", "無法連線 legacy-ml", blocked=True)
    monkeypatch.setattr(profile.legacy_client, "voxprofile", down)
    job = _run(db, m)
    assert job.status == "blocked" and job.error_code == "legacy_ml_unavailable"
    assert db.get(Media, m.id).status == "blocked"
    assert _followups(db, job) == []


def test_refresh_speakers_called(db, fake_service, monkeypatch, media_with_segments):  # noqa: ANN001
    import voti.pipeline.cluster as cluster
    m, _ = media_with_segments
    seen = []
    monkeypatch.setattr(cluster, "refresh_profiles_for_media", lambda mid: seen.append(mid) or 0)
    job = _run(db, m)
    assert seen == [m.id] and job.result["speakers_refreshed"] == 0
