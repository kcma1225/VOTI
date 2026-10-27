import shutil
import subprocess
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from twcorpus import align
from twcorpus.config import Settings
from twcorpus.db import connect, init_db
from twcorpus.jobs import Reporter, enqueue, estimate


@pytest.mark.parametrize("text,language,spelled", [
    ("我用iPhone拍，2024年", "zh-TW", ["wo", "yong", "iphone", "pai", "nian"]),
    ("Don’t stop — café 42!", "en", ["don't", "stop", "cafe"]),
    ("綠色", "zh", ["lv", "se"]),
    ("你好 hello", "en", ["hello"]),  # Han characters are not romanized for an English track
])
def test_units_rebuild_text_and_only_romanize_spoken_words(text, language, spelled):
    found = align.units(text, language)
    assert "".join(u["word"] for u in found) == text
    assert [u["romanized"] for u in found if u["romanized"]] == spelled
    assert all(set(u["romanized"]) <= align.ALPHABET for u in found if u["romanized"])


def test_supported_languages():
    assert align.supported("zh-Hant") and align.supported("en_US") and align.supported("zh")
    assert not align.supported("nan") and not align.supported(None)


def test_estimate_is_linear_and_only_for_running_jobs():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = {"status": "running", "claimed_at": started.isoformat(), "progress_current": 250, "progress_total": 1000}
    timing = estimate(job, at=started + timedelta(seconds=30))
    assert timing["fraction"] == 0.25 and timing["elapsed_sec"] == 30 and timing["eta_sec"] == pytest.approx(90)
    # Too little progress or time: no guess rather than a wild one.
    assert estimate({**job, "progress_current": 10}, at=started + timedelta(seconds=30))["eta_sec"] is None
    assert estimate({**job, "status": "queued", "claimed_at": None})["eta_sec"] is None


def test_reporter_throttles_writes_and_honours_cancel(tmp_path):
    settings = Settings(tmp_path / "data")
    init_db(settings.database)
    job_id = enqueue(settings, "embedding")
    report = Reporter(settings, job_id)
    report.set("embedding", 100, force=True)
    report.set("embedding", 200)  # within 0.7 s of the last write: skipped
    with connect(settings.database) as db:
        row = db.execute("SELECT phase,progress_current,progress_total FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert (row["phase"], row["progress_current"], row["progress_total"]) == ("embedding", 100, 1000)
    with connect(settings.database) as db:
        db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
    with pytest.raises(InterruptedError):
        report.set("aligning", 300)  # phase change forces a write, and every write checks for cancel


def _say(text: str, voice: str, target):
    subprocess.run(["say", "-v", voice, "-o", str(target), "--data-format=LEI16@16000", text], check=True)


@pytest.mark.skipif(shutil.which("say") is None or shutil.which("ffmpeg") is None, reason="needs macOS say + ffmpeg")
def test_real_alignment_with_synthesized_speech(tmp_path):
    """End-to-end with the real MMS_FA weights on locally synthesized speech (no network, no Desktop media)."""
    settings = Settings(tmp_path / "data")
    cached = align.weights_path(Settings(Path(__file__).resolve().parents[1] / "data"))
    if not cached.is_file():
        pytest.skip("MMS_FA weights not downloaded")
    settings.data_dir.mkdir()
    (settings.data_dir / "models" / "mms_fa").mkdir(parents=True)
    (settings.data_dir / "models" / "mms_fa" / align.WEIGHTS_FILE).symlink_to(cached.resolve())
    from twcorpus.core import import_local
    audio = tmp_path / "speech.wav"
    _say("The quick brown fox jumps over the lazy dog.", "Samantha", audio)
    with wave.open(str(audio)) as w:
        length = w.getnframes() / w.getframerate()
    srt = tmp_path / "speech.srt"
    srt.write_text(_srt(length, "The quick brown fox jumps over the lazy dog."), encoding="utf-8")
    sid = import_local(settings, audio, srt, title="tts", language="en", subtitle_language="en",
                       subtitle_kind="local_provided", speaker_count="single", taiwan_basis="unknown",
                       license_label="unknown", license_evidence="")
    with connect(settings.database) as db:
        track = db.execute("SELECT id FROM tracks WHERE source_id=?", (sid,)).fetchone()["id"]
    result = align.align_track(settings, track, sid)
    assert result["aligned"] == 1 and not result["failed"]
    words = next(iter(align.track_words(settings, track).values()))
    timed = [w for w in words if w["start_sec"] is not None]
    assert [w["word"] for w in timed] == "The quick brown fox jumps over the lazy dog".split()
    starts = [w["start_sec"] for w in timed]
    assert starts == sorted(starts) and 0 <= starts[0] < starts[-1] <= length
    assert all(w["end_sec"] > w["start_sec"] for w in timed)


def _srt(length: float, text: str) -> str:
    whole, frac = divmod(length, 1)
    minutes, seconds = divmod(int(whole), 60)
    return f"1\n00:00:00,000 --> 00:{minutes:02d}:{seconds:02d},{int(frac * 1000):03d}\n{text}\n"
