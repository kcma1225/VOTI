"""Shared helpers for test_pipeline_*.py: isolated DB/bucket env and synthetic audio generators.

Uses database `voti_test_pipe` and bucket `voti-test-pipe` (override with VOTI_TEST_PIPE_DATABASE_URL /
VOTI_TEST_PIPE_S3_*). Must be imported before anything reads voti.config.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

TEST_DB = os.environ.get("VOTI_TEST_PIPE_DATABASE_URL",
                         "postgresql+psycopg://voti:voti@127.0.0.1:55432/voti_test_pipe")
os.environ["DATABASE_URL"] = TEST_DB
os.environ["REDIS_URL"] = os.environ.get("VOTI_TEST_PIPE_REDIS_URL", "redis://127.0.0.1:56379/7")
os.environ["S3_ENDPOINT"] = os.environ.get("VOTI_TEST_PIPE_S3_ENDPOINT", "http://127.0.0.1:59000")
os.environ["S3_ACCESS_KEY"] = os.environ.get("VOTI_TEST_PIPE_S3_ACCESS_KEY", "voti")
os.environ["S3_SECRET_KEY"] = os.environ.get("VOTI_TEST_PIPE_S3_SECRET_KEY", "voti-secret-change-me")
os.environ["S3_BUCKET"] = "voti-test-pipe"
os.environ.setdefault("MODEL_CACHE", str(Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "voti-models"))
os.environ.setdefault("WORK_DIR", "/tmp/voti-test-pipe-work")

from voti.config import get_config  # noqa: E402

get_config.cache_clear()

SR = 16000


def rng(seed: int = 0) -> np.random.Generator:
    return np.random.default_rng(seed)


def speechlike(seconds: float, sr: int = SR, f0: float = 140.0, formants=(700, 1200, 2600), seed: int = 0,
               level: float = 0.2) -> np.ndarray:
    """Voiced, syllable-modulated harmonic signal with formant resonances (≈4 syllables/s, jittered pitch)."""
    from scipy import signal
    g = rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    f0_track = f0 * (1 + 0.08 * np.sin(2 * np.pi * 0.7 * t + g.uniform(0, 6)) + 0.02 * g.standard_normal(n).cumsum() / np.sqrt(n))
    phase = 2 * np.pi * np.cumsum(f0_track) / sr
    src = sum(np.sin(k * phase) / k for k in range(1, 30) if k * f0 < sr / 2 - 200)
    src = src + 0.05 * g.standard_normal(n)
    out = np.zeros(n)
    for fc in formants:
        b, a = signal.iirpeak(fc, Q=6, fs=sr)
        out += signal.lfilter(b, a, src)
    syll = 0.5 * (1 - np.cos(2 * np.pi * 4.0 * t + g.uniform(0, 6)))
    out *= syll ** 1.5
    out /= np.max(np.abs(out)) + 1e-9
    return (level * out).astype(np.float32)


def with_pauses(chunks: list[tuple[str, float]], sr: int = SR, seed: int = 0, noise: float = 0.002,
                **kw) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """[("speech"|"silence", seconds), ...] → audio, speech intervals."""
    g = rng(seed)
    parts, regions, t = [], [], 0.0
    for i, (kind, sec) in enumerate(chunks):
        if kind == "speech":
            parts.append(speechlike(sec, sr, seed=seed + i, **kw))
            regions.append((t, t + sec))
        else:
            parts.append(np.zeros(int(sec * sr), dtype=np.float32))
        t += sec
    x = np.concatenate(parts)
    return (x + noise * g.standard_normal(x.size)).astype(np.float32), regions


def vad_from_regions(regions, duration: float, hop: float = 0.016875) -> np.ndarray:
    n = int(np.ceil(duration / hop))
    p = np.full(n, 0.05, dtype=np.float32)
    for a, b in regions:
        p[int(a / hop):int(np.ceil(b / hop))] = 0.95
    return p


def jingle(seconds: float = 6.0, sr: int = SR, seed: int = 42) -> np.ndarray:
    """A short music-like jingle: arpeggiated tones + percussive noise bursts."""
    g = rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = np.zeros(n)
    notes = g.choice([262, 294, 330, 392, 440, 523, 587, 659, 784], size=int(seconds * 4))
    for i, f in enumerate(notes):
        a = int(i * sr / 4)
        b = min(n, a + int(sr / 4))
        env = np.exp(-np.arange(b - a) / (0.08 * sr))
        x[a:b] += env * (np.sin(2 * np.pi * f * t[a:b]) + 0.5 * np.sin(2 * np.pi * 2 * f * t[a:b]))
        x[a:a + 400] += 0.3 * g.standard_normal(min(400, n - a))
    return (0.3 * x / np.max(np.abs(x))).astype(np.float32)
