"""CUDA out-of-memory handling for torch steps on a shared GPU (embed, diarize, align).

Policy (OomFallback / run_with_oom_fallback): run on voti.devices.torch_device(); on torch.cuda.OutOfMemoryError
(or an equivalent CUDA/cuBLAS/cuDNN allocation failure) free the cache and retry once on the GPU, then fall back
to the CPU for the rest of this job; if the CPU run also runs out of memory, raise a retryable
JobError("out_of_memory") so the queue retries the job later (when the other GPU users may be done).
DNSMOS (onnxruntime) has its own GPU → CPU fallback in voti.pipeline.dnsmos; Brouhaha/Vox-Profile run in legacy-ml.
"""
from __future__ import annotations

import gc
import logging
from collections.abc import Callable
from typing import TypeVar

from ..queue import JobError

log = logging.getLogger("voti.pipeline.gpu")
T = TypeVar("T")

_OOM_MARKERS = ("cuda out of memory", "out of memory", "cublas_status_alloc_failed",
                "cudnn_status_not_enough_workspace", "cudnn_status_alloc_failed", "cuda error: out of memory",
                "can't allocate memory")


def is_cuda_oom(e: BaseException) -> bool:
    """torch.cuda.OutOfMemoryError (anywhere in the cause chain) or a CUDA allocation failure message."""
    seen = 0
    cur: BaseException | None = e
    while cur is not None and seen < 8:
        seen += 1
        try:
            import torch
            if isinstance(cur, torch.cuda.OutOfMemoryError):
                return True
        except ImportError:  # pragma: no cover
            pass
        if isinstance(cur, RuntimeError) and any(m in str(cur).lower() for m in _OOM_MARKERS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def free_cuda() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


class OomFallback:
    """Keeps the device of one job: starts on the configured device and moves to the CPU after repeated OOM."""

    def __init__(self, what: str, device: str | None = None, gpu_retries: int = 1):
        from ..devices import torch_device
        self.what = what
        self.device = device or torch_device()
        self.gpu_retries = gpu_retries
        self.oom_events: list[str] = []

    @property
    def fell_back(self) -> bool:
        return self.device == "cpu" and bool(self.oom_events)

    def call(self, fn: Callable[[str], T]) -> T:
        retried = 0
        while True:
            try:
                return fn(self.device)
            except Exception as e:  # noqa: BLE001
                cuda_oom = is_cuda_oom(e)
                if not cuda_oom and not (self.device == "cpu" and isinstance(e, MemoryError)):
                    raise
                free_cuda()
                self.oom_events.append(self.device)
                if self.device != "cpu" and retried < self.gpu_retries:
                    retried += 1
                    log.warning("%s: CUDA out of memory on %s; emptied the cache, retrying on the GPU",
                                self.what, self.device)
                    continue
                if self.device != "cpu":
                    log.warning("%s: CUDA out of memory again on %s; falling back to CPU for this job",
                                self.what, self.device)
                    self.device = "cpu"
                    continue
                raise JobError("out_of_memory", f"{self.what}：記憶體不足（GPU 與 CPU 皆失敗），稍後自動重試",
                               retryable=True) from e


def run_with_oom_fallback(fn: Callable[[str], T], what: str) -> T:
    """fn(device) with the OOM policy above."""
    return OomFallback(what).call(fn)
