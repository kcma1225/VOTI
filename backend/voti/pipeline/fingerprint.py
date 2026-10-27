"""Cross-media repeated audio (intros, outros, jingles, sponsor reads) within a channel.

`media.fingerprint` — landmark fingerprints (Wang/"Shazam" style) of the normalized audio:
  8 kHz, 128 ms Hann STFT, 32 ms hop; spectral peaks (local maxima, ≤ PEAKS_PER_SEC per second, above
  a silence floor); each anchor peak is paired with its next FANOUT peaks within 0.03–3.2 s and ±1 kHz;
  hash = f1 (9 bits) | Δf (8 bits) | Δt (7 bits) → 24-bit hash + anchor frame. Peaks are relative to the local
  spectrum, so the hashes ignore gain/loudness; pairs are relative in time, so any time offset works, and
  ±1-frame jitter is absorbed by matching offsets with ±1 frame tolerance.
  Stored as media/<id>/fingerprint.npz (asset kind fingerprint).

`channel.dedup` — all fingerprints of a channel go into one sorted hash table; equal hashes from different
  media vote for (media_a, media_b, time offset). Offsets with enough votes become matches; consecutive votes
  give the matched span. Matches are linked into groups (union-find over overlapping spans), each group gets
  RepeatedRegion rows (excluded=True by default; earlier human decisions and group ids are carried over),
  and Segment.repeated_overlap / the "repeated" flag are recomputed.
"""
from __future__ import annotations

import io
import logging
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from sqlalchemy import delete, select

from .. import storage
from ..db import session_scope
from ..models import Asset, Media, RepeatedRegion, Segment
from ..queue import JobError
from .common import SR, covered, load_normalized, work_dir

log = logging.getLogger("voti.pipeline.fingerprint")

FP_SR = 8000
NFFT = 1024
HOP = 256
HOP_SEC = HOP / FP_SR            # 0.032 s
PEAKS_PER_SEC = 15
FANOUT = 5
DT_MIN, DT_MAX = 1, 100          # frames
DF_MAX = 127                     # bins (7.8 Hz each)
SILENCE_DB = -65.0
FP_VERSION = "landmark-v1"

# matching
MAX_PAIR_K = 24                  # pairs per hash run: each occurrence vs the next K (keeps cost linear)
MIN_HITS = 20
MIN_HIT_RATE = 3.0               # hits per second of matched span
MIN_MATCH_SEC = 2.0
MIN_MATCH_FRACTION = 0.25        # matched hashes / hashes of the span (identical audio ≈ 0.4–0.9;
                                 # similar-sounding but different audio ≈ 0.1–0.2)
MAX_GAP_SEC = 3.0
TAIL_SEC = 0.5                   # matched span extends a little past the last anchor
HASH_BUDGET = 30_000_000         # above this, only the first/last EDGE_SEC of each media are matched
EDGE_SEC = 600.0
DEDUP_NS = uuid.UUID("0b7e8a8c-3d2f-4b8e-9a51-1e7f7a3c5d21")


# ───────────────────────────── fingerprint ─────────────────────────────

def _spectrogram(x: np.ndarray) -> np.ndarray:
    """(frames, NFFT/2+1) log-power dB of 8 kHz audio."""
    if x.size < NFFT:
        x = np.pad(x, (0, NFFT - x.size))
    n = 1 + (x.size - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    win = np.hanning(NFFT).astype(np.float32)
    spec = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
    return (10 * np.log10(spec / (np.sum(win ** 2)) + 1e-12)).astype(np.float32)


def _peaks(S: np.ndarray, t_offset: int) -> tuple[np.ndarray, np.ndarray]:
    from scipy.ndimage import maximum_filter
    frame_db = 10 * np.log10(np.mean(10 ** (S / 10), axis=1) + 1e-12)
    local = maximum_filter(S, size=(11, 25), mode="constant", cval=-200.0)
    is_peak = (S >= local) & (S > SILENCE_DB - 20) & (frame_db[:, None] > SILENCE_DB)
    is_peak[:, :8] = False                                  # < 60 Hz
    # peaks must stand out from their frame's median (tonal/structural, not flat noise)
    is_peak &= S > (np.median(S, axis=1, keepdims=True) + 10)
    t, f = np.nonzero(is_peak)
    if t.size == 0:
        return t.astype(np.int32), f.astype(np.int32)
    mag = S[t, f]
    # density cap: strongest PEAKS_PER_SEC per 1-s block
    block = (t * HOP_SEC).astype(np.int64)
    order = np.lexsort((-mag, block))
    t, f, block = t[order], f[order], block[order]
    first = np.r_[0, np.flatnonzero(np.diff(block)) + 1]
    rank = np.arange(t.size) - np.repeat(first, np.diff(np.r_[first, t.size]))
    keep = rank < PEAKS_PER_SEC
    t, f = t[keep] + t_offset, f[keep]
    o = np.lexsort((f, t))
    return t[o].astype(np.int32), f[o].astype(np.int32)


def peaks(audio16k: np.ndarray, chunk_sec: float = 300.0) -> tuple[np.ndarray, np.ndarray]:
    from scipy.signal import resample_poly
    x = resample_poly(np.asarray(audio16k, dtype=np.float32), 1, SR // FP_SR).astype(np.float32)
    ctx = int(2.0 / HOP_SEC)
    step = int(chunk_sec / HOP_SEC)
    total = max(1, 1 + (x.size - NFFT) // HOP) if x.size >= NFFT else 1
    ts, fs = [], []
    for a in range(0, total, step):
        lo, hi = max(0, a - ctx), min(total, a + step + ctx)
        S = _spectrogram(x[lo * HOP: (hi - 1) * HOP + NFFT])
        t, f = _peaks(S, lo)
        keep = (t >= a) & (t < min(total, a + step))
        ts.append(t[keep])
        fs.append(f[keep])
    return np.concatenate(ts), np.concatenate(fs)


def hashes_from_peaks(t: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = t.size
    if n < 2:
        return np.zeros(0, np.uint32), np.zeros(0, np.int32)
    count = np.zeros(n, dtype=np.int32)
    hs, ts = [], []
    for k in range(1, 60):
        if k >= n:
            break
        i = np.arange(n - k)
        j = i + k
        dt = t[j] - t[i]
        df = f[j] - f[i]
        ok = (dt >= DT_MIN) & (dt <= DT_MAX) & (np.abs(df) <= DF_MAX) & (count[i] < FANOUT)
        if not ok.any():
            if (dt > DT_MAX).all():
                break
            continue
        ii = i[ok]
        count[ii] += 1
        h = ((f[ii].astype(np.uint32) & 0x1FF) << 15) | (((df[ok] + 128).astype(np.uint32) & 0xFF) << 7) \
            | (dt[ok].astype(np.uint32) & 0x7F)
        hs.append(h)
        ts.append(t[ii])
    if not hs:
        return np.zeros(0, np.uint32), np.zeros(0, np.int32)
    return np.concatenate(hs).astype(np.uint32), np.concatenate(ts).astype(np.int32)


def fingerprint(audio16k: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t, f = peaks(audio16k)
    return hashes_from_peaks(t, f)


def to_npz(hashes: np.ndarray, times: np.ndarray, duration: float) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, hashes=hashes.astype(np.uint32), times=times.astype(np.int32),
                        hop_sec=np.float32(HOP_SEC), duration=np.float32(duration), version=np.array(FP_VERSION))
    return buf.getvalue()


def from_npz(data: bytes) -> tuple[np.ndarray, np.ndarray, float]:
    z = np.load(io.BytesIO(data))
    return z["hashes"], z["times"], float(z["duration"])


# ───────────────────────────── matching ─────────────────────────────

@dataclass
class Match:
    a: int
    b: int
    a_start: float
    a_end: float
    offset: float      # b time = a time + offset
    hits: int
    score: float


def find_matches(fps: Sequence[tuple[np.ndarray, np.ndarray]]) -> list[Match]:
    """fps[i] = (hashes, anchor_frames) of media i → matches between different media."""
    if len(fps) < 2:
        return []
    sorted_times = [np.sort(t) for _, t in fps]

    def n_hashes(m: int, f0: float, f1: float) -> int:
        st = sorted_times[m]
        return int(np.searchsorted(st, f1, side="right") - np.searchsorted(st, f0, side="left"))

    H = np.concatenate([h for h, _ in fps]).astype(np.uint32)
    T = np.concatenate([t for _, t in fps]).astype(np.int32)
    M = np.concatenate([np.full(h.size, i, dtype=np.int32) for i, (h, _) in enumerate(fps)])
    if H.size == 0:
        return []
    # drop hashes that are everywhere (silence/hum artefacts): more than ~4 per media on average
    order = np.lexsort((T, M, H))
    H, T, M = H[order], T[order], M[order]
    del order
    run_start = np.r_[0, np.flatnonzero(np.diff(H)) + 1]
    run_len = np.diff(np.r_[run_start, H.size])
    keep = np.repeat(run_len <= max(16, 4 * len(fps)), run_len)
    H, T, M = H[keep], T[keep], M[keep]
    del keep
    pa, pb, pta, ptb = [], [], [], []
    for k in range(1, MAX_PAIR_K + 1):
        if k >= H.size:
            break
        same = (H[:-k] == H[k:]) & (M[:-k] != M[k:])
        if not same.any():
            if not (H[:-k] == H[k:]).any():
                break
            continue
        i = np.flatnonzero(same)
        pa.append(M[i]); pb.append(M[i + k]); pta.append(T[i]); ptb.append(T[i + k])
    del H, T, M
    if not pa:
        return []
    A, B, TA, TB = (np.concatenate(x).astype(np.int64) for x in (pa, pb, pta, ptb))
    OFF = TB - TA
    span = int(max(1, np.abs(OFF).max()) * 2 + 8)
    nm = len(fps)
    key = (A * nm + B) * span + (OFF + span // 2)
    o = np.argsort(key, kind="stable")
    key, A, B, TA, OFF = key[o], A[o], B[o], TA[o], OFF[o]
    ukeys, starts, counts = np.unique(key, return_index=True, return_counts=True)

    def cnt_of(k: np.ndarray) -> np.ndarray:
        pos = np.searchsorted(ukeys, k)
        pos_c = np.minimum(pos, ukeys.size - 1)
        return np.where(ukeys[pos_c] == k, counts[pos_c], 0)

    smooth = counts + cnt_of(ukeys - 1) + cnt_of(ukeys + 1)
    cand = np.flatnonzero((smooth >= MIN_HITS) & (smooth >= cnt_of(ukeys - 1) + cnt_of(ukeys) * 0)
                          & (counts >= cnt_of(ukeys - 1)) & (counts > cnt_of(ukeys + 1)))
    matches: list[Match] = []
    max_gap = MAX_GAP_SEC / HOP_SEC
    for ci in cand:
        k0 = ukeys[ci]
        lo = np.searchsorted(key, k0 - 1, side="left")
        hi = np.searchsorted(key, k0 + 1, side="right")
        ta = np.sort(TA[lo:hi])
        a, b = int(A[starts[ci]]), int(B[starts[ci]])
        off = int(OFF[starts[ci]])
        breaks = np.flatnonzero(np.diff(ta) > max_gap)
        for run in np.split(ta, breaks + 1):
            if run.size < MIN_HITS:
                continue
            s0, s1 = run[0] * HOP_SEC, run[-1] * HOP_SEC + TAIL_SEC
            dur = s1 - s0
            if dur < MIN_MATCH_SEC or run.size / dur < MIN_HIT_RATE:
                continue
            na = n_hashes(a, run[0], run[-1])
            nb = n_hashes(b, run[0] + off - 1, run[-1] + off + 1)
            if run.size < MIN_MATCH_FRACTION * max(1, min(na, nb)):
                continue
            matches.append(Match(a, b, float(s0), float(s1), off * HOP_SEC, int(run.size),
                                 float(run.size / dur)))
    return matches


@dataclass
class Region:
    media: int
    start: float
    end: float
    group: int
    score: float
    match_count: int


def group_matches(matches: Sequence[Match], durations: Sequence[float]) -> list[Region]:
    """Union-find over matched spans: spans overlapping (≥50% of the shorter) on the same media are one node;
    every match links its two spans. Returns per-media regions labelled with a group index."""
    spans: list[tuple[int, float, float, float]] = []  # (media, start, end, score)
    edges = []
    for m in matches:
        ia = len(spans)
        spans.append((m.a, m.a_start, m.a_end, m.score))
        spans.append((m.b, max(0.0, m.a_start + m.offset), min(durations[m.b], m.a_end + m.offset), m.score))
        edges.append((ia, ia + 1))
    parent = list(range(len(spans)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    for x, y in edges:
        union(x, y)
    by_media = defaultdict(list)
    for i, (med, a, b, _) in enumerate(spans):
        by_media[med].append(i)
    for med, idx in by_media.items():
        idx.sort(key=lambda i: spans[i][1])
        for p, i in enumerate(idx):
            for j in idx[p + 1:]:
                if spans[j][1] >= spans[i][2]:
                    break
                ov = min(spans[i][2], spans[j][2]) - max(spans[i][1], spans[j][1])
                shorter = min(spans[i][2] - spans[i][1], spans[j][2] - spans[j][1])
                if shorter > 0 and ov >= 0.5 * shorter:
                    union(i, j)
    groups = defaultdict(list)
    for i in range(len(spans)):
        groups[find(i)].append(i)
    regions: list[Region] = []
    for gi, (root, members) in enumerate(sorted(groups.items())):
        media_set = {spans[i][0] for i in members}
        per_media = defaultdict(list)
        for i in members:
            per_media[spans[i][0]].append(spans[i])
        for med, sp in per_media.items():
            sp.sort(key=lambda x: x[1])
            cur = None
            for _, a, b, sc in sp:
                if cur and a <= cur[1]:
                    cur = [cur[0], max(cur[1], b), max(cur[2], sc)]
                else:
                    if cur:
                        regions.append(Region(med, cur[0], cur[1], gi, cur[2], len(media_set) - 1))
                    cur = [a, b, sc]
            if cur:
                regions.append(Region(med, cur[0], cur[1], gi, cur[2], len(media_set) - 1))
    return regions


def position_of(start: float, end: float, duration: float) -> str:
    edge = min(120.0, 0.25 * duration) if duration > 0 else 120.0
    if start <= edge:
        return "intro"
    if duration > 0 and end >= duration - edge:
        return "outro"
    return "middle"


# ───────────────────────────── job entry points ─────────────────────────────

def run_fingerprint(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None) -> dict:
    t0 = time.monotonic()
    with work_dir("fp") as wd:
        with session_scope() as s:
            if s.get(Media, media_id) is None:
                raise JobError("not_found", "找不到媒體", retryable=False)
            audio = load_normalized(s, media_id, wd)
    progress(0.2, "fingerprint")
    h, t = fingerprint(audio)
    duration = audio.size / SR
    with session_scope() as s:
        storage.store_bytes_asset(s, to_npz(h, t, duration), storage.media_key(media_id, "fingerprint.npz"),
                                  "fingerprint", media_id=media_id,
                                  params={"version": FP_VERSION, "hashes": int(h.size), "hop_sec": HOP_SEC})
    return {"hashes": int(h.size), "hashes_per_sec": round(h.size / max(duration, 1e-6), 1),
            "elapsed_sec": round(time.monotonic() - t0, 2)}


def recompute_repeated_overlap(s, media_ids: set[uuid.UUID]) -> int:
    """Segment.repeated_overlap = fraction covered by excluded RepeatedRegions; keeps the `repeated` flag in sync
    (same semantics as the API's PATCH /repeated)."""
    if not media_ids:
        return 0
    regions = defaultdict(list)
    for r in s.scalars(select(RepeatedRegion).where(RepeatedRegion.media_id.in_(media_ids),
                                                    RepeatedRegion.excluded.is_(True))):
        regions[r.media_id].append((r.start_sec, r.end_sec))
    n = 0
    for seg in s.scalars(select(Segment).where(Segment.media_id.in_(media_ids), Segment.active.is_(True))):
        dur = seg.end_sec - seg.start_sec
        ov = round(min(1.0, covered(seg.start_sec, seg.end_sec, regions.get(seg.media_id, [])) / dur), 4) \
            if dur > 0 else 0.0
        flags = [f for f in (seg.flags or []) if f != "repeated"]
        if ov > 0:
            flags.append("repeated")
        if ov != seg.repeated_overlap or flags != list(seg.flags or []):
            seg.repeated_overlap, seg.flags = ov, sorted(flags)
            n += 1
    return n


def _restrict_edges(h: np.ndarray, t: np.ndarray, duration: float) -> tuple[np.ndarray, np.ndarray]:
    sec = t * HOP_SEC
    keep = (sec <= EDGE_SEC) | (sec >= duration - EDGE_SEC)
    return h[keep], t[keep]


def run_dedup(channel_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None) -> dict:
    t0 = time.monotonic()
    with session_scope() as s:
        rows = s.execute(select(Media.id, Asset.key).join(Asset, (Asset.media_id == Media.id)
                                                          & (Asset.kind == "fingerprint"))
                         .where(Media.channel_id == channel_id).order_by(Media.published_at, Media.id)).all()
        old = [(r.media_id, r.start_sec, r.end_sec, r.group_id, r.excluded) for r in
               s.scalars(select(RepeatedRegion).where(RepeatedRegion.channel_id == channel_id))]
    media_ids = [r[0] for r in rows]
    fps, durations = [], []
    for i, (mid, key) in enumerate(rows):
        h, t, d = from_npz(storage.get_bytes(key))
        fps.append((h, t))
        durations.append(d)
        if i % 20 == 0:
            progress(0.4 * i / max(1, len(rows)), "load")
    total = sum(h.size for h, _ in fps)
    restricted = total > HASH_BUDGET
    if restricted:
        log.warning("channel %s: %d hashes > budget; matching first/last %ds only", channel_id, total, EDGE_SEC)
        fps = [_restrict_edges(h, t, d) for (h, t), d in zip(fps, durations)]
    progress(0.45, "match")
    matches = find_matches(fps)
    regions = group_matches(matches, durations)

    # carry over group ids / human exclusion decisions from the previous run
    old_by_media = defaultdict(list)
    for mid, a, b, gid, exc in old:
        old_by_media[mid].append((a, b, gid, exc))
    by_group = defaultdict(list)
    for r in regions:
        by_group[r.group].append(r)
    taken: set[uuid.UUID] = set()
    out_rows = []
    for gi, regs in sorted(by_group.items()):
        votes, excl = Counter(), []
        for r in regs:
            for a, b, gid, exc in old_by_media.get(media_ids[r.media], []):
                ov = min(b, r.end) - max(a, r.start)
                if ov > 0.5 * min(b - a, r.end - r.start):
                    votes[gid] += 1
                    excl.append(exc)
        gid = next((g for g, _ in votes.most_common() if g not in taken), None)
        if gid is None:
            anchor = min((str(media_ids[r.media]), round(r.start, 1)) for r in regs)
            gid = uuid.uuid5(DEDUP_NS, f"{channel_id}:{anchor[0]}:{anchor[1]}")
            while gid in taken:
                gid = uuid.uuid5(DEDUP_NS, f"{gid}:next")
        taken.add(gid)
        excluded = not (excl and sum(1 for e in excl if not e) > len(excl) / 2)
        for r in regs:
            out_rows.append(RepeatedRegion(channel_id=channel_id, group_id=gid, media_id=media_ids[r.media],
                                           start_sec=round(r.start, 3), end_sec=round(r.end, 3),
                                           position=position_of(r.start, r.end, durations[r.media]),
                                           match_count=r.match_count, score=round(r.score, 3), excluded=excluded))
    progress(0.9, "save")
    with session_scope() as s:
        s.execute(delete(RepeatedRegion).where(RepeatedRegion.channel_id == channel_id))
        s.add_all(out_rows)
        s.flush()
        touched = set(media_ids) | {m for m, *_ in old}
        updated = recompute_repeated_overlap(s, touched)
    return {"media": len(media_ids), "hashes": int(total), "restricted_to_edges": restricted,
            "matches": len(matches), "groups": len(by_group), "regions": len(out_rows),
            "segments_updated": updated, "elapsed_sec": round(time.monotonic() - t0, 2)}
