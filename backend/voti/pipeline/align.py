"""`media.align`: character-level timing of segment text by CTC forced alignment (torchaudio MMS_FA).

For the media page's karaoke highlighting: every segment's text (corrected_text if set, else original_text) is
split into display units whose concatenation is exactly the text:
  * Han characters — one unit each, romanized with pypinyin (toneless, phrase-aware per run of Han characters;
    ü → v), the uroman-like input MMS_FA expects;
  * Latin words — one unit per word (diacritics stripped, lower-cased);
  * digit / other alphanumeric runs (spoken but not spellable: "2024", "ＡＩ") — one unit aligned against a
    `*` (star) token, which absorbs whatever is said there; its time is the star span (score null);
  * punctuation, spaces, symbols — untimed units (null times), merged with neighbouring untimed characters.
Each segment is aligned inside its own window [start − PAD_SEC, end + PAD_SEC] with star tokens at both ends
(they absorb the padding and untranscribed speech). Segments longer than MAX_SEC are not aligned.

Boundary refinement: the caption timing (snapped to VAD by voti.pipeline.segment) often cuts the first or last
word — subtitles change while the speaker keeps talking, and burned-in subtitles disappear early. A segment whose
aligned speech runs past one of its edges by more than EDGE_TOL is re-aligned in a window widened by REACH_SEC
(never past its neighbours), and the edge moves out to the aligned speech + EDGE_PAD_SEC (at most REACH_SEC; only
outward). A neighbour that touches the widened edge gives way: both are cut at the quietest point between the
neighbour's last (first) aligned unit and this segment's first (last) one; when their aligned speech overlaps,
nothing moves. Human-touched (protected) segments never move and are never cut into. Moved segments get the flag
boundary_refined and a fresh quality snapshot (SNR/C50/speech ratio/loudness/clipping; DNSMOS is kept); their
revision is unchanged, so embeddings / voice profiles stay valid (a < 1 s edge move barely changes them).
Payload {"refine_only": true}: no re-alignment of current rows — only the refinement (backfill).

Languages: zh*/cmn* (Mandarin Han text) and en*. Taiwanese (nan*) / Hakka (hak*) Han text has no usable
romanizer for this model — such segments are stored `skipped` with the reason (the UI falls back to sentence
highlighting); so are other languages and segments without text.

Result: one `word_alignments` row per segment (see voti.models.WordAlignment), valid while its revision and
text_hash match the segment. Model: torchaudio.pipelines.MMS_FA (with star), weights downloaded once into
MODEL_CACHE/mms_fa (~1.2 GB). torchaudio 2.8 warns that `forced_align` is deprecated (it moves to TorchCodec /
a separate package in 2.9); the call still works on 2.8.
GPU when available (voti.devices); CUDA OOM → cache emptied, one GPU retry, then CPU for the job
(voti.pipeline.gpu); the CUDA cache is emptied after the job.

Job payload: {} (pipeline / backfill: every active segment without a valid alignment), {"segment_ids": [...]}
(after an edit: those + any pending/stale ones), {"pending_only": true}, {"force": true} (re-align all),
{"chain": false} (not part of the pipeline chain: Media.status is never touched).
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..config import get_config
from ..db import session_scope
from ..models import Segment, WordAlignment, utcnow
from ..queue import JobError
from .common import SR, load_normalized, work_dir

log = logging.getLogger("voti.pipeline.align")

MODEL_ID = "torchaudio/MMS_FA"
WEIGHTS_FILE = "mms_fa_ctc_alignment_mling_uroman.pt"
PAD_SEC = 0.2
EDGE_TOL = 0.05        # aligned speech beyond a boundary by more than this → refine
EDGE_PAD_SEC = 0.1     # kept around the aligned speech
REACH_SEC = 0.8        # largest boundary move
REFINED = "boundary_refined"
MAX_SEC = 60.0
MIN_SAMPLES = int(0.1 * SR)
WRITE_EVERY = 50
ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz'")
STAR = "*"

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")
LATIN_WORD = re.compile(r"[A-Za-zÀ-ɏ]+(?:['’][A-Za-zÀ-ɏ]+)*")
SPOKEN_OTHER = re.compile(r"[0-9０-９Ａ-Ｚａ-ｚ]+(?:[.,][0-9０-９]+)*%?")

_lock = threading.Lock()


# ───────────────────────────── text → units (pure) ─────────────────────────────

def segment_text(seg) -> str:  # noqa: ANN001
    return seg.corrected_text if seg.corrected_text is not None else (seg.original_text or "")


def text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def base_language(language: str | None) -> str:
    return (language or "").lower().replace("_", "-").split("-")[0]


def language_support(language: str | None, text: str = "") -> tuple[str | None, str | None]:
    """→ (mode "zh"|"en", None) when alignable, else (None, reason)."""
    base = base_language(language)
    if base in ("nan", "hak", "tw"):
        return None, f"no_romanizer:{language}"  # Taiwanese / Hakka Han text: pinyin would be wrong
    if base in ("zh", "cmn"):
        return "zh", None
    if base == "en":
        return "en", None
    if not base and HAN.search(text or ""):
        return "zh", None
    return None, f"unsupported_language:{language or 'unknown'}"


@dataclass
class Unit:
    text: str
    token: str | None = None   # romanized word, STAR, or None (untimed)
    start: float | None = None
    end: float | None = None
    score: float | None = None


def latin(word: str) -> str | None:
    """Strip diacritics and lowercase; None when anything outside a–z and the apostrophe remains."""
    plain = unicodedata.normalize("NFKD", word.replace("’", "'"))
    plain = "".join(c for c in plain if not unicodedata.combining(c)).lower()
    return plain if plain and set(plain) <= ALPHABET and plain.strip("'") else None


def _simplified(run: str) -> str:
    """Traditional → Simplified (OpenCC t2s) when it keeps the length: pypinyin's phrase dictionary (polyphones
    such as 銀行 yin hang, 重新 chong xin) is keyed by Simplified text."""
    try:
        from ..textconv import converter
        out = converter("t2s").convert(run)
    except Exception:  # noqa: BLE001 - opencc missing: per-character readings still work
        return run
    return out if len(out) == len(run) else run


def han_romanize(run: str) -> list[str | None]:
    """Toneless pinyin per character of a run of Han characters (phrase-aware: 銀行 → yin hang)."""
    from pypinyin import Style, lazy_pinyin
    out = lazy_pinyin(_simplified(run), style=Style.NORMAL, errors=lambda chars: [None] * len(chars),
                      v_to_u=False)
    if len(out) != len(run):  # defensive: fall back to one call per character
        out = [(lazy_pinyin(c, style=Style.NORMAL, errors=lambda x: [None], v_to_u=False) or [None])[0]
               for c in run]

    def ok(p):  # noqa: ANN001, ANN202
        p = (p or "").lower()
        return p if p and set(p) <= ALPHABET else None
    return [ok(p) for p in out]


def units(text: str, mode: str) -> list[Unit]:
    """Split text into display units; "".join(u.text) == text. mode: "zh" (Han + Latin) or "en" (Latin)."""
    found: list[Unit] = []
    pending = ""

    def flush() -> None:
        nonlocal pending
        if pending:
            found.append(Unit(pending))
            pending = ""

    i = 0
    while i < len(text):
        ch = text[i]
        if mode == "zh" and HAN.match(ch):
            j = i
            while j < len(text) and HAN.match(text[j]):
                j += 1
            for c, roman in zip(text[i:j], han_romanize(text[i:j])):
                if roman:
                    flush()
                    found.append(Unit(c, roman))
                else:
                    pending += c
            i = j
            continue
        m = LATIN_WORD.match(text, i)
        if m:
            spelled = latin(m.group())
            flush()
            found.append(Unit(m.group(), spelled or STAR))
            i = m.end()
            continue
        m = SPOKEN_OTHER.match(text, i)
        if m:
            flush()
            found.append(Unit(m.group(), STAR))
            i = m.end()
            continue
        pending += ch
        i += 1
    flush()
    # adjacent star units would compete for the same speech: merge them
    merged: list[Unit] = []
    for u in found:
        if merged and u.token == STAR and merged[-1].token == STAR:
            merged[-1].text += u.text
        elif merged and u.token == STAR and merged[-1].token is None and len(merged) >= 2 and \
                merged[-2].token == STAR and not merged[-1].text.strip():
            gap = merged.pop()  # "10 20" style: star + blank + star → one star unit
            merged[-1].text += gap.text + u.text
        else:
            merged.append(u)
    return merged


def units_out(us: Sequence[Unit]) -> list[list]:
    return [[u.text, _r(u.start), _r(u.end), None if u.score is None else round(float(u.score), 4)] for u in us]


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 3)


def assign_times(us: list[Unit], spans: Sequence[tuple[float, float, float | None]], offset: float) -> None:
    """spans: per aligned unit (token not None, in order) (start, end, score) relative to the window start."""
    aligned = [u for u in us if u.token is not None]
    if len(aligned) != len(spans):
        raise ValueError(f"alignment returned {len(spans)} spans for {len(aligned)} units")
    for u, (a, b, sc) in zip(aligned, spans):
        u.start, u.end = offset + a, offset + max(a, b)
        u.score = None if u.token == STAR else sc


# ───────────────────────────── model ─────────────────────────────

def weights_path() -> Path:
    return Path(get_config().model_cache) / "mms_fa" / WEIGHTS_FILE


class MmsAligner:
    """torchaudio MMS_FA with star token. align(samples, tokens) → [(start_s, end_s, score)] per token word."""

    def __init__(self, device: str):
        import torch
        import torchaudio  # noqa: F401
        from torchaudio.pipelines import MMS_FA
        target = weights_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.model = MMS_FA.get_model(with_star=True, dl_kwargs={"model_dir": str(target.parent),
                                                                 "file_name": target.name, "progress": False})
        self.model = self.model.to(torch.device(device)).eval()
        self.tokenizer = MMS_FA.get_tokenizer()
        self.aligner = MMS_FA.get_aligner()
        self.device = device

    def align(self, samples: np.ndarray, words: list[str]) -> list[tuple[float, float, float | None]]:
        import torch
        with torch.inference_mode():
            emission, _ = self.model(torch.from_numpy(np.ascontiguousarray(samples, np.float32))
                                     .unsqueeze(0).to(self.device))
        emission = emission[0].float().cpu()
        frames = emission.size(0)
        tokens = self.tokenizer([STAR] + list(words) + [STAR])
        if sum(len(t) for t in tokens) > frames:
            raise ValueError("text_longer_than_audio")
        spans = self.aligner(emission, tokens)[1:-1]
        ratio = samples.size / frames / SR
        out = []
        for word in spans:
            length = sum(sp.end - sp.start for sp in word)
            score = sum(sp.score * (sp.end - sp.start) for sp in word) / length if length else None
            out.append((word[0].start * ratio, word[-1].end * ratio, score))
        return out


@lru_cache(maxsize=2)
def load_aligner(device: str):
    try:
        return MmsAligner(device)
    except ImportError as e:
        raise JobError("align_unavailable", f"此 worker 未安裝 torchaudio：{e}", blocked=True) from e
    except Exception as e:  # noqa: BLE001
        from ..queue import is_transient
        from .gpu import is_cuda_oom
        if is_cuda_oom(e):
            raise
        if is_transient(e) or isinstance(e, OSError):
            raise JobError("align_model_unavailable", f"MMS_FA 模型下載失敗（稍後重試）：{str(e)[:300]}",
                           retryable=True) from e
        raise JobError("align_model_unavailable", f"MMS_FA 模型載入失敗：{str(e)[:300]}", blocked=True) from e


# ───────────────────────────── job ─────────────────────────────

@dataclass
class SegIn:
    id: uuid.UUID
    revision: int
    start: float
    end: float
    text: str
    language: str | None


def _valid(row: WordAlignment | None, seg: Segment) -> bool:
    return bool(row is not None and row.status in ("aligned", "skipped", "failed") and row.revision == seg.revision
                and row.text_hash == text_hash(segment_text(seg)))


def _stale(row: WordAlignment | None, seg: Segment) -> bool:
    """A row exists but no longer matches (edit) or a re-alignment was requested."""
    return row is not None and (row.status == "pending" or not _valid(row, seg))


def select_todo(media_id: uuid.UUID, segment_ids: Sequence[str] | None = None, force: bool = False,
                pending_only: bool = False, done: set | None = None) -> list[SegIn]:
    done = done or set()
    want = {uuid.UUID(str(x)) for x in (segment_ids or [])}
    with session_scope() as s:
        segs = s.scalars(select(Segment).where(Segment.media_id == media_id, Segment.active.is_(True))
                         .order_by(Segment.start_sec)).all()
        rows = {r.segment_id: r for r in s.scalars(select(WordAlignment).where(WordAlignment.media_id == media_id))}
        out = []
        for g in segs:
            row = rows.get(g.id)
            if force:
                pick = True
            elif pending_only or want:
                pick = (g.id in want and not _valid(row, g)) or _stale(row, g)
            else:
                pick = not _valid(row, g)
            key = (g.id, g.revision, text_hash(segment_text(g)))
            if pick and key not in done:
                out.append(SegIn(g.id, g.revision or 1, g.start_sec, g.end_sec, segment_text(g), g.language))
        return out


def _write(media_id: uuid.UUID, results: list[dict]) -> None:
    if not results:
        return
    now = utcnow()
    with session_scope() as s:
        for r in results:
            stmt = insert(WordAlignment).values(media_id=media_id, created_at=now, **r)
            s.execute(stmt.on_conflict_do_update(
                index_elements=["segment_id"],
                set_={k: stmt.excluded[k] for k in ("media_id", "revision", "text_hash", "status", "reason",
                                                    "model_id", "units", "created_at")}))


def _plan(g: SegIn) -> tuple[list[Unit] | None, dict | None]:
    """→ (units to align, None) or (None, final row for a skipped/failed segment)."""
    base = {"segment_id": g.id, "revision": g.revision, "text_hash": text_hash(g.text), "model_id": MODEL_ID}
    if not (g.text or "").strip():
        return None, {**base, "status": "skipped", "reason": "no_text", "units": []}
    mode, reason = language_support(g.language, g.text)
    if mode is None:
        return None, {**base, "status": "skipped", "reason": reason, "units": [[g.text, None, None, None]]}
    us = units(g.text, mode)
    if not any(u.token not in (None, STAR) for u in us):
        return None, {**base, "status": "skipped", "reason": "nothing_to_align", "units": units_out(us)}
    if g.end - g.start > MAX_SEC:
        return None, {**base, "status": "failed", "reason": f"too_long:{g.end - g.start:.0f}s", "units": units_out(us)}
    return us, None


# ───────────────────────────── boundary refinement ─────────────────────────────

@dataclass
class Edge:
    """A segment for refine_bounds: boundaries and the absolute first / last aligned (timed) unit time."""
    start: float
    end: float
    first: float | None
    last: float | None
    protected: bool = False
    candidate: bool = False       # re-aligned in a widened window (first/last come from that alignment)


def speech_span(units_rows: Sequence[Sequence] | None) -> tuple[float | None, float | None]:
    """[[unit, start, end, score], ...] → (first start, last end) over scored units (star units are not speech
    evidence: they also absorb noise and neighbouring speech)."""
    t = [(float(u[1]), float(u[2])) for u in (units_rows or []) if len(u) > 3 and u[1] is not None
         and u[2] is not None and u[3] is not None]
    return (t[0][0], t[-1][1]) if t else (None, None)


def needs_refine(start: float, end: float, first: float | None, last: float | None) -> bool:
    return first is not None and last is not None and (first < start - EDGE_TOL or last > end + EDGE_TOL)


def refine_bounds(segs: Sequence[Edge], quiet: Callable[[float, float], float]) -> list[tuple[float, float]]:
    """segs in time order → new (start, end) per segment (pure). quiet(a, b): the quietest time in [a, b]."""
    new = [[g.start, g.end] for g in segs]
    for i, g in enumerate(segs):
        if not g.candidate or g.protected or g.first is None or g.last is None:
            continue
        if g.first < g.start - EDGE_TOL:
            want: float | None = max(0.0, g.first - EDGE_PAD_SEC, g.start - REACH_SEC)
            if i > 0 and new[i - 1][1] > want:
                p = segs[i - 1]
                if p.protected:  # never cut into a human-checked neighbour
                    want = max(want, new[i - 1][1])
                elif p.last is None or p.last >= g.first:  # unknown / overlapping speech: leave it
                    want = None
                else:
                    c = quiet(max(p.last, want), g.first)
                    want = c
                    new[i - 1][1] = min(new[i - 1][1], c)
            if want is not None and want < new[i][0]:
                new[i][0] = want
        if g.last > g.end + EDGE_TOL:
            want = min(g.last + EDGE_PAD_SEC, g.end + REACH_SEC)
            if i + 1 < len(segs) and new[i + 1][0] < want:
                n = segs[i + 1]
                if n.protected:
                    want = min(want, new[i + 1][0])
                elif n.first is None or n.first <= g.last:
                    want = None
                else:
                    c = quiet(g.last, min(n.first, want))
                    want = c
                    new[i + 1][0] = max(new[i + 1][0], c)
            if want is not None and want > new[i][1]:
                new[i][1] = want
    return [(round(a, 3), round(b, 3)) for a, b in new]


def quietest(audio: np.ndarray, a: float, b: float, hop: float = 0.01, win: float = 0.03) -> float:
    """Time of the lowest short-time energy in [a, b] (midpoint when the gap is shorter than one window)."""
    if b - a < win + hop:
        return (a + b) / 2
    best, best_t = None, (a + b) / 2
    n, h = int(win * SR), int(hop * SR)
    i0, i1 = int(a * SR), int(b * SR) - n
    for i in range(i0, max(i0 + 1, i1 + 1), h):
        x = audio[i:i + n]
        if x.size == 0:
            break
        e = float(np.mean(x.astype(np.float64) ** 2))
        if best is None or e < best:
            best, best_t = e, (i + n / 2) / SR
    return best_t


def refine(media_id: uuid.UUID, audio: np.ndarray, align_fn: Callable[[np.ndarray, list[str]], list],
           only_ids: set | None = None) -> dict:
    """Refine the boundaries of segments whose aligned speech runs past an edge (see module doc). only_ids:
    consider just these (segments aligned in this job); None = every segment of the media."""
    from .segment import _protected, quality_snapshot
    from .common import load_frames
    duration = audio.size / SR
    with session_scope() as s:
        segs = s.scalars(select(Segment).where(Segment.media_id == media_id, Segment.active.is_(True))
                         .order_by(Segment.start_sec)).all()
        rows = {r.segment_id: r for r in s.scalars(select(WordAlignment).where(WordAlignment.media_id == media_id))}
        info = []
        for g in segs:
            row = rows.get(g.id)
            ok = row is not None and row.status == "aligned" and _valid(row, g)
            first, last = speech_span(row.units) if ok else (None, None)
            info.append(dict(id=g.id, revision=g.revision or 1, text=segment_text(g), language=g.language,
                             edge=Edge(g.start_sec, g.end_sec, first, last, _protected(g))))
    stats = {"candidates": 0, "refined": 0, "neighbours": 0}
    wide: dict = {}
    for i, d in enumerate(info):
        e = d["edge"]
        if e.protected or (only_ids is not None and d["id"] not in only_ids) or \
                not needs_refine(e.start, e.end, e.first, e.last):
            continue
        mode, _ = language_support(d["language"], d["text"])
        if mode is None:
            continue
        us = units(d["text"], mode)
        a = max(0.0, e.start - REACH_SEC - PAD_SEC, info[i - 1]["edge"].start if i else 0.0)
        b = min(duration, e.end + REACH_SEC + PAD_SEC, info[i + 1]["edge"].end if i + 1 < len(info) else duration)
        samples = audio[int(a * SR):int(b * SR)]
        try:
            spans = align_fn(samples, [u.token for u in us if u.token is not None])
            assign_times(us, spans, a)
        except ValueError:
            continue
        rows_out = units_out(us)
        first, last = speech_span(rows_out)
        if first is None:
            continue
        stats["candidates"] += 1
        e.first, e.last, e.candidate = first, last, True
        wide[d["id"]] = rows_out
    if not wide:
        return stats
    new = refine_bounds([d["edge"] for d in info], lambda x, y: quietest(audio, x, y))
    with session_scope() as s:
        frames = load_frames(s, media_id)
        changed = {info[i]["id"]: (i, ab) for i, ab in enumerate(new)
                   if abs(ab[0] - info[i]["edge"].start) > 1e-3 or abs(ab[1] - info[i]["edge"].end) > 1e-3}
        locked = {g.id: g for g in s.scalars(select(Segment).where(Segment.id.in_(list(changed) + list(wide)))
                                             .with_for_update())}
        for sid, (i, (a, b)) in changed.items():
            g, d = locked.get(sid), info[i]
            if g is None or not g.active or _protected(g) or (g.revision or 1) != d["revision"] or \
                    segment_text(g) != d["text"] or b - a < 0.05:
                continue
            g.start_sec, g.end_sec = a, b
            if frames is not None:
                for k, v in quality_snapshot(a, b, frames, audio).items():
                    setattr(g, k, v)
            if sid in wide:
                g.flags = sorted(set(g.flags or []) | {REFINED})
                stats["refined"] += 1
            else:
                stats["neighbours"] += 1
        for sid, us in wide.items():
            g, d = locked.get(sid), next(x for x in info if x["id"] == sid)
            if g is None or (g.revision or 1) != d["revision"] or segment_text(g) != d["text"]:
                continue
            stmt = insert(WordAlignment).values(segment_id=sid, media_id=media_id, revision=d["revision"],
                                                text_hash=text_hash(d["text"]), status="aligned", reason=None,
                                                model_id=MODEL_ID, units=us, created_at=utcnow())
            s.execute(stmt.on_conflict_do_update(
                index_elements=["segment_id"],
                set_={k: stmt.excluded[k] for k in ("revision", "text_hash", "status", "reason", "units",
                                                    "created_at")}))
    return stats


def run(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None,
        segment_ids: Sequence[str] | None = None, force: bool = False, pending_only: bool = False,
        refine_only: bool = False) -> dict:
    from .gpu import OomFallback, free_cuda
    t0 = time.monotonic()
    stats = {"aligned": 0, "skipped": 0, "failed": 0, "reasons": {}}
    done: set = set()
    audio = None
    fallback: OomFallback | None = None
    rounds = 0
    try:
        with work_dir("align") as wd:
            todo = [] if refine_only else select_todo(media_id, segment_ids, force, pending_only)
            while todo and rounds < 4:
                rounds += 1
                results: list[dict] = []
                planned = []
                for g in todo:
                    us, final = _plan(g)
                    done.add((g.id, g.revision, text_hash(g.text)))
                    if final is not None:
                        results.append(final)
                        stats[final["status"]] += 1
                        key = final["reason"].split(":")[0]
                        stats["reasons"][key] = stats["reasons"].get(key, 0) + 1
                    else:
                        planned.append((g, us))
                _write(media_id, results)
                results = []
                if planned:
                    if audio is None:
                        with session_scope() as s:
                            audio = load_normalized(s, media_id, wd)
                    if fallback is None:
                        fallback = OomFallback("MMS_FA 字詞對齊")
                        progress(0.05, "load_model")
                        fallback.call(load_aligner)  # download / load once (blocked/retryable errors surface here)
                    duration = audio.size / SR
                    for i, (g, us) in enumerate(planned):
                        a = max(0.0, g.start - PAD_SEC)
                        b = min(duration, g.end + PAD_SEC)
                        samples = audio[int(a * SR):int(b * SR)]
                        row = {"segment_id": g.id, "revision": g.revision, "text_hash": text_hash(g.text),
                               "model_id": MODEL_ID}
                        try:
                            if samples.size < MIN_SAMPLES:
                                raise ValueError("audio_too_short")
                            words = [u.token for u in us if u.token is not None]
                            with _lock:
                                spans = fallback.call(lambda dev: load_aligner(dev).align(samples, words))
                            assign_times(us, spans, a)
                            row.update(status="aligned", reason=None, units=units_out(us))
                            stats["aligned"] += 1
                        except ValueError as e:
                            row.update(status="failed", reason=str(e)[:200], units=units_out(us))
                            stats["failed"] += 1
                            stats["reasons"][str(e)[:40]] = stats["reasons"].get(str(e)[:40], 0) + 1
                        results.append(row)
                        if len(results) >= WRITE_EVERY:
                            _write(media_id, results)
                            results = []
                        progress(0.1 + 0.85 * (i + 1) / len(planned), "align")
                    _write(media_id, results)
                # edits that arrived while we were running (pending / stale rows) are picked up here
                todo = select_todo(media_id, None, False, True, done)
            aligned_ids = None if refine_only else {k[0] for k in done}
            if aligned_ids is None or aligned_ids:
                if audio is None:
                    with session_scope() as s:
                        audio = load_normalized(s, media_id, wd)
                if fallback is None:
                    fallback = OomFallback("MMS_FA 字詞對齊")
                    fallback.call(load_aligner)

                def wide_align(samples: np.ndarray, words: list[str]) -> list:
                    if samples.size < MIN_SAMPLES:
                        raise ValueError("audio_too_short")
                    with _lock:
                        return fallback.call(lambda dev: load_aligner(dev).align(samples, words))
                progress(0.97, "refine")
                stats["refine"] = refine(media_id, audio, wide_align, aligned_ids)
    finally:
        free_cuda()
    stats.update(rounds=rounds, device=fallback.device if fallback else None,
                 cpu_fallback=bool(fallback and fallback.fell_back), model=MODEL_ID,
                 elapsed_sec=round(time.monotonic() - t0, 2))
    return stats


def mark_pending(s, seg: Segment) -> None:  # noqa: ANN001
    """Called by the API after a text / boundary edit (same transaction): the segment needs re-alignment."""
    row = s.get(WordAlignment, seg.id)
    if row is None:
        s.add(WordAlignment(segment_id=seg.id, media_id=seg.media_id, revision=seg.revision or 1,
                            text_hash=text_hash(segment_text(seg)), status="pending", model_id=MODEL_ID, units=[]))
    else:
        row.status, row.reason, row.created_at = "pending", None, utcnow()
