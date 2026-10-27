"""media.validate — caption ↔ audio validation (voti.pipeline.validate) and its exclusion everywhere.

Pure tests: caption language → expected VoxLingua107 code, the decision rule, alignment confidence.
DB tests (test database + bucket, fake language ID): validate.run verdicts, manual decisions kept, re-validation
after edits (align → validate chain), CUDA OOM fallback, pipeline chaining / missing model, API (PATCH validity,
filters, media/channel/stats counts, dataset preview + export exclusion, settings, backfill endpoints).
Real-model test (optional): VoxLingua107 + MMS_FA on real segments copied from a processed video; set
VOTI_VALIDATE_REAL to a directory with segments.json, <media_id>.wav and <media_id>.frames_quality.npz
(skipped otherwise / when the models are not in MODEL_CACHE).
"""
from __future__ import annotations

import json
import os
import struct
import numpy as np
import pytest
from sqlalchemy import select

from voti.pipeline import align, gpu, validate

SR = 16000
CFG = dict(validate.DEFAULTS)


# ───────────────────────────── pure ─────────────────────────────

def test_expected_language():
    assert validate.expected_language("zh-TW") == ("zh", False)
    assert validate.expected_language("zh-Hans") == ("zh", False)
    assert validate.expected_language("cmn") == ("zh", False)
    assert validate.expected_language("en-US") == ("en", False)
    assert validate.expected_language("ja") == ("ja", False)
    assert validate.expected_language("he") == ("iw", False)
    assert validate.expected_language("nan-TW") == (None, True)
    assert validate.expected_language("hak") == (None, True)
    assert validate.expected_language("yue") == (None, True)
    assert validate.expected_language(None, "中文字幕") == ("zh", False)
    assert validate.expected_language(None, "no tag") == (None, False)


def _d(text="中文字幕內容", source="manual_caption", lang="zh-TW", lid=None, score=None, n=10, **cfg):
    return validate.decide(text=text, text_source=source, language=lang, lid=lid, align_score=score,
                           align_units=n, cfg={**CFG, **cfg})


def test_decide_rules():
    # English speech under Chinese captions
    v = _d(lid=[("en", 0.92), ("zh", 0.05)], score=0.2)
    assert (v.validity, v.reason) == ("invalid", "language_mismatch") and v.details["expected"] == "zh"
    # Mandarin, good alignment
    assert _d(lid=[("zh", 0.99)], score=0.8).validity == "valid"
    # unsure LID (Mandarin heard as zh 0.51 / ko 0.48 or ko 0.6) never flags the language
    assert _d(lid=[("ko", 0.6), ("zh", 0.39)], score=0.8).validity == "valid"
    # same language, text does not align
    v = _d(lid=[("zh", 0.99)], score=0.25)
    assert (v.validity, v.reason) == ("invalid", "text_mismatch")
    # too few scored units: alignment not used
    assert _d(lid=[("zh", 0.99)], score=0.1, n=2).validity == "valid"
    # no LID (too short) but alignment fine → valid; neither signal → unknown
    assert _d(score=0.7).validity == "valid"
    assert _d().validity == "unknown" and _d().details["note"] == "no_signal"
    # no text is never invalid
    assert _d(text="", source="none", lid=[("en", 0.99)]).validity == "unknown"
    assert _d(text="   ", lid=[("en", 0.99)]).validity == "unknown"
    # English captions
    assert _d(lang="en", lid=[("zh", 0.95)]).reason == "language_mismatch"
    assert _d(lang="en", lid=[("en", 0.95)], score=0.6).validity == "valid"
    # thresholds come from the settings
    assert _d(lid=[("en", 0.75)], lid_min_prob=0.8).validity == "valid"
    assert _d(lid=[("zh", 0.99)], score=0.3, align_min_score=0.2).validity == "valid"


def test_decide_taiwanese_is_lenient():
    for lang in ("nan-TW", "hak-TW", "yue"):
        # Taiwanese is often heard as zh / vi / th: never a mismatch
        assert _d(lang=lang, lid=[("zh", 0.95)]).validity == "valid"
        assert _d(lang=lang, lid=[("vi", 0.9)]).validity == "valid"
        assert _d(lang=lang, lid=[("th", 0.99)]).validity == "valid"
        # a clearly different language is
        v = _d(lang=lang, lid=[("en", 0.9)])
        assert (v.validity, v.reason) == ("invalid", "language_mismatch") and v.details["expected"] == "sinitic"
        assert _d(lang=lang, lid=[("ja", 0.95)]).reason == "language_mismatch"
        assert _d(lang=lang, lid=[("en", 0.5)]).validity == "valid"
        # the pinyin alignment score is never used for these tracks
        assert _d(lang=lang, lid=[("zh", 0.9)], score=0.05).validity == "valid"


def test_align_confidence():
    units = [["你", 0.1, 0.2, 0.9], ["，", None, None, None], ["2024", 0.3, 0.5, None], ["好", 0.5, 0.6, 0.01]]
    mean, n, low = validate.align_confidence(units)
    assert mean == pytest.approx(0.455) and n == 2 and low == pytest.approx(0.5)
    assert validate.align_confidence([]) == (None, 0, None)
    assert validate.align_confidence(None) == (None, 0, None)


# ───────────────────────────── DB: validate.run with a fake language ID ─────────────────────────────

def _wav_bytes(seconds: float) -> bytes:
    pcm = (np.sin(np.arange(int(seconds * SR)) / 10) * 8000).astype("<i2")
    fmt = struct.pack("<HHIIHH", 1, 1, SR, SR * 2, 2, 16)
    body = b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", pcm.nbytes) + pcm.tobytes()
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _media_with_audio(factory, seconds=30.0, **kw):  # noqa: ANN001
    from voti import storage
    m = factory.media(duration_sec=seconds, **kw)
    storage.put_bytes(_wav_bytes(seconds), storage.media_key(m.id, "normalized.wav"), "audio/wav")
    return m


class FakeLid:
    """Language per segment start (seconds, rounded) — the clip length tells which segment it is."""
    calls: list = []
    by_len: dict = {}

    @staticmethod
    def batch(clips, device, top_k=3):  # noqa: ANN001
        FakeLid.calls.append((device, len(clips)))
        return [FakeLid.by_len.get(round(c.size / SR, 1), [("zh", 0.99), ("ko", 0.01)]) for c in clips]


@pytest.fixture
def fake_lid(monkeypatch):
    FakeLid.calls, FakeLid.by_len = [], {}
    monkeypatch.setattr(validate, "load_lid", lambda dev: object())
    monkeypatch.setattr(validate, "lid_batch", FakeLid.batch)
    monkeypatch.setattr("voti.devices.torch_device", lambda: "cuda:0")
    monkeypatch.setattr(gpu, "free_cuda", lambda: None)
    return FakeLid


def _aligned(db, seg, score: float):  # noqa: ANN001
    """A current word_alignments row whose Han units all have `score`."""
    from voti.models import WordAlignment
    text = align.segment_text(seg)
    units = [[c, seg.start_sec, seg.end_sec, score] for c in text]
    db.merge(WordAlignment(segment_id=seg.id, media_id=seg.media_id, revision=seg.revision or 1,
                           text_hash=align.text_hash(text), status="aligned", model_id=align.MODEL_ID, units=units))
    db.commit()


@pytest.fixture
def corpus(db, factory, fake_lid):  # noqa: ANN001
    """zh: Mandarin ok; en: English speech (2.5 s); swap: unrelated text; nan: Taiwanese; empty: no text;
    short: 0.6 s (no LID)."""
    m = _media_with_audio(factory, status="ready")
    seg = {
        "zh": factory.segment(m, 1.0, 3.0, original_text="大家好我們今天", language="zh-TW"),
        "en": factory.segment(m, 4.0, 6.5, original_text="這是翻譯的字幕內容", language="zh-TW"),
        "swap": factory.segment(m, 7.0, 10.0, original_text="完全不相干的句子", language="zh-TW"),
        "nan": factory.segment(m, 11.0, 14.5, original_text="逐家好歡迎收看", language="nan-TW"),
        "empty": factory.segment(m, 15.0, 17.0, original_text="", text_source="none", language="zh-TW"),
        "short": factory.segment(m, 18.0, 18.6, original_text="好的好的", language="zh-TW"),
    }
    fake_lid.by_len = {2.5: [("en", 0.92), ("cy", 0.04), ("zh", 0.02)], 3.5: [("vi", 0.8), ("zh", 0.15)]}
    _aligned(db, seg["zh"], 0.85)
    _aligned(db, seg["en"], 0.2)
    _aligned(db, seg["swap"], 0.25)
    _aligned(db, seg["short"], 0.7)
    return m, seg


def test_validate_run_verdicts(db, corpus, fake_lid):  # noqa: ANN001
    from voti.models import Segment
    m, seg = corpus
    res = validate.run(m.id)
    assert res["segments"] == 6 and res["invalid"] == 2 and res["reasons"] == {"language_mismatch": 1,
                                                                               "text_mismatch": 1}
    assert res["device"] == "cuda:0" and res["lid_run"] == 4  # empty: no audio; short: < 1 s of speech
    db.expire_all()
    g = {k: db.get(Segment, v.id) for k, v in seg.items()}
    assert (g["zh"].validity, g["zh"].lid_lang, g["zh"].align_score) == ("valid", "zh", 0.85)
    assert (g["en"].validity, g["en"].invalid_reason, g["en"].lid_lang, g["en"].lid_prob) == \
        ("invalid", "language_mismatch", "en", 0.92)
    assert g["en"].validation["lid"][0] == ["en", 0.92] and len(g["en"].validation["lid"]) == 3
    assert (g["swap"].validity, g["swap"].invalid_reason) == ("invalid", "text_mismatch")
    assert g["nan"].validity == "valid" and g["nan"].align_score is None  # vi 0.8 is not a mismatch for nan
    assert g["empty"].validity == "unknown" and g["empty"].validation["note"] == "no_text"
    assert g["short"].validity == "valid" and g["short"].lid_lang is None  # alignment alone
    assert all(x.validity_source == "auto" for x in g.values())
    assert g["zh"].validation["thresholds"]["align_min_score"] == 0.4
    # idempotent: everything current → nothing to do, no model call
    fake_lid.calls = []
    assert validate.run(m.id)["segments"] == 0 and fake_lid.calls == []
    # force re-checks all (LID recomputed)
    assert validate.run(m.id, force=True)["segments"] == 6 and fake_lid.calls


def test_validate_respects_manual_and_settings(db, corpus, fake_lid, annotator_client, admin_client):  # noqa: ANN001
    from voti.models import AuditLog, Segment
    m, seg = corpus
    validate.run(m.id)
    # a human says the English segment is fine after all
    r = annotator_client.patch(f"/api/segments/{seg['en'].id}", json={"validity": "valid"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["validity"], d["validity_source"], d["invalid_reason"]) == ("valid", "manual", None)
    assert d["history"][0]["before"]["validity"] == "invalid" and d["history"][0]["after"]["validity"] == "valid"
    r = annotator_client.patch(f"/api/segments/{seg['zh'].id}", json={"validity": "invalid"})
    assert (r.json()["validity"], r.json()["invalid_reason"]) == ("invalid", "manual")
    # automatic re-validation (even forced) never overrides them
    validate.run(m.id, force=True)
    db.expire_all()
    assert db.get(Segment, seg["en"].id).validity == "valid"
    assert db.get(Segment, seg["zh"].id).validity == "invalid"
    assert db.scalar(select(AuditLog).where(AuditLog.entity_id == str(seg["zh"].id))).action == "segment.review"
    # back to automatic: re-check queued for this segment
    from voti.models import Job
    r = annotator_client.patch(f"/api/segments/{seg['zh'].id}", json={"validity": "auto"})
    assert (r.json()["validity"], r.json()["validity_source"]) == ("unknown", None)
    job = db.scalar(select(Job).where(Job.kind == "media.validate"))
    assert job.payload["chain"] is False and job.payload["segment_ids"] == [str(seg["zh"].id)]
    assert annotator_client.patch(f"/api/segments/{seg['zh'].id}", json={"validity": "bogus"}).status_code == 422
    # disabled in the settings: nothing happens
    assert admin_client.put("/api/settings/validation", json={"enabled": False}).status_code == 200
    assert validate.run(m.id, force=True) == {"skipped": True, "code": "validation_disabled"}


def test_validate_oom_falls_back_to_cpu(db, corpus, fake_lid, monkeypatch):  # noqa: ANN001
    import torch
    m, _ = corpus
    used = []

    def batch(clips, device, top_k=3):  # noqa: ANN001
        used.append(device)
        if device != "cpu":
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        return [[("zh", 0.99)] for _ in clips]
    monkeypatch.setattr(validate, "lid_batch", batch)
    res = validate.run(m.id)
    assert res["device"] == "cpu" and res["cpu_fallback"] is True and used == ["cuda:0", "cuda:0", "cpu"]


def test_edit_realigns_then_revalidates(db, corpus, fake_lid, annotator_client, monkeypatch):  # noqa: ANN001
    """PATCH text → media.align {chain:false} → media.validate {chain:false, segment_ids}; LID is reused (same
    audio revision), a boundary change recomputes it."""
    from voti.models import Job, Media, Segment
    from voti.queue import run_one
    import voti.pipeline.handlers  # noqa: F401
    m, seg = corpus
    validate.run(m.id)

    class Aligner:  # every unit scores 0.9
        def __init__(self, dev):
            pass

        def align(self, samples, words):
            step = samples.size / SR / (len(words) + 1)
            return [(i * step, (i + 1) * step, 0.9) for i in range(len(words))]
    monkeypatch.setattr(align, "load_aligner", lambda dev: Aligner(dev))
    r = annotator_client.patch(f"/api/segments/{seg['swap'].id}", json={"corrected_text": "正確的字幕"})
    assert r.status_code == 200
    job = db.scalar(select(Job).where(Job.kind == "media.align"))
    run_one(str(job.id), "test")
    db.expire_all()
    vjob = db.scalar(select(Job).where(Job.kind == "media.validate"))
    assert vjob.parent_id == job.id and vjob.payload == {"chain": False, "reason": "edit",
                                                         "segment_ids": [str(seg["swap"].id)]}
    fake_lid.calls = []
    run_one(str(vjob.id), "test")
    db.expire_all()
    vjob = db.get(Job, vjob.id)
    assert vjob.status == "completed", vjob.error_message
    assert vjob.result["segments"] == 1 and vjob.result["lid_reused"] == 1 and fake_lid.calls == []
    g = db.get(Segment, seg["swap"].id)
    assert g.validity == "valid" and g.align_score == pytest.approx(0.9)
    # it was invalid before → lazy embed/profile queued (never embedded while invalid)
    kinds = {j.kind for j in db.scalars(select(Job).where(Job.parent_id == vjob.id))}
    assert kinds == {"media.embed", "media.profile"}
    assert db.get(Media, m.id).status == "ready" and db.get(Media, m.id).stage is None


def test_pipeline_chain_and_missing_model(db, factory, fake_lid, monkeypatch):  # noqa: ANN001
    from voti.models import Job, Media
    from voti.queue import JobError, PIPELINE, enqueue, is_side_job, run_one
    import voti.pipeline.handlers  # noqa: F401
    assert PIPELINE.index("media.validate") == PIPELINE.index("media.align") + 1
    assert PIPELINE[PIPELINE.index("media.validate") + 1] == "media.embed"
    assert is_side_job("media.validate", {"chain": False}) and not is_side_job("media.validate", {})
    m = _media_with_audio(factory, status="processing")
    factory.segment(m, 1.0, 3.0, original_text="你好", language="zh-TW")
    job = enqueue(db, "media.validate", media_id=m.id)
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.status == "completed" and job.result["valid"] == 1
    assert db.scalar(select(Job).where(Job.parent_id == job.id)).kind == "media.embed"

    def unavailable(dev):
        raise JobError("lid_model_unavailable", "VoxLingua107 載入失敗", blocked=True)
    monkeypatch.setattr(validate, "load_lid", unavailable)
    job = enqueue(db, "media.validate", media_id=m.id, payload={"force": True})
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.status == "completed" and job.result["skipped"] is True
    assert db.get(Media, m.id).status == "processing"
    # outside the chain the same error fails the job but never the media
    db.query(Media).filter_by(id=m.id).update({"status": "ready"})
    job = enqueue(db, "media.validate", media_id=m.id, payload={"chain": False, "force": True})
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    assert db.get(Job, job.id).status == "blocked" and db.get(Media, m.id).status == "ready"


def test_embed_profile_cluster_skip_invalid(db, corpus, fake_lid):  # noqa: ANN001
    from voti.pipeline import profile
    m, seg = corpus
    validate.run(m.id)
    picked = {sid for sid, *_ in profile.select_segments(m.id, "whisper-large-v3")}
    assert seg["zh"].id in picked and seg["en"].id not in picked and seg["swap"].id not in picked


# ───────────────────────────── API: exclusion + endpoints ─────────────────────────────

GOOD = dict(snr=20.0, dnsmos_ovrl=3.2, speech_ratio=0.9, speaker_state="single", language="zh-TW")


@pytest.fixture
def mixed(db, factory):  # noqa: ANN001
    from voti.models import ClusterRun, MediaQuality, Speaker, SpeakerAssignment
    ch = factory.channel(title="DW 中文")
    m = factory.media(channel_id=ch.id, title="新聞", duration_sec=60, status="ready")
    db.add(MediaQuality(media_id=m.id, real_bandwidth_hz=8000, sample_rate=48000))
    db.commit()
    ok = factory.segment(m, 1.0, 3.0, original_text="甲", review_status="passed", validity="valid", **GOOD)
    unk = factory.segment(m, 4.0, 5.0, original_text="乙", **GOOD)
    bad = factory.segment(m, 6.0, 10.0, original_text="丙", validity="invalid", invalid_reason="language_mismatch",
                          validity_source="auto", lid_lang="en", lid_prob=0.92, review_status="passed", **GOOD)
    run = factory.add(ClusterRun(model_id="ecapa", algorithm="agglomerative", threshold=0.5, is_current=True))
    sp = factory.add(Speaker(run_id=run.id, label="cand_001", total_sec=6.0))
    db.add_all([SpeakerAssignment(run_id=run.id, segment_id=g.id, speaker_id=sp.id) for g in (ok, bad)])
    db.commit()
    return ch, m, ok, unk, bad, sp


def test_api_exclusion_everywhere(annotator_client, mixed, db):  # noqa: ANN001
    ch, m, ok, unk, bad, sp = mixed
    c = annotator_client
    # review list: all by default, filterable
    items = c.get("/api/segments", params={"media_id": str(m.id)}).json()["items"]
    assert len(items) == 3
    b = next(i for i in items if i["id"] == str(bad.id))
    assert (b["validity"], b["invalid_reason"], b["lid_lang"], b["lid_prob"]) == ("invalid", "language_mismatch",
                                                                                 "en", 0.92)
    assert [i["id"] for i in c.get("/api/segments", params={"validity": "invalid"}).json()["items"]] == [str(bad.id)]
    assert c.get("/api/segments", params={"validity": "valid,unknown"}).json()["total"] == 2
    assert c.get("/api/segments", params={"validity": "nope"}).status_code == 422
    n = c.get(f"/api/segments/{bad.id}/neighbors", params={"validity": "invalid"}).json()
    assert n["total"] == 1 and n["in_filter"] is True
    # media summary / detail
    md = c.get(f"/api/media/{m.id}").json()
    assert md["invalid_count"] == 1 and md["invalid_sec"] == pytest.approx(4.0)
    assert md["validity_counts"] == {"valid": 1, "invalid": 1, "unknown": 1} and md["validation_job_active"] is False
    assert c.get("/api/media", params={"channel_id": str(ch.id)}).json()["items"][0]["invalid_count"] == 1
    # channel hours
    hours = c.get(f"/api/channels/{ch.id}").json()["hours"]
    assert hours["segments"] == pytest.approx(3.0 / 3600, abs=1e-3) and hours["invalid"] == pytest.approx(4 / 3600,
                                                                                                          abs=1e-3)
    # stats overview
    o = c.get("/api/stats/overview").json()
    assert o["segments"] == {"total": 2, "passed": 1, "needs_fix": 0, "invalid": 1}
    assert o["hours"]["segments"] == pytest.approx(round(3.0 / 3600, 3))
    assert o["hours"]["reviewed"] == pytest.approx(round(2.0 / 3600, 3))
    assert o["hours"]["invalid"] == pytest.approx(round(4.0 / 3600, 3))
    # dataset preview: always excluded, reported
    p = c.post("/api/datasets/preview", json={"filters": {}}).json()
    assert p["segment_count"] == 2 and p["total_sec"] == pytest.approx(3.0) and p["excluded"]["invalid"] == 1
    p = c.post("/api/datasets/preview", json={"filters": {"review_status": ["passed"]}}).json()
    assert p["segment_count"] == 1 and p["excluded"]["invalid"] == 1
    # diversity / speaker channels: the invalid segment's 4 s no longer count for the speaker
    dv = c.get("/api/speakers/diversity").json()
    assert dv["speaker_hours"][0]["total_sec"] == pytest.approx(2.0)
    assert c.get(f"/api/speakers/{sp.id}").json()["channels"][0]["total_sec"] == pytest.approx(2.0)


def test_export_never_contains_invalid(annotator_client, mixed, db):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import run_one
    import voti.datasets  # noqa: F401
    _, m, ok, unk, bad, _ = mixed
    from voti import storage
    storage.put_bytes(_wav_bytes(60.0), storage.media_key(m.id, "normalized.wav"), "audio/wav")
    d = annotator_client.post("/api/datasets", json={"name": "v1", "filters": {}}).json()
    exp = annotator_client.post(f"/api/datasets/{d['id']}/export", json={"format": "jsonl",
                                                                          "include_audio": False}).json()
    run_one(exp["job"]["id"], "test")
    db.expire_all()
    assert db.get(Job, exp["job"]["id"]).status == "completed"
    rows = [json.loads(x) for x in
            annotator_client.get(f"/api/exports/{exp['id']}/download/manifest.jsonl").text.splitlines()]
    assert {r["segment_id"] for r in rows} == {str(ok.id), str(unk.id)}
    e = annotator_client.get(f"/api/exports/{exp['id']}").json()
    assert e["stats"]["excluded"]["invalid"] == 1


def test_validate_endpoints_and_settings(annotator_client, admin_client, mixed, factory, db):  # noqa: ANN001
    from voti.models import Job, Segment
    ch, m, ok, unk, bad, _ = mixed
    other = factory.media(channel_id=ch.id, title="空", duration_sec=10, status="ready")  # no segments
    assert annotator_client.post(f"/api/media/{other.id}/validate").json() == {"queued": 0}
    assert annotator_client.post(f"/api/media/{m.id}/validate", json={"force": True}).json() == {"queued": 1}
    job = db.scalar(select(Job).where(Job.kind == "media.validate"))
    assert job.payload == {"chain": False, "force": True, "reason": "backfill"} and job.queue == "ml"
    assert annotator_client.get(f"/api/media/{m.id}").json()["validation_job_active"] is True
    # channel / backfill: one job per media (deduplicated with the queued one)
    assert annotator_client.post(f"/api/channels/{ch.id}/validate").json() == {"queued": 1}
    assert annotator_client.post("/api/validate/backfill").status_code == 403
    assert admin_client.post("/api/validate/backfill", json={}).json() == {"queued": 1}
    assert len(db.scalars(select(Job).where(Job.kind == "media.validate")).all()) == 1
    # nothing to do once every segment has a current validation (or a manual decision)
    db.query(Job).delete()
    for g in db.scalars(select(Segment)):
        g.validation = {"version": validate.VERSION, "revision": g.revision}
    db.commit()
    assert admin_client.post("/api/validate/backfill").json() == {"queued": 0}
    assert admin_client.post("/api/validate/backfill", json={"force": True}).json() == {"queued": 1}
    # settings
    s = admin_client.get("/api/settings").json()["validation"]
    assert s == {"enabled": True, "lid_min_prob": 0.7, "lid_min_speech_sec": 1.0, "align_min_score": 0.4,
                 "align_min_units": 4}
    r = admin_client.put("/api/settings/validation", json={"align_min_score": 0.35})
    assert r.status_code == 200 and r.json()["align_min_score"] == 0.35 and r.json()["lid_min_prob"] == 0.7
    assert admin_client.put("/api/settings/validation", json={"lid_min_prob": 1.5}).status_code == 422
    assert admin_client.put("/api/settings/validation", json={"bogus": 1}).status_code == 422
    assert annotator_client.put("/api/settings/validation", json={"enabled": False}).status_code == 403
    from voti.db import session_scope
    from voti.pipeline.common import get_setting
    with session_scope() as ss:
        assert validate.settings_value(get_setting(ss, "validation"))["align_min_score"] == 0.35


def test_migration_adds_columns(db_engine):  # noqa: ANN001
    """init_db is idempotent and the segments table has the validation columns with the default."""
    from sqlalchemy import inspect, text

    from voti.db import init_db
    init_db()
    cols = {c["name"]: c for c in inspect(db_engine).get_columns("segments")}
    for name in ("validity", "invalid_reason", "validity_source", "lid_lang", "lid_prob", "align_score",
                 "validation"):
        assert name in cols
    assert "unknown" in str(cols["validity"]["default"])
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = 'segments_invalid'")).scalar() == 1


# ───────────────────────────── real models on real segments (optional) ─────────────────────────────

REAL = os.environ.get("VOTI_VALIDATE_REAL")


@pytest.mark.skipif(not REAL, reason="set VOTI_VALIDATE_REAL to a directory of real segments + audio")
def test_real_models_on_real_segments(db, factory):  # noqa: ANN001
    """Real VoxLingua107 + MMS_FA on segments copied from a processed DW Chinese video (Mandarin narration with
    English interview clips under Chinese subtitles): narration valid, interviews language_mismatch, a caption
    swapped with another segment's caption text_mismatch."""
    from voti import storage
    from voti.models import Segment
    from voti.pipeline.common import Frames
    if not validate.model_dir().joinpath("embedding_model.ckpt").is_file() or not align.weights_path().is_file():
        pytest.skip("VoxLingua107 / MMS_FA weights not in MODEL_CACHE")
    spec = json.load(open(os.path.join(REAL, "segments.json")))
    mid = spec["media_id"]
    m = factory.media(duration_sec=spec["duration"], status="ready", title=spec["title"])
    storage.put_file(os.path.join(REAL, f"{mid}.wav"), storage.media_key(m.id, "normalized.wav"), "audio/wav")
    frames = Frames.from_npz_bytes(open(os.path.join(REAL, f"{mid}.frames_quality.npz"), "rb").read())
    storage.store_bytes_asset(db, frames.to_npz_bytes(), storage.media_key(m.id, "frames_quality.npz"),
                              "frames_quality", media_id=m.id)
    db.commit()
    segs = {}
    for g in spec["segments"]:
        segs[g["key"]] = factory.segment(m, g["start"], g["end"], original_text=g["text"], language=g["language"])
    align.run(m.id)
    res = validate.run(m.id)
    db.expire_all()
    got = {k: db.get(Segment, v.id) for k, v in segs.items()}
    report = {k: (g.validity, g.invalid_reason, g.lid_lang, g.lid_prob, g.align_score) for k, g in got.items()}
    print("\n" + "\n".join(f"{k:12s} {v}" for k, v in sorted(report.items())))
    print(res)
    for g in spec["segments"]:
        exp = g["expect"]
        assert (got[g["key"]].validity, got[g["key"]].invalid_reason) == (
            ("valid", None) if exp == "valid" else ("invalid", exp)), (g["key"], report[g["key"]])
