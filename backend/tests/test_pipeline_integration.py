"""End-to-end pipeline through the real job queue (Postgres + S3), on synthetic episodes of one channel.

Real: ffmpeg, Silero, DNSMOS, ECAPA, fingerprints, segmentation, clustering.
Faked: Brouhaha (legacy-ml) from signal energy, pyannote diarization (gated model) from known speaker turns,
Vox-Profile (legacy-ml) returns fixed traits.
Skipped when the test database / object store are unreachable.
"""
from __future__ import annotations

import uuid

import _pipeline_support as sup
import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import delete, select

SR = sup.SR
HOP = 0.016875


@pytest.fixture(scope="module")
def infra():
    try:
        from voti import storage
        from voti.db import init_db
        init_db()
        storage.ensure_bucket()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"test infra unavailable: {e}")
    return True


def _episode(seed: int, speech_turns: list[tuple[str, float]], jingle_at_end: bool = True):
    """→ (audio, turns[(spk, start, end)], speech regions). Speakers A/B differ in pitch and formants."""
    voices = {"A": dict(f0=110, formants=(650, 1100, 2500)), "B": dict(f0=210, formants=(850, 1600, 2900))}
    parts, turns, t = [sup.jingle(6.0)], [], 6.0
    parts.append(np.zeros(int(0.5 * SR), np.float32)); t += 0.5
    for i, (spk, sec) in enumerate(speech_turns):
        x = sup.speechlike(sec, seed=seed * 50 + i, **voices[spk])
        parts.append(x); turns.append((spk, t, t + sec)); t += sec
        parts.append(np.zeros(int(0.6 * SR), np.float32)); t += 0.6
    if jingle_at_end:
        parts.append(sup.jingle(5.0, seed=7)); t += 5.0
    audio = np.concatenate(parts)
    audio += 0.002 * np.random.default_rng(seed).standard_normal(audio.size).astype(np.float32)
    return audio.astype(np.float32), turns


def _fake_brouhaha(key: str) -> dict:
    from voti import storage
    import io
    data, sr = sf.read(io.BytesIO(storage.get_bytes(key)), dtype="float32")
    n = int(np.ceil(data.size / (HOP * sr)))
    hop_n = int(HOP * sr)
    e = np.array([np.sqrt(np.mean(data[i * hop_n:(i + 1) * hop_n] ** 2) + 1e-12) for i in range(n)])
    # smooth over 0.2 s so syllable dips stay speech
    k = int(0.2 / HOP)
    e = np.convolve(e, np.ones(k) / k, mode="same")
    vad = (e > 0.01).astype(np.float32) * 0.9 + 0.05
    return {"hop_sec": HOP, "vad": vad, "snr": np.where(vad > 0.5, 25.0, 0.0).astype(np.float32),
            "c50": np.full(n, 35.0, np.float32), "model": "fake-brouhaha"}


class _FakePipeline:
    def __init__(self, turns_by_len):
        self.turns_by_len = turns_by_len

    def __call__(self, file, hook=None):
        from pyannote.core import Annotation, Segment as PS
        n = file["waveform"].shape[-1]
        ann = Annotation()
        for spk, a, b in self.turns_by_len[n]:
            ann[PS(a, b)] = f"SPEAKER_{'00' if spk == 'A' else '01'}"
        return type("Out", (), {"speaker_diarization": ann})()


def _fake_voxprofile(key, spans, backbone="whisper-large-v3"):
    res = [None if b - a < 1 else {"age": 30.0, "age_group": "adult", "sex": "female", "sex_prob": 0.9,
                                   "voice_quality": {"labels": ["clear"], "probs": {"clear": 0.8}},
                                   "emotion": {"label": "neutral", "probs": {"neutral": 0.7, "happy": 0.3}},
                                   "emotion_dim": {"arousal": 0.5, "valence": 0.5, "dominance": 0.5},
                                   "speech_flow": {"label": "normal", "probs": {"normal": 1.0}},
                                   "accent_broad": {}, "accent_narrow": {}} for a, b in spans]
    return {"results": res, "model_versions": {"fake": "1"}, "backbone": backbone}


def _drain(max_jobs=200):
    from voti import queue
    from voti.db import session_scope
    from voti.models import Job
    done = []
    for _ in range(max_jobs):
        with session_scope() as s:
            j = s.scalar(select(Job).where(Job.status == "queued").order_by(Job.created_at))
            jid, kind = (str(j.id), j.kind) if j else (None, None)
        if jid is None:
            return done
        queue.run_one(jid, "pytest")
        with session_scope() as s:
            j = s.get(Job, uuid.UUID(jid))
            done.append((kind, j.status, j.error_code, j.result))
            assert j.status == "completed", (kind, j.error_code, j.error_message)
    raise AssertionError("queue did not drain")


def test_full_pipeline_three_episodes(infra, tmp_path, monkeypatch):
    from voti import storage
    from voti.db import session_scope
    from voti.models import (Asset, Channel, ClusterRun, Cue, DiarizationTurn, Embedding, Job, Media, MediaQuality,
                             RepeatedRegion, Segment, SpeakerAssignment, SpeakerOverride, SpeechRegion, Speaker,
                             Track, VoiceProfile)
    from voti.pipeline import diarize, legacy_client
    from voti import queue
    import voti.pipeline.handlers  # noqa: F401
    import voti.pipeline.profile  # noqa: F401

    monkeypatch.setattr(legacy_client, "brouhaha", _fake_brouhaha)
    monkeypatch.setattr(legacy_client, "voxprofile", _fake_voxprofile)

    episodes = [_episode(1, [("A", 4.0), ("B", 3.0), ("A", 5.0), ("B", 4.0)]),
                _episode(2, [("B", 3.5), ("A", 6.0), ("B", 3.0)]),
                _episode(3, [("A", 5.0), ("A", 3.0), ("B", 4.0)])]
    turns_by_len = {a.size: t for a, t in episodes}
    monkeypatch.setattr(diarize, "_load_pipeline", lambda *a, **k: _FakePipeline(turns_by_len))

    with session_scope() as s:
        s.execute(delete(Job))
        ch = Channel(platform="test", platform_channel_id=f"UC{uuid.uuid4().hex[:20]}", title="測試頻道")
        s.add(ch)
        s.flush()
        channel_id = ch.id
        media_ids = []
        for i, (audio, turns) in enumerate(episodes):
            m = Media(platform="test", platform_media_id=uuid.uuid4().hex[:16], channel_id=channel_id,
                      title=f"ep{i}", status="downloaded")
            s.add(m)
            s.flush()
            media_ids.append(m.id)
            # original at 44.1 kHz stereo, upsampled from 16 kHz → bandwidth ≈ 8 kHz
            from scipy.signal import resample_poly
            up = resample_poly(audio, 441, 160).astype(np.float32)
            p = tmp_path / f"orig{i}.flac"
            sf.write(p, np.stack([up, up], axis=1), 44100)
            storage.store_asset(s, p, storage.media_key(m.id, "original.flac"), "original_audio", media_id=m.id)
            if i < 2:  # ep0 manual captions, ep1 auto captions, ep2 none
                tr = Track(media_id=m.id, language="zh-TW", kind="manual" if i == 0 else "auto", format="vtt")
                s.add(tr)
                s.flush()
                for k, (spk, a, b) in enumerate(turns):
                    s.add(Cue(track_id=tr.id, idx=k, start_sec=a + 0.15, end_sec=b - 0.1, raw_text=f"句子{k}",
                              text=f"第{k}句話。", flags=[]))
            queue.enqueue(s, "media.normalize", media_id=m.id, channel_id=channel_id)
    done = _drain()
    kinds = [d[0] for d in done]
    assert kinds.count("media.fingerprint") == 3 and "channel.dedup" in kinds

    with session_scope() as s:
        for i, mid in enumerate(media_ids):
            m = s.get(Media, mid)
            assert m.status == "ready" and m.stage is None
            mq = s.get(MediaQuality, mid)
            assert mq.sample_rate == 44100 and mq.channels == 2 and mq.codec == "flac"
            assert 7000 < mq.real_bandwidth_hz < 9000 and mq.upsampled_suspect is True
            assert mq.lufs_integrated is not None and mq.speech_ratio > 0.4
            assert mq.dnsmos_ovrl is not None and mq.frames_asset_id is not None
            assert s.scalar(select(SpeechRegion.id).where(SpeechRegion.media_id == mid,
                                                          SpeechRegion.source == "brouhaha")) is not None  # silero barely fires on synthetic voices
            assert s.scalars(select(DiarizationTurn).where(DiarizationTurn.media_id == mid)).first() is not None
            segs = s.scalars(select(Segment).where(Segment.media_id == mid, Segment.active.is_(True))
                             .order_by(Segment.start_sec)).all()
            assert segs, f"no segments for ep{i}"
            assert {g.text_source for g in segs} == {["manual_caption", "auto_caption", "none"][i]}
            assert all(g.speaker_state in ("single", "multiple", "overlap", "unknown") for g in segs)
            assert any(g.speaker_state == "single" and g.local_speaker for g in segs)
            assert all(g.dnsmos_ovrl is not None for g in segs if g.end_sec - g.start_sec >= 1.0)
            emb = s.scalars(select(Embedding).join(Segment, Segment.id == Embedding.segment_id)
                            .where(Segment.media_id == mid)).all()
            assert emb and len(emb[0].vector) == 192
            assert s.scalar(select(Asset.id).where(Asset.media_id == mid, Asset.kind == "fingerprint"))
            assert s.scalar(select(VoiceProfile.id).join(Segment, Segment.id == VoiceProfile.segment_id)
                            .where(Segment.media_id == mid)) is not None
        regs = s.scalars(select(RepeatedRegion).where(RepeatedRegion.channel_id == channel_id)).all()
        groups = {}
        for r in regs:
            groups.setdefault(r.group_id, []).append(r)
        assert len(groups) == 2, [(r.media_id, r.start_sec, r.end_sec) for r in regs]
        positions = sorted({r.position for r in regs})
        assert positions == ["intro", "outro"]
        for g in groups.values():
            assert {r.media_id for r in g} == set(media_ids) and all(r.excluded and r.match_count == 2 for r in g)
        intro_seg = s.scalars(select(Segment).where(Segment.media_id == media_ids[2], Segment.active.is_(True),
                                                    Segment.repeated_overlap > 0)).all()
        for g in intro_seg:
            assert "repeated" in g.flags
        # override: say the first A-segment of ep0 and the first B-segment of ep1 are "主持人"
        seg_a = s.scalars(select(Segment).where(Segment.media_id == media_ids[0], Segment.local_speaker == "SPEAKER_00",
                                                Segment.speaker_state == "single",
                                                Segment.validity != "invalid")).first()
        seg_b = s.scalars(select(Segment).where(Segment.media_id == media_ids[1], Segment.local_speaker == "SPEAKER_01",
                                                Segment.speaker_state == "single",
                                                Segment.validity != "invalid")).first()
        s.add(SpeakerOverride(segment_id=seg_a.id, speaker_name="主持人"))
        s.add(SpeakerOverride(segment_id=seg_b.id, speaker_name="主持人"))
        queue.enqueue(s, "speakers.cluster", payload={"threshold": 0.6, "scope": {"channel_ids": [str(channel_id)]}})
    done = _drain()
    assert done[-1][0] == "speakers.cluster"
    with session_scope() as s:
        run = s.scalar(select(ClusterRun).where(ClusterRun.is_current.is_(True)))
        assert run is not None and run.stats["segments"] > 0
        a = s.get(SpeakerAssignment, (run.id, seg_a.id))
        b = s.get(SpeakerAssignment, (run.id, seg_b.id))
        assert a.speaker_id == b.speaker_id and a.manual and b.manual
        sp = s.get(Speaker, a.speaker_id)
        assert sp.display_name == "主持人" and sp.profile.get("sex", {}).get("female", 0) > 0.5
        assert all(isinstance(c, str) for c in sp.channel_ids)
        # a name typed on the other speaker (no override) survives re-clustering
        other = s.scalars(select(Speaker).where(Speaker.run_id == run.id, Speaker.id != sp.id)
                          .order_by(Speaker.total_sec.desc())).first()
        other.display_name, other.verified = "來賓", True
        other_segs = set(s.scalars(select(SpeakerAssignment.segment_id).where(
            SpeakerAssignment.speaker_id == other.id)))
        first_run = run.id
        queue.enqueue(s, "speakers.cluster", payload={"threshold": 0.6, "scope": {"channel_ids": [str(channel_id)]},
                                                      "auto": True})
    _drain()
    with session_scope() as s:
        run = s.scalar(select(ClusterRun).where(ClusterRun.is_current.is_(True)))
        assert run.id != first_run and run.stats["carried_over"] >= 1
        guest = s.scalar(select(Speaker).where(Speaker.run_id == run.id, Speaker.display_name == "來賓"))
        assert guest is not None and guest.verified
        moved = set(s.scalars(select(SpeakerAssignment.segment_id).where(SpeakerAssignment.speaker_id == guest.id)))
        assert len(moved & other_segs) >= 0.5 * len(other_segs)

    # idempotent re-segmentation: same ids, nothing duplicated
    with session_scope() as s:
        before = {g.id for g in s.scalars(select(Segment).where(Segment.media_id == media_ids[0],
                                                                Segment.active.is_(True)))}
        queue.enqueue(s, "media.segment", media_id=media_ids[0], payload={"chain": False})
    _drain()
    with session_scope() as s:
        after = {g.id for g in s.scalars(select(Segment).where(Segment.media_id == media_ids[0],
                                                               Segment.active.is_(True)))}
        assert before == after
        assert s.get(Media, media_ids[0]).status == "ready"
