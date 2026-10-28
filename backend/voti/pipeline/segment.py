"""`media.segment`: caption cues (or VAD/diarization when there are no captions) → Segment rows.

Planning (pure, unit-tested: `plan_from_cues`, `plan_from_vad`):
  1. cues are cleaned (empty dropped, rolling overlaps clamped) and grouped: a cue is merged into the current
     group while the group is shorter than segment_min_sec, or while the join is a short gap that is neither a
     sentence end nor a VAD pause and the result stays under a soft target; groups never exceed segment_max_sec.
  2. groups longer than segment_max_sec are split at the longest VAD pause nearest the middle (text split
     proportionally at punctuation → flag text_split).
  3. boundaries snap to VAD: a start inside speech moves back to the speech onset, a start in silence moves
     forward to the onset (symmetrically for ends); if speech cannot be avoided the least-speechy frame on the
     outer side is used (never inward: that cuts words; flag boundary_in_speech). Moves > 0.25 s → flag
     boundary_adjusted. Then edge padding (edge_pad_sec), never overlapping neighbours.
Then each segment gets the dominant diarization speaker, speaker_state, a quality snapshot and flags.
The alignment step (voti.pipeline.align) later widens boundaries that still cut speech (flag boundary_refined);
a re-run keeps those refined boundaries for the same segment.
Re-running is idempotent: ids are uuid5(media_id, start, end); rows that disappear are set active=False;
human-touched rows (reviewed / corrected / boundary-edited) are kept and new segments overlapping them dropped.
Segments whose cues were converted Simplified → Traditional (cue flag s2t_converted, voti.textconv) get the flag
s2t_converted; their original_text is the converted text.
OCR tracks (kind "ocr", voti.pipeline.ocr) are used only when the media has no manual/uploaded/auto track, and only
their cues flagged `ocr_paired` (subtitle text that coexists with speech); text_source "ocr_caption". Speech
without such a cue gets no segment (gaps between OCR cues stay unsegmented).
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select

from ..db import session_scope
from ..models import Cue, DiarizationTurn, Media, RepeatedRegion, Segment, Track
from ..queue import JobError
from . import audio as audio_mod
from .common import (SR, covered, finite_or_none, get_setting, hysteresis_regions, load_frames, load_normalized,
                     work_dir)

log = logging.getLogger("voti.pipeline.segment")

SEG_NS = uuid.UUID("6f1c2b1e-6a43-4c38-9a59-6c1f2f3f0a11")
SPEECH_THR = 0.5
SNAP_WINDOW = 0.6         # s searched around a caption boundary for a speech onset/offset
SNAP_SILENCE_REACH = 2.0  # s searched when a caption boundary lies in silence
ADJUST_FLAG_SEC = 0.25
MERGE_GAP = 0.6           # cues closer than this may be merged…
MUST_MERGE_GAP = 1.5      # …and below segment_min_sec they are merged up to this gap
SOFT_TARGET_SEC = 10.0
PAUSE_SEC = 0.3
SENTENCE_END = re.compile(r"[。！？!?…\.]\s*[」』”\"')）]*\s*$")
TEXT_SOURCE = {"manual": "manual_caption", "auto": "auto_caption", "uploaded": "uploaded", "ocr": "ocr_caption"}
OCR_PAIRED = "ocr_paired"  # voti.pipeline.ocr: only OCR cues paired with speech become segments
S2T_FLAG = "s2t_converted"  # voti.textconv.FLAG (not imported: textconv pulls in opencc)
REFINED = "boundary_refined"  # voti.pipeline.align moved the boundaries to the aligned speech


@dataclass
class CueIn:
    id: str
    start: float
    end: float
    text: str
    flags: list[str] = field(default_factory=list)


@dataclass
class Plan:
    start: float
    end: float
    text: str = ""
    cue_ids: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)


@dataclass
class Config:
    min_sec: float = 1.0
    max_sec: float = 20.0
    pad: float = 0.1

    @classmethod
    def from_setting(cls, d: dict) -> "Config":
        mn = float(d.get("segment_min_sec", 1.0))
        mx = max(float(d.get("segment_max_sec", 20.0)), mn + 0.5)
        return cls(min_sec=mn, max_sec=mx, pad=float(d.get("edge_pad_sec", 0.1)))


class Vad:
    """Speech-probability helper on a frame grid."""

    def __init__(self, prob: np.ndarray, hop: float, duration: float):
        self.p, self.hop, self.duration = np.asarray(prob, dtype=np.float32), float(hop), float(duration)
        self.sp = self.p >= SPEECH_THR

    def idx(self, t: float) -> int:
        return int(min(max(0, int(t / self.hop)), max(0, len(self.p) - 1)))

    def t(self, i: int) -> float:
        return min(self.duration, i * self.hop)

    def speech_at(self, t: float) -> bool:
        return bool(self.sp.size and self.sp[self.idx(t)])

    def pause_between(self, a: float, b: float, min_pause: float = PAUSE_SEC) -> bool:
        """Is there a non-speech run >= min_pause inside [a, b] (or is the gap itself long enough)?"""
        if b - a >= min_pause and not self.sp[self.idx(a):self.idx(b) + 1].any():
            return True
        ia, ib = self.idx(a), self.idx(b) + 1
        run, need = 0, max(1, int(round(min_pause / self.hop)))
        for v in self.sp[ia:ib]:
            run = 0 if v else run + 1
            if run >= need:
                return True
        return False

    def _least_speechy(self, a: int, b: int, default: int) -> tuple[float, bool]:
        a, b = max(0, a), min(len(self.p), b)
        k = a + int(np.argmin(self.p[a:b])) if b > a else default
        return self.t(k), bool(self.sp[min(k, len(self.sp) - 1)])

    def snap_start(self, t: float, lo: float, hi: float) -> tuple[float, bool]:
        """Start boundary → (new start, still_inside_speech). lo/hi bound the search (neighbours)."""
        if not self.sp.size:
            return t, False
        i, w = self.idx(t), int(SNAP_WINDOW / self.hop)
        ilo, ihi = self.idx(lo), self.idx(hi)
        if self.sp[i]:  # inside speech → walk back to the onset
            j = i
            while j > ilo and j > i - w and self.sp[j - 1]:
                j -= 1
            if j == 0 or not self.sp[j - 1]:
                return self.t(j), False
            # continuous speech: only look outward (earlier) — moving the start later cuts the first word
            return self._least_speechy(max(ilo, i - w), i + 1, i)
        j = i  # in silence → forward to the onset (leading silence is trimmed; padding re-adds a little)
        while j < min(ihi, i + int(SNAP_SILENCE_REACH / self.hop)) and not self.sp[j]:
            j += 1
        return (self.t(j), False) if self.sp[j] else (t, False)

    def snap_end(self, t: float, lo: float, hi: float) -> tuple[float, bool]:
        if not self.sp.size:
            return t, False
        i, w = self.idx(max(0.0, t - 1e-6)), int(SNAP_WINDOW / self.hop)
        ilo, ihi = self.idx(lo), self.idx(hi)
        n = len(self.sp)
        if self.sp[i]:  # inside speech → walk forward to the offset
            j = i
            while j < ihi and j < i + w and j + 1 < n and self.sp[j + 1]:
                j += 1
            if j + 1 >= n or not self.sp[j + 1]:
                return self.t(j + 1), False
            # continuous speech: only look outward (later) — moving the end earlier cuts the last word
            return self._least_speechy(i, max(i + 1, min(ihi, i + w) + 1), i)
        j = i  # in silence → back to the end of the last speech
        while j > max(ilo, i - int(SNAP_SILENCE_REACH / self.hop)) and not self.sp[j]:
            j -= 1
        return (self.t(j + 1), False) if self.sp[j] else (t, False)

    def best_split(self, a: float, b: float) -> tuple[float, bool]:
        """Split point inside (a, b): middle of the longest pause, preferring the centre; else least speechy."""
        ia, ib = self.idx(a) + 1, self.idx(b)
        if ib - ia < 3 or not self.sp.size:
            return (a + b) / 2, False
        mid = (ia + ib) / 2
        best, best_score = None, -1e9
        k = ia
        while k < ib:
            if not self.sp[k]:
                s = k
                while k < ib and not self.sp[k]:
                    k += 1
                length = (k - s) * self.hop
                centre = (s + k) / 2
                score = min(length, 1.0) - abs(centre - mid) / max(1, ib - ia)
                if score > best_score:
                    best, best_score = centre, score
            k += 1
        if best is not None:
            return self.t(int(best)), False
        lo, hi = ia + (ib - ia) // 4, ib - (ib - ia) // 4
        k = lo + int(np.argmin(self.p[lo:hi])) if hi > lo else int(mid)
        return self.t(k), True


def join_text(parts: Sequence[str]) -> str:
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and out[-1].isascii() and out[-1].isalnum() and p[0].isascii() and p[0].isalnum():
            out += " "
        out += p
    return out


def split_text(text: str, frac: float) -> tuple[str, str]:
    """Split text near frac of its length, preferring punctuation/space within ±25%."""
    if not text:
        return "", ""
    n = len(text)
    target = int(round(frac * n))
    best, best_d = target, n
    for i, ch in enumerate(text):
        if ch in "，,。.！!？?、；;：: " and abs(i + 1 - target) <= max(2, n // 4):
            d = abs(i + 1 - target)
            if d < best_d:
                best, best_d = i + 1, d
    best = min(max(best, 1), n - 1) if n > 1 else n
    return text[:best].strip(), text[best:].strip()


def clean_cues(cues: Sequence[CueIn], duration: float) -> list[CueIn]:
    out = []
    for c in sorted(cues, key=lambda c: (c.start, c.end)):
        text = (c.text or "").strip()
        a, b = max(0.0, c.start), min(duration, c.end)
        if not text or b - a < 0.05:
            continue
        out.append(CueIn(c.id, a, b, text, list(c.flags or [])))
    for i in range(len(out) - 1):  # rolling captions overlap their successor
        if out[i + 1].start < out[i].end:
            out[i].end = max(out[i].start + 0.05, out[i + 1].start)
    return out


def _split_long(p: Plan, vad: Vad, cfg: Config) -> list[Plan]:
    if p.end - p.start <= cfg.max_sec:
        return [p]
    t, in_speech = vad.best_split(p.start + cfg.min_sec, p.end - cfg.min_sec)
    frac = (t - p.start) / (p.end - p.start)
    left_t, right_t = split_text(p.text, frac)
    flags = set(p.flags) | ({"text_split"} if p.text else set()) | ({"boundary_in_speech"} if in_speech else set())
    left = Plan(p.start, t, left_t, list(p.cue_ids), set(flags))
    right = Plan(t, p.end, right_t, list(p.cue_ids), set(flags))
    return _split_long(left, vad, cfg) + _split_long(right, vad, cfg)


def group_cues(cues: Sequence[CueIn], vad: Vad, cfg: Config) -> list[Plan]:
    plans: list[Plan] = []
    cur: list[CueIn] = []
    for c in cues:
        if cur:
            g_start, g_end = cur[0].start, cur[-1].end
            gap = c.start - g_end
            merged = c.end - g_start
            cur_dur = g_end - g_start
            if ("speaker_change" in c.flags and cur_dur >= 0.5) or OCR_GAP in c.flags:
                plans.append(_plan_of(cur))
                cur = [c]
                continue
            must = cur_dur < cfg.min_sec and gap <= MUST_MERGE_GAP and merged <= cfg.max_sec
            may = (gap <= MERGE_GAP and merged <= min(cfg.max_sec, max(SOFT_TARGET_SEC, cfg.min_sec))
                   and not SENTENCE_END.search(cur[-1].text) and not vad.pause_between(g_end, c.start))
            if must or may:
                cur.append(c)
                continue
            plans.append(_plan_of(cur))
        cur = [c]
    if cur:
        plans.append(_plan_of(cur))
    return plans


OCR_GAP = "ocr_after_gap"  # (in-memory flag) the previous OCR event was rejected: never merge across it


def ocr_cues(rows: Sequence) -> list[CueIn]:
    """OCR track cues → only the paired ones; a cue that follows a rejected event starts a new group (its audio
    gap holds speech whose subtitle was rejected, e.g. a concurrent clip caption)."""
    out, prev_ok = [], True
    for c in rows:
        ok = OCR_PAIRED in (c.flags or [])
        if ok:
            flags = list(c.flags or []) + ([] if prev_ok else [OCR_GAP])
            out.append(CueIn(str(c.id), c.start_sec, c.end_sec, c.text, flags))
        prev_ok = ok
    return out


def _plan_of(cues: Sequence[CueIn]) -> Plan:
    flags = {"music"} if any("music" in c.flags for c in cues) else set()
    if any(S2T_FLAG in c.flags for c in cues):  # text came (partly) from Simplified → Traditional conversion
        flags.add(S2T_FLAG)
    if any("speaker_change" in c.flags for c in cues[1:]):
        flags.add("caption_speaker_change")
    return Plan(cues[0].start, cues[-1].end, join_text([c.text for c in cues]), [c.id for c in cues], flags)


def snap_and_pad(plans: list[Plan], vad: Vad, cfg: Config) -> list[Plan]:
    out: list[Plan] = []
    for i, p in enumerate(plans):
        lo = out[-1].end if out else 0.0
        hi_next = plans[i + 1].start if i + 1 < len(plans) else vad.duration
        s, s_in = vad.snap_start(max(p.start, lo), lo, min(p.end, hi_next))
        e, e_in = vad.snap_end(p.end, max(s, p.start), hi_next)
        if e - s < 0.2:  # snapping collapsed it (no speech at all): keep caption timing
            s, e, s_in, e_in = max(p.start, lo), p.end, False, False
        q = Plan(s, e, p.text, p.cue_ids, set(p.flags))
        if abs(s - p.start) > ADJUST_FLAG_SEC or abs(e - p.end) > ADJUST_FLAG_SEC:
            q.flags.add("boundary_adjusted")
        if s_in or e_in:
            q.flags.add("boundary_in_speech")
        out.append(q)
    # edge padding without overlapping neighbours
    for i, p in enumerate(out):
        prev_end = out[i - 1].end if i else 0.0
        next_start = out[i + 1].start if i + 1 < len(out) else vad.duration
        p.start = max(0.0, p.start - cfg.pad, (prev_end + p.start) / 2 if i else 0.0)
        p.end = min(vad.duration, p.end + cfg.pad, (p.end + next_start) / 2 if i + 1 < len(out) else vad.duration)
    for p in out:
        p.start, p.end = round(p.start, 3), round(p.end, 3)
    return [p for p in out if p.end - p.start > 0.05]


def plan_from_cues(cues: Sequence[CueIn], vad: Vad, cfg: Config) -> list[Plan]:
    clean = clean_cues(cues, vad.duration)
    plans = []
    for p in group_cues(clean, vad, cfg):
        plans.extend(_split_long(p, vad, cfg))
    return snap_and_pad(plans, vad, cfg)


def plan_from_vad(vad: Vad, cfg: Config, turns: Sequence[tuple[str, float, float, bool]] = ()) -> list[Plan]:
    """No captions: speech regions, split at speaker changes, packed to min..soft target, long ones split."""
    regions = hysteresis_regions(vad.p, vad.hop, min_speech=0.2, min_silence=0.25)

    def speaker(a: float, b: float) -> str | None:
        cov: dict[str, float] = {}
        for spk, x, y, _ in turns:
            if y > a and x < b:
                cov[spk] = cov.get(spk, 0.0) + min(b, y) - max(a, x)
        return max(cov, key=cov.get) if cov else None

    plans: list[Plan] = []
    cur: Plan | None = None
    cur_spk = None
    for a, b in regions:
        spk = speaker(a, b)
        if cur is not None:
            gap, merged = a - cur.end, b - cur.start
            same = spk is None or cur_spk is None or spk == cur_spk
            if same and ((cur.end - cur.start < cfg.min_sec and gap <= MUST_MERGE_GAP and merged <= cfg.max_sec)
                         or (gap <= MERGE_GAP and merged <= min(cfg.max_sec, SOFT_TARGET_SEC))):
                cur.end = b
                continue
            plans.append(cur)
        cur, cur_spk = Plan(a, b), spk
    if cur is not None:
        plans.append(cur)
    out = []
    for p in plans:
        out.extend(_split_long(p, vad, cfg))
    for p in out:
        p.flags.discard("text_split")
    return snap_and_pad(out, vad, cfg)


# ───────────────────────────── per-segment attributes ─────────────────────────────

def speaker_info(start: float, end: float, turns: Sequence[tuple[str, float, float, bool]],
                 diarized: bool) -> tuple[str | None, str]:
    if not diarized:
        return None, "unknown"
    by_spk: dict[str, list[tuple[float, float]]] = {}
    overlaps = []
    for spk, a, b, ov in turns:
        if b > start and a < end:
            by_spk.setdefault(spk, []).append((a, b))
            if ov:
                overlaps.append((a, b))
    if not by_spk:
        return None, "unknown"
    cov = {k: covered(start, end, v) for k, v in by_spk.items()}
    ranked = sorted(cov.items(), key=lambda kv: -kv[1])
    dom = ranked[0][0]
    total = covered(start, end, [x for v in by_spk.values() for x in v])
    dur = end - start
    ov = covered(start, end, overlaps)
    if ov >= max(0.3, 0.1 * dur):
        return dom, "overlap"
    if len(ranked) > 1 and ranked[1][1] >= max(0.5, 0.15 * total):
        return dom, "multiple"
    return dom, "single"


def choose_track(tracks: Sequence[Track], preferred_id: str | None = None) -> Track | None:
    def lang_rank(lang: str) -> int:
        lang = (lang or "").lower()
        for i, pref in enumerate(("zh-tw", "zh-hant", "zh-hant-tw", "nan", "zh", "cmn")):
            if lang == pref:
                return i
        if lang.startswith("zh") or lang.startswith("nan"):
            return 10
        return 20
    kind_rank = {"manual": 0, "uploaded": 1, "auto": 2, "ocr": 3}  # burned-in subtitles only without any CC
    usable = [t for t in tracks if t.kind in kind_rank]
    if not usable:
        return None
    pref = [t for t in usable if preferred_id and str(t.id) == str(preferred_id)]
    if pref and pref[0].kind == min((t.kind for t in usable), key=kind_rank.get):
        return pref[0]
    return sorted(usable, key=lambda t: (kind_rank[t.kind], lang_rank(t.language), str(t.id)))[0]


def segment_id(media_id: uuid.UUID, start: float, end: float) -> uuid.UUID:
    return uuid.uuid5(SEG_NS, f"{media_id}:{start:.3f}:{end:.3f}")


def quality_snapshot(start: float, end: float, frames, audio: np.ndarray) -> dict:
    sl = frames.span(start, end)
    vadp = frames.brouhaha_vad[sl]
    sp = vadp >= SPEECH_THR
    snr = frames.snr[sl][sp] if sp.any() else frames.snr[sl]
    c50 = frames.c50[sl][sp] if sp.any() else frames.c50[sl]
    x = audio[int(start * SR):int(end * SR)]
    return {"speech_ratio": finite_or_none(sp.mean()) if sp.size else None,
            "snr": finite_or_none(np.nanmean(snr)) if snr.size else None,
            "c50": finite_or_none(np.nanmean(c50)) if c50.size else None,
            "clipping_ratio": audio_mod.clipping_ratio(x), "lufs": audio_mod.segment_lufs(x)}


def quality_flags(q: dict, dur: float, state: str, cfg: Config, thr: dict) -> set[str]:
    f = set()
    if q.get("speech_ratio") is not None and q["speech_ratio"] < float(thr.get("low_speech_ratio", 0.5)):
        f.add("low_speech")
    if q.get("snr") is not None and q["snr"] < float(thr.get("low_snr_db", 10)):
        f.add("low_snr")
    if q.get("clipping_ratio") is not None and q["clipping_ratio"] > float(thr.get("clipping_ratio", 0.001)):
        f.add("clipped")
    if dur < cfg.min_sec:
        f.add("too_short")
    if dur > cfg.max_sec + 2 * cfg.pad + 1e-6:
        f.add("too_long")
    if state == "overlap":
        f.add("overlap")
    elif state == "multiple":
        f.add("multi_speaker")
    return f


def _protected(seg: Segment) -> bool:
    return bool(seg.active and (seg.review_status != "unchecked" or seg.corrected_text is not None
                                or (seg.revision or 1) > 1))


def run(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None) -> dict:
    from . import dnsmos
    t0 = time.monotonic()
    with session_scope() as s:
        media = s.get(Media, media_id)
        if media is None:
            raise JobError("not_found", "找不到媒體", retryable=False)
        cfg = Config.from_setting(get_setting(s, "pipeline"))
        thr = get_setting(s, "quality_thresholds")
        frames = load_frames(s, media_id)
        if frames is None:
            raise JobError("quality_missing", "尚未產生逐幀品質資料（請先執行 media.quality）", retryable=False)
        primary = ((media.metadata_ or {}).get("download") or {}).get("primary_track") or {}
        track = choose_track(s.scalars(select(Track).where(Track.media_id == media_id)).all(),
                             primary.get("track_id"))
        cues = []
        if track is not None:
            rows = s.scalars(select(Cue).where(Cue.track_id == track.id).order_by(Cue.idx)).all()
            cues = ocr_cues(rows) if track.kind == "ocr" else \
                [CueIn(str(c.id), c.start_sec, c.end_sec, c.text, list(c.flags or [])) for c in rows]
        turns = [(t.local_speaker, t.start_sec, t.end_sec, t.is_overlap) for t in
                 s.scalars(select(DiarizationTurn).where(DiarizationTurn.media_id == media_id)
                           .order_by(DiarizationTurn.start_sec))]
        repeated = [(r.start_sec, r.end_sec) for r in
                    s.scalars(select(RepeatedRegion).where(RepeatedRegion.media_id == media_id,
                                                           RepeatedRegion.excluded.is_(True)))]
        existing = {seg.id: seg for seg in s.scalars(select(Segment).where(Segment.media_id == media_id))}
        protected = [(seg.id, seg.start_sec, seg.end_sec) for seg in existing.values() if _protected(seg)]
    with work_dir("segment") as wd:
        with session_scope() as s:
            audio = load_normalized(s, media_id, wd)
    duration = audio.size / SR
    vad = Vad(frames.brouhaha_vad, frames.hop_sec, duration)

    progress(0.1, "plan")
    if cues:
        plans = plan_from_cues(cues, vad, cfg)
        text_source = TEXT_SOURCE.get(track.kind, "none")
    else:
        plans = plan_from_vad(vad, cfg, turns)
        text_source = "none"
    plans = [p for p in plans if not any(min(p.end, b) - max(p.start, a) > 0 for _, a, b in protected)]

    # quality snapshot for new plans and protected segments; a plan whose row was refined by the alignment
    # (voti.pipeline.align, flag boundary_refined) keeps the refined boundaries
    plan_ids = [segment_id(media_id, p.start, p.end) for p in plans]
    refined = {sid: (seg.start_sec, seg.end_sec) for sid, seg in existing.items()
               if REFINED in (seg.flags or [])}
    spans = [refined.get(sid, (p.start, p.end)) for sid, p in zip(plan_ids, plans)] + \
        [(a, b) for _, a, b in protected]
    progress(0.3, "dnsmos")
    clips = [audio[int(a * SR):int(b * SR)] if b - a >= 1.0 else np.zeros(0, np.float32) for a, b in spans]
    scores = dnsmos.score_clips(clips)
    progress(0.8, "save")

    diarized = bool(turns)
    new_ids = set()
    with session_scope() as s:
        for i, (a, b) in enumerate(spans):
            q = quality_snapshot(a, b, frames, audio)
            d = scores[i]
            if d is not None:
                q.update(dnsmos_sig=finite_or_none(d[0]), dnsmos_bak=finite_or_none(d[1]),
                         dnsmos_ovrl=finite_or_none(d[2]), dnsmos_p808=finite_or_none(d[3]))
            spk, state = speaker_info(a, b, turns, diarized)
            rep = round(min(1.0, covered(a, b, repeated) / (b - a)), 4) if b > a else 0.0
            if i < len(plans):
                p = plans[i]
                sid = plan_ids[i]
                new_ids.add(sid)
                seg = s.get(Segment, sid)
                if seg is None:
                    seg = Segment(id=sid, media_id=media_id, start_sec=a, end_sec=b, original_text=p.text,
                                  revision=1, review_status="unchecked")
                    s.add(seg)
                elif S2T_FLAG in p.flags and S2T_FLAG not in (seg.flags or []):
                    # unprotected row from before the cues were converted: take the converted caption text
                    seg.original_text = p.text
                seg.active = True
                seg.track_id = track.id if (track is not None and cues) else None
                seg.cue_ids = list(dict.fromkeys(p.cue_ids))
                seg.text_source = text_source
                seg.language = track.language if (track is not None and cues) else media.language
                flags = quality_flags(q, b - a, state, cfg, thr) | p.flags | ({REFINED} if sid in refined else set())
                if not p.text and text_source != "none":
                    flags.add("no_text")
            else:
                seg = s.get(Segment, protected[i - len(plans)][0])
                keep = {f for f in (seg.flags or []) if f in ("boundary_adjusted", "boundary_in_speech", REFINED,
                                                             "text_split",
                                                             "music", "caption_speaker_change", "no_text",
                                                             S2T_FLAG)}
                flags = quality_flags(q, b - a, state, cfg, thr) | keep
                if seg.review_status != "unchecked":  # human may have set speaker_state
                    state, spk = seg.speaker_state, seg.local_speaker if seg.local_speaker else spk
            for k, v in q.items():
                setattr(seg, k, v)
            seg.local_speaker = spk[:32] if spk else None
            seg.speaker_state = state
            seg.repeated_overlap = rep
            if rep > 0:
                flags.add("repeated")
            seg.flags = sorted(flags)
        keep_ids = new_ids | {pid for pid, _, _ in protected}
        deactivated = 0
        for sid, seg in existing.items():
            if sid not in keep_ids and seg.active:
                s.get(Segment, sid).active = False
                deactivated += 1
    return {"segments": len(plans), "protected": len(protected), "deactivated": deactivated,
            "text_source": text_source, "track_id": str(track.id) if (track is not None and cues) else None,
            "total_sec": round(sum(p.end - p.start for p in plans), 2),
            "elapsed_sec": round(time.monotonic() - t0, 2)}
