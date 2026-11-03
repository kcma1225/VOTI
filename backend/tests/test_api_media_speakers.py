"""GET /api/media/{id}/speakers: per-media diarized speakers with aggregated Vox-Profile and cluster link."""
from __future__ import annotations

import pytest


def _vp(segment, sex="male", sex_prob=0.9, age=40.0, age_group="40-49", emotion=None, backbone="whisper-large-v3",
        **kw):
    from voti.models import VoiceProfile
    return VoiceProfile(
        segment_id=segment.id, revision=segment.revision, backbone=backbone, sex=sex, sex_prob=sex_prob, age=age,
        age_group=age_group,
        emotion={"label": "neutral", "probs": emotion or {"neutral": 0.8, "happy": 0.2}},
        voice_quality={"label": "deep", "labels": ["deep"], "probs": {"deep": 0.9, "breathy": 0.2}},
        speech_flow={"label": "fluent", "probs": {"fluent": 0.7, "disfluent": 0.3}, "n_windows": 2},
        accent_broad={"label": "asian", "probs": {"asian": 0.6, "british isles": 0.4}},
        emotion_dim={}, accent_narrow={}, model_versions={}, **kw)


@pytest.fixture
def diarized(factory, db):
    from voti.models import ClusterRun, DiarizationTurn, Speaker, SpeakerAssignment
    m = factory.media(title="podcast", status="ready")
    # equal usable speech (8 s each): more diarized time wins, so SPEAKER_00 comes first though SPEAKER_01 speaks first
    db.add_all([
        DiarizationTurn(media_id=m.id, local_speaker="SPEAKER_01", start_sec=0, end_sec=10, model_id="pyannote"),
        DiarizationTurn(media_id=m.id, local_speaker="SPEAKER_00", start_sec=10, end_sec=30, model_id="pyannote"),
        DiarizationTurn(media_id=m.id, local_speaker="SPEAKER_02", start_sec=30, end_sec=31, model_id="pyannote"),
    ])
    db.commit()
    a1 = factory.segment(m, 0.5, 4.5, local_speaker="SPEAKER_01", speaker_state="single", snr=20)   # 4 s
    a2 = factory.segment(m, 5.0, 9.0, local_speaker="SPEAKER_01", speaker_state="single", snr=25)   # 4 s
    b1 = factory.segment(m, 10.0, 16.0, local_speaker="SPEAKER_00", speaker_state="single", snr=30)  # 6 s
    b2 = factory.segment(m, 16.0, 18.0, local_speaker="SPEAKER_00", speaker_state="single", snr=10)  # 2 s
    bad = factory.segment(m, 20.0, 29.0, local_speaker="SPEAKER_00", validity="invalid",
                          invalid_reason="language_mismatch")
    db.add_all([
        _vp(a1, sex="female", sex_prob=0.8, age=30.0, age_group="30-39"),
        _vp(a2, sex="female", sex_prob=1.0, age=20.0, age_group="20-29"),
        # an outdated revision and another backbone must not count
        _vp(a2, sex="male", sex_prob=1.0, age=90.0, backbone="wavlm-large"),
        _vp(b1, sex="male", sex_prob=0.9, age=50.0, age_group="50-59",
            emotion={"angry": 1.0}),
        _vp(b2, sex="male", sex_prob=0.7, age=30.0, age_group="30-39", emotion={"neutral": 1.0}),
        _vp(bad, sex="female", sex_prob=1.0, age=10.0, age_group="<20"),
    ])
    db.commit()
    run = factory.add(ClusterRun(model_id="ecapa", algorithm="agglomerative", threshold=0.6, is_current=True))
    host = factory.add(Speaker(run_id=run.id, label="cand_007", display_name="主持人"))
    other = factory.add(Speaker(run_id=run.id, label="cand_008"))
    db.add_all([SpeakerAssignment(run_id=run.id, segment_id=b1.id, speaker_id=host.id, similarity=0.9),
                SpeakerAssignment(run_id=run.id, segment_id=b2.id, speaker_id=other.id, similarity=0.7),
                SpeakerAssignment(run_id=run.id, segment_id=bad.id, speaker_id=other.id, similarity=0.7)])
    db.commit()
    return m, {"a1": a1, "a2": a2, "b1": b1, "b2": b2, "bad": bad}, host


def test_media_speakers(annotator_client, diarized):
    m, g, host = diarized
    r = annotator_client.get(f"/api/media/{m.id}/speakers")
    assert r.status_code == 200, r.text
    out = r.json()
    assert [x["local_speaker"] for x in out] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    assert [x["color_index"] for x in out] == [0, 1, 2]
    s0, s1, s2 = out

    # invalid segment excluded from counts, time and profile
    assert s0["segment_count"] == 2 and s0["invalid_count"] == 1
    assert s0["total_sec"] == pytest.approx(8.0)
    assert s0["diarized_sec"] == pytest.approx(20.0)
    assert s0["first_sec"] == pytest.approx(10.0)
    p0 = s0["profile"]
    assert s0["profiled_segments"] == 2
    assert p0["sex_top"] == "male"
    assert p0["sex_prob"] == pytest.approx((6 * 0.9 + 2 * 0.7) / 8, abs=1e-3)
    assert p0["age_mean"] == pytest.approx((6 * 50 + 2 * 30) / 8, abs=1e-2)
    assert p0["age_group"] == {"50-59": 0.75, "30-39": 0.25}
    assert p0["emotion"] == {"angry": 0.75, "neutral": 0.25}
    assert p0["voice_quality"]["deep"] == pytest.approx(0.9)
    assert p0["speech_flow"]["fluent"] == pytest.approx(0.7)
    assert p0["accent_broad"]["asian"] == pytest.approx(0.6)
    # the cluster holding most of this speaker's (valid) seconds
    assert s0["cluster"]["id"] == str(host.id)
    assert s0["cluster"]["display_name"] == "主持人" and s0["cluster"]["label"] == "cand_007"
    assert s0["cluster"]["share"] == pytest.approx(0.75)
    # samples: cleanest 2–15 s single-speaker segments first, returned in time order, never invalid ones
    assert s0["sample_segment_ids"] == [str(g["b1"].id), str(g["b2"].id)]

    p1 = s1["profile"]
    assert s1["segment_count"] == 2 and s1["profiled_segments"] == 2
    assert p1["sex_top"] == "female"
    assert p1["sex_prob"] == pytest.approx(0.9)
    assert p1["age_mean"] == pytest.approx(25.0)   # the wavlm-large row (age 90) is ignored
    assert s1["cluster"] is None
    assert s1["first_sec"] == pytest.approx(0.5)

    # diarized but no segment: listed with zeros and an empty profile
    assert s2["segment_count"] == 0 and s2["profiled_segments"] == 0
    assert s2["profile"]["sex_top"] is None and s2["profile"]["emotion"] == {}
    assert s2["sample_segment_ids"] == []


def test_media_speakers_empty_and_404(annotator_client, factory):
    import uuid
    m = factory.media(title="nothing")
    r = annotator_client.get(f"/api/media/{m.id}/speakers")
    assert r.status_code == 200 and r.json() == []
    assert annotator_client.get(f"/api/media/{uuid.uuid4()}/speakers").status_code == 404


def test_media_speakers_most_data_first(annotator_client, factory, db):
    """語者 1 is the speaker with the most usable speech, not SPEAKER_00 (invalid segments don't count)."""
    from voti.models import DiarizationTurn
    m = factory.media(title="interview", status="ready")
    db.add_all([
        DiarizationTurn(media_id=m.id, local_speaker="SPEAKER_00", start_sec=0, end_sec=1, model_id="pyannote"),
        DiarizationTurn(media_id=m.id, local_speaker="SPEAKER_01", start_sec=1, end_sec=40, model_id="pyannote"),
        DiarizationTurn(media_id=m.id, local_speaker="SPEAKER_02", start_sec=40, end_sec=60, model_id="pyannote"),
    ])
    db.commit()
    factory.segment(m, 0.0, 1.0, local_speaker="SPEAKER_00")
    factory.segment(m, 1.0, 10.0, local_speaker="SPEAKER_01")
    factory.segment(m, 10.0, 40.0, local_speaker="SPEAKER_01", validity="invalid", invalid_reason="text_mismatch")
    factory.segment(m, 40.0, 60.0, local_speaker="SPEAKER_02")
    out = annotator_client.get(f"/api/media/{m.id}/speakers").json()
    assert [x["local_speaker"] for x in out] == ["SPEAKER_02", "SPEAKER_01", "SPEAKER_00"]
    assert [x["color_index"] for x in out] == [0, 1, 2]
