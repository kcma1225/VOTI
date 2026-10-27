"""`media.quality`: Brouhaha (legacy-ml: VAD/SNR/C50 per frame) + Silero VAD cross-check + DNSMOS over speech.

Writes frames_quality.npz (layout in docs/ARCHITECTURE.md), the speech/SNR/DNSMOS columns of MediaQuality and
SpeechRegion rows for both VADs. Container/loudness/bandwidth columns are owned by media.normalize.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

import numpy as np
from sqlalchemy import delete

from .. import storage
from ..db import session_scope
from ..models import Media, MediaQuality, SpeechRegion
from ..queue import JobError
from . import dnsmos, vad
from .common import (Frames, finite_or_none, hysteresis_regions, load_normalized, normalized_key, resample_frames,
                     work_dir)

log = logging.getLogger("voti.pipeline.quality")

SPEECH_THR = 0.5
DNSMOS_HOP_SEC = 1.0
DNSMOS_MIN_SPEECH = 0.5   # window kept when >= half of it is speech (brouhaha)


def _brouhaha(key: str) -> dict:
    """legacy_client.brouhaha with every connectivity failure mapped to a blocked JobError."""
    try:
        from . import legacy_client
    except ImportError as e:  # pragma: no cover - module missing in a broken deployment
        raise JobError("legacy_ml_unavailable", f"legacy-ml 用戶端無法載入：{e}", blocked=True) from e
    try:
        out = legacy_client.brouhaha(key)
    except JobError:
        raise
    except (OSError, ConnectionError) as e:
        raise JobError("legacy_ml_unavailable", f"無法連線 legacy-ml：{e}", blocked=True, retryable=True) from e
    except Exception as e:  # httpx errors etc.
        if "httpx" in type(e).__module__ or "Unavailable" in type(e).__name__:
            raise JobError("legacy_ml_unavailable", f"無法連線 legacy-ml：{e}", blocked=True, retryable=True) from e
        raise
    get = out.get if isinstance(out, dict) else (lambda k, d=None: getattr(out, k, d))
    return {"hop_sec": float(get("hop_sec")), "vad": np.asarray(get("vad"), dtype=np.float32),
            "snr": np.asarray(get("snr"), dtype=np.float32), "c50": np.asarray(get("c50"), dtype=np.float32),
            "model": str(get("model", "brouhaha") or "brouhaha")}


def dnsmos_window_starts(speech: np.ndarray, hop: float, duration: float) -> list[float]:
    """Start times (s) of 9.01 s windows (1 s hop) that are mostly speech."""
    if duration < dnsmos.INPUT_LENGTH:
        return [0.0] if speech.size and speech.mean() >= 0.2 else []
    starts = []
    cum = np.concatenate([[0], np.cumsum(speech.astype(np.float64))])
    n_win = int(round(dnsmos.INPUT_LENGTH / hop))
    for k in range(int(np.floor(duration - dnsmos.INPUT_LENGTH)) + 1):
        t = k * DNSMOS_HOP_SEC
        a = int(t / hop)
        b = min(len(speech), a + n_win)
        if b > a and (cum[b] - cum[a]) / (b - a) >= DNSMOS_MIN_SPEECH:
            starts.append(t)
    return starts


def compute_dnsmos(audio: np.ndarray, starts: list[float]) -> tuple[np.ndarray, np.ndarray]:
    sr = dnsmos.SR
    if not starts:
        return np.zeros(0, dtype=np.float32), np.zeros((0, 4), dtype=np.float32)
    if audio.size < dnsmos.WIN:
        score = dnsmos.score_clip(audio)
        return np.array([audio.size / sr / 2], dtype=np.float32), score.reshape(1, 4).astype(np.float32)
    out = []
    chunk = 256
    for a in range(0, len(starts), chunk):
        w = np.stack([dnsmos.window_at(audio, int(t * sr)) for t in starts[a:a + chunk]])
        out.append(dnsmos.score_windows(w))
    t = np.asarray(starts, dtype=np.float32) + dnsmos.INPUT_LENGTH / 2
    return t, np.concatenate(out).astype(np.float32)


def summarize(frames: Frames, duration: float) -> dict:
    hop = frames.hop_sec
    sp = frames.brouhaha_vad >= SPEECH_THR
    si = frames.silero_vad >= SPEECH_THR
    speech_sec = float(sp.sum() * hop)
    out = {"speech_sec": speech_sec, "speech_ratio": min(1.0, speech_sec / duration) if duration > 0 else None,
           "vad_agreement": float(np.mean(sp == si)) if sp.size else None,
           "snr_mean": None, "snr_p10": None, "c50_mean": None,
           "dnsmos_sig": None, "dnsmos_bak": None, "dnsmos_ovrl": None, "dnsmos_p808": None}
    if sp.any():
        snr, c50 = frames.snr[sp], frames.c50[sp]
        out.update(snr_mean=finite_or_none(np.nanmean(snr)), snr_p10=finite_or_none(np.nanpercentile(snr, 10)),
                   c50_mean=finite_or_none(np.nanmean(c50)))
    if frames.dnsmos.shape[0]:
        m = frames.dnsmos.mean(axis=0)
        out.update(dnsmos_sig=finite_or_none(m[0]), dnsmos_bak=finite_or_none(m[1]),
                   dnsmos_ovrl=finite_or_none(m[2]), dnsmos_p808=finite_or_none(m[3]))
    return out


def _hist(values: np.ndarray, edges: np.ndarray) -> list[dict]:
    v = values[np.isfinite(values)]
    counts, _ = np.histogram(v, bins=edges)
    return [{"bin": float(edges[i]), "count": int(c)} for i, c in enumerate(counts)]


def run(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None) -> dict:
    t0 = time.monotonic()
    timings = {}
    with session_scope() as s:
        media = s.get(Media, media_id)
        if media is None:
            raise JobError("not_found", "找不到媒體", retryable=False)
    key = normalized_key(media_id)

    progress(0.05, "brouhaha")
    b = _brouhaha(key)
    timings["brouhaha"] = time.monotonic() - t0
    hop, n = b["hop_sec"], len(b["vad"])

    with work_dir("quality") as wd:
        with session_scope() as s:
            audio = load_normalized(s, media_id, wd)
    duration = audio.size / 16000.0

    progress(0.35, "silero")
    t1 = time.monotonic()
    s_hop, s_probs = vad.silero_probs(audio)
    silero = resample_frames(s_probs, s_hop, hop, n)
    timings["silero"] = time.monotonic() - t1

    progress(0.5, "dnsmos")
    t1 = time.monotonic()
    starts = dnsmos_window_starts(b["vad"] >= SPEECH_THR, hop, duration)
    try:
        d_t, d_scores = compute_dnsmos(audio, starts)
    except dnsmos.DnsmosUnavailable as e:
        raise JobError("dnsmos_unavailable", str(e), blocked=True) from e
    timings["dnsmos"] = time.monotonic() - t1

    frames = Frames(hop_sec=hop, brouhaha_vad=b["vad"], snr=b["snr"], c50=b["c50"], silero_vad=silero,
                    dnsmos_t=d_t, dnsmos=d_scores)
    summary = summarize(frames, duration)
    b_regions = hysteresis_regions(frames.brouhaha_vad, hop)
    s_regions = hysteresis_regions(silero, hop)

    progress(0.9, "save")
    sp = frames.brouhaha_vad >= SPEECH_THR
    details = {
        "models": {"brouhaha": b["model"], "silero": vad.model_version(), "dnsmos": dnsmos.MODEL_ID},
        "hop_sec": hop, "frames": n, "dnsmos_windows": int(d_t.size),
        "histograms": {"snr": _hist(frames.snr[sp], np.arange(-10, 81, 5)),
                       "c50": _hist(frames.c50[sp], np.arange(-10, 61, 5))},
        "speech_regions": {"brouhaha": len(b_regions), "silero": len(s_regions)},
        "timings_sec": {k: round(v, 2) for k, v in timings.items()},
    }
    with session_scope() as s:
        asset = storage.store_bytes_asset(s, frames.to_npz_bytes(), storage.media_key(media_id, "frames_quality.npz"),
                                          "frames_quality", media_id=media_id,
                                          params={"models": details["models"], "hop_sec": hop})
        mq = s.get(MediaQuality, media_id)
        if mq is None:
            mq = MediaQuality(media_id=media_id)
            s.add(mq)
        for k, v in summary.items():
            setattr(mq, k, v)
        mq.frames_asset_id = asset.id
        mq.details = {**(mq.details or {}), "quality": details}
        s.execute(delete(SpeechRegion).where(SpeechRegion.media_id == media_id))
        for a, e in b_regions:
            sl = frames.span(a, e)
            s.add(SpeechRegion(media_id=media_id, source="brouhaha", start_sec=round(a, 3), end_sec=round(e, 3),
                               snr_mean=finite_or_none(np.nanmean(frames.snr[sl]))))
        for a, e in s_regions:
            s.add(SpeechRegion(media_id=media_id, source="silero", start_sec=round(a, 3), end_sec=round(e, 3)))
    return {**{k: (round(v, 3) if isinstance(v, float) else v) for k, v in summary.items()},
            "dnsmos_windows": int(d_t.size), "elapsed_sec": round(time.monotonic() - t0, 2)}
