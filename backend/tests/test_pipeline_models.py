"""Silero VAD, DNSMOS (official ONNX, downloaded + sha256-verified on first use) and quality summaries."""
from __future__ import annotations

import _pipeline_support as sup
import numpy as np
import pytest

from voti.pipeline import common, dnsmos, quality, vad


def test_silero_probabilities_follow_speech_and_silence():
    x, regions = sup.with_pauses([("silence", 2.0), ("speech", 4.0), ("silence", 3.0)], seed=3)
    hop, p = vad.silero_probs(x)
    assert hop == pytest.approx(0.032)
    assert p.size == int(np.ceil(x.size / 512))
    assert np.all((p >= 0) & (p <= 1))
    t = (np.arange(p.size) + 0.5) * hop
    assert p[t < 1.5].mean() < 0.2  # silence


def test_resample_and_regions_helpers():
    p = np.r_[np.zeros(10), np.ones(20), np.zeros(10)].astype(np.float32)
    r = common.resample_frames(p, 0.032, 0.016, 80)
    assert r.size == 80 and r[30] > 0.9 and r[5] < 0.1
    regs = common.hysteresis_regions(p, 0.1, min_speech=0.2, min_silence=0.3)
    assert regs == [(pytest.approx(1.0), pytest.approx(3.0))]
    assert common.covered(0, 10, [(1, 3), (2, 4), (8, 12)]) == 5


@pytest.fixture(scope="module")
def dnsmos_ready():
    try:
        dnsmos.ensure_models()
    except dnsmos.DnsmosUnavailable as e:
        pytest.skip(f"DNSMOS models not downloadable: {e}")


def test_dnsmos_scores_clean_vs_noisy(dnsmos_ready):
    clean = sup.speechlike(10, seed=1)
    noisy = clean + 0.15 * np.random.default_rng(0).standard_normal(clean.size).astype(np.float32)
    sc, sn = dnsmos.score_clip(clean), dnsmos.score_clip(noisy)
    for s in (sc, sn):
        assert s.shape == (4,) and np.all((s > 0.5) & (s < 5.5))
    assert sc[1] > sn[1]  # background (BAK) is better for the clean clip


def test_dnsmos_clip_windows_follow_official_rules():
    # official tiling doubles the clip until >= 9.01 s: 3 s → 12 s → floor(12 - 9.01) + 1 = 3 windows
    assert dnsmos.clip_windows(np.ones(16000 * 3, np.float32)).shape == (3, dnsmos.WIN)
    assert dnsmos.clip_windows(np.ones(16000 * 5, np.float32)).shape == (1, dnsmos.WIN)  # 5 → 10 s → 1
    assert dnsmos.clip_windows(np.ones(16000 * 12, np.float32)).shape[0] == 3              # floor(12 - 9.01) + 1
    assert dnsmos.clip_windows(np.zeros(0, np.float32)).shape[0] == 0


def test_dnsmos_window_starts_only_over_speech():
    hop = 0.016875
    dur = 40.0
    sp = sup.vad_from_regions([(20.0, 40.0)], dur, hop) > 0.5
    starts = quality.dnsmos_window_starts(sp, hop, dur)
    assert starts and min(starts) >= 15.0 and max(starts) <= 31.0
    assert quality.dnsmos_window_starts(sp[: int(5 / hop)], hop, 5.0) == []


def test_summarize_frames():
    hop = 0.02
    n = 500
    v = np.zeros(n, np.float32); v[100:400] = 0.9
    snr = np.full(n, 5.0, np.float32); snr[100:400] = np.linspace(10, 30, 300)
    fr = common.Frames(hop, v, snr, np.full(n, 20.0, np.float32), v.copy(), np.array([5.0], np.float32),
                       np.array([[3.0, 4.0, 2.5, 3.5]], np.float32))
    s = quality.summarize(fr, 10.0)
    assert s["speech_sec"] == pytest.approx(6.0) and s["speech_ratio"] == pytest.approx(0.6)
    assert s["vad_agreement"] == 1.0 and s["snr_mean"] == pytest.approx(20.0, abs=0.1)
    assert s["snr_p10"] == pytest.approx(12.0, abs=0.2) and s["dnsmos_ovrl"] == pytest.approx(2.5)
    back = common.Frames.from_npz_bytes(fr.to_npz_bytes())
    assert back.hop_sec == pytest.approx(hop) and back.dnsmos.shape == (1, 4)


def test_brouhaha_unreachable_is_blocked(monkeypatch):
    from voti.pipeline import legacy_client
    from voti.queue import JobError

    def boom(key):
        raise ConnectionError("refused")
    monkeypatch.setattr(legacy_client, "brouhaha", boom)
    with pytest.raises(JobError) as ei:
        quality._brouhaha("media/x/normalized.wav")
    assert ei.value.code == "legacy_ml_unavailable" and ei.value.blocked
