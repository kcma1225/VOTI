"""legacy-ml HTTP service (internal, port 8600). Contract: docs/ARCHITECTURE.md "legacy-ml HTTP contract".

POST /brouhaha    {"key", "bucket"?}                          → per-frame VAD / SNR / C50 (base64 float32 LE)
POST /voxprofile  {"key", "spans": [[s, e], ...], "backbone", "bucket"?} → per-span traits (null for < 1 s)
GET  /healthz                                                  → {"ok", "device", "loaded", ...}

Models load lazily on first use (downloads go to HF_HOME=/models/hf) and stay resident. One inference runs at
a time (GPU lock); /healthz never waits for it.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import audio
from .brouhaha_runner import Brouhaha
from .voxprofile_runner import BACKBONES, VoxProfile

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("legacy_ml")

MAX_SPANS = int(os.environ.get("VOXPROFILE_MAX_SPANS", "512"))


def _device() -> torch.device:
    want = os.environ.get("DEVICE", "auto")
    if want == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(want)


DEVICE = _device()
if DEVICE.type == "cuda" and os.environ.get("ALLOW_TF32", "1") == "1":
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
if DEVICE.type == "cpu" and os.environ.get("TORCH_THREADS"):
    torch.set_num_threads(int(os.environ["TORCH_THREADS"]))

_gpu_lock = threading.Lock()
_load_lock = threading.Lock()
_brouhaha: Brouhaha | None = None
_vox: VoxProfile | None = None
_busy: dict = {}

app = FastAPI(title="voti legacy-ml", docs_url=None, redoc_url=None)


def brouhaha_model() -> Brouhaha:
    global _brouhaha
    with _load_lock:
        if _brouhaha is None:
            _brouhaha = Brouhaha(DEVICE, int(os.environ.get("BROUHAHA_BATCH", "32")))
        return _brouhaha


def vox_model() -> VoxProfile:
    global _vox
    with _load_lock:
        if _vox is None:
            _vox = VoxProfile(DEVICE, int(os.environ.get("VOXPROFILE_BATCH", "8" if DEVICE.type == "cuda" else "2")))
        return _vox


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode()


def _fetch(key: str, bucket: str | None) -> audio.LocalAudio:
    try:
        return audio.fetch(key, bucket)
    except audio.NotFound as e:
        raise HTTPException(404, str(e)) from e


class BrouhahaReq(BaseModel):
    key: str
    bucket: str | None = None


class VoxReq(BaseModel):
    key: str
    spans: list[tuple[float, float]] = Field(default_factory=list)
    backbone: str = "whisper-large-v3"
    bucket: str | None = None


@app.get("/healthz")
def healthz() -> dict:
    loaded = []
    if _brouhaha is not None:
        loaded.append("brouhaha")
    if _vox is not None:
        loaded += _vox.loaded()
    out = {"ok": True, "device": DEVICE.type, "loaded": loaded, "backbones": list(BACKBONES), "busy": dict(_busy)}
    if DEVICE.type == "cuda":
        i = DEVICE.index or 0
        out["gpu"] = torch.cuda.get_device_name(i)
        out["gpu_mem_allocated_gb"] = round(torch.cuda.memory_allocated(i) / 2**30, 2)
    return out


@app.post("/brouhaha")
def brouhaha(req: BrouhahaReq) -> dict:
    t = time.time()
    la = _fetch(req.key, req.bucket)
    try:
        model = brouhaha_model()
    except Exception as e:  # noqa: BLE001
        log.exception("brouhaha load failed")
        raise HTTPException(503, f"brouhaha model unavailable: {e}") from e
    with _gpu_lock:
        _busy.update(task="brouhaha", key=req.key, since=time.time())
        try:
            r = model.run(la)
        finally:
            _busy.clear()
    log.info("brouhaha %s: %.1fs audio, %d frames in %.1fs", req.key, r["duration_sec"], r["n_frames"], time.time() - t)
    return {k: v for k, v in r.items() if k not in ("vad", "snr", "c50")} | {
        "vad_b64": _b64(r["vad"]), "snr_b64": _b64(r["snr"]), "c50_b64": _b64(r["c50"]), "dtype": "float32-le"}


@app.post("/voxprofile")
def voxprofile(req: VoxReq) -> dict:
    t = time.time()
    if req.backbone not in BACKBONES:
        raise HTTPException(400, f"unknown backbone {req.backbone}; expected one of {list(BACKBONES)}")
    if len(req.spans) > MAX_SPANS:
        raise HTTPException(400, f"too many spans ({len(req.spans)} > {MAX_SPANS}); send smaller batches")
    for a, b in req.spans:
        if not (0 <= a <= b):
            raise HTTPException(400, f"bad span [{a}, {b}]")
    la = _fetch(req.key, req.bucket)
    ys = [audio.read_range(la, a, b) for a, b in req.spans]
    try:
        vox = vox_model()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"vox-profile unavailable: {e}") from e
    with _gpu_lock:
        _busy.update(task="voxprofile", key=req.key, spans=len(ys), since=time.time())
        try:
            try:
                vox.ensure(req.backbone)
            except Exception as e:  # noqa: BLE001
                log.exception("vox-profile load failed")
                raise HTTPException(503, f"vox-profile models unavailable: {e}") from e
            results = vox.run(ys, req.backbone)
        finally:
            _busy.clear()
    dt = time.time() - t
    log.info("voxprofile %s %s: %d spans (%.1fs audio) in %.1fs", req.backbone, req.key, len(ys),
             sum(len(y) for y in ys) / audio.SR, dt)
    return {"results": results, "backbone": req.backbone, "model_versions": vox.versions(req.backbone),
            "elapsed_sec": round(dt, 3)}
