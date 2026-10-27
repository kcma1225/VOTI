"""ffprobe / ffmpeg: probe the original download, normalize to 16 kHz mono PCM16, and measure
loudness (EBU R128), true peak, clipping and the *real* audio bandwidth of the original.

Real bandwidth: a long-term average spectrum (mean log power) over speech-ish frames of the ORIGINAL audio at
its native sample rate. The highest frequency still >= FLOOR_MARGIN_DB above the spectral floor measured just
below Nyquist is the content bandwidth. When that is well below Nyquist and the spectrum falls to the floor
through a steep (brick-wall) edge, the file was most likely upsampled from a lower-rate source
(e.g. 8/16 kHz audio published as 44.1/48 kHz) → upsampled_suspect.
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

log = logging.getLogger("voti.pipeline.audio")

CLIP_LEVEL = 0.99            # |x| >= this (full scale = 1.0) counts as clipped
FLOOR_MARGIN_DB = 10.0       # content = LTAS >= floor + margin
MIN_FLOOR_SEPARATION_DB = 20.0
EDGE_DROP_DB = 25.0          # brick-wall: >= this drop across ±(100 Hz + EDGE_REL_WIDTH·f)
EDGE_REL_WIDTH = 0.1
EDGE_MIN_HZ = 2500.0         # upsampled sources (8/11/16/22/32 kHz) have their edge above this
UPSAMPLED_RATIO = 0.7        # bandwidth < 0.7 * Nyquist (+ sharp edge) → upsampled_suspect


class AudioError(RuntimeError):
    pass


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise AudioError(f"缺少 {tool}；請安裝 FFmpeg")


def ffmpeg_version() -> str:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10).stdout
        return out.splitlines()[0] if out else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ───────────────────────────── probe ─────────────────────────────

def probe(path: Path) -> dict:
    """Container/stream facts of the first audio stream."""
    require_tools()
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise AudioError(f"無法解碼音訊：{r.stderr.strip()[:400]}")
    data = json.loads(r.stdout or "{}")
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise AudioError("來源不含音訊軌")
    st, fmt = streams[0], data.get("format", {})
    duration = _float(st.get("duration")) or _float(fmt.get("duration"))
    if not duration or not math.isfinite(duration) or duration <= 0:
        raise AudioError("來源音訊長度無效")
    bitrate = _int(st.get("bit_rate")) or _int(fmt.get("bit_rate"))
    format_name = fmt.get("format_name", "") or ""
    ext = path.suffix.lower().lstrip(".")
    names = format_name.split(",")
    container = ext if ext and ext in names else (names[0] if names and names[0] else ext or None)
    return {"duration_sec": float(duration), "sample_rate": _int(st.get("sample_rate")),
            "channels": _int(st.get("channels")), "codec": (st.get("codec_name") or "")[:32] or None,
            "bitrate": bitrate, "container": (container or "")[:32] or None, "format_name": format_name}


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ───────────────────────────── normalize + loudness ─────────────────────────────

def _run_ffmpeg(args: list[str], duration: float, progress: Callable[[float], None] | None, timeout: int,
                failure: str) -> str:
    """Run ffmpeg feeding progress(fraction) from -progress; return stderr (loglevel info)."""
    with tempfile.TemporaryFile(mode="w+") as errors:
        proc = subprocess.Popen(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-y", "-nostats",
                                 "-progress", "pipe:1", *args],
                                stdout=subprocess.PIPE, stderr=errors, text=True)
        watchdog = threading.Timer(timeout, proc.kill)
        watchdog.start()
        try:
            for line in proc.stdout:
                if progress and line.startswith("out_time_us=") and duration > 0:
                    try:
                        progress(min(1.0, max(0.0, int(line.split("=", 1)[1]) / 1e6 / duration)))
                    except ValueError:
                        continue
            code = proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            watchdog.cancel()
        errors.seek(0)
        err = errors.read()
    if code:
        raise AudioError(f"{failure}：{err.strip()[-600:] or '逾時或被中止'}")
    return err


_SUMMARY_I = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.M)
_SUMMARY_PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.M)


def parse_ebur128(stderr: str) -> dict:
    tail = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else stderr
    out = {"lufs_integrated": None, "true_peak_db": None}
    m = _SUMMARY_I.search(tail)
    if m and m.group(1) != "-inf":
        out["lufs_integrated"] = float(m.group(1))
    m = _SUMMARY_PEAK.search(tail)
    if m and m.group(1) != "-inf":
        out["true_peak_db"] = float(m.group(1))
    return out


def normalize(original: Path, target: Path, duration: float = 0.0,
              progress: Callable[[float], None] | None = None, timeout: int = 4 * 3600) -> dict:
    """original → 16 kHz mono PCM16 wav (full timeline, no level change). EBU R128 integrated loudness and
    true peak are measured on the original stream in the same pass (ebur128 filter before resampling)."""
    require_tools()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp.wav")
    args = ["-i", str(original), "-map", "0:a:0", "-vn", "-af", "ebur128=peak=true:framelog=verbose",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(tmp)]
    try:
        err = _run_ffmpeg(args, duration, progress, timeout, "音訊標準化失敗")
        import soundfile as sf
        info = sf.info(str(tmp))
        if info.samplerate != 16000 or info.channels != 1 or info.subtype != "PCM_16":
            raise AudioError("音訊標準化格式驗證失敗")
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return {"normalized_duration_sec": info.frames / 16000, "ffmpeg_args": args[2:-1], **parse_ebur128(err)}


def loudness(path: Path) -> dict:
    """EBU R128 integrated loudness + true peak of any file (separate pass)."""
    err = _run_ffmpeg(["-i", str(path), "-map", "0:a:0", "-vn", "-af", "ebur128=peak=true:framelog=verbose",
                       "-f", "null", "-"], 0, None, 4 * 3600, "響度量測失敗")
    return parse_ebur128(err)


# ───────────────────────────── spectrum / bandwidth / clipping ─────────────────────────────

def _nfft_for(sr: int) -> int:
    return 1 << int(math.ceil(math.log2(max(256, sr * 0.04))))


class LtasAccumulator:
    """Streaming long-term average spectrum. Frames are bucketed by energy (1 dB buckets), so the final
    average can be restricted to the loud, speech-ish frames without keeping every frame in memory."""
    E_MIN, E_MAX = -130, 10

    def __init__(self, sr: int):
        self.sr, self.nfft = sr, _nfft_for(sr)
        self.window = np.hanning(self.nfft).astype(np.float32)
        self.wnorm = float(np.sum(self.window ** 2))
        nb = self.E_MAX - self.E_MIN + 1
        self.sum_db = np.zeros((nb, self.nfft // 2 + 1), dtype=np.float64)
        self.count = np.zeros(nb, dtype=np.int64)
        self.buf = np.zeros(0, dtype=np.float32)
        self.samples = 0
        self.clipped = 0

    def feed(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float32)
        self.samples += x.size
        self.clipped += int(np.count_nonzero(np.abs(x) >= CLIP_LEVEL))
        self.buf = np.concatenate([self.buf, x]) if self.buf.size else x
        n = self.buf.size // self.nfft
        if n == 0:
            return
        frames = self.buf[: n * self.nfft].reshape(n, self.nfft)
        self.buf = self.buf[n * self.nfft:].copy()
        spec = np.abs(np.fft.rfft(frames * self.window, axis=1)) ** 2 / self.wnorm
        energy_db = 10 * np.log10(np.mean(spec, axis=1) * 2 + 1e-14)
        b = np.clip(np.round(energy_db).astype(np.int64), self.E_MIN, self.E_MAX) - self.E_MIN
        np.add.at(self.sum_db, b, 10 * np.log10(spec + 1e-14))
        np.add.at(self.count, b, 1)

    def ltas(self, rel_db: float = 30.0, abs_min_db: float = -70.0) -> tuple[np.ndarray, np.ndarray, int]:
        """(freqs, mean log-power dB, frames used) over frames within rel_db of the 95th-percentile frame energy."""
        freqs = np.fft.rfftfreq(self.nfft, 1 / self.sr)
        total = int(self.count.sum())
        if total == 0:
            return freqs, np.full(freqs.size, -140.0), 0
        cum = np.cumsum(self.count)
        e95 = int(np.searchsorted(cum, 0.95 * total)) + self.E_MIN
        lo = max(e95 - rel_db, abs_min_db)
        sel = (np.arange(self.count.size) + self.E_MIN) >= lo
        if self.count[sel].sum() == 0:
            sel = self.count > 0
        n = int(self.count[sel].sum())
        return freqs, self.sum_db[sel].sum(axis=0) / n, n


def estimate_bandwidth(freqs: np.ndarray, ltas_db: np.ndarray, sr: int) -> dict:
    """Content bandwidth from an LTAS (dB per bin). Pure function (unit-tested).

    1. Brick-wall search: the lowest frequency f where the smoothed spectrum drops >= EDGE_DROP_DB across
       ±(100 Hz + 10 % of f) and everything above f stays >= EDGE_DROP_DB - 5 below the level just under f. Lossy
       re-encoding (YouTube opus/aac) adds a codec-noise plateau above an upsampled source's true edge, so
       the lowest such edge — not the last frequency above the digital floor — is the real bandwidth.
    2. Otherwise: highest frequency >= FLOOR_MARGIN_DB above the floor measured just below Nyquist.
    """
    nyq = sr / 2.0
    df = float(freqs[1] - freqs[0])
    k = max(1, int(round(200.0 / df)))  # ~200 Hz moving average
    pad = np.pad(ltas_db, (k, k), mode="edge")
    sm = np.convolve(pad, np.ones(k) / k, mode="same")[k:-k]
    top = (freqs >= 0.96 * nyq) & (freqs <= 0.995 * nyq)
    if not top.any():
        top = freqs >= freqs[-max(2, len(freqs) // 50)]
    floor = float(np.median(sm[top]))
    band = (freqs >= 100) & (freqs <= 0.995 * nyq)
    ref = float(np.percentile(sm[band], 95))
    result = {"nyquist_hz": nyq, "floor_db": round(floor, 2), "ref_db": round(ref, 2)}

    step = max(1, int(round(50.0 / df)))
    hi_lim = 0.995 * nyq
    for i in range(0, len(freqs), step):
        f = float(freqs[i])
        # edge windows scale with frequency (a steep natural roll-off is steep in Hz at low f, not in octaves)
        w_in, w_out = 50.0 + 0.02 * f, 100.0 + EDGE_REL_WIDTH * f
        if f < EDGE_MIN_HZ or f + w_out > hi_lim:
            continue
        below = sm[(freqs >= f - w_out) & (freqs <= f - w_in)]
        after = sm[(freqs >= f + w_in) & (freqs <= f + w_out)]
        if below.size == 0 or after.size == 0:
            continue
        lb, la = float(np.median(below)), float(np.median(after))
        if lb - la < EDGE_DROP_DB or lb < ref - 60:
            continue
        rest = sm[(freqs >= f + w_in) & (freqs <= hi_lim)]
        if float(np.percentile(rest, 95)) > lb - (EDGE_DROP_DB - 5):
            continue
        # refine: first crossing of the mid level inside the transition
        win = np.flatnonzero((freqs >= f - w_out) & (freqs <= f + w_out))
        mid = (lb + la) / 2
        cross = win[sm[win] < mid]
        bw = float(freqs[cross[0]]) if cross.size else f
        return {**result, "bandwidth_hz": round(bw, 1), "edge_drop_db": round(lb - la, 2), "sharp_edge": True,
                "upsampled_suspect": bool(bw < UPSAMPLED_RATIO * nyq)}

    if ref - floor < MIN_FLOOR_SEPARATION_DB:
        # content (or broadband noise) reaches Nyquist: no measurable floor below it
        return {**result, "bandwidth_hz": nyq, "edge_drop_db": 0.0, "sharp_edge": False, "upsampled_suspect": False}
    above = np.flatnonzero((sm >= floor + FLOOR_MARGIN_DB) & (freqs >= 50) & (freqs <= hi_lim))
    bw = float(freqs[above[-1]]) if above.size else 0.0
    return {**result, "bandwidth_hz": round(bw, 1), "edge_drop_db": 0.0, "sharp_edge": False,
            "upsampled_suspect": False}


def analyze_original(path: Path, sample_rate: int | None, timeout: int = 4 * 3600) -> dict:
    """Decode the original at its native rate (mono mix) and measure bandwidth + clipping, streaming."""
    require_tools()
    sr = int(sample_rate or 0)
    if sr <= 0:
        sr = probe(path)["sample_rate"] or 48000
    proc = subprocess.Popen(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
                             "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-c:a", "pcm_f32le",
                             "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    watchdog = threading.Timer(timeout, proc.kill)
    watchdog.start()
    acc = LtasAccumulator(sr)
    err_holder: list[bytes] = []
    t = threading.Thread(target=lambda: err_holder.append(proc.stderr.read()), daemon=True)
    t.start()
    try:
        chunk = acc.nfft * 256 * 4
        while True:
            data = proc.stdout.read(chunk)
            if not data:
                break
            usable = len(data) - len(data) % 4
            acc.feed(np.frombuffer(data[:usable], dtype="<f4"))
        code = proc.wait()
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
    t.join(5)
    if code:
        raise AudioError(f"頻寬分析解碼失敗：{(err_holder[0] if err_holder else b'').decode(errors='replace')[-400:]}")
    freqs, ltas, used = acc.ltas()
    bw = estimate_bandwidth(freqs, ltas, sr) if used else {"bandwidth_hz": None, "upsampled_suspect": None}
    return {**bw, "sample_rate": sr, "frames_used": used,
            "clipping_ratio": (acc.clipped / acc.samples) if acc.samples else None,
            "spectrum": log_spectrum(freqs, ltas, sr / 2.0) if used else []}


def log_spectrum(freqs: np.ndarray, ltas_db: np.ndarray, nyquist: float, points: int = 160,
                 f_min: float = 20.0) -> list[dict]:
    """Compact LTAS for the UI: ~points log-spaced bands from f_min to Nyquist, mean dB per band."""
    edges = np.geomspace(f_min, nyquist, points + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (freqs >= lo) & (freqs < hi)
        centre = float(np.sqrt(lo * hi))
        db = float(np.mean(ltas_db[sel])) if sel.any() else float(np.interp(centre, freqs, ltas_db))
        if out and out[-1]["hz"] == round(centre, 1):
            continue
        out.append({"hz": round(centre, 1), "db": round(db, 1)})
    return out


def clipping_ratio(x: np.ndarray) -> float | None:
    x = np.asarray(x)
    if x.size == 0:
        return None
    return float(np.count_nonzero(np.abs(x) >= CLIP_LEVEL) / x.size)


def segment_lufs(x: np.ndarray, sr: int = 16000) -> float | None:
    """Integrated loudness (BS.1770 / EBU R128 gating) of a short clip; None when < 0.4 s or silent."""
    if x.size < int(0.4 * sr):
        return None
    try:
        import pyloudnorm as pyln
        v = float(pyln.Meter(sr).integrated_loudness(np.asarray(x, dtype=np.float64)))
    except (ValueError, ImportError):
        return None
    return v if math.isfinite(v) else None
