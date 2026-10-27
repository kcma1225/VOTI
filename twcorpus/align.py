"""Word-level timing by CTC forced alignment with torchaudio's MMS_FA model (English and Chinese only).

Each cue is aligned inside its own subtitle window, so timing is only as good as the cue boundaries. Chinese is
romanized per Han character with pypinyin (tones dropped), the way MMS_FA expects uroman-like input. Units the
model cannot spell (digits, punctuation, symbols) are stored untimed rather than given invented times.
"""
from __future__ import annotations

import re
import unicodedata
import urllib.request
import wave
from functools import lru_cache
from pathlib import Path

from .config import Settings
from .core import asset_path, dumps, ensure_normalized, new_id
from .db import connect, now, rows, transaction
from .speakers import ModelUnavailable

MODEL_ID = "torchaudio/MMS_FA"
METHOD = "mms_fa_ctc_forced_alignment"
WEIGHTS_URL = "https://dl.fbaipublicfiles.com/mms/torchaudio/ctc_alignment_mling_uroman/model.pt"
WEIGHTS_FILE = "mms_fa_ctc_alignment_mling_uroman.pt"
WEIGHTS_BYTES = 1_262_000_000  # approximate, only for the UI warning
LANGUAGES = ("en", "zh")
PAD_SEC = 0.25
MAX_CUE_SEC = 60.0

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")
LATIN_WORD = re.compile(r"[A-Za-z\u00c0-\u024f]+(?:['’][A-Za-z\u00c0-\u024f]+)*")
ALPHABET = set("abcdefghijklmnopqrstuvwxyz'")


def base_language(language: str | None) -> str:
    return (language or "").lower().replace("_", "-").split("-")[0]


def supported(language: str | None) -> bool:
    return base_language(language) in LANGUAGES


def _latin(word: str) -> str | None:
    """Strip diacritics and lowercase; None when anything outside a–z and the apostrophe remains."""
    plain = unicodedata.normalize("NFKD", word.replace("’", "'"))
    plain = "".join(c for c in plain if not unicodedata.combining(c)).lower()
    return plain if plain and set(plain) <= ALPHABET and plain.strip("'") else None


def _han(char: str) -> str | None:
    from pypinyin import Style, lazy_pinyin
    spelled = lazy_pinyin(char, style=Style.NORMAL, errors="ignore")
    value = spelled[0].lower() if spelled else ""
    return value if value and set(value) <= ALPHABET else None


def units(text: str, language: str) -> list[dict]:
    """Split text into display units whose concatenation is exactly `text`.

    {"word": ..., "romanized": str | None}; only units with a romanization are aligned."""
    chinese = base_language(language) == "zh"
    found, index, pending = [], 0, ""

    def flush():
        nonlocal pending
        if pending:
            found.append({"word": pending, "romanized": None})
            pending = ""
    while index < len(text):
        char = text[index]
        if chinese and HAN.match(char):
            spelled = _han(char)
            if spelled:
                flush()
                found.append({"word": char, "romanized": spelled})
            else:
                pending += char
            index += 1
            continue
        match = LATIN_WORD.match(text, index)
        if match:
            spelled = _latin(match.group())
            if spelled:
                flush()
                found.append({"word": match.group(), "romanized": spelled})
            else:
                pending += match.group()
            index = match.end()
            continue
        pending += char
        index += 1
    flush()
    return found


def weights_path(settings: Settings) -> Path:
    return settings.data_dir / "models" / "mms_fa" / WEIGHTS_FILE


def weights_cached(settings: Settings) -> bool:
    return weights_path(settings).is_file()


def _download(settings: Settings, progress=None) -> None:
    """Stream the public MMS_FA checkpoint into data/models/mms_fa with real byte progress."""
    target = weights_path(settings)
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    try:
        with urllib.request.urlopen(WEIGHTS_URL, timeout=60) as response, partial.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while block := response.read(1024 * 1024):
                out.write(block)
                done += len(block)
                if progress and total:
                    progress(done / total)
        if total and partial.stat().st_size != total:
            raise OSError("下載不完整")
        partial.replace(target)
    except (OSError, ValueError) as exc:
        raise ModelUnavailable(f"MMS_FA 模型下載受阻：{str(exc)[:300]}") from exc
    finally:
        partial.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def _load(path: str, device: str):
    import torch
    from torchaudio.pipelines import MMS_FA
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ModelUnavailable("CUDA GPU 不可用；請設定 SPEAKER_DEVICE=cpu")
    try:
        target = Path(path)
        model = MMS_FA.get_model(with_star=True, dl_kwargs={"model_dir": str(target.parent),
                                                            "file_name": target.name, "progress": False})
    except Exception as exc:
        raise ModelUnavailable(f"MMS_FA 模型載入失敗：{str(exc)[:300]}") from exc
    return model.to(device), MMS_FA.get_tokenizer(), MMS_FA.get_aligner()


def load_model(settings: Settings, progress=None):
    try:
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
    except Exception as exc:
        raise ModelUnavailable(f"無法載入 PyTorch/torchaudio：{exc}") from exc
    report = progress or (lambda phase, fraction: None)
    _download(settings, lambda f: report("model_download", f))
    report("model_loading", 0.0)
    loaded = _load(str(weights_path(settings)), settings.speaker_device)
    report("model_loading", 1.0)
    return loaded


def _read_window(wav_path: Path, start: float, end: float):
    import numpy as np
    with wave.open(str(wav_path), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        first, last = max(0, int(start * rate)), min(frames, int(end * rate))
        audio.setpos(first)
        data = audio.readframes(max(0, last - first))
    return np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0, first / rate


def align_cue(model, tokenizer, aligner, wav_path: Path, duration: float, cue: dict, language: str,
              device: str = "cpu") -> list[dict]:
    """Returns units with start_sec/end_sec/score set for aligned words, None for untimed ones."""
    import torch
    pieces = units(cue["cleaned_text"], language)
    spoken = [u for u in pieces if u["romanized"]]
    if not spoken:
        raise ValueError("沒有可對齊的英文字或漢字")
    if cue["start_sec"] is None or cue["end_sec"] is None or cue["end_sec"] <= cue["start_sec"]:
        raise ValueError("字幕時間無效")
    if cue["end_sec"] - cue["start_sec"] > MAX_CUE_SEC:
        raise ValueError(f"字幕句超過 {MAX_CUE_SEC:.0f} 秒")
    samples, offset = _read_window(wav_path, max(0.0, cue["start_sec"] - PAD_SEC), min(duration, cue["end_sec"] + PAD_SEC))
    if samples.size < 1600:
        raise ValueError("音訊視窗過短")
    with torch.inference_mode():
        emission, _ = model(torch.from_numpy(samples).unsqueeze(0).to(device))
    # Star tokens at both ends absorb the padding and any speech the cue does not transcribe.
    tokens = tokenizer(["*"] + [u["romanized"] for u in spoken] + ["*"])
    if sum(len(t) for t in tokens) > emission.size(1):
        raise ValueError("文字多於音訊可容納的長度；字幕時間可能有誤")
    spans = aligner(emission[0].cpu(), tokens)[1:-1]
    ratio = samples.size / emission.size(1) / 16000
    for unit, word in zip(spoken, spans):
        unit["start_sec"] = round(offset + word[0].start * ratio, 3)
        unit["end_sec"] = round(offset + word[-1].end * ratio, 3)
        length = sum(s.end - s.start for s in word)
        unit["score"] = round(sum(s.score * (s.end - s.start) for s in word) / length, 4) if length else None
    return pieces


def align_track(settings: Settings, track_id: str, source_id: str | None = None, progress=None) -> dict:
    report = progress or (lambda phase, fraction: None)
    with connect(settings.database) as db:
        track = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        if not track or (source_id and track["source_id"] != source_id):
            raise ValueError("找不到字幕軌")
        source = db.execute("SELECT * FROM sources WHERE id=?", (track["source_id"],)).fetchone()
        cues = rows(db, "SELECT * FROM cues WHERE track_id=? ORDER BY ordinal", (track_id,))
    if track["kind"] == "translated" or base_language(track["language"]) != base_language(source["audio_language"]):
        raise ValueError("字幕不是音訊同語言轉寫，不做字詞對齊")
    if not supported(track["language"]):
        raise ValueError("字詞對齊目前只支援英文與中文")
    normalized = ensure_normalized(settings, track["source_id"])
    wav_path = asset_path(settings, normalized["path"])
    with wave.open(str(wav_path), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    model, tokenizer, aligner = load_model(settings, report)
    aligned, failed = 0, []
    report("aligning", 0.0)
    for index, cue in enumerate(cues):
        try:
            pieces = align_cue(model, tokenizer, aligner, wav_path, duration, cue, track["language"],
                               settings.speaker_device)
        except ValueError as exc:
            failed.append(f"#{cue['ordinal']} {exc}")
            pieces = None
        if pieces is not None:
            timestamp = now()
            with connect(settings.database) as db, transaction(db):
                db.execute("DELETE FROM word_alignments WHERE cue_id=? AND model_id=?", (cue["id"], MODEL_ID))
                for ordinal, unit in enumerate(pieces):
                    db.execute("INSERT INTO word_alignments(id,track_id,cue_id,ordinal,word,romanized,start_sec,end_sec,"
                               "score,method,model_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (new_id(), track_id, cue["id"], ordinal, unit["word"], unit["romanized"],
                                unit.get("start_sec"), unit.get("end_sec"), unit.get("score"), METHOD, MODEL_ID,
                                timestamp))
            aligned += 1
        report("aligning", (index + 1) / len(cues))
    return {"aligned": aligned, "failed": failed, "params": dumps({"pad_sec": PAD_SEC, "star_padding": True})}


def track_words(settings: Settings, track_id: str) -> dict[str, list[dict]]:
    """cue_id -> ordered units; cues never aligned are absent."""
    with connect(settings.database) as db:
        found = rows(db, "SELECT cue_id,word,romanized,start_sec,end_sec,score FROM word_alignments "
                     "WHERE track_id=? AND model_id=? ORDER BY cue_id,ordinal", (track_id, MODEL_ID))
    words: dict[str, list[dict]] = {}
    for unit in found:
        words.setdefault(unit.pop("cue_id"), []).append(unit)
    return words
