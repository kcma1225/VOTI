"""Audio probing, normalization, loudness, clipping and real-bandwidth detection on synthetic signals."""
from __future__ import annotations

import shutil
import subprocess

import _pipeline_support as sup  # noqa: F401  (env first)
import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from voti.pipeline import audio

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _noise(sr: int, seconds: float, cutoff: float | None, seed: int = 0) -> np.ndarray:
    g = np.random.default_rng(seed)
    x = g.standard_normal(int(sr * seconds)) * 0.1
    if cutoff:
        x = signal.lfilter(signal.firwin(1023, cutoff, fs=sr, window=("kaiser", 10)), 1, x)
    env = 0.5 + 0.5 * np.abs(np.sin(np.arange(x.size) / sr * 2.0))  # loudness variation
    return (x * env).astype(np.float32)


@needs_ffmpeg
def test_bandwidth_lowpassed_noise_in_44k_file(tmp_path):
    p = tmp_path / "lp4k.wav"
    sf.write(p, _noise(44100, 20, 4000), 44100, subtype="PCM_16")
    r = audio.analyze_original(p, 44100)
    assert abs(r["bandwidth_hz"] - 4000) < 300
    assert r["upsampled_suspect"] is True
    assert r["sharp_edge"] is True
    sp = r["spectrum"]
    assert 128 <= len(sp) <= 256 and sp[0]["hz"] < 30 and sp[-1]["hz"] > 20000
    assert all(a["hz"] < b["hz"] for a, b in zip(sp, sp[1:]))
    lo = np.mean([p["db"] for p in sp if 1000 < p["hz"] < 3000])
    hi = np.mean([p["db"] for p in sp if p["hz"] > 6000])
    assert lo - hi > 40


@needs_ffmpeg
def test_bandwidth_full_band_not_suspect(tmp_path):
    p = tmp_path / "full.wav"
    sf.write(p, _noise(44100, 20, None), 44100, subtype="PCM_16")
    r = audio.analyze_original(p, 44100)
    assert r["bandwidth_hz"] > 0.9 * 22050
    assert r["upsampled_suspect"] is False


@needs_ffmpeg
@pytest.mark.parametrize("codec,ext", [("libopus", "webm"), ("aac", "m4a")])
def test_bandwidth_survives_lossy_reencode(tmp_path, codec, ext):
    """YouTube re-encodes uploads: the codec noise plateau above the true edge must not hide it."""
    wav = tmp_path / "lp.wav"
    sf.write(wav, _noise(48000, 20, 4000), 48000, subtype="PCM_16")
    out = tmp_path / f"lp.{ext}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", codec, "-b:a", "96k", str(out)],
                   check=True)
    info = audio.probe(out)
    r = audio.analyze_original(out, info["sample_rate"])
    assert abs(r["bandwidth_hz"] - 4000) < 400
    assert r["upsampled_suspect"] is True


@needs_ffmpeg
def test_lossy_full_band_not_suspect(tmp_path):
    wav = tmp_path / "full.wav"
    sf.write(wav, _noise(44100, 15, None), 44100, subtype="PCM_16")
    out = tmp_path / "full.m4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "128k", str(out)],
                   check=True)
    r = audio.analyze_original(out, 44100)
    assert r["bandwidth_hz"] > 0.7 * 22050
    assert r["upsampled_suspect"] is False


@needs_ffmpeg
def test_probe_normalize_loudness_and_clipping(tmp_path):
    sr = 48000
    t = np.arange(sr * 5) / sr
    x = np.stack([0.5 * np.sin(2 * np.pi * 440 * t), 0.5 * np.sin(2 * np.pi * 660 * t)], axis=1)
    x[sr:sr + 4800] = np.clip(x[sr:sr + 4800] * 4, -1, 1)  # 0.1 s hard clipped
    src = tmp_path / "in.wav"
    sf.write(src, x, sr, subtype="PCM_16")
    info = audio.probe(src)
    assert info["sample_rate"] == sr and info["channels"] == 2 and info["codec"] == "pcm_s16le"
    assert info["container"] == "wav" and abs(info["duration_sec"] - 5) < 0.01
    res = audio.normalize(src, tmp_path / "n.wav", info["duration_sec"])
    y, ysr = sf.read(tmp_path / "n.wav")
    assert ysr == 16000 and y.ndim == 1 and abs(len(y) / 16000 - 5) < 0.01
    assert res["lufs_integrated"] is not None and -15 < res["lufs_integrated"] < 0
    assert res["true_peak_db"] is not None and res["true_peak_db"] > -1.0
    spec = audio.analyze_original(src, sr)
    assert 0.001 < spec["clipping_ratio"] < 0.05


def test_parse_ebur128_summary():
    err = """[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -19.3 LUFS
    Threshold: -29.6 LUFS

  Loudness range:
    LRA:         2.1 LU

  True peak:
    Peak:       -0.4 dBFS
"""
    assert audio.parse_ebur128(err) == {"lufs_integrated": -19.3, "true_peak_db": -0.4}
    assert audio.parse_ebur128("I: -inf LUFS")["lufs_integrated"] is None


def test_estimate_bandwidth_natural_rolloff_not_brickwall():
    """Speech-like spectrum: gradual HF roll-off into a noise floor is not an upsampling edge."""
    sr = 48000
    f = np.fft.rfftfreq(2048, 1 / sr)
    for slope in (12, 25):  # dB/octave above 500 Hz, into a -100 dB floor
        spec = np.maximum(-20 - slope * np.log2(np.maximum(f, 200) / 500).clip(0), -100.0)
        r = audio.estimate_bandwidth(f, spec, sr)
        assert r["upsampled_suspect"] is False, slope


def test_segment_lufs_and_clipping_helpers():
    x = 0.1 * np.sin(2 * np.pi * 1000 * np.arange(16000) / 16000).astype(np.float32)
    assert audio.segment_lufs(x) is not None and -30 < audio.segment_lufs(x) < -15
    assert audio.segment_lufs(x[:1000]) is None
    assert audio.clipping_ratio(np.array([0.0, 1.0, -1.0, 0.5])) == 0.5
