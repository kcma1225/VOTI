"""media.align (character timing) and CUDA OOM fallback.

Pure tests: romanization / unit splitting / time assignment, the OOM policy (voti.pipeline.gpu) with mocked
OutOfMemoryError. DB tests (test database + bucket): align.run with a fake aligner, re-alignment of edited
segments, the pipeline handler. Real-model test: MMS_FA on a short Mandarin clip, skipped unless the weights are
in MODEL_CACHE/mms_fa and the clip path is given in VOTI_ALIGN_CLIP (wav) + VOTI_ALIGN_TEXT.
"""
from __future__ import annotations

import os
import struct

import numpy as np
import pytest
from sqlalchemy import select

from voti.pipeline import align, gpu

SR = 16000


# ───────────────────────────── units (pure) ─────────────────────────────

@pytest.mark.parametrize("text", ["我們去銀行。", "今天2024年 OK嗎？", "Hello, world!", "她說：「好的」", "  ", "",
                                  "綠色的律師 café naïve", "ＡＩ時代 3.5%", "台灣\n有 10 20 個"])
def test_units_concatenate_back_to_text(text):
    us = align.units(text, "zh")
    assert "".join(u.text for u in us) == text


def test_units_zh_per_character_phrase_aware_pinyin():
    us = align.units("我們去銀行，重新開始。", "zh")
    assert [(u.text, u.token) for u in us] == [
        ("我", "wo"), ("們", "men"), ("去", "qu"), ("銀", "yin"), ("行", "hang"), ("，", None), ("重", "chong"),
        ("新", "xin"), ("開", "kai"), ("始", "shi"), ("。", None)]


def test_units_latin_words_digits_and_punctuation():
    us = align.units("今天2024年 OK嗎？綠", "zh")
    assert [(u.text, u.token) for u in us] == [("今", "jin"), ("天", "tian"), ("2024", "*"), ("年", "nian"),
                                               (" ", None), ("OK", "ok"), ("嗎", "ma"), ("？", None), ("綠", "lv")]
    us = align.units("Café naïve, it's 10 20!", "en")
    assert [(u.text, u.token) for u in us] == [("Café", "cafe"), (" ", None), ("naïve", "naive"), (", ", None),
                                               ("it's", "it's"), (" ", None), ("10 20", "*"), ("!", None)]
    # in English mode Han characters are untimed
    assert [(u.text, u.token) for u in align.units("hi 你好", "en")] == [("hi", "hi"), (" 你好", None)]


def test_language_support():
    assert align.language_support("zh-TW") == ("zh", None)
    assert align.language_support("zh-Hans") == ("zh", None)
    assert align.language_support("cmn") == ("zh", None)
    assert align.language_support("en-US") == ("en", None)
    assert align.language_support("nan-TW") == (None, "no_romanizer:nan-TW")
    assert align.language_support("hak-TW") == (None, "no_romanizer:hak-TW")
    assert align.language_support("ja") == (None, "unsupported_language:ja")
    assert align.language_support(None, "沒有語言標記") == ("zh", None)


def test_assign_times_and_output_shape():
    us = align.units("你好，2號", "zh")
    align.assign_times(us, [(0.10, 0.30, 0.9), (0.30, 0.52, 0.8), (0.60, 0.90, 0.1), (0.9, 1.1, 0.7)], offset=10.0)
    out = align.units_out(us)
    assert out == [["你", 10.1, 10.3, 0.9], ["好", 10.3, 10.52, 0.8], ["，", None, None, None],
                   ["2", 10.6, 10.9, None], ["號", 10.9, 11.1, 0.7]]
    with pytest.raises(ValueError):
        align.assign_times(us, [(0, 1, 1)], 0.0)


# ───────────────────────────── OOM policy (pure, mocked) ─────────────────────────────

def _oom():
    import torch
    return torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB")


def test_oom_retries_gpu_once_then_cpu(monkeypatch):
    calls = []
    freed = []
    monkeypatch.setattr(gpu, "free_cuda", lambda: freed.append(1))

    def fn(dev):
        calls.append(dev)
        if dev != "cpu":
            raise _oom()
        return "ok"
    fb = gpu.OomFallback("test", device="cuda:0")
    assert fb.call(fn) == "ok"
    assert calls == ["cuda:0", "cuda:0", "cpu"] and len(freed) == 2 and fb.fell_back and fb.device == "cpu"
    # the job stays on the CPU afterwards
    assert fb.call(fn) == "ok" and calls[-1] == "cpu"


def test_oom_second_gpu_try_succeeds(monkeypatch):
    monkeypatch.setattr(gpu, "free_cuda", lambda: None)
    calls = []

    def fn(dev):
        calls.append(dev)
        if len(calls) == 1:
            raise RuntimeError("CUDA error: out of memory")
        return dev
    assert gpu.OomFallback("t", device="cuda:0").call(fn) == "cuda:0" and calls == ["cuda:0", "cuda:0"]


def test_oom_everywhere_is_retryable_job_error(monkeypatch):
    from voti.queue import JobError, classify
    monkeypatch.setattr(gpu, "free_cuda", lambda: None)

    def fn(dev):
        if dev == "cpu":
            raise RuntimeError("DefaultCPUAllocator: can't allocate memory: you tried to allocate 1 bytes")
        raise _oom()
    with pytest.raises(JobError) as e:
        gpu.OomFallback("t", device="cuda:0").call(fn)
    assert classify(e.value)[0] == "out_of_memory" and e.value.retryable and not e.value.blocked


def test_other_errors_are_not_swallowed(monkeypatch):
    def fn(dev):
        raise ValueError("bad input")
    with pytest.raises(ValueError):
        gpu.OomFallback("t", device="cuda:0").call(fn)
    assert gpu.is_cuda_oom(RuntimeError("wrap")) is False
    try:
        try:
            raise _oom()
        except Exception as inner:
            raise RuntimeError("pipeline failed") from inner
    except RuntimeError as outer:
        assert gpu.is_cuda_oom(outer)


def test_embed_batch_falls_back_to_cpu(monkeypatch):
    import torch

    from voti.pipeline import embed

    class FakeModel:
        def __init__(self, dev):
            self.logical, self.device = dev, "cpu"  # tensors stay on the CPU; `logical` is the simulated device

        def encode_batch(self, wav, lens):
            if self.logical != "cpu":
                raise torch.cuda.OutOfMemoryError("CUDA out of memory")
            return torch.ones(wav.shape[0], 1, 192)
    used = []
    monkeypatch.setattr(embed, "_model", lambda dev=None: used.append(dev) or FakeModel(dev))
    monkeypatch.setattr("voti.devices.torch_device", lambda: "cuda:0")
    out = embed.embed_batch([np.zeros(SR, np.float32), np.zeros(2 * SR, np.float32)])
    assert out.shape == (2, 192) and np.allclose(np.linalg.norm(out, axis=1), 1.0)
    assert used == ["cuda:0", "cuda:0", "cpu"]


def test_diarize_falls_back_to_cpu(monkeypatch):
    import torch

    from voti.pipeline import diarize
    used = []

    def fake_diarize(audio, device, progress=None):
        used.append(device)
        if device != "cpu":
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        return [("SPEAKER_00", 0.0, 1.0)]
    monkeypatch.setattr(diarize, "_diarize", fake_diarize)
    monkeypatch.setattr("voti.devices.torch_device", lambda: "cuda:0")
    assert diarize.diarize_audio(np.zeros(SR, np.float32)) == [("SPEAKER_00", 0.0, 1.0)]
    assert used == ["cuda:0", "cuda:0", "cpu"]


# ───────────────────────────── align.run against the test DB (fake aligner) ─────────────────────────────

class FakeAligner:
    """Evenly spreads the tokens over the middle of the window; can simulate CUDA OOM on the GPU."""
    calls: list = []

    def __init__(self, device, oom_on_gpu=False):
        self.device, self.oom_on_gpu = device, oom_on_gpu

    def align(self, samples, words):
        FakeAligner.calls.append((self.device, list(words)))
        if self.oom_on_gpu and self.device != "cpu":
            raise _oom()
        dur = samples.size / SR
        step = (dur - 0.4) / max(1, len(words))
        return [(0.2 + i * step, 0.2 + (i + 1) * step, 0.9) for i in range(len(words))]


def _wav_bytes(seconds: float) -> bytes:
    pcm = (np.sin(np.arange(int(seconds * SR)) / 10) * 8000).astype("<i2")
    fmt = struct.pack("<HHIIHH", 1, 1, SR, SR * 2, 2, 16)
    body = b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", pcm.nbytes) + pcm.tobytes()
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


@pytest.fixture
def fake_aligner(monkeypatch):
    FakeAligner.calls = []
    state = {"oom": False}
    monkeypatch.setattr(align, "load_aligner", lambda dev: FakeAligner(dev, state["oom"]))
    monkeypatch.setattr("voti.devices.torch_device", lambda: "cuda:0")
    monkeypatch.setattr(gpu, "free_cuda", lambda: None)
    return state


def _media_with_audio(factory, seconds=20.0, **kw):  # noqa: ANN001
    from voti import storage
    m = factory.media(duration_sec=seconds, **kw)
    storage.put_bytes(_wav_bytes(seconds), storage.media_key(m.id, "normalized.wav"), "audio/wav")
    return m


def test_align_run_writes_rows_and_skips_unsupported(db, factory, fake_aligner):  # noqa: ANN001
    from voti.models import WordAlignment
    m = _media_with_audio(factory, status="ready")
    a = factory.segment(m, 1.0, 3.0, original_text="你好，世界。", language="zh-TW")
    b = factory.segment(m, 4.0, 6.0, original_text="逐家好", language="nan-TW")
    c = factory.segment(m, 7.0, 9.0, original_text="", language="zh-TW", text_source="none")
    d = factory.segment(m, 10.0, 12.0, original_text="原本", corrected_text="Hello 台灣", language="zh-TW")
    res = align.run(m.id)
    assert (res["aligned"], res["skipped"], res["failed"]) == (2, 2, 0)
    assert res["reasons"] == {"no_romanizer": 1, "no_text": 1} and res["device"] == "cuda:0"
    rows = {r.segment_id: r for r in db.scalars(select(WordAlignment))}
    ra = rows[a.id]
    assert ra.status == "aligned" and ra.revision == 1 and ra.text_hash == align.text_hash("你好，世界。")
    assert "".join(u[0] for u in ra.units) == "你好，世界。"
    timed = [u for u in ra.units if u[1] is not None]
    assert [u[0] for u in timed] == ["你", "好", "世", "界"]
    assert all(0.8 <= u[1] <= u[2] <= 3.2 for u in timed)  # inside the padded window, monotonic
    assert [u[1] for u in timed] == sorted(u[1] for u in timed)
    assert rows[b.id].status == "skipped" and rows[b.id].reason == "no_romanizer:nan-TW"
    assert rows[c.id].reason == "no_text"
    assert [u[0] for u in rows[d.id].units] == ["Hello", " ", "台", "灣"]  # corrected_text wins
    # idempotent: nothing left to do
    FakeAligner.calls = []
    assert align.run(m.id)["aligned"] == 0 and FakeAligner.calls == []


def test_align_realigns_edited_segments_only(db, factory, fake_aligner, annotator_client):  # noqa: ANN001
    from voti.models import Job, Media, WordAlignment
    from voti.queue import run_one
    import voti.pipeline.handlers  # noqa: F401
    m = _media_with_audio(factory, status="ready")
    a = factory.segment(m, 1.0, 3.0, original_text="你好", language="zh-TW")
    b = factory.segment(m, 4.0, 6.0, original_text="再見", language="zh-TW")
    align.run(m.id)
    r = annotator_client.patch(f"/api/segments/{a.id}", json={"corrected_text": "您好嗎"})
    assert r.json()["alignment"]["status"] == "pending"
    job = db.scalar(select(Job).where(Job.kind == "media.align"))
    FakeAligner.calls = []
    run_one(str(job.id), "test")
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.status == "completed", job.error_message
    assert [w for _, w in FakeAligner.calls] == [["nin", "hao", "ma"]]  # only the edited segment
    d = annotator_client.get(f"/api/segments/{a.id}").json()
    assert d["alignment"]["status"] == "aligned" and "".join(w["t"] for w in d["words"]) == "您好嗎"
    assert db.get(Media, m.id).status == "ready" and db.get(Media, m.id).stage is None
    # boundary edit → revision 2 → stale until re-aligned
    annotator_client.patch(f"/api/segments/{b.id}", json={"start_sec": 4.2})
    assert annotator_client.get(f"/api/segments/{b.id}").json()["words"] is None
    job = db.scalar(select(Job).where(Job.kind == "media.align", Job.status == "queued"))
    run_one(str(job.id), "test")
    db.expire_all()
    assert db.get(WordAlignment, b.id).revision == 2
    assert annotator_client.get(f"/api/segments/{b.id}").json()["alignment"]["status"] == "aligned"


def test_align_cuda_oom_falls_back_to_cpu(db, factory, fake_aligner):  # noqa: ANN001
    fake_aligner["oom"] = True
    m = _media_with_audio(factory, status="ready")
    factory.segment(m, 1.0, 3.0, original_text="你好", language="zh-TW")
    factory.segment(m, 4.0, 6.0, original_text="再見", language="zh-TW")
    res = align.run(m.id)
    assert res["aligned"] == 2 and res["device"] == "cpu" and res["cpu_fallback"] is True
    devs = [d for d, _ in FakeAligner.calls]
    assert devs == ["cuda:0", "cuda:0", "cpu", "cpu"]  # GPU, GPU retry, then CPU for the rest of the job


def test_pipeline_align_step_chains_and_survives_missing_model(db, factory, monkeypatch):  # noqa: ANN001
    from voti.models import Job, Media
    from voti.queue import JobError, enqueue, run_one
    import voti.pipeline.handlers  # noqa: F401

    def unavailable(dev):
        raise JobError("align_model_unavailable", "MMS_FA 模型載入失敗", blocked=True)
    monkeypatch.setattr(align, "load_aligner", unavailable)
    monkeypatch.setattr("voti.devices.torch_device", lambda: "cpu")
    m = _media_with_audio(factory, status="processing")
    factory.segment(m, 1.0, 3.0, original_text="你好", language="zh-TW")
    job = enqueue(db, "media.align", media_id=m.id)
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.status == "completed" and job.result["skipped"] is True
    assert db.scalar(select(Job).where(Job.parent_id == job.id)).kind == "media.validate"
    assert db.get(Media, m.id).status == "processing"
    # outside the chain the same error fails the job but never the media
    job = enqueue(db, "media.align", media_id=m.id, payload={"chain": False})
    db.commit()
    db.query(Media).filter_by(id=m.id).update({"status": "ready"})
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    assert db.get(Job, job.id).status == "blocked" and db.get(Media, m.id).status == "ready"


# ───────────────────────────── real MMS_FA (optional) ─────────────────────────────

def test_mms_fa_real_clip(tmp_path):
    clip, text = os.environ.get("VOTI_ALIGN_CLIP"), os.environ.get("VOTI_ALIGN_TEXT")
    if not (clip and text and os.path.exists(clip) and align.weights_path().is_file()):
        pytest.skip("set VOTI_ALIGN_CLIP / VOTI_ALIGN_TEXT and put the MMS_FA weights in MODEL_CACHE/mms_fa")
    import soundfile as sf
    audio, sr = sf.read(clip, dtype="float32", always_2d=True)
    assert sr == SR
    audio = audio[:, 0]
    us = align.units(text, "zh")
    words = [u.token for u in us if u.token is not None]
    fb = gpu.OomFallback("test")
    import time
    fb.call(align.load_aligner)
    t0 = time.monotonic()
    spans = fb.call(lambda dev: align.load_aligner(dev).align(audio, words))
    elapsed = time.monotonic() - t0
    align.assign_times(us, spans, 0.0)
    out = align.units_out(us)
    print(f"\nMMS_FA on {fb.device}: {audio.size / SR:.2f}s audio aligned in {elapsed:.3f}s")
    for u in out:
        print(u)
    timed = [u for u in out if u[1] is not None]
    assert len(timed) >= len(text.replace(" ", "")) // 2
    starts = [u[1] for u in timed]
    assert starts == sorted(starts) and 0 <= starts[0] and timed[-1][2] <= audio.size / SR + 1e-3
