from pathlib import Path
import json
import subprocess

from twcorpus.config import Settings
from twcorpus.core import (create_source, export_corpus, import_local, list_segments,
                           review_segment, statistics)
from twcorpus.db import connect, init_db
from twcorpus.jobs import enqueue, list_jobs, recover, retry, run_one


def fixture(tmp_path):
    audio = tmp_path / "fixture.wav"
    subtitles = tmp_path / "fixture.srt"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                    "-ac", "1", "-ar", "16000", "-y", str(audio)], check=True)
    subtitles.write_text("1\n00:00:00,500 --> 00:00:02,000\n你好，台灣！\n\n"
                         "2\n00:00:02,400 --> 00:00:04,500\nHello, 42\n", encoding="utf-8")
    return Settings(tmp_path / "data"), audio, subtitles


def test_local_loop_review_export_and_version(tmp_path):
    settings, audio, sub = fixture(tmp_path)
    sid = import_local(settings, audio, sub, subtitle_kind="local_provided", license_label="unknown")
    assert import_local(settings, audio, sub, subtitle_kind="local_provided") == sid
    segments = list_segments(settings)
    assert len(segments) == 2
    first = segments[-1]
    review_segment(settings, first["id"], corrected_text="你好，臺灣！", review_status="passed", review_level="fully_corrected")
    new = next(s for s in list_segments(settings) if s["id"] == first["id"])
    assert new["revision"] == 1
    with connect(settings.database) as db:
        db.execute("UPDATE segments SET embedding_status='ready' WHERE id=?", (first["id"],))
    review_segment(settings, first["id"], start_sec=0.4, end_sec=2.1)
    changed = next(s for s in list_segments(settings) if s["id"] == first["id"])
    assert changed["revision"] == 2
    assert changed["embedding_status"] == "pending"
    with connect(settings.database) as db:
        versions = db.execute("SELECT count(*) FROM segment_versions WHERE segment_id=?", (first["id"],)).fetchone()[0]
        assert versions == 2
    output = export_corpus(settings, corrected_only=True)
    records = [json.loads(s) for s in (output / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["text"] == "你好，臺灣！"
    assert records[0]["text_source"] == "human_revision"
    assert (output / records[0]["audio_path"]).is_file()
    assert records[0]["source_id"] == sid
    assert statistics(settings)["reviewed_hours_sum"] > 0


def test_jobs_dedup_and_recovery(tmp_path):
    settings = Settings(tmp_path / "data")
    init_db(settings.database)
    sid = create_source(settings, "youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "測試")
    one = enqueue(settings, "youtube_acquire", sid, {"subtitle_language": "zh", "subtitle_kind": "uploader_provided"})
    assert enqueue(settings, "youtube_acquire", sid) == one
    with connect(settings.database) as db:
        db.execute("UPDATE jobs SET status='running',claimed_at='2000-01-01T00:00:00+00:00' WHERE id=?", (one,))
    assert recover(settings) == 1
    retry(settings, one)
    assert len(list_jobs(settings)) == 1


def test_stats_coverage_and_unacquired(tmp_path):
    settings, audio, sub = fixture(tmp_path)
    import_local(settings, audio, sub)
    create_source(settings, "youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "候選")
    info = statistics(settings)
    assert info["candidate_sources"] == 2
    assert info["acquired_sources"] == 1
    assert info["reviewed_hours_sum"] == 0
    assert info["source_coverage_hours"] <= info["segment_hours_sum"]


def test_lanes_keep_light_jobs_from_waiting_behind_model_jobs(tmp_path):
    from twcorpus.jobs import _claim
    settings = Settings(tmp_path / "data")
    settings.data_dir.mkdir()
    heavy = enqueue(settings, "embedding")
    light = enqueue(settings, "youtube_inspect", payload={"urls": []})
    assert _claim(settings, "light")["id"] == light
    assert _claim(settings, "light") is None
    assert _claim(settings, "heavy")["id"] == heavy


def test_regional_subtitle_tag_matches_audio_language_and_unknown_audio_adopts_it(tmp_path):
    from twcorpus.core import adopt_track_language, add_subtitles, get_source
    settings, _, subtitle = fixture(tmp_path)
    settings.data_dir.mkdir(exist_ok=True)
    sid = create_source(settings, "youtube", "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk", "t",
                        language="unknown")
    assert get_source(settings, sid)["audio_language"] == "unknown"
    add_subtitles(settings, sid, subtitle, "zh-TW", "uploader_provided")
    adopt_track_language(settings, sid, "zh-TW")
    assert get_source(settings, sid)["audio_language"] == "zh"
    adopt_track_language(settings, sid, "en")  # an audio language that is already known is left alone
    assert get_source(settings, sid)["audio_language"] == "zh"
