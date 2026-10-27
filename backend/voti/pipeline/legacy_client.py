"""HTTP client for the `legacy-ml` inference service (Brouhaha + Vox-Profile).

See docs/ARCHITECTURE.md "legacy-ml HTTP contract". The service lives in its own image
(services/legacy-ml) because both upstream projects pin old torch/pyannote stacks.

    brouhaha(key)                     -> {"hop_sec", "vad", "snr", "c50", "model"}  (np.float32 arrays)
    voxprofile(key, spans, backbone)  -> {"results": [dict | None, ...], "model_versions": {...}, "backbone": str}
    health()                          -> {"ok": True, "device": "cuda|cpu", "loaded": [...]}

Unreachable service → voti.queue.JobError("legacy_ml_unavailable", ..., blocked=True) so the media is
parked as blocked (retryable once the service is back) instead of failed.
"""
from __future__ import annotations

import base64
import logging
import time

import httpx
import numpy as np

from ..config import get_config
from ..queue import JobError

log = logging.getLogger("voti.legacy_client")

BACKBONES = ("whisper-large-v3", "wavlm-large")
CONNECT_RETRIES = 4
RETRY_BACKOFF_SEC = 3.0
# Brouhaha on a 2 h file on CPU can take a long time; Vox-Profile batches are bounded by the caller.
BROUHAHA_TIMEOUT = httpx.Timeout(connect=10.0, read=4 * 3600.0, write=60.0, pool=60.0)
VOXPROFILE_TIMEOUT = httpx.Timeout(connect=10.0, read=1800.0, write=60.0, pool=60.0)
HEALTH_TIMEOUT = httpx.Timeout(10.0)


def _url(path: str) -> str:
    return get_config().legacy_ml_url.rstrip("/") + path


def _decode(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype="<f4").astype(np.float32)


def _request(method: str, path: str, *, json: dict | None, timeout: httpx.Timeout) -> dict:
    last: Exception | None = None
    for attempt in range(CONNECT_RETRIES):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.request(method, _url(path), json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError) as e:
            last = e
            log.warning("legacy-ml %s %s: %s (attempt %d/%d)", method, path, e, attempt + 1, CONNECT_RETRIES)
            if attempt + 1 < CONNECT_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
            continue
        except httpx.TimeoutException as e:
            raise JobError("legacy_ml_timeout", f"legacy-ml {path} 逾時：{e}", blocked=False) from e
        if r.status_code == 503:  # service up but model not loadable (e.g. download failed)
            last = RuntimeError(r.text[:500])
            if attempt + 1 < CONNECT_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            break
        if r.status_code == 404:
            raise JobError("legacy_ml_not_found", f"legacy-ml {path}: {_detail(r)}", blocked=False, retryable=False)
        if r.status_code >= 400:
            raise JobError("legacy_ml_error", f"legacy-ml {path} HTTP {r.status_code}: {_detail(r)}")
        return r.json()
    raise JobError("legacy_ml_unavailable", f"無法連線 legacy-ml 服務（{_url(path)}）：{last}", blocked=True)


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail", r.text))[:1000]
    except ValueError:
        return r.text[:1000]


def brouhaha(key: str) -> dict:
    """Run Brouhaha over a whole 16 kHz mono wav stored at S3 `key`.

    Returns {"hop_sec": float, "vad": f32[N], "snr": f32[N], "c50": f32[N], "model": str,
             "duration_sec": float}. Frame i covers time i*hop_sec (frame start).
    """
    d = _request("POST", "/brouhaha", json={"key": key}, timeout=BROUHAHA_TIMEOUT)
    out = {"hop_sec": float(d["hop_sec"]), "model": d.get("model", "brouhaha"),
           "duration_sec": float(d.get("duration_sec", 0.0))}
    for name in ("vad", "snr", "c50"):
        out[name] = _decode(d[f"{name}_b64"]) if f"{name}_b64" in d else np.asarray(d[name], dtype=np.float32)
    return out


def voxprofile(key: str, spans: list[tuple[float, float]], backbone: str = "whisper-large-v3") -> dict:
    """Vox-Profile traits for each (start, end) span (seconds) of the wav at S3 `key`.

    Returns {"results": [dict | None per span], "model_versions": {...}, "backbone": str}.
    A result is None when the span is shorter than 1 s. Each result dict:
      age: float (years), age_group: str (derived from age: teens|twenties|…|nineties),
      sex: "female"|"male", sex_prob: p(sex), sex_probs: {female, male}
      emotion / accent_broad / accent_narrow: {"label": str, "probs": {label: p}}
      voice_quality: {"label": top, "labels": [p > 0.5], "probs": {label: p}}   (multi-label sigmoid)
      speech_flow: {"label": "Fluent"|"Disfluent", "probs", "n_windows", "disfluent_window_ratio",
                    "disfluency_types": {type: p}, "disfluency_detected": [type]}
      emotion_dim: {"arousal": x, "valence": y, "dominance": z}  (0–1)
      duration_sec, n_chunks, low_confidence (span < 3 s)
    """
    if backbone not in BACKBONES:
        raise ValueError(f"unknown backbone {backbone}")
    body = {"key": key, "backbone": backbone, "spans": [[float(a), float(b)] for a, b in spans]}
    d = _request("POST", "/voxprofile", json=body, timeout=VOXPROFILE_TIMEOUT)
    if len(d.get("results", [])) != len(spans):
        raise JobError("legacy_ml_error", f"voxprofile 回傳 {len(d.get('results', []))} 筆，預期 {len(spans)}")
    return {"results": d["results"], "model_versions": d.get("model_versions", {}), "backbone": d.get("backbone", backbone)}


def health() -> dict:
    return _request("GET", "/healthz", json=None, timeout=HEALTH_TIMEOUT)
