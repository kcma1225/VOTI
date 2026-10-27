"""FFmpeg media validation, full-timeline PCM conversion, and lossless time slices."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path


class MediaError(RuntimeError):
    pass


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError(f"缺少 {tool}；請先安裝 FFmpeg")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(path: Path) -> dict:
    require_tools()
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,format_name:stream=codec_type,sample_rate,channels", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=45,
    )
    if result.returncode:
        raise MediaError(f"無法解碼音訊：{result.stderr.strip()[:400]}")
    data = json.loads(result.stdout)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise MediaError("來源不含音訊軌")
    duration = float(data.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError("來源音訊長度無效")
    return {"duration_sec": duration, "sample_rate": int(streams[0].get("sample_rate") or 0),
            "channels": int(streams[0].get("channels") or 0),
            "format": data.get("format", {}).get("format_name", "")}


def _run_ffmpeg(args: list[str], duration: float, progress, timeout: int, failure: str) -> None:
    """Run ffmpeg; progress(fraction) is fed from its own -progress output (out_time vs. probed duration)."""
    with tempfile.TemporaryFile(mode="w+") as errors:
        process = subprocess.Popen(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                                    "-progress", "pipe:1", "-nostats", *args],
                                   stdout=subprocess.PIPE, stderr=errors, text=True)
        watchdog = threading.Timer(timeout, process.kill)
        watchdog.start()
        try:
            for line in process.stdout:
                if progress and line.startswith("out_time_us=") and duration > 0:
                    try:
                        done = int(line.split("=", 1)[1]) / 1e6
                    except ValueError:
                        continue
                    progress(min(1.0, max(0.0, done / duration)))
            code = process.wait()
        except BaseException:
            # A cancel raised from the progress callback must not leave ffmpeg running.
            process.kill()
            process.wait()
            raise
        finally:
            watchdog.cancel()
        errors.seek(0)
        if code:
            raise MediaError(f"{failure}：{errors.read().strip()[:400] or '逾時或被中止'}")

def normalize(original: Path, target: Path, progress=None, timeout: int = 600) -> dict:
    info = probe(original)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".wav", delete=False) as f:
        temporary = Path(f.name)
    try:
        _run_ffmpeg(["-i", str(original), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                     "-c:a", "pcm_s16le", str(temporary)], info["duration_sec"], progress, timeout, "音訊標準化失敗")
        with wave.open(str(temporary), "rb") as audio:
            if audio.getframerate() != 16000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise MediaError("音訊標準化格式驗證失敗")
            normalized_duration = audio.getnframes() / 16000
        temporary.replace(target)
        return {**info, "normalized_duration_sec": normalized_duration,
                "format_spec": "mono/16000Hz/signed-16-bit-PCM; full original timeline"}
    finally:
        temporary.unlink(missing_ok=True)

MP3_BITRATE = "96k"

def to_mp3(original: Path, target: Path, progress=None, timeout: int = 1800) -> dict:
    """Playback copy of the whole original timeline (constant bitrate, so browsers seek accurately).
    Analysis keeps using the 16 kHz WAV; this file is only for listening."""
    info = probe(original)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".mp3", delete=False) as f:
        temporary = Path(f.name)
    try:
        _run_ffmpeg(["-i", str(original), "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", MP3_BITRATE,
                     "-f", "mp3", str(temporary)], info["duration_sec"], progress, timeout, "MP3 轉檔失敗")
        check = probe(temporary)
        if "mp3" not in check["format"] or abs(check["duration_sec"] - info["duration_sec"]) > 1.0:
            raise MediaError("MP3 轉檔結果驗證失敗（格式或長度不符）")
        temporary.replace(target)
        return {**info, "mp3_duration_sec": check["duration_sec"],
                "format_spec": f"mp3/CBR {MP3_BITRATE}; full original timeline"}
    finally:
        temporary.unlink(missing_ok=True)

def slice_audio(wav: Path, target: Path, start_sec: float, end_sec: float) -> dict:
    """Slice by PCM frame, retaining exact and documented sample coordinates."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav), "rb") as source:
        rate, frames = source.getframerate(), source.getnframes()
        if (rate != 16000 or source.getnchannels() != 1 or source.getsampwidth() != 2):
            raise MediaError("切片來源必須是 16 kHz 單聲道 16-bit PCM WAV")
        start = max(0, round(start_sec * rate))
        end = min(frames, round(end_sec * rate))
        if start >= end or start_sec < 0 or end_sec > frames / rate + 1 / rate:
            raise MediaError("切片邊界無效或越界")
        source.setpos(start)
        pcm = source.readframes(end - start)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".wav", delete=False) as f:
        temporary = Path(f.name)
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(pcm)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"start_frame": start, "end_frame": end, "duration_sec": (end - start) / rate}


def basic_audio_flags(wav: Path) -> list[str]:
    """Cheap peak/RMS checks; not an SNR or a VAD prediction."""
    import numpy as np
    with wave.open(str(wav), "rb") as audio:
        data = audio.readframes(audio.getnframes())
    if not data:
        return ["empty_audio"]
    samples = np.frombuffer(data, dtype="<i2").astype(np.float64)
    rms = float(np.sqrt(np.mean(samples * samples)))
    peak = float(np.abs(samples).max())
    flags = []
    if rms < 80:
        flags.append("low_volume_or_silence")
    if peak >= 32760:
        flags.append("possible_clipping")
    return flags
