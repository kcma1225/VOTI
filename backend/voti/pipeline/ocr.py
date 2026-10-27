"""`media.ocr`: burned-in (hard) subtitles → an OCR caption track paired with speech.

For videos without a usable CC track (or when a user opts in) `media.download` also stores a 720p video-only stream
as asset `ocr_video` (media/<id>/ocr/video.<ext>). This job (ml queue, between media.diarize and media.segment):

  1. discovery — full frames every `discovery_step_sec` (≤ `max_discovery_frames`) → OCR boxes → subtitle styles and
     tracks from temporal evidence (voti.pipeline.hardsub; titles, logos, tickers, slides, UI text are rejected);
  2. tracking — the tracks' bands (merged into crop regions) at `fps`; a region whose content did not change since
     its last OCR reuses that result;
  3. events — per track, consecutive readings → events with a character-level consensus text;
  4. pairing — events × Brouhaha speech regions (frames_quality) → `paired` or a rejection reason;
  5. storage — Track(kind="ocr", format="json") whose asset is media/<id>/ocr/events.json (every event with bbox,
     style, track, confidence, frames, agreement, pairing), one Cue per event (raw_text = consensus, text = cleaned
     + Simplified → Traditional per the `text` setting; flags "ocr", "ocr_paired" | "ocr_rejected",
     "s2t_converted"), optional crops media/<id>/ocr/crops/<idx>.webp, Media.metadata["ocr"] summary;
  6. the video asset and object are deleted (only results + crops stay).

Only cues flagged `ocr_paired` become segments (pipeline.segment, text_source "ocr_caption"); they then go through
align + validate like caption segments (validate is stricter for ocr_caption: no alignment score → unknown).

OCR engine: PP-OCRv5 (Apache-2.0, PaddleOCR) ONNX models run by RapidOCR's pre/post-processing on onnxruntime
(CUDA when available, see voti.devices; CPU otherwise). Weights are downloaded once from ModelScope (RapidOCR's
model mirror, sha256-pinned) into MODEL_CACHE/ocr. The CUDA EP re-plans every convolution whenever the input
shape changes, so the engine keeps one session per (bucketed) input shape.

Payload: {} (pipeline) | {"rerun": true} (POST /media/{id}/ocr: chains media.segment {"reprocess": true}).
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import delete, func, select

from .. import storage
from ..config import get_config
from ..db import session_scope
from ..models import Asset, Cue, Media, Segment, Track, utcnow
from ..queue import JobError
from . import hardsub as H
from .common import get_setting, hysteresis_regions, load_frames, work_dir

log = logging.getLogger("voti.pipeline.ocr")

VIDEO_ASSET = "ocr_video"
EVENTS_ASSET = "ocr_events"
MAX_SIDE = 1280                     # frames are decoded so that the longer side is at most this
_MS = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5"
MODELS = {  # (file, url, sha256) — PP-OCRv5 ONNX as published by RapidOCR 3.10.0 (Apache-2.0)
    "server": {"det": ("ch_PP-OCRv5_det_server.onnx", f"{_MS}/det/ch_PP-OCRv5_det_server.onnx",
                       "0f8846b1d4bba223a2a2f9d9b44022fbc22cc019051a602b41a7fda9667e4cad"),
               "rec": ("ch_PP-OCRv5_rec_server.onnx", f"{_MS}/rec/ch_PP-OCRv5_rec_server.onnx",
                       "e09385400eaaaef34ceff54aeb7c4f0f1fe014c27fa8b9905d4709b65746562a")},
    "mobile": {"det": ("ch_PP-OCRv5_det_mobile.onnx", f"{_MS}/det/ch_PP-OCRv5_det_mobile.onnx",
                       "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae"),
               "rec": ("ch_PP-OCRv5_rec_mobile.onnx", f"{_MS}/rec/ch_PP-OCRv5_rec_mobile.onnx",
                       "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5")},
}

# settings key "ocr" (docs/ARCHITECTURE.md "Runtime settings"); mirrored in voti.pipeline.common.DEFAULT_SETTINGS
DEFAULTS: dict = {
    "enabled": True,                 # media.download may fetch video for OCR at all
    "auto_when_no_captions": True,   # no usable CC → download video + OCR instead of skipping the media
    "fps": 5.0,                      # tracking frame rate (subtitle timing resolution 1/fps)
    "discovery_step_sec": 2.0,       # full-frame sampling for style discovery
    "max_discovery_frames": 900,     # long videos: the discovery step grows to stay under this
    "model": "server",               # server | mobile (PP-OCRv5) on a GPU
    "cpu_model": "mobile",           # … on the CPU (server recognition is ~25x slower there: ~3 s per band crop)
    "keep_crops": True,              # store one small webp crop per event (review UI)
    "min_event_conf": 0.85,          # mean OCR confidence below this → rejected (low_ocr_confidence)
}
_lock = threading.Lock()


def settings_value(raw: dict | None) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (raw or {}).items() if k in DEFAULTS and v is not None})
    for k in ("model", "cpu_model"):
        if out[k] not in MODELS:
            out[k] = DEFAULTS[k]
    return out


# ───────────────────────────── models / engine ─────────────────────────────

def model_dir() -> Path:
    return Path(get_config().model_cache) / "ocr"


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@lru_cache(maxsize=4)
def ensure_models(kind: str = "server") -> dict[str, Path]:
    """Download (once, sha256-verified) the det/rec ONNX files into MODEL_CACHE/ocr."""
    import urllib.request
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for part, (name, url, digest) in MODELS[kind].items():
        path = d / name
        if not path.exists() or _sha256(path) != digest:
            tmp = path.with_suffix(".part")
            log.info("downloading OCR model %s", url)
            try:
                with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:  # noqa: S310 (pinned https)
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
            except OSError as e:
                tmp.unlink(missing_ok=True)
                raise JobError("ocr_model_unavailable", f"OCR 模型下載失敗（稍後重試）：{e}", retryable=True) from e
            got = _sha256(tmp)
            if got != digest:
                tmp.unlink(missing_ok=True)
                raise JobError("ocr_model_unavailable", f"OCR 模型 {name} sha256 不符：{got}", retryable=True)
            tmp.replace(path)
        out[part] = path
    return out


class ShapeSessions:
    """onnxruntime sessions of one model, one per input shape on CUDA (the CUDA EP re-runs its cuDNN algorithm
    search on every input-shape change, 0.1–0.5 s each; a session per shape keeps each session on one shape).
    On the CPU a single session serves every shape."""

    def __init__(self, path: Path, device: str, max_sessions: int = 8):
        self.path, self.device, self.max = str(path), device, max_sessions
        self.sessions: dict = {}

    def _new(self):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = 8
        so.log_severity_level = 3
        if self.device.startswith("cuda"):
            dev_id = int(self.device.split(":", 1)[1]) if ":" in self.device else 0
            prov = [("CUDAExecutionProvider", {"device_id": dev_id, "cudnn_conv_algo_search": "EXHAUSTIVE",
                                               "cudnn_conv_use_max_workspace": "0",
                                               "arena_extend_strategy": "kSameAsRequested"}), "CPUExecutionProvider"]
        else:
            prov = ["CPUExecutionProvider"]
        return ort.InferenceSession(self.path, so, providers=prov)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        key = tuple(x.shape) if self.device.startswith("cuda") else None
        sess = self.sessions.get(key)
        if sess is None:
            if len(self.sessions) >= self.max:
                self.sessions.pop(next(iter(self.sessions)))
            sess = self.sessions[key] = self._new()
        return sess.run(None, {sess.get_inputs()[0].name: x})[0]


class OcrEngine:
    """PP-OCRv5 det + rec (RapidOCR pre/post-processing). __call__(rgb, t, dx, dy) → [hardsub.Box] in frame
    coordinates. Boxes lower than `min_h` px or narrower than tall are dropped before recognition."""

    W_BUCKET = 640        # recogniser input widths are rounded up to multiples of this (≤ 1280)
    BATCHES = (1, 8)      # … and batches padded to one of these sizes

    def __init__(self, kind: str = "server", device: str = "cpu", min_h: float = 12.0):
        import onnxruntime as ort
        if device.startswith("cuda") and hasattr(ort, "preload_dlls"):
            try:  # CUDA / cuDNN libraries that ship with the torch wheels
                ort.preload_dlls()
            except Exception as e:  # noqa: BLE001
                log.warning("onnxruntime preload_dlls failed: %s", e)
        from rapidocr import ModelType, OCRVersion, RapidOCR
        paths = ensure_models(kind)
        mt = ModelType.SERVER if kind == "server" else ModelType.MOBILE
        params = {"Global.log_level": "error", "Global.use_cls": False,
                  "Global.model_root_dir": str(model_dir()),
                  "Det.ocr_version": OCRVersion.PPOCRV5, "Rec.ocr_version": OCRVersion.PPOCRV5,
                  "Det.model_type": mt, "Rec.model_type": mt,
                  "Det.model_path": str(paths["det"]), "Rec.model_path": str(paths["rec"]),
                  "Det.limit_type": "max", "Det.limit_side_len": MAX_SIDE,
                  "EngineConfig.onnxruntime.use_cuda": False}  # sessions are replaced below
        self.rapid = RapidOCR(params=params)
        self.det = self.rapid._load_det_model()
        self.rec = self.rapid._load_rec_model()
        self.det.session = ShapeSessions(paths["det"], device)
        self.rec.session = ShapeSessions(paths["rec"], device)
        self.device, self.kind, self.min_h = device, kind, min_h
        self.n_calls = 0
        self.ocr_sec = 0.0

    def detect(self, bgr: np.ndarray) -> list[np.ndarray]:
        pad = 0
        if bgr.shape[0] < 64:  # DB needs some context around a thin band
            pad = (64 - bgr.shape[0]) // 2 + 1
            bgr = np.pad(bgr, ((pad, pad), (0, 0), (0, 0)))
        r = self.det(bgr)
        if r.boxes is None:
            return []
        out = []
        for q in r.boxes:
            q = np.asarray(q, dtype=np.float32)
            q[:, 1] -= pad
            bh = q[:, 1].max() - q[:, 1].min()
            bw = q[:, 0].max() - q[:, 0].min()
            if bh >= self.min_h and bw >= 0.8 * bh:
                out.append(q)
        return out

    def recognize(self, bgr: np.ndarray, quads: list[np.ndarray]) -> list[tuple[str, float]]:
        from rapidocr.utils.process_img import get_rotate_crop_image
        if not quads:
            return []
        crops = [get_rotate_crop_image(bgr, q.copy()) for q in quads]
        _, ih, _ = self.rec.rec_image_shape
        ratios = [c.shape[1] / max(1, c.shape[0]) for c in crops]
        order = np.argsort(ratios)
        out: list = [("", 0.0)] * len(crops)
        for i in range(0, len(order), 8):
            idx = order[i:i + 8]
            mx = max(ratios[k] for k in idx)
            width = int(min(2 * self.W_BUCKET, max(self.W_BUCKET, -(-int(ih * mx) // self.W_BUCKET) * self.W_BUCKET)))
            batch = [self.rec.resize_norm_img(crops[k], width / ih)[None] for k in idx]
            nb = next(b for b in self.BATCHES if b >= len(batch))
            batch += [np.zeros_like(batch[0])] * (nb - len(batch))
            preds = self.rec.session(np.concatenate(batch).astype(np.float32))
            lines, _ = self.rec.postprocess_op(preds, False,
                                               wh_ratio_list=[ratios[k] for k in idx] + [1.0] * (nb - len(idx)),
                                               max_wh_ratio=width / ih)
            for k, (txt, sc) in zip(idx, lines[:len(idx)]):
                out[k] = (str(txt), float(sc))
        return out

    def __call__(self, rgb: np.ndarray, t: float, dx: float = 0.0, dy: float = 0.0,
                 min_conf: float = 0.3) -> list[H.Box]:
        t0 = time.monotonic()
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        quads = self.detect(bgr)
        res = self.recognize(bgr, quads)
        out = []
        for q, (txt, sc) in zip(quads, res):
            if not txt.strip() or sc < min_conf:
                continue
            x0, y0 = float(q[:, 0].min()), float(q[:, 1].min())
            x1, y1 = float(q[:, 0].max()), float(q[:, 1].max())
            fill, edge = H.glyph_colors(rgb[max(0, int(y0)):int(y1) + 1, max(0, int(x0)):int(x1) + 1])
            out.append(H.Box(t, txt, sc, x0 + dx, y0 + dy, x1 + dx, y1 + dy, fill, edge))
        self.n_calls += 1
        self.ocr_sec += time.monotonic() - t0
        return out


@lru_cache(maxsize=2)
def load_engine(kind: str, device: str) -> OcrEngine:
    try:
        return OcrEngine(kind, device)
    except ImportError as e:
        raise JobError("ocr_unavailable", f"此 worker 未安裝 OCR 套件（rapidocr / onnxruntime）：{e}",
                       blocked=True) from e


def release_engine() -> None:
    """Drop the cached engines (their onnxruntime sessions free the GPU memory)."""
    import gc
    getattr(load_engine, "cache_clear", lambda: None)()
    gc.collect()


# ───────────────────────────── video ─────────────────────────────

def probe_video(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    d = json.loads(out)
    st = (d.get("streams") or [{}])[0]
    if not st.get("width"):
        raise JobError("ocr_video_invalid", "影片檔沒有可讀的影像串流", retryable=False)
    return {"width": int(st["width"]), "height": int(st["height"]),
            "duration": float((d.get("format") or {}).get("duration") or 0)}


def frame_size(w: int, h: int) -> tuple[int, int]:
    """Decode size: longer side ≤ MAX_SIDE, even dimensions."""
    s = min(1.0, MAX_SIDE / max(w, h))
    return int(round(w * s / 2)) * 2, int(round(h * s / 2)) * 2


def frames(path: Path, fps: float, size: tuple[int, int], crop: tuple[int, int] | None = None,
           times: set[int] | None = None) -> Iterator[tuple[float, np.ndarray]]:
    """(t, RGB frame or full-width crop rows [y0, y1)) at `fps` (frame k ↔ t = k / fps)."""
    W, Hh = size
    vf = f"fps={fps}:round=near,scale={W}:{Hh}"
    h = Hh
    if crop:
        vf += f",crop={W}:{crop[1] - crop[0]}:0:{crop[0]}"
        h = crop[1] - crop[0]
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-threads", "4", "-i", str(path), "-an", "-vf", vf,
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=W * h * 3 * 4)
    size_b = W * h * 3
    k = 0
    try:
        while True:
            buf = p.stdout.read(size_b)
            if len(buf) < size_b:
                break
            yield k / fps, np.frombuffer(buf, np.uint8).reshape(h, W, 3)
            k += 1
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def _thumb(band: np.ndarray) -> np.ndarray:
    import cv2
    g = cv2.cvtColor(band, cv2.COLOR_RGB2GRAY)
    return cv2.resize(g, (max(1, g.shape[1] // 4), max(1, g.shape[0] // 4)), interpolation=cv2.INTER_AREA
                      ).astype(np.int16)


def unchanged(a: np.ndarray | None, b: np.ndarray, block: int = 8, thr: float = 6.0) -> bool:
    """Max mean-absolute difference over block×block tiles of two band thumbnails below thr (same content)."""
    if a is None or a.shape != b.shape:
        return False
    d = np.abs(a - b).astype(np.float32)
    h, w = d.shape
    if w < block:
        return float(d.mean()) < thr
    hh, ww = max(1, h // block), w // block
    if h >= block:
        tiles = d[:hh * block, :ww * block].reshape(hh, block, ww, block).mean(axis=(1, 3))
    else:
        tiles = d[:, :ww * block].reshape(h, ww, block).mean(axis=(0, 2))
    return float(tiles.max()) < thr


# ───────────────────────────── extraction (engine-agnostic) ─────────────────────────────

class VideoSource:
    """Frames of a local video file (ffmpeg). Tests substitute an object with the same interface."""

    def __init__(self, path: Path):
        info = probe_video(path)
        self.path = path
        self.size = frame_size(info["width"], info["height"])
        self.duration = info["duration"] or 0.0

    def frames(self, fps: float, crop: tuple[int, int] | None = None) -> Iterator[tuple[float, np.ndarray]]:
        return frames(self.path, fps, self.size, crop)


def extract(source, engine, regions_speech: list[tuple[float, float]], cfg: dict,  # noqa: ANN001
            progress: Callable[[float, str], None] = lambda f, p: None) -> dict:
    """Run discovery → tracking → events → pairing. source: VideoSource-like (size, duration, frames(fps, crop));
    engine(rgb, t, dx, dy) → [Box] in frame coordinates.
    → {"size", "styles", "groups", "bands", "events": [Event], "pairs": [dict], "stats"}"""
    cfg = {**DEFAULTS, **(cfg or {})}
    W, Hh = source.size
    dur = source.duration or 0.0
    step = float(cfg["discovery_step_sec"])
    if dur and dur / step > cfg["max_discovery_frames"]:
        step = dur / cfg["max_discovery_frames"]
    starts = np.array([a for a, _ in regions_speech]) if regions_speech else np.zeros(0)
    ends = np.array([b for _, b in regions_speech]) if regions_speech else np.zeros(0)

    def speech_at(t: float) -> bool | None:
        if not regions_speech:
            return None
        k = int(np.searchsorted(starts, t, side="right")) - 1
        return bool(k >= 0 and ends[k] >= t - 0.2) or bool(k + 1 < len(starts) and starts[k + 1] - t < 0.2)

    t0 = time.monotonic()
    boxes, times = [], []
    for t, fr in source.frames(1.0 / step):
        times.append(t)
        boxes += engine(fr, t)
        if dur:
            progress(0.05 + 0.3 * min(1.0, t / dur), "discover")
    t_disc = time.monotonic() - t0
    styles = H.cluster_boxes(boxes, Hh)
    H.score_styles(styles, times, step, W, Hh, speech_at)
    groups = H.merge_lines(styles)
    bands = [H.band_of(styles, g, Hh) for g in groups]
    regions = H.merge_bands(bands)
    stats = {"frame_size": [W, Hh], "duration": round(dur, 2), "discovery_step_sec": round(step, 3),
             "discovery_frames": len(times), "discovery_boxes": len(boxes), "styles_found": len(styles),
             "styles_accepted": sum(s.accepted for s in styles), "tracks": len(groups),
             "discovery_sec": round(t_disc, 1)}
    events: list[H.Event] = []
    t1 = time.monotonic()
    ocr_frames = reused = 0
    if groups:
        fps = float(cfg["fps"])
        y0, y1 = min(a for a, _ in regions), max(b for _, b in regions)
        ftimes: list[float] = []
        per_region: list[list[list[H.Box]]] = [[] for _ in regions]
        last = [None] * len(regions)
        last_res: list[list[H.Box]] = [[] for _ in regions]
        for t, fr in source.frames(fps, crop=(y0, y1)):
            ftimes.append(t)
            for k, (a, b) in enumerate(regions):
                band = fr[a - y0:b - y0]
                th = _thumb(band)
                if unchanged(last[k], th):
                    res = [H.Box(t, x.text, x.conf, x.x0, x.y0, x.x1, x.y1, x.fill, x.edge) for x in last_res[k]]
                    reused += 1
                else:
                    res = engine(band, t, 0, a)
                    last[k], last_res[k] = th, res
                    ocr_frames += 1
                per_region[k].append(res)
            if dur and len(ftimes) % 25 == 0:
                progress(0.35 + 0.55 * min(1.0, t / dur), "track")
        for gi, g in enumerate(groups):
            b0, b1 = bands[gi]
            obs = []
            for fi, t in enumerate(ftimes):
                bs = [x for k in range(len(regions)) for x in per_region[k][fi] if b0 <= x.cy <= b1]
                obs.append(H.frame_obs(t, bs, styles, g, Hh, W))
            evs = H.build_events(obs, ftimes, 1.0 / fps)
            for e in evs:
                e.track = gi
            events += evs
    events.sort(key=lambda e: e.start)
    pairs = H.pair_events(events, regions_speech, {"min_event_conf": cfg["min_event_conf"]})
    stats.update(tracking_frames=ocr_frames + reused, ocr_frames=ocr_frames, reused_frames=reused,
                 tracking_sec=round(time.monotonic() - t1, 1), events=len(events),
                 paired=sum(p["status"] == "paired" for p in pairs),
                 rejected=dict(Counter(p["reason"] for p in pairs if p["reason"])))
    return {"size": (W, Hh), "styles": styles, "groups": groups, "bands": bands, "events": events, "pairs": pairs,
            "stats": stats}


def grab_crops(source, events: list[H.Event], fps: float) -> dict[int, bytes]:  # noqa: ANN001
    """One webp crop per event (its representative frame, bbox + margin) from a single decoding pass."""
    import cv2
    want: dict[int, list[int]] = {}
    for i, e in enumerate(events):
        want.setdefault(int(round(e.best_t * fps)), []).append(i)
    if not want:
        return {}
    out: dict[int, bytes] = {}
    W, Hh = source.size
    last_k = max(want)
    for t, fr in source.frames(fps):
        k = int(round(t * fps))
        for i in want.get(k, []):
            x0, y0, x1, y1 = events[i].bbox
            h = y1 - y0
            box = (max(0, int(x0 - 0.4 * h)), max(0, int(y0 - 0.3 * h)), min(W, int(x1 + 0.4 * h)),
                   min(Hh, int(y1 + 0.3 * h)))
            crop = fr[box[1]:box[3], box[0]:box[2]]
            if crop.size:
                if crop.shape[0] > 96:  # keep crops small: ≤ 96 px high
                    s = 96 / crop.shape[0]
                    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), 96), interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".webp", np.ascontiguousarray(crop[:, :, ::-1]), [cv2.IMWRITE_WEBP_QUALITY, 75])
                if ok:
                    out[i] = buf.tobytes()
        if k >= last_k:
            break
    return out


# ───────────────────────────── job ─────────────────────────────

def events_key(media_id) -> str:  # noqa: ANN001
    return storage.media_key(media_id, "ocr/events.json")


def crop_key(media_id, idx: int) -> str:  # noqa: ANN001
    return storage.media_key(media_id, f"ocr/crops/{idx}.webp")


def video_asset(s, media_id: uuid.UUID) -> Asset | None:  # noqa: ANN001
    return s.scalar(select(Asset).where(Asset.media_id == media_id, Asset.kind == VIDEO_ASSET)
                    .order_by(Asset.created_at.desc()))


def needed(s, media_id: uuid.UUID) -> bool:  # noqa: ANN001
    """Does the pipeline have to run media.ocr for this media (a video for OCR is waiting)?"""
    return video_asset(s, media_id) is not None


def _speech_regions(frames_q) -> list[tuple[float, float]]:  # noqa: ANN001
    if frames_q is None:
        return []
    return hysteresis_regions(frames_q.brouhaha_vad, frames_q.hop_sec, min_speech=0.2, min_silence=0.25)


def _event_rows(media_id: uuid.UUID, ex: dict, crops: dict[int, bytes], text_cfg: dict) -> tuple[list[dict], dict]:
    """Events → stored rows (events.json) with cleaned + converted text; → (rows, s2t record)."""
    from .. import textconv
    raw = [e.text for e in ex["events"]]
    clean = [H.clean_text(t) for t in raw]
    d = textconv.decide("zh", [c for c, p in zip(clean, ex["pairs"]) if p["status"] == "paired"] or clean)
    convert = d["convert"] and text_cfg.get("convert_simplified", True)
    rows = []
    for i, (e, p) in enumerate(zip(ex["events"], ex["pairs"])):
        text = textconv.convert(clean[i], text_cfg["opencc_config"]) if convert else clean[i]
        rows.append({"idx": i, "start": round(e.start, 3), "end": round(e.end, 3), "raw_text": e.text, "text": text,
                     "s2t_converted": text != clean[i], "track": e.track, "style": e.style,
                     "bbox": [int(round(v)) for v in e.bbox], "lines": e.lines, "conf": round(e.conf, 4),
                     "n_frames": e.n_frames, "agreement": round(e.agreement, 3), "best_t": round(e.best_t, 3),
                     "status": p["status"], "reason": p["reason"], "speech_sec": p["speech_sec"],
                     "paired_start": p["start"], "paired_end": p["end"],
                     "crop_key": crop_key(media_id, i) if i in crops else None})
    s2t = {k: v for k, v in d.items() if k != "convert"}
    s2t.update(converted=bool(convert), config=text_cfg["opencc_config"], opencc=textconv.opencc_version(),
               cues_converted=sum(r["s2t_converted"] for r in rows))
    return rows, s2t


def _store(media_id: uuid.UUID, ex: dict, rows: list[dict], s2t: dict, cfg: dict, summary: dict) -> str | None:
    """Replace the media's OCR track + cues + events asset; → track id (None without events)."""
    payload = {"version": 1, "model": f"PP-OCRv5_{cfg['model']}", "frame_size": list(ex["size"]),
               "styles": [s.dict() for s in ex["styles"] if s.accepted],
               "tracks": [{"track": gi, "styles": g, "band": list(ex["bands"][gi])} for gi, g in enumerate(ex["groups"])],
               "events": rows, "stats": ex["stats"], "s2t": s2t}
    data = json.dumps(payload, ensure_ascii=False).encode()
    with session_scope() as s:
        m = s.get(Media, media_id)
        if m is None:
            raise JobError("not_found", "找不到媒體", retryable=False)
        for old in s.scalars(select(Track).where(Track.media_id == media_id, Track.kind == "ocr")):
            s.execute(delete(Cue).where(Cue.track_id == old.id))
            s.delete(old)
        s.flush()
        track_id = None
        if rows:
            asset = storage.store_bytes_asset(s, data, events_key(media_id), EVENTS_ASSET, media_id=media_id,
                                              content_type="application/json",
                                              params={"model": payload["model"], "events": len(rows), "s2t": s2t,
                                                      "stats": ex["stats"]})
            lang = "zh-TW" if any(H.han_count(r["text"]) for r in rows) else "und"
            tr = Track(media_id=media_id, language=lang, kind="ocr", format="json", asset_id=asset.id)
            s.add(tr)
            s.flush()
            track_id = str(tr.id)
            s.add_all([Cue(track_id=tr.id, idx=r["idx"], start_sec=r["start"], end_sec=r["end"],
                           raw_text=r["raw_text"], text=r["text"],
                           flags=["ocr", "ocr_paired" if r["status"] == "paired" else "ocr_rejected"]
                           + (["s2t_converted"] if r["s2t_converted"] else []))
                       for r in rows])
        m.metadata_ = {**(m.metadata_ or {}), "ocr": {**summary, "track_id": track_id}}
    return track_id


def _delete_video(media_id: uuid.UUID) -> bool:
    with session_scope() as s:
        a = video_asset(s, media_id)
        if a is None:
            return False
        key = a.key
        for x in s.scalars(select(Asset).where(Asset.media_id == media_id, Asset.kind == VIDEO_ASSET)):
            s.delete(x)
    try:
        storage.delete(key)
    except Exception:  # noqa: BLE001
        log.warning("could not delete OCR video %s", key)
    return True


def _device() -> str:
    from ..devices import torch_device
    try:
        import onnxruntime as ort
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            return "cpu"
    except ImportError:
        return "cpu"
    return torch_device()


def run(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None, engine=None) -> dict:
    """The media.ocr job. `engine` (tests) replaces the PP-OCR engine: callable(rgb, t, dx, dy) → [Box]."""
    from .. import textconv
    t0 = time.monotonic()
    with session_scope() as s:
        if s.get(Media, media_id) is None:
            raise JobError("not_found", "找不到媒體", retryable=False)
        cfg = settings_value(get_setting(s, "ocr"))
        text_cfg = textconv.text_settings(s)
        asset = video_asset(s, media_id)
        if asset is None:
            raise JobError("ocr_video_missing", "沒有可供 OCR 的影片（請重新下載影片）", retryable=False)
        key, ext = asset.key, asset.key.rsplit(".", 1)[-1]
        frames_q = load_frames(s, media_id)
    regions = _speech_regions(frames_q)
    device = "test"
    with work_dir("ocr") as wd:
        video = storage.get_file(key, wd / f"video.{ext}")
        progress(0.02, "load_model")
        if engine is None:
            device = _device()
            try:
                engine = load_engine(cfg["model"] if device != "cpu" else cfg["cpu_model"], device)
            except JobError:
                raise
            except Exception as e:  # noqa: BLE001 - GPU session problems: run on the CPU
                if device == "cpu":
                    raise
                log.warning("OCR engine on %s failed (%s); using CPU", device, e)
                device = "cpu"
                engine = load_engine(cfg["cpu_model"], "cpu")
            cfg["model"] = getattr(engine, "kind", cfg["model"])
        source = VideoSource(video)
        try:
            with _lock:
                ex = extract(source, engine, regions, cfg, progress)
        except JobError:
            raise
        except Exception as e:  # noqa: BLE001
            if device.startswith("cuda") and any(k in str(e).lower() for k in ("memory", "cuda", "cudnn")):
                raise JobError("out_of_memory", f"OCR：GPU 記憶體不足或 CUDA 錯誤，稍後自動重試（{str(e)[:200]}）",
                               retryable=True) from e
            raise
        finally:
            if device.startswith("cuda"):
                engine = None     # noqa: F841 - drop the last reference before collecting
                release_engine()  # ~4.5 GB of per-shape CUDA sessions: give the shared GPU back between jobs
        progress(0.92, "crops")
        crops = grab_crops(source, ex["events"], float(cfg["fps"])) if cfg["keep_crops"] and ex["events"] else {}
    rows, s2t = _event_rows(media_id, ex, crops, text_cfg)
    for i, data in crops.items():
        storage.put_bytes(data, crop_key(media_id, i), "image/webp")
    st = ex["stats"]
    summary = {"status": "done" if rows else "no_subtitles", "at": utcnow().isoformat(),
               "model": f"PP-OCRv5_{cfg['model']}", "device": device, "fps": cfg["fps"],
               "styles": [{"id": x.id, "cy": round(x.cy, 1), "h": round(x.h, 1), "fill": [round(c) for c in x.fill],
                           "edge": [round(c) for c in x.edge]} for x in ex["styles"] if x.accepted],
               "tracks": st["tracks"], "events": st["events"], "paired": st["paired"], "rejected": st["rejected"],
               "paired_sec": round(sum((r["paired_end"] or 0) - (r["paired_start"] or 0) for r in rows
                                       if r["status"] == "paired"), 2),
               "crops": len(crops), "text_converted": s2t["converted"], "video_deleted": True,
               "elapsed_sec": round(time.monotonic() - t0, 1), "stats": st,
               "speech_source": "brouhaha" if regions else None}
    track_id = _store(media_id, ex, rows, s2t, cfg, summary)
    _delete_video(media_id)
    progress(1.0, "done")
    return {"track_id": track_id, **{k: summary[k] for k in ("status", "events", "paired", "rejected", "tracks",
                                                              "paired_sec", "crops", "device", "elapsed_sec")}}


def mark_failed(media_id: uuid.UUID, code: str, message: str) -> None:
    """Media.metadata.ocr.status = failed (the video is kept for a retry)."""
    with session_scope() as s:
        m = s.get(Media, media_id)
        if m is not None:
            prev = dict((m.metadata_ or {}).get("ocr") or {})
            prev.update(status="failed", error_code=code, error=message[:500], at=utcnow().isoformat())
            m.metadata_ = {**(m.metadata_ or {}), "ocr": prev}


def segments_using(s, media_id: uuid.UUID) -> int:  # noqa: ANN001
    return s.scalar(select(func.count()).select_from(Segment).where(
        Segment.media_id == media_id, Segment.active.is_(True), Segment.text_source == "ocr_caption")) or 0
