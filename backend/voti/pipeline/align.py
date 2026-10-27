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


def run(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None,
        segment_ids: Sequence[str] | None = None, force: bool = False, pending_only: bool = False) -> dict:
    from .gpu import OomFallback, free_cuda
    t0 = time.monotonic()
    stats = {"aligned": 0, "skipped": 0, "failed": 0, "reasons": {}}
    done: set = set()
    audio = None
    fallback: OomFallback | None = None
    rounds = 0
    try:
        with work_dir("align") as wd:
            todo = select_todo(media_id, segment_ids, force, pending_only)
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
