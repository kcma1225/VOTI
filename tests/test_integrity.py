import csv
from pathlib import Path
import json
import subprocess

import numpy as np
import pytest

from twcorpus import media
from twcorpus.config import Settings
from twcorpus.core import export_corpus, import_local, list_segments, review_segment
from twcorpus.db import connect, now
from twcorpus.jobs import enqueue, override_group, run_clustering, run_one, vectors


def make_audio(path, seconds=6):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-ac", "1", "-ar", "16000", "-y", str(path)], check=True)


def imported(tmp_path, srt):
    audio, sub = tmp_path / "a.wav", tmp_path / "a.srt"
    make_audio(audio)
    sub.write_text(srt, encoding="utf-8")
    settings = Settings(tmp_path / "data")
    sid = import_local(settings, audio, sub, subtitle_kind="local_provided")
    return settings, sid


def put_vector(settings, segment, vector):
    # Controlled test vectors live only in this temporary database.
    with connect(settings.database) as db:
        db.execute("INSERT INTO embeddings(id,segment_id,segment_revision,model_id,vector,dimensions,dtype,device,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (segment["id"] + "e", segment["id"], segment["revision"],
                    "speechbrain/spkrec-ecapa-voxceleb", np.asarray(vector, dtype="float32").tobytes(),
                    len(vector), "float32", "test", now()))
        db.execute("UPDATE segments SET embedding_status='ready' WHERE id=?", (segment["id"],))


SRT = ("1\n00:00:00,200 --> 00:00:01,800\n他說：「好，\"OK\"」\n第二行, 逗號\n\n"
       "2\n00:00:03,000 --> 00:00:05,500\n第二段\n")


def test_text_edit_keeps_embedding_boundary_edit_invalidates(tmp_path):
    settings, _ = imported(tmp_path, SRT)
    segment = list_segments(settings)[-1]
    put_vector(settings, segment, [1, 0, 0])
    review_segment(settings, segment["id"], corrected_text="改字")
    assert segment["id"] in vectors(settings)
    review_segment(settings, segment["id"], end_sec=segment["end_sec"] - 0.1)
    assert segment["id"] not in vectors(settings)


def test_manual_group_survives_new_cluster_run(tmp_path):
    settings, _ = imported(tmp_path, SRT)
    first, second = list_segments(settings)
    put_vector(settings, first, [1, 0, 0])
    put_vector(settings, second, [0.99, 0.01, 0])
    run_clustering(settings, 0.2)
    override_group(settings, first["id"], "人工_A")
    run_clustering(settings, 0.2)
    by_id = {s["id"]: s for s in list_segments(settings)}
    assert by_id[first["id"]]["manual_group"] == "人工_A"
    assert by_id[second["id"]]["candidate_group"] is not None
    assert [s["id"] for s in list_segments(settings, group_id="人工_A")] == [first["id"]]


def test_translated_subtitles_are_not_segmented(tmp_path):
    audio, sub = tmp_path / "b.wav", tmp_path / "b.srt"
    make_audio(audio)
    sub.write_text(SRT, encoding="utf-8")
    with pytest.raises(ValueError, match="同語言"):
        import_local(Settings(tmp_path / "data"), audio, sub, language="nan",
                     subtitle_language="zh", subtitle_kind="translated")


def test_missing_ffmpeg_reports_block(monkeypatch, tmp_path):
    monkeypatch.setattr(media.shutil, "which", lambda tool: None)
    with pytest.raises(media.MediaError, match="FFmpeg"):
        media.probe(tmp_path / "missing.wav")


def test_missing_model_blocks_without_vectors(monkeypatch, tmp_path):
    from twcorpus import speakers
    settings, _ = imported(tmp_path, "1\n00:00:00,000 --> 00:00:03,000\n測試\n")
    def unavailable(*args):
        raise speakers.ModelUnavailable("模型下載或載入受阻：no access")
    monkeypatch.setattr(speakers, "_load_model", unavailable)
    enqueue(settings, "embedding")
    run_one(settings)
    with connect(settings.database) as db:
        job = db.execute("SELECT status FROM jobs").fetchone()
        assert job["status"] == "blocked"
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0


def test_backup_restores_into_new_directory(tmp_path):
    import uuid
    import psycopg
    from twcorpus.maintenance import backup, restore, verify_assets
    settings, _ = imported(tmp_path, SRT)
    saved = backup(settings, tmp_path / "backups")
    other = "r_" + uuid.uuid4().hex[:10]
    init_sql = (Path(__file__).resolve().parents[1] / "db" / "init.sql").read_text(encoding="utf-8")
    with psycopg.connect(settings.database_url, autocommit=True) as pg:
        pg.execute(f"CREATE SCHEMA {other}")
        pg.execute(f"SET search_path={other}")
        pg.execute(init_sql)
    try:
        restored = Settings(saved, db_schema=other)  # assets/ was copied into the backup directory
        assert restore(restored, saved)["segments"] == 2
        assert verify_assets(restored) == {"assets": verify_assets(settings)["assets"], "missing": [], "changed": []}
        assert len(list_segments(restored)) == 2
        with pytest.raises(ValueError, match="不是空的"):
            restore(restored, saved)
    finally:
        with psycopg.connect(settings.database_url, autocommit=True) as pg:
            pg.execute(f"DROP SCHEMA {other} CASCADE")
    first = next((settings.data_dir / "assets").rglob("*.wav"))
    first.write_bytes(b"tampered")
    assert verify_assets(settings)["changed"]

def test_export_snapshot_csv_utf8_and_frozen(tmp_path):
    settings, _ = imported(tmp_path, SRT)
    target = export_corpus(settings)
    review_segment(settings, list_segments(settings)[0]["id"], corrected_text="匯出後修改")
    lines = (target / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line)["corrected_text"] is None for line in lines)
    with (target / "manifest.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert any("「好，\"OK\"」" in row["text"] for row in rows)
    for row in rows:
        assert not row["audio_path"].startswith("/")
        assert (target / row["audio_path"]).is_file()
        assert 0 <= float(row["start_sec"]) < float(row["end_sec"])
