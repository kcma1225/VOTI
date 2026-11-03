"""Microsoft DNSMOS (P.835 sig/bak/ovrl + P.808) via onnxruntime.

Models and maths follow microsoft/DNS-Challenge DNSMOS/dnsmos_local.py exactly:
  * sig_bak_ovr.onnx on raw 16 kHz windows of 9.01 s (144160 samples), outputs raw sig/bak/ovr →
    official polynomial fits (non-personalized)
  * model_v8.onnx (P.808) on a 120-band log-mel of the same window minus its last 160 samples
    (n_fft=321, hop=160, power_to_db(ref=max) → (x+40)/40)
  * windows hop 1 s; clips shorter than 9.01 s are repeated until long enough
The ONNX files are downloaded on first use into MODEL_CACHE/dnsmos/ from a pinned commit and verified by sha256.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import get_config

log = logging.getLogger("voti.pipeline.dnsmos")

SR = 16000
INPUT_LENGTH = 9.01
WIN = int(INPUT_LENGTH * SR)  # 144160
MODEL_ID = "dnsmos-p835-sig_bak_ovr+p808-model_v8"
_REPO = "https://raw.githubusercontent.com/microsoft/DNS-Challenge"
MODELS = {
    # path in repo, pinned commit (last commit touching the file), sha256 of the file
    "sig_bak_ovr.onnx": ("DNSMOS/DNSMOS/sig_bak_ovr.onnx", "82f1b17e7776a43eee395d0f45bae8abb700ad00",
                         "269fbebdb513aa23cddfbb593542ecc540284a91849ac50516870e1ac78f6edd"),
    "model_v8.onnx": ("DNSMOS/DNSMOS/model_v8.onnx", "e14b010991d9d85c27070b79e34028bc60d789d6",
                      "9246480c58567bc6affd4200938e77eef49468c8bc7ed3776d109c07456f6e91"),
}
# Official non-personalized polynomial fits (np.poly1d coefficient order: highest power first)
P_OVR = np.poly1d([-0.06766283, 1.11546468, 0.04602535])
P_SIG = np.poly1d([-0.08397278, 1.22083953, 0.0052439])
P_BAK = np.poly1d([-0.13166888, 1.60915514, -0.39604546])
_lock = threading.Lock()


class DnsmosUnavailable(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_dir() -> Path:
    return Path(get_config().model_cache) / "dnsmos"


def ensure_models() -> dict[str, Path]:
    """Download (once) and sha256-verify both ONNX files."""
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, (repo_path, commit, digest) in MODELS.items():
        path = d / name
        if not path.exists() or _sha256(path) != digest:
            url = f"{_REPO}/{commit}/{repo_path}"
            tmp = path.with_suffix(".part")
            log.info("downloading DNSMOS model %s", url)
            try:
                with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:  # noqa: S310 (pinned https)
                    f.write(r.read())
            except OSError as e:
                tmp.unlink(missing_ok=True)
                raise DnsmosUnavailable(f"DNSMOS 模型下載失敗（{url}）：{e}") from e
            got = _sha256(tmp)
            if got != digest:
                tmp.unlink(missing_ok=True)
                raise DnsmosUnavailable(f"DNSMOS 模型 {name} sha256 不符：{got}")
            os.replace(tmp, path)
        out[name] = path
    return out


@lru_cache(maxsize=2)
def _sessions(force_cpu: bool = False):
    import onnxruntime as ort

    from ..devices import onnx_providers
    paths = ensure_models()
    if hasattr(ort, "preload_dlls") and "CUDAExecutionProvider" in ort.get_available_providers():
        try:  # use the CUDA/cuDNN libraries that ship with the torch wheels
            ort.preload_dlls()
        except Exception as e:  # noqa: BLE001
            log.warning("onnxruntime preload_dlls failed: %s", e)
    so = ort.SessionOptions()
    so.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 2) // 2))
    providers = ["CPUExecutionProvider"] if force_cpu else (onnx_providers() or ["CPUExecutionProvider"])
    return (ort.InferenceSession(str(paths["sig_bak_ovr.onnx"]), so, providers=providers),
            ort.InferenceSession(str(paths["model_v8.onnx"]), so, providers=providers))


def _melspec(windows: np.ndarray) -> np.ndarray:
    """(B, WIN) → (B, 900, 120) exactly like dnsmos_local.audio_melspec on audio[:-160]
    (batched melspectrogram; power_to_db with ref=max applied per window)."""
    import librosa
    mel = librosa.feature.melspectrogram(y=np.ascontiguousarray(windows[:, :-160]), sr=SR, n_fft=321,
                                         hop_length=160, n_mels=120)          # (B, 120, 900)
    amin, top_db = 1e-10, 80.0
    db = 10.0 * np.log10(np.maximum(amin, mel))
    ref = 10.0 * np.log10(np.maximum(amin, mel.max(axis=(1, 2), keepdims=True)))
    db = db - ref
    db = np.maximum(db, db.max(axis=(1, 2), keepdims=True) - top_db)
    return np.ascontiguousarray(((db + 40) / 40).transpose(0, 2, 1), dtype=np.float32)


_cpu_only = False


def _run_batch(w: np.ndarray) -> np.ndarray:
    global _cpu_only
    for attempt in (0, 1):
        primary, p808 = _sessions(_cpu_only)
        try:
            raw = primary.run(None, {"input_1": w})[0]           # (b, 3) sig, bak, ovr raw
            p = p808.run(None, {"input_1": _melspec(w)})[0][:, 0]
            return np.stack([P_SIG(raw[:, 0]), P_BAK(raw[:, 1]), P_OVR(raw[:, 2]), p], axis=1)
        except Exception as e:  # noqa: BLE001 - e.g. CUDA out of memory on a shared GPU
            if _cpu_only or attempt:
                raise
            log.warning("DNSMOS on GPU failed (%s); falling back to CPU for this worker", str(e)[:200])
            _cpu_only = True
    raise RuntimeError("unreachable")


def score_windows(windows: np.ndarray, batch: int = 16) -> np.ndarray:
    """(B, WIN) float32 windows → (B, 4) [sig, bak, ovrl, p808]."""
    windows = np.asarray(windows, dtype=np.float32).reshape(-1, WIN)
    if windows.shape[0] == 0:
        return np.zeros((0, 4), dtype=np.float32)
    with _lock:
        res = [_run_batch(windows[a:a + batch]) for a in range(0, windows.shape[0], batch)]
    return np.concatenate(res).astype(np.float32)


def window_at(audio: np.ndarray, start_sample: int) -> np.ndarray:
    w = audio[start_sample:start_sample + WIN]
    if w.size < WIN:
        w = np.pad(w, (0, WIN - w.size))
    return w


def clip_windows(clip: np.ndarray) -> np.ndarray:
    """Official clip handling: repeat until >= 9.01 s, then 1 s hops over (at least one) full window."""
    x = np.asarray(clip, dtype=np.float32)
    if x.size == 0:
        return np.zeros((0, WIN), dtype=np.float32)
    while x.size < WIN:
        x = np.concatenate([x, x])
    num_hops = int(np.floor(x.size / SR) - INPUT_LENGTH) + 1
    return np.stack([x[i * SR:i * SR + WIN] for i in range(max(1, num_hops)) if i * SR + WIN <= x.size])


def score_clip(clip: np.ndarray) -> np.ndarray | None:
    """Mean [sig, bak, ovrl, p808] of one clip (e.g. a segment), or None when empty."""
    w = clip_windows(clip)
    if w.shape[0] == 0:
        return None
    return score_windows(w).mean(axis=0)


def score_clips(clips: list[np.ndarray], max_windows_per_clip: int = 12) -> list[np.ndarray | None]:
    """Batch many clips (segments) through the models at once; windows per clip are capped (evenly spaced)."""
    all_w, owners = [], []
    for i, c in enumerate(clips):
        w = clip_windows(c)
        if w.shape[0] > max_windows_per_clip:
            w = w[np.linspace(0, w.shape[0] - 1, max_windows_per_clip).astype(int)]
        all_w.append(w)
        owners.extend([i] * w.shape[0])
    if not owners:
        return [None] * len(clips)
    scores = score_windows(np.concatenate([w for w in all_w if w.shape[0]]))
    owners_a = np.asarray(owners)
    return [scores[owners_a == i].mean(axis=0) if np.any(owners_a == i) else None for i in range(len(clips))]
