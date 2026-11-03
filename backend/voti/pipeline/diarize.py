"""`media.diarize`: pyannote.audio 4.x `pyannote/speaker-diarization-community-1` → DiarizationTurn rows.

Each turn row carries one local speaker. Turns are split at overlap boundaries: pieces where another speaker
talks at the same time get is_overlap=True, so the timeline shows both speakers *and* the overlap.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from functools import lru_cache

import numpy as np
from sqlalchemy import delete

from ..config import get_config
from ..db import session_scope
from ..models import DiarizationTurn, Media
from ..queue import JobError
from .common import SR, get_setting, load_normalized, work_dir

log = logging.getLogger("voti.pipeline.diarize")

MODEL_ID = "pyannote/speaker-diarization-community-1"
TOKEN_HELP = ("語者分段模型 pyannote/speaker-diarization-community-1 需要授權：請管理員以 Hugging Face 帳號到 "
              "https://huggingface.co/pyannote/speaker-diarization-community-1 接受使用條款，"
              "並在 .env 設定 HF_TOKEN（具讀取權限的存取權杖）後重啟 worker-ml，再重試此工作。")
_lock = threading.Lock()

os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")  # no telemetry from a research server


@lru_cache(maxsize=2)
def _load_pipeline(device: str | None = None):
    token = get_config().hf_token.strip()
    if not token:
        raise JobError("hf_token_missing", TOKEN_HELP, blocked=True)
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as e:
        raise JobError("diarization_unavailable", f"此 worker 未安裝 pyannote.audio：{e}", blocked=True) from e
    try:
        pipe = Pipeline.from_pretrained(MODEL_ID, token=token)
    except Exception as e:  # noqa: BLE001 - hub errors vary by version
        name = type(e).__name__
        if any(x in name for x in ("Gated", "Unauthorized", "Repository", "HTTP")) or "401" in str(e) or "403" in str(e):
            raise JobError("hf_unauthorized", f"{TOKEN_HELP}（{name}）", blocked=True) from e
        raise
    if pipe is None:  # pyannote swallows hub HTTP errors (gated / bad token) and returns None
        raise JobError("hf_unauthorized", TOKEN_HELP, blocked=True)
    from ..devices import torch_device
    pipe.to(torch.device(device or torch_device()))
    return pipe


def split_overlaps(turns: list[tuple[str, float, float]]) -> list[tuple[str, float, float, bool]]:
    """Split each (speaker, start, end) turn at points where the set of active *other* speakers changes;
    mark pieces with another active speaker as overlap. Pure function (unit-tested)."""
    if not turns:
        return []
    bounds = sorted({t for _, a, b in turns for t in (a, b)})
    out: list[tuple[str, float, float, bool]] = []
    for spk, a, b in sorted(turns, key=lambda x: (x[1], x[2], x[0])):
        cuts = [t for t in bounds if a < t < b]
        edges = [a, *cuts, b]
        for x, y in zip(edges, edges[1:]):
            mid = (x + y) / 2
            others = any(o != spk and oa <= mid < ob for o, oa, ob in turns)
            if out and out[-1][0] == spk and out[-1][3] == others and abs(out[-1][2] - x) < 1e-9:
                out[-1] = (spk, out[-1][1], y, others)
            else:
                out.append((spk, x, y, others))
    return [(s, round(a, 3), round(b, 3), o) for s, a, b, o in out if b - a > 1e-3]


def diarize_audio(audio: np.ndarray, progress: Callable[[str], None] | None = None) -> list[tuple[str, float, float]]:
    """CUDA OOM: cache emptied and retried once on the GPU, then the CPU (voti.pipeline.gpu)."""
    from .gpu import free_cuda, run_with_oom_fallback
    try:
        return run_with_oom_fallback(lambda dev: _diarize(audio, dev, progress), "pyannote 語者分段")
    finally:
        free_cuda()


def _diarize(audio: np.ndarray, device: str, progress: Callable[[str], None] | None = None
             ) -> list[tuple[str, float, float]]:
    import torch
    pipe = _load_pipeline(device)
    wav = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)).unsqueeze(0)

    def hook(step_name, step_artifact, file=None, total=None, completed=None):  # noqa: ANN001
        if progress:
            progress(step_name)

    with _lock, torch.inference_mode():
        out = pipe({"waveform": wav, "sample_rate": SR}, hook=hook)
    ann = getattr(out, "speaker_diarization", out)
    return [(str(label), float(seg.start), float(seg.end)) for seg, _, label in ann.itertracks(yield_label=True)]


def run(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None) -> dict:
    t0 = time.monotonic()
    with session_scope() as s:
        if s.get(Media, media_id) is None:
            raise JobError("not_found", "找不到媒體", retryable=False)
        if not get_setting(s, "pipeline").get("diarization_enabled", True):
            return {"skipped": True, "reason": "diarization_disabled"}
    from ..devices import torch_device
    from .gpu import is_cuda_oom
    try:
        _load_pipeline(torch_device())  # fail fast (blocked) before downloading audio
    except Exception as e:  # noqa: BLE001
        if not is_cuda_oom(e):
            raise  # an OOM while moving the model is handled by diarize_audio's fallback
    with work_dir("diarize") as wd:
        with session_scope() as s:
            audio = load_normalized(s, media_id, wd)
    progress(0.1, "diarize")
    turns = diarize_audio(audio, lambda step: progress(0.5, f"diarize:{step}"))
    pieces = split_overlaps(turns)
    with session_scope() as s:
        s.execute(delete(DiarizationTurn).where(DiarizationTurn.media_id == media_id))
        s.add_all([DiarizationTurn(media_id=media_id, local_speaker=spk[:32], start_sec=a, end_sec=b, is_overlap=o,
                                   model_id=MODEL_ID) for spk, a, b, o in pieces])
    speakers = sorted({p[0] for p in pieces})
    overlap_sec = sum(b - a for _, a, b, o in pieces if o) / 2 if pieces else 0.0
    return {"speakers": len(speakers), "turns": len(pieces), "overlap_sec": round(overlap_sec, 2),
            "elapsed_sec": round(time.monotonic() - t0, 2), "audio_sec": round(audio.size / SR, 2)}
