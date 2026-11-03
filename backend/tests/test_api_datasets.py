"""Dataset filters, preview, CRUD, and the dataset.export handler end-to-end (synthetic normalized.wav)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import tarfile
import wave

import numpy as np
import pytest
from sqlalchemy import select

from test_api_segments import SR, upload_wav

GOOD = dict(snr=20.0, dnsmos_ovrl=3.2, speech_ratio=0.9, speaker_state="single")


@pytest.fixture
def ds_corpus(factory, db):
    from voti.models import ClusterRun, MediaQuality, Speaker, SpeakerAssignment, VoiceProfile
    ch = factory.channel(title="台語台")
    m1 = factory.media(channel_id=ch.id, title="第一集", duration_sec=10, license="creative_commons", status="ready")
    m2 = factory.media(channel_id=ch.id, title="第二集", duration_sec=10, license="youtube", status="ready")
    db.add_all([MediaQuality(media_id=m1.id, real_bandwidth_hz=8000, sample_rate=48000),
                MediaQuality(media_id=m2.id, real_bandwidth_hz=8000, sample_rate=44100)])
    db.commit()
    seg = {
        "a": factory.segment(m1, 1.0, 3.0, original_text="甲", **GOOD),
        "b": factory.segment(m1, 4.0, 6.5, original_text="乙", corrected_text="乙改", review_status="passed",
                             **GOOD),
        "c": factory.segment(m2, 0.5, 2.0, original_text="丙", **GOOD),
        # excluded ones
        "auto": factory.segment(m2, 3.0, 5.0, text_source="auto_caption", **GOOD),
        "noisy": factory.segment(m2, 5.0, 7.0, **{**GOOD, "snr": 3.0}),
        "rep": factory.segment(m2, 7.0, 9.0, repeated_overlap=0.8, **GOOD),
        "short": factory.segment(m2, 9.0, 9.5, **GOOD),
        "clip": factory.segment(m1, 7.0, 8.0, flags=["clipped"], **GOOD),
        "bad_review": factory.segment(m1, 8.0, 9.5, review_status="excluded", **GOOD),
    }
    run = factory.add(ClusterRun(model_id="ecapa", algorithm="agglomerative", threshold=0.5, is_current=True))
    sp = factory.add(Speaker(run_id=run.id, label="cand_001", total_sec=6.0))
    db.add_all([SpeakerAssignment(run_id=run.id, segment_id=seg[k].id, speaker_id=sp.id) for k in ("a", "b")])
    db.add(VoiceProfile(segment_id=seg["a"].id, revision=1, backbone="whisper-large-v3", sex="female", age=30,
                        age_group="adult", emotion={"label": "neutral"}))
    db.commit()
    return ch, m1, m2, seg, sp


def test_preview_defaults(annotator_client, ds_corpus):
    _, _, _, seg, _ = ds_corpus
    r = annotator_client.post("/api/datasets/preview", json={"filters": {}})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["segment_count"] == 3 and p["total_sec"] == pytest.approx(2.0 + 2.5 + 1.5)
    assert p["speakers"] == 1 and p["channels"] == 1
    assert p["by_channel"][0]["title"] == "台語台" and p["by_channel"][0]["segment_count"] == 3
    assert p["by_sex"] == {"male": 0.0, "female": 2.0, "unknown": 4.0}
    assert p["excluded"]["repeated"] == 1 and p["excluded"]["auto_caption"] == 1
    assert p["excluded"]["low_quality"] == 2  # noisy + clipped
    assert p["filters"]["min_snr"] == 10  # defaults filled in


def test_preview_filters(annotator_client, ds_corpus):
    _, m1, _, seg, sp = ds_corpus

    def count(f):
        r = annotator_client.post("/api/datasets/preview", json={"filters": f})
        assert r.status_code == 200, r.text
        return r.json()["segment_count"]
    assert count({"text_sources": ["manual_caption", "auto_caption"]}) == 4
    assert count({"exclude_repeated": False}) == 4
    assert count({"min_snr": None}) == 4
    assert count({"exclude_flags": []}) == 4
    assert count({"min_duration": 0.1}) == 4
    assert count({"review_status": ["passed"]}) == 1
    assert count({"media_ids": [str(m1.id)]}) == 2
    assert count({"licenses": ["youtube"]}) == 1
    assert count({"speaker_ids": [str(sp.id)]}) == 2
    assert count({"sex": ["female"]}) == 1
    assert count({"min_bandwidth_hz": 9000}) == 0
    assert count({"max_sec_per_speaker": 2.6}) == 2  # keeps one of a/b + the unassigned c
    r = annotator_client.post("/api/datasets/preview", json={"filters": {"min_snr": "abc"}})
    assert r.status_code == 422 and r.json()["code"] == "invalid_filters"


def test_dataset_crud(annotator_client, db):
    r = annotator_client.post("/api/datasets", json={"name": "v1", "filters": {"min_snr": 15}})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["filters"]["min_snr"] == 15 and d["filters"]["text_sources"] == ["manual_caption"]
    r = annotator_client.patch(f"/api/datasets/{d['id']}", json={"name": "v2"})
    assert r.json()["name"] == "v2"
    assert annotator_client.get("/api/datasets").json()["total"] == 1
    assert annotator_client.delete(f"/api/datasets/{d['id']}").status_code == 204
    assert annotator_client.get(f"/api/datasets/{d['id']}").status_code == 404


def _run_job(job_id: str) -> None:
    import voti.datasets  # noqa: F401  registers the handler
    from voti.queue import run_one
    run_one(job_id, "pytest")


def test_export_end_to_end(annotator_client, ds_corpus, db):
    from voti import storage
    from voti.models import Asset, DatasetExport, Job
    _, m1, m2, seg, _ = ds_corpus
    pcm1 = upload_wav(m1.id, 10.0)
    pcm2 = upload_wav(m2.id, 10.0)
    d = annotator_client.post("/api/datasets", json={"name": "台語 v1", "description": "test", "filters": {}}).json()
    r = annotator_client.post(f"/api/datasets/{d['id']}/export", json={"format": "jsonl", "include_audio": True})
    assert r.status_code == 201, r.text
    exp = r.json()
    assert exp["status"] == "queued" and exp["job"]["kind"] == "dataset.export" and exp["job"]["queue"] == "export"
    assert annotator_client.get(f"/api/exports/{exp['id']}/download/manifest.jsonl").status_code == 409

    _run_job(exp["job"]["id"])
    db.expire_all()
    job = db.get(Job, exp["job"]["id"])
    assert job.status == "completed", (job.error_code, job.error_message)

    r = annotator_client.get(f"/api/datasets/{d['id']}/exports")
    e = r.json()["items"][0]
    assert e["status"] == "completed" and e["segment_count"] == 3
    assert e["files"] == ["manifest.jsonl", "manifest.csv", "README.md", "dataset.tar"]
    assert e["total_sec"] == pytest.approx(6.0)

    rows = [json.loads(x) for x in
            annotator_client.get(f"/api/exports/{exp['id']}/download/manifest.jsonl").text.splitlines()]
    assert {r["segment_id"] for r in rows} == {str(seg[k].id) for k in ("a", "b", "c")}
    by_id = {r["segment_id"]: r for r in rows}
    b = by_id[str(seg["b"].id)]
    assert b["text"] == "乙改" and b["text_corrected"] is True and b["speaker"]["label"] == "cand_001"
    assert b["media"]["title"] == "第一集" and b["channel"]["title"] == "台語台"
    assert b["quality"]["sample_rate"] == 48000 and b["quality"]["bandwidth_hz"] == 8000
    assert by_id[str(seg["a"].id)]["profile"]["sex"] == "female" and by_id[str(seg["c"].id)]["speaker"] is None

    r = annotator_client.get(f"/api/exports/{exp['id']}/download/dataset.tar")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
        names = tar.getnames()
        assert names == [row["audio_path"] for row in rows]
        pcms = {str(m1.id): pcm1, str(m2.id): pcm2}
        for row in rows:
            pcm = pcms[row["media"]["voti_id"]]
            data = tar.extractfile(row["audio_path"]).read()
            assert hashlib.sha256(data).hexdigest() == row["sha256"]
            with wave.open(io.BytesIO(data)) as w:
                assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
                frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            assert np.array_equal(frames, pcm[int(row["start_sec"] * SR):int(row["end_sec"] * SR)])

    text = annotator_client.get(f"/api/exports/{exp['id']}/download/manifest.csv").content.decode("utf-8-sig")
    csv_rows = list(csv.DictReader(io.StringIO(text)))
    csv_b = next(x for x in csv_rows if x["segment_id"] == str(seg["b"].id))
    assert len(csv_rows) == 3 and csv_b["text"] == "乙改" and csv_b["sha256"] == b["sha256"]
    readme = annotator_client.get(f"/api/exports/{exp['id']}/download/README.md").text
    assert "台語 v1" in readme and "dataset.tar" in readme
    assert annotator_client.get(f"/api/exports/{exp['id']}/download/..%2Fetc").status_code == 404

    de = db.get(DatasetExport, exp["id"])
    keys = set(db.scalars(select(Asset.key).where(Asset.key.like(f"exports/{exp['id']}/%"))).all())
    assert keys == {f"exports/{exp['id']}/{n}" for n in ("manifest.jsonl", "manifest.csv", "README.md", "dataset.tar")}
    assert db.get(Asset, de.archive_asset_id).kind == "export_archive"
    assert storage.head(f"exports/{exp['id']}/dataset.tar") is not None


def test_export_without_audio_and_failure(annotator_client, ds_corpus, db):
    from voti.models import DatasetExport, Job
    _, m1, m2, _, _ = ds_corpus
    d = annotator_client.post("/api/datasets", json={"name": "noaudio", "filters": {}}).json()
    exp = annotator_client.post(f"/api/datasets/{d['id']}/export", json={"format": "csv",
                                                                         "include_audio": False}).json()
    _run_job(exp["job"]["id"])
    db.expire_all()
    e = annotator_client.get(f"/api/exports/{exp['id']}").json()
    assert e["status"] == "completed" and e["files"] == ["manifest.jsonl", "manifest.csv", "README.md"]
    # include_audio with no normalized.wav uploaded → job fails and export is marked failed
    from voti import storage
    for m in (m1, m2):
        try:
            storage.delete(storage.media_key(m.id, "normalized.wav"))
        except Exception:  # noqa: BLE001
            pass
    exp2 = annotator_client.post(f"/api/datasets/{d['id']}/export", json={"include_audio": True}).json()
    _run_job(exp2["job"]["id"])
    db.expire_all()
    assert db.get(Job, exp2["job"]["id"]).status == "failed"
    assert db.get(DatasetExport, exp2["id"]).status == "failed"
