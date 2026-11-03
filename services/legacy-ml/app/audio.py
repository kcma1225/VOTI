"""S3 access and wav reading for legacy-ml.

The service only reads `normalized.wav` style objects (16 kHz mono PCM). Objects are downloaded once
into a small local LRU cache (keyed by bucket/key/ETag) because the backend calls /voxprofile several
times per media (batches of spans) and /brouhaha once.
"""
from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass

import boto3
import numpy as np
import soundfile as sf
import torch
import torchaudio.functional as AF
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

log = logging.getLogger("legacy_ml.audio")

SR = 16000


class NotFound(Exception):
    pass


@dataclass
class LocalAudio:
    path: str
    sample_rate: int
    channels: int
    frames: int

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


_s3 = None
_cache: "OrderedDict[tuple, LocalAudio]" = OrderedDict()
_cache_lock = threading.Lock()
CACHE_FILES = int(os.environ.get("AUDIO_CACHE_FILES", "3"))
CACHE_DIR = os.environ.get("AUDIO_CACHE_DIR", "/tmp/legacy-ml-audio")


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT", "http://rustfs:9000"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "voti"),
            aws_secret_access_key=os.environ.get("S3_SECRET_KEY", ""),
            region_name=os.environ.get("S3_REGION", "us-east-1"),
            config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 5, "mode": "standard"},
                              signature_version="s3v4"),
        )
    return _s3


def default_bucket() -> str:
    return os.environ.get("S3_BUCKET", "voti")


def fetch(key: str, bucket: str | None = None) -> LocalAudio:
    """Download s3://bucket/key (cached by ETag) and return its local path + header info."""
    bucket = bucket or default_bucket()
    try:
        head = s3().head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            raise NotFound(f"s3://{bucket}/{key} not found") from e
        raise
    ck = (bucket, key, head.get("ETag"))
    with _cache_lock:
        if ck in _cache:
            _cache.move_to_end(ck)
            if os.path.exists(_cache[ck].path):
                return _cache[ck]
            del _cache[ck]
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, f"{abs(hash(ck)):x}.wav")
        tmp = path + ".part"
        s3().download_file(bucket, key, tmp)
        os.replace(tmp, path)
        info = sf.info(path)
        la = LocalAudio(path=path, sample_rate=info.samplerate, channels=info.channels, frames=info.frames)
        _cache[ck] = la
        while len(_cache) > CACHE_FILES:
            _, old = _cache.popitem(last=False)
            try:
                os.remove(old.path)
            except OSError:
                pass
        return la


def to_mono_16k(block: np.ndarray, sr: int) -> np.ndarray:
    """(frames, channels) float32 → (frames,) float32 at 16 kHz."""
    y = block.mean(axis=1) if block.ndim == 2 else block
    y = np.ascontiguousarray(y, dtype=np.float32)
    if sr != SR:
        y = AF.resample(torch.from_numpy(y), sr, SR).numpy()
    return y


def read_range(la: LocalAudio, start_sec: float, end_sec: float) -> np.ndarray:
    """Read [start, end) seconds as mono 16 kHz float32 (clipped to the file)."""
    a = max(0, int(round(start_sec * la.sample_rate)))
    b = min(la.frames, int(round(end_sec * la.sample_rate)))
    if b <= a:
        return np.zeros(0, dtype=np.float32)
    with sf.SoundFile(la.path) as f:
        f.seek(a)
        block = f.read(b - a, dtype="float32", always_2d=True)
    return to_mono_16k(block, la.sample_rate)


def iter_blocks(la: LocalAudio, starts_ends_16k: list[tuple[int, int]]):
    """Yield mono 16 kHz arrays for [a, b) ranges given in 16 kHz sample units (streaming, bounded memory)."""
    ratio = la.sample_rate / SR
    with sf.SoundFile(la.path) as f:
        for a, b in starts_ends_16k:
            fa, fb = int(round(a * ratio)), min(la.frames, int(round(b * ratio)))
            f.seek(fa)
            block = f.read(max(0, fb - fa), dtype="float32", always_2d=True)
            y = to_mono_16k(block, la.sample_rate)
            # resampling may be off by a sample; force exact length (pad with zeros at file end only)
            want = b - a
            if len(y) > want:
                y = y[:want]
            yield y
