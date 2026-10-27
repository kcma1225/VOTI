"""Landmark fingerprints: a jingle repeated across synthetic episodes is found and grouped."""
from __future__ import annotations

import time

import _pipeline_support as sup
import numpy as np

from voti.pipeline import fingerprint as fp

SR = sup.SR


def _episode(seconds: float, seed: int, inserts: list[tuple[float, np.ndarray, float]]) -> np.ndarray:
    """Speech-like filler with clips inserted at (time, clip, gain); plus small background noise."""
    x = np.concatenate([sup.speechlike(7.0, seed=seed * 100 + i, f0=110 + 15 * (seed % 4))
                        for i in range(int(np.ceil(seconds / 7.0)))])[: int(seconds * SR)]
    for t, clip, gain in inserts:
        a = int(t * SR)
        x[a:a + clip.size] = gain * clip[: x.size - a]
    g = np.random.default_rng(seed)
    return (x + 0.003 * g.standard_normal(x.size)).astype(np.float32)


def test_jingle_grouped_across_three_episodes():
    jing = sup.jingle(6.0)
    outro = sup.jingle(5.0, seed=7)
    eps = [
        _episode(90, 1, [(3.0, jing, 1.0), (80.0, outro, 1.0)]),
        _episode(75, 2, [(11.37, jing, 0.4), (66.2, outro, 0.6)]),     # quieter, different offsets
        _episode(120, 3, [(0.51, jing, 1.6), (110.03, outro, 1.2)]),   # louder (clips a little)
        _episode(60, 4, []),                                            # unrelated episode
    ]
    eps[2] = np.clip(eps[2], -1, 1)
    t0 = time.monotonic()
    fps = [fp.fingerprint(e) for e in eps]
    matches = fp.find_matches(fps)
    regions = fp.group_matches(matches, [e.size / SR for e in eps])
    elapsed = time.monotonic() - t0
    assert elapsed < 30
    groups: dict[int, list] = {}
    for r in regions:
        groups.setdefault(r.group, []).append(r)
    assert all(r.media != 3 for r in regions), "unrelated episode must not match"
    # jingle group: one region per episode 0..2 near the insert time
    starts = {0: 3.0, 1: 11.37, 2: 0.51}
    jg = [g for g in groups.values() if any(abs(r.start - starts[r.media]) < 1.0 for r in g if r.media in starts)]
    assert len(jg) == 1
    got = {r.media: r for r in jg[0]}
    assert set(got) == {0, 1, 2}
    for m, r in got.items():
        assert abs(r.start - starts[m]) < 1.0
        assert abs((r.end - r.start) - 6.0) < 1.5
        assert r.match_count == 2
        assert fp.position_of(r.start, r.end, eps[m].size / SR) == "intro"
    # outro group
    ends = {0: 80.0, 1: 66.2, 2: 110.03}
    og = [g for g in groups.values() if any(abs(r.start - ends[r.media]) < 1.0 for r in g if r.media in ends)]
    assert len(og) == 1 and {r.media for r in og[0]} == {0, 1, 2}
    for r in og[0]:
        assert fp.position_of(r.start, r.end, eps[r.media].size / SR) == "outro"


def test_no_matches_between_unrelated_audio():
    eps = [_episode(60, s, []) for s in (11, 12, 13)]
    assert fp.find_matches([fp.fingerprint(e) for e in eps]) == []


def test_fingerprint_npz_roundtrip_and_density():
    x = _episode(30, 5, [(5.0, sup.jingle(4.0), 1.0)])
    h, t = fp.fingerprint(x)
    assert h.dtype == np.uint32 and t.dtype == np.int32 and h.size == t.size
    assert 20 < h.size / 30 < 200  # hashes per second stay bounded
    h2, t2, d = fp.from_npz(fp.to_npz(h, t, 30.0))
    assert np.array_equal(h, h2) and np.array_equal(t, t2) and d == 30.0


def test_position_of():
    assert fp.position_of(5, 20, 1800) == "intro"
    assert fp.position_of(1750, 1795, 1800) == "outro"
    assert fp.position_of(900, 930, 1800) == "middle"
