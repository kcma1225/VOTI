"""Compute-device selection for torch models (config.device: auto | cpu | cuda | cuda:N)."""
from __future__ import annotations

import logging
from functools import lru_cache

from .config import get_config

log = logging.getLogger("voti.devices")


@lru_cache
def torch_device() -> str:
    """'cuda' (or 'cuda:N') when requested/available, else 'cpu'. Never fails on CPU-only machines."""
    want = (get_config().device or "auto").strip().lower()
    if want == "cpu":
        return "cpu"
    try:
        import torch
    except ImportError:
        return "cpu"
    has_cuda = torch.cuda.is_available()
    if want == "auto":
        dev = "cuda:0" if has_cuda else "cpu"  # explicit index: speechbrain cannot parse a bare "cuda"
    elif want.startswith("cuda"):
        if not has_cuda:
            log.warning("DEVICE=%s requested but CUDA is not available; falling back to cpu", want)
            dev = "cpu"
        else:
            dev = want if ":" in want else "cuda:0"
    else:
        log.warning("unknown DEVICE=%s; using cpu", want)
        dev = "cpu"
    log.info("torch device: %s", dev)
    return dev


def onnx_providers() -> list:
    """onnxruntime execution providers: CUDA first when the GPU build is installed and torch uses cuda."""
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    avail = ort.get_available_providers()
    dev = torch_device()
    if dev.startswith("cuda") and "CUDAExecutionProvider" in avail:
        device_id = int(dev.split(":", 1)[1]) if ":" in dev else 0
        return [("CUDAExecutionProvider", {"device_id": device_id, "arena_extend_strategy": "kSameAsRequested"}),
                "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]
