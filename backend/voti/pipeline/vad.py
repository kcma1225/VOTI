"""Silero VAD (snakers4/silero-vad, MIT) frame probabilities on 16 kHz audio.

The model consumes 512-sample (32 ms) chunks with recurrent state; `audio_forward` runs the whole file
in one call. Silero is small and runs on CPU (the JIT model is not faster on GPU for this workload).
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache

import numpy as np

log = logging.getLogger("voti.pipeline.vad")

SR = 16000
CHUNK = 512
HOP_SEC = CHUNK / SR  # 0.032 s
MODEL_ID = "silero-vad"
_lock = threading.Lock()


def model_version() -> str:
    try:
        from importlib.metadata import version
        return f"silero-vad {version('silero-vad')}"
    except Exception:  # noqa: BLE001
        return "silero-vad"


@lru_cache(maxsize=1)
def _model():
    from silero_vad import load_silero_vad
    return load_silero_vad(onnx=False)


def silero_probs(audio: np.ndarray, block_sec: float = 600.0) -> tuple[float, np.ndarray]:
    """→ (hop_sec, speech probability per 32 ms frame). Processes long audio in blocks (state reset at block
    boundaries; blocks are long enough that this is irrelevant)."""
    import torch
    x = np.asarray(audio, dtype=np.float32)
    if x.size == 0:
        return HOP_SEC, np.zeros(0, dtype=np.float32)
    block = int(block_sec * SR) // CHUNK * CHUNK
    out = []
    with _lock, torch.inference_mode():
        m = _model()
        for a in range(0, x.size, block):
            seg = torch.from_numpy(x[a:a + block].copy())
            m.reset_states()
            p = m.audio_forward(seg, SR)
            out.append(p.squeeze(0).float().cpu().numpy())
        m.reset_states()
    probs = np.concatenate(out).astype(np.float32)
    n = int(np.ceil(x.size / CHUNK))
    return HOP_SEC, probs[:n]
