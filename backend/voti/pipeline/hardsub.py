"""Hard-subtitle (burned-in caption) extraction — pure logic, no OCR engine and no I/O (unit-tested with synthetic
boxes/frames). The job that drives it is voti.pipeline.ocr (`media.ocr`); docs/ARCHITECTURE.md "Hard-subtitle OCR".

  discovery : full-frame OCR boxes at sparse times (every ~2 s) → clusters ("styles") by line position, glyph height
              and fill/outline colours; each style is judged on temporal evidence across the whole video (coverage,
              text change rate, run lengths, centring/anchoring, script, scattered boxes, scrolling, speech share) —
              never on one frame. Accepted styles are grouped into subtitle tracks (colour variants at one position,
              stacked lines of one block); each track gets a horizontal band.
  tracking  : band-crop OCR at ~5 fps → per-frame observation per track (boxes of the track's styles, lines joined
              top → bottom) → events (similar text + overlapping x-extent, one-frame gap tolerance).
  voting    : character-level consensus over the event's readings (medoid backbone + weighted alignment votes).
  pairing   : events × speech regions → paired spans (snapped to speech) or a rejection reason (no_speech,
              little_speech, rate_mismatch, static_text, concurrent_tracks, low_ocr_confidence, too_short_text, …).
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")
LATIN = re.compile(r"[A-Za-z]")


# ───────────────────────────── text helpers ─────────────────────────────

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def norm_text(s: str) -> str:
    """Comparison key: NFKC, no whitespace."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def similarity(a: str, b: str) -> float:
    a, b = norm_text(a), norm_text(b)
    if not a and not b:
        return 1.0
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


def han_count(s: str) -> int:
    return len(HAN.findall(s or ""))


def han_ratio(s: str) -> float:
    s = norm_text(s)
    letters = [c for c in s if c.isalnum() or HAN.match(c)]
    return han_count(s) / len(letters) if letters else 0.0


def _align(ref: str, hyp: str) -> list[tuple[int, str]]:
    """Edit alignment of hyp onto ref -> list of (ref position, op) where op is the hyp char for match/sub at
    position i, "" for deletion at i, and ("+", c) insertions recorded as (i - 0.5) positions."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]))
    out = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i, j] == d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            out.append((i - 1, hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i, j] == d[i - 1, j] + 1:
            out.append((i - 1, ""))
            i -= 1
        else:
            out.append((i - 0.5, hyp[j - 1]))  # insertion before ref[i]
            j -= 1
    return out[::-1]


def vote_text(texts: list[str], weights: list[float] | None = None) -> tuple[str, float]:
    """Character-level consensus of several OCR readings of the same line.
    → (consensus text, agreement = weighted share of readings equal to the consensus)."""
    texts = [t for t in texts if t]
    if not texts:
        return "", 0.0
    w = list(weights) if weights else [1.0] * len(texts)
    w = [max(1e-3, float(x)) for x in w[:len(texts)]]
    total = sum(w)
    exact = Counter()
    for t, x in zip(texts, w):
        exact[t] += x
    best, bw = exact.most_common(1)[0]
    if bw / total >= 0.5 or len(exact) == 1:
        return best, bw / total
    # medoid backbone (weighted similarity), then per-position weighted votes
    uniq = list(exact)
    backbone = max(uniq, key=lambda u: sum(exact[v] * similarity(u, v) for v in uniq))
    slots: dict = defaultdict(Counter)
    for t, x in exact.items():
        for pos, ch in _align(backbone, t):
            slots[pos][ch] += x
    out = []
    keys = sorted(set(list(slots.keys()) + list(range(len(backbone)))))
    for pos in keys:
        votes = slots.get(pos, Counter())
        if isinstance(pos, float) and not float(pos).is_integer():
            ch, cw = votes.most_common(1)[0]
            if cw > total / 2:
                out.append(ch)
            continue
        # matched position: unseen weight (readings that did not touch this slot) counts for the backbone char
        seen = sum(votes.values())
        votes[backbone[int(pos)]] += max(0.0, total - seen)
        ch, _ = votes.most_common(1)[0]
        out.append(ch)
    cons = "".join(out)
    return cons, exact.get(cons, 0.0) / total


# ───────────────────────────── boxes & styles ─────────────────────────────

@dataclass
class Box:
    t: float
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float
    fill: tuple = (0.0, 0.0, 0.0)     # mean RGB of glyph fill (0..255)
    edge: tuple = (0.0, 0.0, 0.0)     # mean RGB of the outline / surrounding ring
    style: int | None = None

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    def dict(self) -> dict:
        return {"t": round(self.t, 3), "text": self.text, "conf": round(self.conf, 4),
                "bbox": [round(self.x0), round(self.y0), round(self.x1), round(self.y1)],
                "fill": [round(c) for c in self.fill], "edge": [round(c) for c in self.edge], "style": self.style}


def glyph_colors(crop: np.ndarray) -> tuple[tuple, tuple]:
    """crop: HxWx3 RGB uint8 of one text line box → (fill RGB, edge RGB).
    Fill = Otsu class that touches the crop border least (glyph strokes sit inside the box); edge = ring of
    pixels around the fill strokes (outline, shadow or box background)."""
    import cv2
    if crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    _, bw = cv2.threshold(g, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    border = np.concatenate([bw[0], bw[-1], bw[:, 0], bw[:, -1]])
    bright_border = border.mean()
    bright_share = bw.mean()
    # border share relative to area share: the glyph class is under-represented on the border
    fill_is_bright = (bright_border / max(bright_share, 1e-3)) <= ((1 - bright_border) / max(1 - bright_share, 1e-3))
    mask = (bw == 1) if fill_is_bright else (bw == 0)
    if mask.sum() < 4:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    k = max(1, int(round(crop.shape[0] * 0.06)))
    ring = cv2.dilate(mask.astype(np.uint8), np.ones((2 * k + 1, 2 * k + 1), np.uint8)).astype(bool) & ~mask
    fill = tuple(float(v) for v in np.median(crop[mask], axis=0))
    edge = tuple(float(v) for v in np.median(crop[ring], axis=0)) if ring.any() else fill
    return fill, edge


def color_dist(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def pair_dist(f1, e1, f2, e2) -> float:
    """Distance of two (fill, edge) colour pairs, order-insensitive: the fill/edge decision flips when the
    background behind an outlined glyph is as bright as the fill."""
    return min(color_dist(f1, f2) + color_dist(e1, e2), color_dist(f1, e2) + color_dist(e1, f2)) / 2


@dataclass
class Style:
    id: int
    cy: float           # median centre y (px)
    h: float            # median glyph-line height (px)
    fill: tuple
    edge: tuple
    boxes: list = field(default_factory=list)
    accepted: bool = False
    reasons: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def matches(self, b: Box, frame_h: float, pos_tol: float = 0.45, h_tol: float = 0.22,
                color_tol: float | None = 90.0) -> bool:
        return (abs(b.cy - self.cy) <= pos_tol * self.h and abs(b.h - self.h) <= h_tol * self.h
                and (color_tol is None or pair_dist(b.fill, b.edge, self.fill, self.edge) <= color_tol))

    def dict(self) -> dict:
        return {"id": self.id, "cy": round(self.cy, 1), "h": round(self.h, 1),
                "fill": [round(c) for c in self.fill], "edge": [round(c) for c in self.edge],
                "accepted": self.accepted, "reasons": self.reasons,
                "stats": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.stats.items()}}


def cluster_boxes(boxes: list[Box], frame_h: float) -> list[Style]:
    """Greedy clustering by centre y, height and fill colour (boxes sorted by confidence first so seeds are clean)."""
    styles: list[Style] = []
    members: list[list[Box]] = []
    for b in sorted(boxes, key=lambda x: -x.conf):
        best, bd = None, 1e9
        for i, s in enumerate(styles):
            if s.matches(b, frame_h):
                d = abs(b.cy - s.cy) / s.h + abs(b.h - s.h) / s.h + color_dist(b.fill, s.fill) / 255
                if d < bd:
                    best, bd = i, d
        if best is None:
            styles.append(Style(len(styles), b.cy, b.h, b.fill, b.edge))
            members.append([b])
        else:
            members[best].append(b)
            mb = members[best]
            if len(mb) <= 50 or len(mb) % 10 == 0:  # running robust centre
                styles[best].cy = float(np.median([x.cy for x in mb]))
                styles[best].h = float(np.median([x.h for x in mb]))
                styles[best].fill = tuple(np.median([x.fill for x in mb], axis=0))
                styles[best].edge = tuple(np.median([x.edge for x in mb], axis=0))
    for s, mb in zip(styles, members):
        s.boxes = mb
        s.cy = float(np.median([x.cy for x in mb]))
        s.h = float(np.median([x.h for x in mb]))
        s.fill = tuple(float(v) for v in np.median([x.fill for x in mb], axis=0))
        s.edge = tuple(float(v) for v in np.median([x.edge for x in mb], axis=0))
    return styles


def runs_of(samples: list[tuple[float, str]], step: float, same: float = 0.75) -> list[tuple[float, float, str]]:
    """Consecutive samples (sorted by time) with similar text → runs (start, end, text). A gap > 1.5 steps ends a run."""
    out = []
    for t, txt in samples:
        if out and t - out[-1][1] <= 1.5 * step + 1e-6 and similarity(out[-1][2], txt) >= same:
            out[-1] = (out[-1][0], t, out[-1][2])
        else:
            out.append((t, t, txt))
    return out


DISCOVERY_DEFAULTS = {
    "min_frames": 6,             # appearances in the discovery samples
    "min_coverage": 0.02,        # share of discovery samples showing the style
    "max_median_run_sec": 8.0,   # median duration of one text (subtitles change every ~1-7 s)
    "max_static_share": 0.35,    # share of appearance time spent in runs longer than 20 s
    "min_change_rate": 0.2,      # share of adjacent sample pairs whose text changed (at a 2 s step; scaled)
    "min_distinct_ratio": 0.25,  # distinct texts / appearances
    "min_speech_share": 0.5,     # share of appearances during speech (when VAD is known)
    "max_center_offset": 0.12,   # |cx − W/2| / W for "centred" boxes
    "min_centered_share": 0.6,   # … share of centred appearances (left/right-aligned styles need run evidence)
    "min_script_share": 0.5,     # Han share of letters (target script) across the style's texts
    "max_scroll_share": 0.3,     # adjacent pairs that look like a horizontal scroll (ticker)
    "min_median_han": 3,         # median Han characters per appearance (labels / numbers are shorter)
    "max_multi_share": 0.35,     # appearances with several far-apart boxes in the band (tables, UI, charts)
    "min_relative_frames": 0.12, # appearances relative to the strongest accepted style (separate positions)
    "min_variant_frames": 0.02,  # … for a colour variant at the position of a strong style
}


def _lcs_sub(a: str, b: str) -> tuple[int, int, int]:
    """Longest common substring → (length, start in a, start in b)."""
    best = (0, 0, 0)
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best[0]:
                    best = (cur[j], i - cur[j], j - cur[j])
        prev = cur
    return best


def is_scroll(a: str, b: str) -> bool:
    """Two consecutive readings of a ticker: a long shared substring at a shifted position."""
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb or na == nb or min(len(na), len(nb)) < 4:
        return False
    n, ia, ib = _lcs_sub(na, nb)
    return n >= 0.5 * min(len(na), len(nb)) and ia != ib


def score_styles(styles: list[Style], sample_times: list[float], step: float, frame_w: float, frame_h: float,
                 speech_at=None, cfg: dict | None = None) -> list[Style]:
    """Temporal evidence per style → accepted / rejected with reasons. speech_at(t) → bool or None."""
    c = {**DISCOVERY_DEFAULTS, **(cfg or {})}
    n_samples = max(1, len(sample_times))
    for s in styles:
        by_t: dict = defaultdict(list)
        for b in s.boxes:
            by_t[round(b.t, 3)].append(b)
        times = sorted(by_t)
        # per sample: join the style's boxes left→right (a style line split by det into pieces)
        seq = []
        for t in times:
            bs = sorted(by_t[t], key=lambda x: x.x0)
            seq.append((t, "".join(x.text for x in bs), bs))
        runs = runs_of([(t, txt) for t, txt, _ in seq], step)
        run_lens = [r[1] - r[0] + step for r in runs]
        appear = sum(run_lens)
        static = sum(x for x in run_lens if x > 20.0)
        pairs = changes = scrolls = 0
        for (t0, x0, b0), (t1, x1, b1) in zip(seq, seq[1:]):
            if t1 - t0 <= max(1.5 * step, 3.0) + 1e-6:  # successive appearances (short gaps between lines allowed)
                pairs += 1
                if similarity(x0, x1) < 0.75:
                    changes += 1
                    if is_scroll(x0, x1):
                        scrolls += 1
        centered = 0
        x0s, x1s = [], []
        for _, _, bs in seq:
            contiguous = all(b1.x0 - b0.x1 <= 1.5 * s.h for b0, b1 in zip(bs, bs[1:]))
            ux0, ux1 = min(b.x0 for b in bs), max(b.x1 for b in bs)
            if contiguous:
                x0s.append(ux0)
                x1s.append(ux1)
                if abs((ux0 + ux1) / 2 - frame_w / 2) <= c["max_center_offset"] * frame_w:
                    centered += 1
        # left / right aligned styles: a stable start (or end) x across appearances
        anchored = 0
        if x0s:
            mx0, mx1 = float(np.median(x0s)), float(np.median(x1s))
            anchored = max(sum(abs(x - mx0) <= 0.6 * s.h for x in x0s), sum(abs(x - mx1) <= 0.6 * s.h for x in x1s))
        texts = [x for _, x, _ in seq]
        joined = "".join(texts)
        multi = 0
        for _, _, bs in seq:
            if len(bs) > 1 and any(b1.x0 - b0.x1 > 1.5 * s.h for b0, b1 in zip(bs, bs[1:])):
                multi += 1
        speech = [speech_at(t) for t in times] if speech_at else []
        speech = [v for v in speech if v is not None]
        st = {
            "frames": len(times), "coverage": len(times) / n_samples,
            "distinct_ratio": len({norm_text(x) for x in texts}) / max(1, len(texts)),
            "median_run_sec": float(np.median(run_lens)) if run_lens else 0.0,
            "static_share": static / appear if appear else 0.0,
            "change_rate": changes / pairs if pairs else 0.0,
            "scroll_share": scrolls / max(1, changes),
            "centered_share": centered / max(1, len(times)),
            "anchored_share": anchored / max(1, len(times)),
            "script_share": han_ratio(joined),
            "speech_share": (sum(speech) / len(speech)) if speech else None,
            "mean_conf": float(np.mean([b.conf for b in s.boxes])),
            "median_han": float(np.median([han_count(x) for x in texts])) if texts else 0.0,
            "multi_share": multi / max(1, len(seq)),
            "y_rel": s.cy / frame_h, "h_rel": s.h / frame_h,
        }
        s.stats = st
        r = []
        if st["frames"] < c["min_frames"] or st["coverage"] < c["min_coverage"]:
            r.append("rare")
        if st["median_run_sec"] > c["max_median_run_sec"] or st["static_share"] > c["max_static_share"]:
            r.append("static")
        # change-rate thresholds are calibrated for a 2 s discovery step; denser sampling sees more repeats
        k = min(1.0, step / 2.0)
        if st["change_rate"] < c["min_change_rate"] * k or st["distinct_ratio"] < c["min_distinct_ratio"] * k:
            r.append("unchanging")
        if st["scroll_share"] > c["max_scroll_share"]:
            r.append("scrolling")
        if st["centered_share"] < c["min_centered_share"] and st["anchored_share"] < c["min_centered_share"]:
            r.append("not_centered")
        if st["script_share"] < c["min_script_share"]:
            r.append("other_script")
        if st["speech_share"] is not None and st["speech_share"] < c["min_speech_share"]:
            r.append("no_speech")
        if st["median_han"] < c["min_median_han"]:
            r.append("short_texts")
        if st["multi_share"] > c["max_multi_share"]:
            r.append("scattered")
        s.reasons = r
        s.accepted = not r
    # weak styles: a style seen much less often than the strongest one is accepted only when it shares the line
    # position of a strong style (colour variants of the same subtitle: other speaker colour, bright background)
    top = max([s.stats["frames"] for s in styles if s.accepted] or [0])
    strong = [s for s in styles if s.accepted and s.stats["frames"] >= c["min_relative_frames"] * top]
    for s in styles:
        if s.accepted and s not in strong:
            near = any(abs(s.cy - x.cy) <= 0.5 * max(s.h, x.h) and abs(s.h - x.h) <= 0.3 * max(s.h, x.h)
                       for x in strong)
            if not near or s.stats["frames"] < c["min_variant_frames"] * top:
                s.accepted, s.reasons = False, ["minor"]
    return styles


def merge_lines(styles: list[Style]) -> list[list[int]]:
    """Group accepted styles into subtitle tracks: styles sharing a band (same line position and glyph height:
    colour variants such as white text over a bright background) or stacked lines of one block (height ±20 %,
    similar colours, vertical gap < 1 line). Styles at another position or with another look (e.g. the boxed
    captions of an embedded clip under the host's subtitles) are separate tracks. → groups of style ids."""
    acc = sorted([s for s in styles if s.accepted], key=lambda s: s.cy)
    groups: list[list[Style]] = []
    for s in acc:
        placed = False
        for g in groups:
            for x in g:
                same_band = abs(s.cy - x.cy) <= 0.35 * max(s.h, x.h) and abs(s.h - x.h) <= 0.2 * max(s.h, x.h)
                stacked = (abs(s.h - x.h) <= 0.2 * max(s.h, x.h) and abs(s.cy - x.cy) <= 2.0 * max(s.h, x.h)
                           and pair_dist(s.fill, s.edge, x.fill, x.edge) <= 90)
                if same_band or stacked:
                    g.append(s)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            groups.append([s])
    return [[s.id for s in g] for g in groups]


_PAREN = re.compile(r"[(（\[［【][^()（）\[\]［］【】]{0,40}[)）\]］】]")


def clean_text(text: str) -> str:
    """Cue text from the OCR consensus: parenthesised editor notes (（無法證實）, [音效]) are usually not spoken
    and are removed; whitespace trimmed."""
    t = _PAREN.sub("", text or "")
    t = fix_brackets(t)
    return re.sub(r"\s+", " ", t).strip()


def fix_brackets(t: str) -> str:
    """PP-OCR often reads the corner bracket 「 as 『 (and 」 as 』). Taiwanese subtitles quote with 「」 and nest
    『』 inside: corner brackets are re-derived from their nesting depth (outermost 「」, nested 『』)."""
    out, depth = [], 0
    for ch in t:
        if ch in "「『":
            out.append("「" if depth == 0 else "『")
            depth += 1
        elif ch in "」』":
            depth = max(0, depth - 1)
            out.append("」" if depth == 0 else "』")
        else:
            out.append(ch)
    return "".join(out)


def concurrent(events: list) -> set[int]:
    """Indices of events of different tracks (Event.track) that overlap in time by ≥ 0.3 s: two subtitle tracks on
    screen at once (host commentary over a clip) cannot be paired with one stream of speech reliably."""
    out: set[int] = set()
    order = sorted(range(len(events)), key=lambda i: events[i].start)
    for a_i, i in enumerate(order):
        for j in order[a_i + 1:]:
            if events[j].start >= events[i].end:
                break
            if events[i].track != events[j].track and \
                    min(events[i].end, events[j].end) - max(events[i].start, events[j].start) >= 0.3:
                out.update((i, j))
    return out


def band_of(styles: list[Style], ids: list[int], frame_h: float, margin: float = 0.6) -> tuple[int, int]:
    ss = [s for s in styles if s.id in ids]
    y0 = min(s.cy - s.h / 2 - margin * s.h for s in ss)
    y1 = max(s.cy + s.h / 2 + margin * s.h for s in ss)
    return max(0, int(y0)), min(int(frame_h), int(math.ceil(y1)))


def merge_bands(bands: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlapping / touching bands → one crop region each (OCR runs once per region and frame)."""
    out: list[list[int]] = []
    for a, b in sorted(bands):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


# ───────────────────────────── tracking & events ─────────────────────────────

@dataclass
class Obs:
    t: float
    text: str
    conf: float
    bbox: tuple
    style: int
    lines: int = 1


@dataclass
class Event:
    start: float
    end: float
    text: str
    raw_texts: list
    style: int
    bbox: tuple
    conf: float
    n_frames: int
    agreement: float
    lines: int = 1
    best_t: float = 0.0           # time of the most confident frame (for crops)
    track: int = 0                # subtitle track (style group) index

    def dict(self) -> dict:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text, "track": self.track,
                "style": self.style, "bbox": [int(v) for v in self.bbox], "conf": round(self.conf, 4),
                "n_frames": self.n_frames, "agreement": round(self.agreement, 3), "lines": self.lines,
                "best_t": round(self.best_t, 3), "readings": dict(Counter(self.raw_texts).most_common(5))}


def frame_obs(t: float, boxes: list[Box], styles: list[Style], group: list[int], frame_h: float,
              frame_w: float = 1280.0, center_tol: float = 0.08) -> Obs | None:
    """Boxes of one frame (in full-frame coordinates) → the group's observation (lines top→bottom joined).
    A box belongs to the group when its line position and glyph height fit a style of the group and either it
    is horizontally centred (subtitle layout; colour estimates are unreliable over bright backgrounds) or its
    colours match the style."""
    ss = [s for s in styles if s.id in group]
    hits = []
    for b in boxes:
        centred = abs(b.cx - frame_w / 2) <= center_tol * frame_w
        m = [s for s in ss if s.matches(b, frame_h, h_tol=0.3, color_tol=None if centred else 90.0)]
        if m:
            b.style = min(m, key=lambda s: abs(b.cy - s.cy)).id
            hits.append(b)
    if not hits:
        return None
    # lines: cluster by cy; within a line order by x
    hits.sort(key=lambda b: b.cy)
    lines: list[list[Box]] = []
    for b in hits:
        if lines and abs(b.cy - np.mean([x.cy for x in lines[-1]])) < 0.5 * b.h:
            lines[-1].append(b)
        else:
            lines.append([b])
    texts = []
    for ln in lines:
        ln.sort(key=lambda b: b.x0)
        parts = [b.text for b in ln]
        texts.append(" ".join(parts) if all(LATIN.search(p[-1:] or "") for p in parts[:-1]) and len(parts) > 1
                     else "".join(parts))
    text = "".join(texts)  # Chinese lines join without a space
    conf = float(np.average([b.conf for b in hits], weights=[max(1, len(b.text)) for b in hits]))
    bbox = (min(b.x0 for b in hits), min(b.y0 for b in hits), max(b.x1 for b in hits), max(b.y1 for b in hits))
    style = Counter(b.style for b in hits).most_common(1)[0][0]
    return Obs(t, text, conf, bbox, style, len(lines))


def iou_x(a: tuple, b: tuple) -> float:
    """Horizontal overlap / union of two bboxes (subtitle boxes are centred: x-extent tracks the text)."""
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    union = max(a[2], b[2]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


TRACK_DEFAULTS = {"same_text": 0.6,     # similarity of consecutive readings of one event
                  "min_conf": 0.75,      # readings below this confidence only extend timing, never vote
                  "max_gap_frames": 1,   # missed frames tolerated inside an event
                  "min_frames": 2}       # shorter flashes are dropped


def build_events(obs: list[Obs | None], times: list[float], step: float, cfg: dict | None = None) -> list[Event]:
    """obs[i] is the observation at times[i] (None = no subtitle). → events with consensus text."""
    c = {**TRACK_DEFAULTS, **(cfg or {})}
    events: list[Event] = []
    cur: list[Obs] = []
    gap = 0

    def close():
        nonlocal cur
        good = [o for o in cur if o.conf >= c["min_conf"]] or []
        if len(cur) >= c["min_frames"] and good:
            text, agree = vote_text([o.text for o in good], [o.conf for o in good])
            mid = (cur[0].t + cur[-1].t) / 2  # representative frame: consensus text, away from fades / cuts
            best = max(good, key=lambda o: (round(similarity(o.text, text), 3), -abs(o.t - mid), o.conf))
            bx = tuple(float(np.median([o.bbox[k] for o in good])) for k in range(4))
            events.append(Event(start=cur[0].t - step / 2, end=cur[-1].t + step / 2, text=text,
                                raw_texts=[o.text for o in cur], style=Counter(o.style for o in good).most_common(1)[0][0],
                                bbox=bx, conf=float(np.mean([o.conf for o in good])), n_frames=len(cur),
                                agreement=agree, lines=Counter(o.lines for o in good).most_common(1)[0][0],
                                best_t=best.t))
        cur = []

    for t, o in zip(times, obs):
        if o is None or not norm_text(o.text):
            if cur:
                gap += 1
                if gap > c["max_gap_frames"]:
                    close()
                    gap = 0
            continue
        if cur:
            ref = max(cur[-3:], key=lambda x: x.conf)
            if similarity(ref.text, o.text) >= c["same_text"] and iou_x(ref.bbox, o.bbox) >= 0.3:
                cur.append(o)
                gap = 0
                continue
            close()
        cur = [o]
        gap = 0
    if cur:
        close()
    return events


# ───────────────────────────── pairing with speech ─────────────────────────────

def speech_regions(probs: np.ndarray, hop: float, on: float = 0.5, off: float = 0.35, min_speech: float = 0.2,
                   merge_gap: float = 0.25) -> list[tuple[float, float]]:
    """Frame speech probabilities (Brouhaha / Silero) → speech regions with hysteresis, short gaps merged."""
    regs = []
    active = False
    s = 0.0
    for i, p in enumerate(probs):
        if not active and p >= on:
            active, s = True, i * hop
        elif active and p < off:
            active = False
            regs.append((s, i * hop))
    if active:
        regs.append((s, len(probs) * hop))
    merged: list[tuple[float, float]] = []
    for a, b in regs:
        if merged and a - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if b - a >= min_speech]


PAIR_DEFAULTS = {"slack_sec": 0.4,          # subtitle timing vs speech tolerance on each side
                 "min_speech_share": 0.45,   # speech inside the (padded) event / event duration
                 "min_cps": 1.0, "max_cps": 12.0,  # Han characters per second of speech
                 "min_chars": 2, "max_sec": 20.0,
                 "min_event_conf": 0.85,
                 "max_event_sec": 12.0}     # longer on-screen text is a title / caption card, not a subtitle


def pair_events(events: list[Event], regions: list[tuple[float, float]], cfg: dict | None = None) -> list[dict]:
    """→ one dict per event: {"event": i, "status": "paired"|"rejected", "reason", "start", "end", "speech_sec"}.
    Paired spans are snapped to the speech inside [event.start − slack, event.end + slack]."""
    c = {**PAIR_DEFAULTS, **(cfg or {})}
    out = []
    starts = np.array([r[0] for r in regions]) if regions else np.zeros(0)
    both = concurrent(events)
    for i, e in enumerate(events):
        a, b = e.start - c["slack_sec"], e.end + c["slack_sec"]
        k0 = int(np.searchsorted(starts, a, side="left")) - 1
        inside = []
        for k in range(max(0, k0), len(regions)):
            ra, rb = regions[k]
            if ra >= b:
                break
            lo, hi = max(a, ra), min(b, rb)
            if hi > lo:
                inside.append((lo, hi))
        speech = sum(hi - lo for lo, hi in inside)
        n_chars = len(norm_text(clean_text(e.text)))
        n_han = han_count(clean_text(e.text))
        rec = {"event": i, "speech_sec": round(speech, 3), "start": None, "end": None}
        dur = max(1e-3, e.end - e.start)
        reason = None
        if i in both:
            reason = "concurrent_tracks"
        elif e.end - e.start > c["max_event_sec"]:
            reason = "static_text"
        elif e.conf < c["min_event_conf"]:
            reason = "low_ocr_confidence"
        elif n_chars < c["min_chars"]:
            reason = "too_short_text"
        elif not inside or speech < 0.15:
            reason = "no_speech"
        elif speech / dur < c["min_speech_share"]:
            reason = "little_speech"
        else:
            cps = n_han / speech if speech else 0
            if n_han and not (c["min_cps"] <= cps <= c["max_cps"]):
                reason = "rate_mismatch"
        if reason is None:
            s0 = max(a, inside[0][0])
            s1 = min(b, inside[-1][1])
            # never start before the previous event's end / end after the next event's start (shared speech)
            if i > 0:
                s0 = max(s0, min(e.start, events[i - 1].end))
            if i + 1 < len(events):
                s1 = min(s1, max(e.end, events[i + 1].start))
            if s1 - s0 > c["max_sec"]:
                reason = "too_long"
            elif s1 - s0 < 0.3:
                reason = "too_short"
            else:
                rec.update(start=round(s0, 3), end=round(s1, 3))
        rec["status"] = "rejected" if reason else "paired"
        rec["reason"] = reason
        out.append(rec)
    return out
