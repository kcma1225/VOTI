"""`media.embed`: ECAPA-TDNN speaker embeddings (speechbrain/spkrec-ecapa-voxceleb) → pgvector.

Only segments with speaker_state single/unknown and >= MIN_SPEECH_SEC of speech are embedded. The input is the
segment's speech frames only (Brouhaha VAD), and for single-speaker segments additionally only the dominant
diarization speaker's non-overlapping turns (falls back to all speech when that leaves too little audio).
Vectors are L2-normalised so cosine distance == pgvector `<=>`.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import delete, select

from ..config import get_config
from ..db import session_scope
from ..models import EMBEDDING_DIM, DiarizationTurn, Embedding, Media, Segment
from ..queue import JobError
from .common import SR, load_frames, load_normalized, work_dir

log = logging.getLogger("voti.pipeline.embed")

MODEL_ID = "speechbrain/spkrec-ecapa-voxceleb"
MIN_SPEECH_SEC = 1.0
MAX_SPEECH_SEC = 30.0
BATCH_SAMPLES = SR * 240  # padded samples per forward pass
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _model():
    try:
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy
    except ImportError as e:
        raise JobError("embedding_unavailable", f"此 worker 未安裝 speechbrain：{e}", blocked=True) from e
    from ..devices import torch_device
    savedir = Path(get_config().model_cache) / "speechbrain" / "spkrec-ecapa-voxceleb"
    try:
        m = EncoderClassifier.from_hparams(source=MODEL_ID, savedir=str(savedir),
                                           run_opts={"device": torch_device()}, local_strategy=LocalStrategy.COPY)
    except Exception as e:  # noqa: BLE001
        raise JobError("embedding_model_unavailable", f"ECAPA 模型下載或載入失敗：{str(e)[:400]}", blocked=True) from e
    m.eval()
    return m


def embed_batch(clips: list[np.ndarray]) -> np.ndarray:
    """List of 16 kHz float32 clips → (N, 192) L2-normalised embeddings (length-sorted, padded batches)."""
    import torch
    if not clips:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    m = _model()
    dev = m.device if hasattr(m, "device") else "cpu"
    order = np.argsort([c.size for c in clips])
    out = np.zeros((len(clips), EMBEDDING_DIM), dtype=np.float32)
    i = 0
    with _lock, torch.inference_mode():
        while i < len(order):
            j = i + 1
            while j < len(order) and clips[order[j]].size * (j - i + 1) <= BATCH_SAMPLES and j - i < 64:
                j += 1
            idx = order[i:j]
            longest = max(clips[k].size for k in idx)
            wav = np.zeros((len(idx), longest), dtype=np.float32)
            lens = np.zeros(len(idx), dtype=np.float32)
            for r, k in enumerate(idx):
                wav[r, :clips[k].size] = clips[k]
                lens[r] = clips[k].size / longest
            e = m.encode_batch(torch.from_numpy(wav).to(dev), torch.from_numpy(lens).to(dev))
            e = e.squeeze(1).float().cpu().numpy()
            out[idx] = e
            i = j
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(norms, 1e-9)


def speech_clip(audio: np.ndarray, start: float, end: float, frames, keep_spans: list[tuple[float, float]] | None
                ) -> np.ndarray:
    """Samples of [start, end) where VAD says speech (∩ keep_spans when given)."""
    a, b = int(start * SR), int(end * SR)
    seg = audio[a:b]
    sl = frames.span(start, end)
    vad = frames.brouhaha_vad[sl] >= 0.5
    t = (sl.start + np.arange(vad.size)) * frames.hop_sec
    sample_t = start + np.arange(seg.size) / SR
    fi = np.clip(np.searchsorted(t, sample_t, side="right") - 1, 0, max(0, vad.size - 1))
    mask = vad[fi] if vad.size else np.zeros(seg.size, bool)
    if keep_spans is not None:
        km = np.zeros(seg.size, bool)
        for x, y in keep_spans:
            km[max(0, int((x - start) * SR)):max(0, int((y - start) * SR))] = True
        mask &= km
    return seg[mask][: int(MAX_SPEECH_SEC * SR)]


def run(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None,
        force: bool = False) -> dict:
    t0 = time.monotonic()
    with session_scope() as s:
        if s.get(Media, media_id) is None:
            raise JobError("not_found", "找不到媒體", retryable=False)
        segs = s.scalars(select(Segment).where(Segment.media_id == media_id, Segment.active.is_(True),
                                               Segment.speaker_state.in_(("single", "unknown")))
                         .order_by(Segment.start_sec)).all()
        if force:
            s.execute(delete(Embedding).where(Embedding.segment_id.in_([g.id for g in segs]),
                                              Embedding.model_id == MODEL_ID))
        have = set(s.execute(select(Embedding.segment_id, Embedding.revision)
                             .where(Embedding.segment_id.in_([g.id for g in segs]), Embedding.model_id == MODEL_ID)).all())
        todo = [(g.id, g.revision, g.start_sec, g.end_sec, g.local_speaker, g.speaker_state) for g in segs
                if (g.id, g.revision) not in have and g.end_sec - g.start_sec >= MIN_SPEECH_SEC]
        turns: dict[str, list[tuple[float, float]]] = {}
        for t in s.scalars(select(DiarizationTurn).where(DiarizationTurn.media_id == media_id,
                                                         DiarizationTurn.is_overlap.is_(False))):
            turns.setdefault(t.local_speaker, []).append((t.start_sec, t.end_sec))
        frames = load_frames(s, media_id) if todo else None
    if not todo:
        return {"embedded": 0, "skipped": 0, "already": len(have)}
    if frames is None:
        raise JobError("quality_missing", "尚未產生逐幀品質資料（請先執行 media.quality）", retryable=False)
    with work_dir("embed") as wd:
        with session_scope() as s:
            audio = load_normalized(s, media_id, wd)
    clips, keep = [], []
    skipped = 0
    for sid, rev, a, b, spk, state in todo:
        clip = None
        if state == "single" and spk and spk in turns:
            clip = speech_clip(audio, a, b, frames, turns[spk])
            if clip.size < MIN_SPEECH_SEC * SR:
                clip = None
        if clip is None:
            clip = speech_clip(audio, a, b, frames, None)
        if clip.size < MIN_SPEECH_SEC * SR:
            skipped += 1
            continue
        clips.append(clip)
        keep.append((sid, rev))
    progress(0.3, "ecapa")
    vecs = embed_batch(clips)
    if keep:
        # ON CONFLICT: a re-run after an interrupted attempt (or a concurrent lazy re-embed) never duplicates
        from sqlalchemy.dialects.postgresql import insert
        with session_scope() as s:
            s.execute(insert(Embedding).values(
                [{"id": uuid.uuid4(), "segment_id": sid, "revision": rev, "model_id": MODEL_ID,
                  "vector": v.astype(np.float32).tolist()} for (sid, rev), v in zip(keep, vecs)])
                .on_conflict_do_nothing(index_elements=["segment_id", "revision", "model_id"]))
    return {"embedded": len(keep), "skipped": skipped, "already": len(have),
            "speech_sec": round(sum(c.size for c in clips) / SR, 2), "elapsed_sec": round(time.monotonic() - t0, 2)}
