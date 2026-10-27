"""Simplified → Traditional (Taiwan) caption conversion: OpenCC quality, detection, ingestion, retroactive job,
settings and API.

Run e.g.: VOTI_TEST_DB=voti_test_s2t VOTI_TEST_BUCKET=voti-test-s2t VOTI_TEST_REDIS_DB=6 pytest tests/test_s2t.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from voti import textconv
from voti.sources import youtube as yt

SIMP = "我们今天来看这个视频，教你怎么安装软件，查看网络信息，然后把文件打印一份。"
TRAD = "我們今天來看這個影片，教你怎麼安裝軟體，檢視網路資訊，然後把檔案列印一份。"
NAN_HAN = "这个阮来讲台语，逐家好，欢迎收看"  # Hokkien in Han characters (with Simplified-looking glyphs)


# ───────────────────────────── conversion / detection (pure) ─────────────────────────────

@pytest.mark.parametrize("src,dst", [("视频", "影片"), ("软件", "軟體"), ("信息", "資訊"), ("网络", "網路"),
                                     ("打印", "列印"), ("鼠标", "滑鼠"), ("内存", "記憶體"), ("头发", "頭髮")])
def test_s2twp_quality(src, dst):  # noqa: ANN001
    assert textconv.convert(src, "s2twp") == dst


def test_full_sentence():
    assert textconv.convert(SIMP) == TRAD


@pytest.mark.parametrize("lang,expected", [
    ("zh-Hans", "simplified"), ("zh-CN", "simplified"), ("zh-SG", "simplified"), ("zh-Hans-en", "simplified"),
    ("zh-Hans-CN", "simplified"), ("zh-TW", "traditional"), ("zh-Hant", "traditional"), ("zh-HK", "traditional"),
    ("zh-Hant-TW", "traditional"), ("zh", "detect"), ("cmn", "detect"), ("nan-TW", None), ("nan", None),
    ("hak", None), ("hak-TW", None), ("en", None), ("yue", None), ("", None), (None, None)])
def test_track_script(lang, expected):  # noqa: ANN001
    assert textconv.track_script(lang) == expected


def test_detect_by_content():
    assert textconv.detect_script([SIMP])["script"] == "simplified"
    assert textconv.detect_script([TRAD])["script"] == "traditional"
    # identical in both scripts → neutral (nothing to convert by characters)
    assert textconv.detect_script(["你好，今天大家好朋友"])["script"] == "neutral"
    assert textconv.detect_script(["大家好", "你好"])["script"] == "unknown"  # too short to tell


def test_decide_per_language():
    assert textconv.decide("zh", [SIMP])["convert"] is True
    assert textconv.decide("zh", [TRAD])["convert"] is False
    assert textconv.decide("zh-Hans", ["大家好"])["convert"] is True       # forced by language
    assert textconv.decide("zh-TW", [SIMP])["convert"] is False            # Traditional tag: never
    assert textconv.decide("nan-TW", [NAN_HAN])["convert"] is False        # Taiwanese Hokkien: never
    assert textconv.decide("hak", [SIMP])["convert"] is False


def test_convert_parsed_cues_keeps_raw_and_flags_changed_only():
    from voti.subtitles import ParsedCue
    cues = [ParsedCue(0, 0.0, 1.0, "<i>视频</i>", "视频", []), ParsedCue(1, 1.0, 2.0, "大家好", "大家好", ["music"])]
    info = textconv.convert_parsed_cues(cues, "zh-Hans", textconv.DEFAULTS)
    assert info["converted"] is True and info["cues_converted"] == 1 and info["config"] == "s2twp"
    assert info["opencc"] == textconv.opencc_version() != "?"
    assert cues[0].text == "影片" and cues[0].raw_text == "<i>视频</i>" and cues[0].flags == ["s2t_converted"]
    assert cues[1].text == "大家好" and cues[1].flags == ["music"]
    off = [ParsedCue(0, 0.0, 1.0, "视频", "视频", [])]
    info = textconv.convert_parsed_cues(off, "zh-Hans", {"convert_simplified": False, "opencc_config": "s2twp"})
    assert info["converted"] is False and info["reason"] == "disabled" and off[0].text == "视频"


def test_segment_plan_carries_flag():
    from voti.pipeline import segment
    p = segment._plan_of([segment.CueIn("a", 0, 1, "影片", ["s2t_converted"]), segment.CueIn("b", 1, 2, "好", [])])
    assert "s2t_converted" in p.flags
    assert "s2t_converted" not in segment._plan_of([segment.CueIn("a", 0, 1, "好", [])]).flags


# ───────────────────────────── ingestion ─────────────────────────────

def _json3(*texts: str) -> dict:
    return {"events": [{"tStartMs": 1000 + 2000 * i, "dDurationMs": 1500, "segs": [{"utf8": t}]}
                       for i, t in enumerate(texts)]}


TRACKS = [("zh-Hans", "manual", _json3("这个视频", "大家好"), True),
          ("zh", "auto", _json3(SIMP), False),
          ("zh-TW", "manual", _json3("这个视频"), False),  # mislabeled content stays: the tag wins
          ("nan-TW", "manual", _json3(NAN_HAN), False)]


def _fake_download(tracks):  # noqa: ANN001
    def fake(vid, workdir, fo, caption_langs=None, include_auto=False, require_captions=True, progress=None,
             should_cancel=None):
        audio = Path(workdir) / "original.webm"
        audio.write_bytes(b"\x1aE\xdf\xa3" + b"0" * 100)
        subs = []
        for lang, kind, data, primary in tracks:
            p = Path(workdir) / f"sub.{lang}.{kind}.json3"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            subs.append({"key": lang, "language": lang, "kind": kind, "primary": primary, "path": p,
                         "format": "json3"})
        info = {"id": vid, "title": "t", "duration": 60, "channel_id": "UC" + "a" * 22, "channel": "c",
                "extractor": "youtube", "subtitles": {}, "automatic_captions": {}}
        return yt.DownloadResult(info=info, audio_path=audio, audio_format={"ext": "webm"}, subtitles=subs,
                                 subtitle_errors=[], warnings=[])
    return fake


def _run(db, kind, **kw):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import enqueue, run_one
    job = enqueue(db, kind, **kw)
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    return db.get(Job, job.id)


@pytest.fixture
def fetch(db, monkeypatch):  # noqa: ANN001
    from voti.models import Setting
    from voti.sources import handlers
    db.add(Setting(key="youtube", value={"sleep_between_requests_sec": 0, "sleep_between_downloads_sec": 0}))
    db.commit()
    monkeypatch.setattr(yt, "download", _fake_download(TRACKS))
    try:
        import yt_dlp  # noqa: F401
    except ImportError:  # API/ML venvs: the handler only needs yt_dlp.utils.DownloadCancelled
        import sys
        import types
        fake = types.ModuleType("yt_dlp")
        fake.utils = types.SimpleNamespace(DownloadCancelled=type("DownloadCancelled", (Exception,), {}))
        monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    return handlers


def _cues(db, media_id):  # noqa: ANN001
    from voti.models import Cue, Track
    out = {}
    for t in db.scalars(select(Track).where(Track.media_id == media_id)):
        out[t.language] = db.scalars(select(Cue).where(Cue.track_id == t.id).order_by(Cue.idx)).all()
    return out


def test_ingestion_converts_simplified_tracks_only(db, fetch):  # noqa: ANN001
    from voti.models import Asset, Media, Track
    m = Media(platform_media_id="sssssssssss", status="detailed")
    db.add(m)
    db.commit()
    job = _run(db, "media.download", media_id=m.id)
    assert job.status == "completed", job.error_message
    cues = _cues(db, m.id)
    hans = cues["zh-Hans"]
    assert hans[0].text == "這個影片" and hans[0].raw_text == "这个视频" and "s2t_converted" in hans[0].flags
    assert hans[1].text == "大家好" and "s2t_converted" not in hans[1].flags
    assert cues["zh"][0].text == TRAD and "s2t_converted" in cues["zh"][0].flags  # detected by content
    assert cues["zh-TW"][0].text == "这个视频" and cues["zh-TW"][0].flags == []
    assert cues["nan-TW"][0].text == NAN_HAN and cues["nan-TW"][0].flags == []
    params = {t.language: db.get(Asset, t.asset_id).params["s2t"]
              for t in db.scalars(select(Track).where(Track.media_id == m.id))}
    assert params["zh-Hans"]["converted"] and params["zh-Hans"]["source"] == "language"
    assert params["zh"]["converted"] and params["zh"]["source"] == "content"
    assert params["zh-TW"]["converted"] is False and params["nan-TW"]["script"] == "not_chinese"
    db.refresh(m)
    tc = m.metadata_["text_conversion"]
    assert tc["converted"] is True and tc["config"] == "s2twp" and tc["cues_converted"] == 2
    assert {t["language"]: t["s2t_converted"] for t in m.metadata_["download"]["tracks"]}["zh-Hans"] is True


def test_ingestion_disabled_by_setting(db, fetch):  # noqa: ANN001
    from voti.models import Media, Setting
    db.add(Setting(key="text", value={"convert_simplified": False, "opencc_config": "s2twp"}))
    m = Media(platform_media_id="ttttttttttt", status="detailed")
    db.add(m)
    db.commit()
    assert _run(db, "media.download", media_id=m.id).status == "completed"
    cues = _cues(db, m.id)
    assert cues["zh-Hans"][0].text == "这个视频" and cues["zh-Hans"][0].flags == []
    db.refresh(m)
    assert m.metadata_["text_conversion"]["converted"] is False


# ───────────────────────────── retroactive job ─────────────────────────────

def _legacy_media(db, lang="zh-Hans"):  # noqa: ANN001
    """A media ingested before conversion existed: Simplified cues, segments, no s2t params."""
    from voti import storage
    from voti.models import Cue, Media, Segment, Track
    m = Media(platform_media_id="lllllllllll", status="ready", detailed_at=None)
    db.add(m)
    db.flush()
    asset = storage.store_bytes_asset(db, b"x", storage.media_key(m.id, f"subtitles/{lang}.manual.json3"),
                                      "subtitle", media_id=m.id, params={"language": lang, "cues": 2})
    tr = Track(media_id=m.id, language=lang, kind="manual", format="json3", asset_id=asset.id)
    db.add(tr)
    db.flush()
    c1 = Cue(track_id=tr.id, idx=0, start_sec=0, end_sec=2, raw_text="这个视频", text="这个视频", flags=[])
    c2 = Cue(track_id=tr.id, idx=1, start_sec=2, end_sec=4, raw_text="网络信息", text="网络信息", flags=["music"])
    db.add_all([c1, c2])
    db.flush()
    plain = Segment(media_id=m.id, track_id=tr.id, cue_ids=[str(c1.id)], start_sec=0, end_sec=2,
                    original_text="这个视频", text_source="manual_caption", flags=["low_snr"])
    human = Segment(media_id=m.id, track_id=tr.id, cue_ids=[str(c2.id)], start_sec=2, end_sec=4,
                    original_text="网络信息", corrected_text="網路資訊（人工）", review_status="passed",
                    text_source="manual_caption", revision=2)
    db.add_all([plain, human])
    db.commit()
    return m, tr, plain, human


def test_retroactive_job_keeps_ids_corrections_and_review(db):  # noqa: ANN001
    import voti.textconv  # noqa: F401  (registers the handler)
    from voti.models import Asset, AuditLog, Cue, Segment
    m, tr, plain, human = _legacy_media(db)
    ids = (plain.id, human.id)
    job = _run(db, "media.convert_text", media_id=m.id)
    assert job.status == "completed", job.error_message
    assert job.queue == "audio"
    assert job.result["cues"] == 2 and job.result["segments"] == 2 and job.result["segments_with_correction"] == 1
    db.expire_all()
    p, h = db.get(Segment, ids[0]), db.get(Segment, ids[1])
    assert p.original_text == "這個影片" and "s2t_converted" in p.flags and "low_snr" in p.flags
    assert h.original_text == "網路資訊" and h.corrected_text == "網路資訊（人工）"
    assert h.review_status == "passed" and h.revision == 2 and (h.start_sec, h.end_sec) == (2, 4)
    cues = db.scalars(select(Cue).where(Cue.track_id == tr.id).order_by(Cue.idx)).all()
    assert [c.text for c in cues] == ["這個影片", "網路資訊"] and [c.raw_text for c in cues] == ["这个视频", "网络信息"]
    assert cues[1].flags == ["music", "s2t_converted"]
    assert db.get(Asset, tr.asset_id).params["s2t"]["converted"] is True
    assert db.get(Asset, tr.asset_id).params["cues"] == 2  # other params kept
    db.refresh(m)
    assert m.status == "ready" and m.metadata_["text_conversion"]["converted"] is True
    logs = db.scalars(select(AuditLog).where(AuditLog.action == "media.convert_text")).all()
    assert len(logs) == 1 and logs[0].entity_id == str(m.id) and logs[0].after["segments"] == 2
    # idempotent: a second run changes nothing
    job2 = _run(db, "media.convert_text", media_id=m.id)
    assert job2.status == "completed" and job2.result["cues"] == 0 and job2.result["already"] == 1
    db.expire_all()
    assert db.get(Segment, ids[0]).original_text == "這個影片"


def test_retroactive_job_skips_traditional_and_hokkien(db):  # noqa: ANN001
    import voti.textconv  # noqa: F401
    from voti.models import Cue
    m, tr, *_ = _legacy_media(db, lang="nan-TW")
    job = _run(db, "media.convert_text", media_id=m.id)
    assert job.status == "completed" and job.result["tracks_converted"] == 0
    db.expire_all()
    assert db.scalar(select(Cue.text).where(Cue.track_id == tr.id, Cue.idx == 0)) == "这个视频"


def test_retroactive_job_disabled(db):  # noqa: ANN001
    import voti.textconv  # noqa: F401
    from voti.models import Setting
    m, *_ = _legacy_media(db)
    db.add(Setting(key="text", value={"convert_simplified": False}))
    db.commit()
    job = _run(db, "media.convert_text", media_id=m.id)
    assert job.status == "completed" and job.result == {"skipped": True, "reason": "disabled"}


def test_segmentation_marks_converted_segments(db):  # noqa: ANN001
    """Segments built from converted cues carry the flag (via the plan) and the converted text."""
    import numpy as np
    from voti.pipeline import segment
    vad = segment.Vad(np.ones(400, np.float32), 0.02, 8.0)
    cues = [segment.CueIn("a", 0.5, 2.0, "這個影片。", ["s2t_converted"]),
            segment.CueIn("b", 4.0, 6.0, "大家好。", [])]
    plans = segment.plan_from_cues(cues, vad, segment.Config())
    assert [("s2t_converted" in p.flags, p.text) for p in plans] == [(True, "這個影片。"), (False, "大家好。")]


def test_export_manifest_text_converted(db):  # noqa: ANN001
    from voti import datasets
    m, tr, plain, human = _legacy_media(db)
    plain.flags = ["s2t_converted"]
    db.commit()
    rows = datasets.manifest_rows(db, [_row(plain), _row(human)])
    by = {r["segment_id"]: r for r in rows}
    assert by[str(plain.id)]["text_converted"] is True and by[str(human.id)]["text_converted"] is False
    assert datasets.csv_row(by[str(plain.id)])["text_converted"] is True
    assert "text_converted" in datasets.CSV_COLUMNS


def _row(seg):  # noqa: ANN001
    class R:
        segment_id = seg.id
    return R()


# ───────────────────────────── settings / API ─────────────────────────────

def test_settings_text_key(admin_client, db):  # noqa: ANN001
    r = admin_client.get("/api/settings")
    assert r.json()["text"] == {"convert_simplified": True, "opencc_config": "s2twp"}
    r = admin_client.put("/api/settings/text", json={"convert_simplified": False})
    assert r.status_code == 200 and r.json() == {"convert_simplified": False, "opencc_config": "s2twp"}
    assert admin_client.put("/api/settings/text", json={"opencc_config": "t2s"}).status_code == 422
    assert admin_client.put("/api/settings/text", json={"bogus": 1}).status_code == 422
    assert admin_client.get("/api/settings").json()["text"]["convert_simplified"] is False


def test_api_convert_endpoints(annotator_client, db):  # noqa: ANN001
    from voti.models import Channel, Job, Setting
    m, tr, *_ = _legacy_media(db)
    ch = Channel(platform="youtube", platform_channel_id="UC" + "c" * 22, title="c")
    db.add(ch)
    db.commit()
    m.channel_id = ch.id
    db.commit()
    d = annotator_client.get(f"/api/media/{m.id}").json()
    assert d["text_converted"] is False and d["text_convertible"] is True
    assert d["tracks"][0]["s2t_converted"] is False and d["tracks"][0]["script"] is None
    r = annotator_client.post(f"/api/media/{m.id}/convert-text")
    assert r.status_code == 200 and r.json() == {"queued": 1}
    job = db.scalar(select(Job).where(Job.kind == "media.convert_text"))
    assert job.media_id == m.id and job.queue == "audio"
    # channel endpoint dedups against the active job → still one job
    r = annotator_client.post(f"/api/channels/{ch.id}/convert-text")
    assert r.status_code == 200 and r.json() == {"queued": 1}
    assert len(db.scalars(select(Job).where(Job.kind == "media.convert_text")).all()) == 1
    # run it, then nothing is left to convert
    from voti.queue import run_one
    import voti.textconv  # noqa: F401
    run_one(str(job.id), "t")
    db.expire_all()
    d = annotator_client.get(f"/api/media/{m.id}").json()
    assert d["text_converted"] is True and d["text_convertible"] is False
    assert d["tracks"][0]["s2t_converted"] is True and d["tracks"][0]["cues_converted"] == 2
    assert d["text_conversion"]["converted"] is True
    assert annotator_client.post(f"/api/channels/{ch.id}/convert-text").json() == {"queued": 0}
    assert annotator_client.post(f"/api/media/{m.id}/convert-text").json() == {"queued": 0}
    # disabled → 409
    db.add(Setting(key="text", value={"convert_simplified": False}))
    db.commit()
    r = annotator_client.post(f"/api/media/{m.id}/convert-text")
    assert r.status_code == 409 and r.json()["code"] == "text_conversion_disabled"
    assert annotator_client.post(f"/api/channels/{ch.id}/convert-text").status_code == 409


def test_api_convert_requires_login(client, db):  # noqa: ANN001
    import uuid
    assert client.post(f"/api/media/{uuid.uuid4()}/convert-text").status_code == 401


def test_job_out_has_run_after(annotator_client, db):  # noqa: ANN001
    from datetime import timedelta

    from voti.models import utcnow
    from voti.queue import enqueue
    job = enqueue(db, "speakers.cluster")
    job.run_after = utcnow() + timedelta(minutes=2)
    db.commit()
    r = annotator_client.get(f"/api/jobs/{job.id}").json()
    assert r["run_after"] is not None and r["status"] == "queued"
