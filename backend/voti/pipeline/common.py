"""Shared helpers for pipeline steps: settings, audio I/O, work dirs, frame arrays, interval maths."""
from __future__ import annotations

import io
import shutil
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage
from ..config import get_config
from ..models import Asset, MediaQuality, Setting

SR = 16000  # normalized audio sample rate

# Defaults mirror docs/ARCHITECTURE.md "Runtime settings".
DEFAULT_SETTINGS: dict[str, dict] = {
    "pipeline": {"voxprofile_backbone": "whisper-large-v3", "diarization_enabled": True, "segment_min_sec": 1.0,
                 "segment_max_sec": 20.0, "edge_pad_sec": 0.1},
    "quality_thresholds": {"low_speech_ratio": 0.5, "low_snr_db": 10, "clipping_ratio": 0.001},
    "validation": {"enabled": True, "lid_min_prob": 0.7, "lid_min_speech_sec": 1.0, "align_min_score": 0.4,
                   "align_min_units": 4},
    # hard-subtitle OCR (voti.pipeline.ocr.DEFAULTS)
    "ocr": {"enabled": True, "auto_when_no_captions": True, "fps": 5.0, "discovery_step_sec": 2.0,
            "max_discovery_frames": 900, "model": "server", "cpu_model": "mobile", "keep_crops": True, "min_event_conf": 0.85},
}


def get_setting(s: Session, key: str) -> dict:
    """Stored value merged over the documented defaults."""
    out = dict(DEFAULT_SETTINGS.get(key, {}))
    row = s.get(Setting, key)
    if row is not None and isinstance(row.value, dict):
        out.update({k: v for k, v in row.value.items() if v is not None})
    return out


@contextmanager
def work_dir(prefix: str = "job") -> Iterator[Path]:
    base = Path(get_config().work_dir)
    try:
        base.mkdir(parents=True, exist_ok=True)
        d = Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=base))
    except OSError:  # host runs without /work
        d = Path(tempfile.mkdtemp(prefix=f"voti-{prefix}-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def normalized_key(media_id: uuid.UUID | str) -> str:
    return storage.media_key(media_id, "normalized.wav")


def read_wav(path: Path) -> np.ndarray:
    """16 kHz mono PCM16 wav → float32 [-1, 1]."""
    import soundfile as sf
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if sr != SR:
        raise ValueError(f"expected {SR} Hz, got {sr}")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


def write_wav(path: Path, audio: np.ndarray, sr: int = SR) -> None:
    import soundfile as sf
    sf.write(str(path), np.asarray(audio, dtype=np.float32), sr, subtype="PCM_16")


def load_normalized(s: Session, media_id: uuid.UUID, dest_dir: Path) -> np.ndarray:
    """Download media/<id>/normalized.wav and return float32 samples."""
    from ..queue import JobError
    asset = s.scalar(select(Asset).where(Asset.media_id == media_id, Asset.kind == "normalized_audio"))
    key = asset.key if asset else normalized_key(media_id)
    if storage.head(key) is None:
        raise JobError("normalized_missing", "找不到標準化音訊（請先執行 media.normalize）", retryable=False)
    return read_wav(storage.get_file(key, dest_dir / "normalized.wav"))


@dataclass
class Frames:
    """Per-frame arrays of frames_quality.npz (see docs/ARCHITECTURE.md)."""
    hop_sec: float
    brouhaha_vad: np.ndarray
    snr: np.ndarray
    c50: np.ndarray
    silero_vad: np.ndarray
    dnsmos_t: np.ndarray
    dnsmos: np.ndarray

    @property
    def n(self) -> int:
        return len(self.brouhaha_vad)

    def span(self, start: float, end: float) -> slice:
        a = max(0, int(np.floor(start / self.hop_sec)))
        b = min(self.n, max(a + 1, int(np.ceil(end / self.hop_sec))))
        return slice(a, b)

    def to_npz_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, hop_sec=np.float32(self.hop_sec), brouhaha_vad=self.brouhaha_vad.astype(np.float32),
                            snr=self.snr.astype(np.float32), c50=self.c50.astype(np.float32),
                            silero_vad=self.silero_vad.astype(np.float32),
                            dnsmos_t=self.dnsmos_t.astype(np.float32),
                            dnsmos=self.dnsmos.astype(np.float32).reshape(-1, 4))
        return buf.getvalue()

    @classmethod
    def from_npz_bytes(cls, data: bytes) -> "Frames":
        z = np.load(io.BytesIO(data))
        return cls(hop_sec=float(z["hop_sec"]), brouhaha_vad=z["brouhaha_vad"], snr=z["snr"], c50=z["c50"],
                   silero_vad=z["silero_vad"], dnsmos_t=z["dnsmos_t"], dnsmos=z["dnsmos"].reshape(-1, 4))


def load_frames(s: Session, media_id: uuid.UUID) -> Frames | None:
    mq = s.get(MediaQuality, media_id)
    asset = s.get(Asset, mq.frames_asset_id) if mq and mq.frames_asset_id else None
    if asset is None:
        asset = s.scalar(select(Asset).where(Asset.media_id == media_id, Asset.kind == "frames_quality"))
    if asset is None:
        return None
    return Frames.from_npz_bytes(storage.get_bytes(asset.key))


# ───────────────────────────── intervals ─────────────────────────────

def mask_to_regions(mask: np.ndarray, hop: float) -> list[tuple[float, float]]:
    """Boolean frame mask → [(start_sec, end_sec)] runs of True."""
    m = np.asarray(mask, dtype=bool)
    if m.size == 0:
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return [(float(a * hop), float(b * hop)) for a, b in zip(starts, ends)]


def merge_regions(regions: Sequence[tuple[float, float]], min_gap: float = 0.0,
                  min_dur: float = 0.0) -> list[tuple[float, float]]:
    """Merge regions separated by < min_gap, then drop ones shorter than min_dur."""
    out: list[list[float]] = []
    for a, b in sorted(regions):
        if out and a - out[-1][1] < min_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out if b - a >= min_dur]


def hysteresis_regions(prob: np.ndarray, hop: float, onset: float = 0.5, offset: float = 0.35,
                       min_speech: float = 0.25, min_silence: float = 0.3) -> list[tuple[float, float]]:
    """Speech probability → merged speech regions (hysteresis thresholds, gap filling, min duration)."""
    p = np.asarray(prob, dtype=np.float32)
    state, start, regions = False, 0, []
    for i, v in enumerate(p):
        if not state and v >= onset:
            state, start = True, i
        elif state and v < offset:
            state = False
            regions.append((start * hop, i * hop))
    if state:
        regions.append((start * hop, len(p) * hop))
    return merge_regions(regions, min_gap=min_silence, min_dur=min_speech)


def covered(start: float, end: float, spans: Sequence[tuple[float, float]]) -> float:
    """Seconds of [start, end) covered by the union of spans."""
    parts = sorted((max(start, a), min(end, b)) for a, b in spans if b > start and a < end)
    total, cs, ce = 0.0, None, None
    for a, b in parts:
        if ce is None or a > ce:
            if ce is not None:
                total += ce - cs
            cs, ce = a, b
        else:
            ce = max(ce, b)
    if ce is not None:
        total += ce - cs
    return total


def resample_frames(values: np.ndarray, src_hop: float, dst_hop: float, n: int) -> np.ndarray:
    """Linear-interpolate a per-frame series (frame centers) onto another hop grid of length n."""
    v = np.asarray(values, dtype=np.float32)
    if v.size == 0:
        return np.zeros(n, dtype=np.float32)
    src_t = (np.arange(v.size) + 0.5) * src_hop
    dst_t = (np.arange(n) + 0.5) * dst_hop
    return np.interp(dst_t, src_t, v).astype(np.float32)


def finite_or_none(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None
