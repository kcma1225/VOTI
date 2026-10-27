"""Caption verification: YouTube lists an uploader caption track whose content is empty (or 404s).

Pure parts (subtitles.track_content, youtube.caption_state / verify_captions / download with a fake YoutubeDL) and
the handlers / API against the test database with every network call stubbed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from voti import subtitles as subs
from voti.sources import youtube as yt

UC = "UChaNiPEqVcO-iE1NIg3wuHQ"


def j3(*texts: str) -> bytes:
    return json.dumps({"events": [{"tStartMs": 1000 * i, "dDurationMs": 900, "segs": [{"utf8": t}]}
                                  for i, t in enumerate(texts)]}, ensure_ascii=False).encode()


SPEECH = j3("大家好", "[音樂]")
MUSIC_ONLY = j3("[音樂]", "♪♪", "[Music]", "（掌聲）", "♪ ~ ♪")
WINDOW_ONLY = json.dumps({"wireMagic": "pb3", "pens": [{}], "events": [{"tStartMs": 0, "id": 1, "wpWinPosId": 1}]})


# ───────────────────────────── subtitles.track_content ─────────────────────────────

@pytest.mark.parametrize("data,fmt,reason", [
    (b"", "json3", "zero_bytes"),
    (b"   \n", "vtt", "zero_bytes"),
    (None, "vtt", "zero_bytes"),
    (b"WEBVTT\nKind: captions\nLanguage: zh-TW\n\n", "vtt", "no_cues"),
    (WINDOW_ONLY.encode(), "json3", "no_cues"),
    (MUSIC_ONLY, "json3", "no_speech"),
    (b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n[\xe9\x9f\xb3\xe6\xa8\x82]\n\n00:00:03.000 --> 00:00:04.000\n"
     b"\xe2\x99\xaa\n\n00:00:05.000 --> 00:00:06.000\n...\n", "vtt", "no_speech"),
    (b"1\n00:00:01,000 --> 00:00:02,000\n[Applause]\n", "srt", "no_speech"),
])
def test_track_content_empty(data, fmt, reason):  # noqa: ANN001
    r = subs.track_content(data, fmt)
    assert r["status"] == "empty" and r["reason"] == reason and r["speech_cues"] == 0 and r["chars"] == 0


def test_track_content_ok_and_error():
    r = subs.track_content(SPEECH, "json3")
    assert r == {"status": "ok", "cues": 2, "speech_cues": 1, "chars": 3}
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n♪ 我愛台灣 ♪\n"
    assert subs.track_content(vtt.encode(), "vtt")["status"] == "ok"
    bad = subs.track_content(b"<html>oops</html>", "json3")
    assert bad["status"] == "error" and bad["reason"].startswith("unparseable")


# ───────────────────────────── youtube.caption_state ─────────────────────────────

def _chk(**statuses):  # noqa: ANN003
    return {"tracks": {k.replace("_", "-"): {"status": v} for k, v in statuses.items()}}


def test_caption_state():
    manual = ["zh-TW", "zh-HK", "nan-TW"]
    assert yt.caption_state(manual, None) == {"has_manual_captions": True,
                                              "manual_caption_langs": ["nan-TW", "zh-HK", "zh-TW"],
                                              "captions_empty": False}
    st = yt.caption_state(manual, _chk(zh_TW="empty", zh_HK="ok"))
    assert st == {"has_manual_captions": True, "manual_caption_langs": ["zh-HK", "nan-TW"], "captions_empty": False}
    # every checked track empty (nan-TW unchecked, no error) → captions_empty
    st = yt.caption_state(manual, _chk(zh_TW="empty", zh_HK="empty"))
    assert st == {"has_manual_captions": False, "manual_caption_langs": [], "captions_empty": True}
    # a network error keeps the claim (benefit of the doubt)
    st = yt.caption_state(manual, _chk(zh_TW="error"))
    assert st["has_manual_captions"] is True and st["captions_empty"] is False
    assert st["manual_caption_langs"] == ["nan-TW", "zh-HK", "zh-TW"]
    assert yt.caption_state([], _chk(zh_TW="empty"))["captions_empty"] is False
    # a check of tracks no longer listed is ignored
    assert yt.caption_state(["zh-TW"], _chk(zh_HK="empty"))["has_manual_captions"] is True


# ───────────────────────────── youtube.verify_captions ─────────────────────────────

def _subs_info(*langs: str, auto: dict | None = None) -> dict:
    return {"id": "aaaaaaaaaaa", "subtitles": {
        lang: [{"ext": "vtt", "url": f"https://www.youtube.com/api/timedtext?lang={lang}&fmt=vtt"},
               {"ext": "json3", "url": f"https://www.youtube.com/api/timedtext?lang={lang}&fmt=json3"}]
        for lang in langs}, "automatic_captions": auto or {}}


def _stub_fetch(monkeypatch, contents: dict):  # noqa: ANN001
    calls = []

    def fake(fmt, fo, headers=None):  # noqa: ANN001
        lang = fmt["url"].split("lang=")[1].split("&")[0]
        calls.append((lang, fmt["ext"]))
        v = contents[lang]
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(yt, "fetch_caption_bytes", fake)
    return calls


FO = yt.FetchOptions(sleep_subtitles_sec=0)


def test_verify_preferred_empty_then_next_ok(monkeypatch):  # noqa: ANN001
    calls = _stub_fetch(monkeypatch, {"zh-TW": MUSIC_ONLY, "zh-Hant": b"",
                                      "zh-HK": yt.YouTubeError("caption_not_found", "404", retryable=False),
                                      "zh-CN": SPEECH, "en": SPEECH})
    check = yt.verify_captions(_subs_info("en", "zh-CN", "zh-HK", "zh-TW", "zh-Hant"), FO)
    assert calls == [("zh-TW", "json3"), ("zh-Hant", "json3"), ("zh-HK", "json3")]  # cap: 3 fetches
    assert {k: (v["status"], v.get("reason")) for k, v in check["tracks"].items()} == {
        "zh-TW": ("empty", "no_speech"), "zh-Hant": ("empty", "zero_bytes"), "zh-HK": ("empty", "not_found")}
    assert check["usable_langs"] == [] and check["source"] == "detail" and check["checked_at"]

    calls = _stub_fetch(monkeypatch, {"zh-TW": MUSIC_ONLY, "zh-HK": SPEECH, "nan-TW": SPEECH})
    check = yt.verify_captions(_subs_info("nan-TW", "zh-HK", "zh-TW"), FO)
    assert [c[0] for c in calls] == ["zh-TW", "zh-HK"] and check["usable_langs"] == ["zh-HK"]
    assert check["tracks"]["zh-HK"] == {"status": "ok", "cues": 2, "speech_cues": 1, "chars": 3, "format": "json3"}


def test_verify_taiwanese_only_and_no_manual(monkeypatch):  # noqa: ANN001
    calls = _stub_fetch(monkeypatch, {"nan-TW": SPEECH})
    assert yt.verify_captions(_subs_info("nan-TW"), FO)["usable_langs"] == ["nan-TW"] and len(calls) == 1
    assert yt.verify_captions(_subs_info(), FO) is None


def test_verify_network_error_is_not_empty(monkeypatch):  # noqa: ANN001
    calls = _stub_fetch(monkeypatch, {"zh-TW": yt.classify_error("Connection reset by peer"), "zh-HK": b""})
    check = yt.verify_captions(_subs_info("zh-TW", "zh-HK"), FO)
    assert len(calls) == 1 and check["tracks"]["zh-TW"]["status"] == "error"  # stops at the first error
    st = yt.caption_state(["zh-TW", "zh-HK"], check)
    assert st["has_manual_captions"] is True and st["captions_empty"] is False
    _stub_fetch(monkeypatch, {"zh-TW": yt.classify_error("HTTP Error 429: Too Many Requests")})
    with pytest.raises(yt.YouTubeError) as ei:
        yt.verify_captions(_subs_info("zh-TW"), FO)
    assert ei.value.code == "rate_limited"


def test_verify_track_without_formats_is_empty():
    info = {"subtitles": {"zh-TW": [{"ext": "json3", "url": "https://x/api/timedtext?hls_timedtext=1"}]}}
    assert yt.verify_caption_track(info, "zh-TW", FO)["reason"] == "no_formats"


# ───────────────────────────── youtube.download with a fake YoutubeDL ─────────────────────────────

class FakeYDL:
    contents: dict = {}
    audio_downloads: list = []

    def __init__(self, opts):  # noqa: ANN001
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):  # noqa: ANN002
        return False

    def extract_info(self, url, download=False):  # noqa: ANN001
        return self.info

    def dl(self, path, sub, subtitle=True):  # noqa: ANN001
        lang = sub["url"].split("lang=")[1].split("&")[0]
        v = self.contents[lang]
        if isinstance(v, Exception):
            raise v
        Path(path).write_bytes(v)

    def process_ie_result(self, info, download=True):  # noqa: ANN001
        FakeYDL.audio_downloads.append(info["id"])
        p = Path(self.opts["outtmpl"]["default"].replace("%(ext)s", "webm"))
        p.write_bytes(b"audio")
        return {**info, "requested_downloads": [{"filepath": str(p), "ext": "webm", "format_id": "251"}]}

    def sanitize_info(self, info):  # noqa: ANN001
        return info


@pytest.fixture
def fake_ydl(monkeypatch):  # noqa: ANN001
    import yt_dlp

    FakeYDL.audio_downloads = []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def test_download_all_empty_stops_before_audio(fake_ydl, tmp_path):  # noqa: ANN001
    fake_ydl.info = _subs_info("zh-TW", "zh-HK", "nan-TW")
    fake_ydl.contents = {"zh-TW": MUSIC_ONLY, "zh-HK": WINDOW_ONLY.encode(), "nan-TW": b"",
                         "en": SPEECH}
    with pytest.raises(yt.YouTubeError) as ei:
        yt.download("aaaaaaaaaaa", tmp_path, FO)
    e = ei.value
    assert e.code == "empty_captions" and e.retryable is False and yt.EMPTY_CAPTIONS_MESSAGE in e.message
    assert fake_ydl.audio_downloads == []  # no audio downloaded
    assert {k: v["status"] for k, v in e.extra["checked"].items()} == {"zh-TW": "empty", "nan-TW": "empty",
                                                                        "zh-HK": "empty"}
    assert sorted(e.extra["caption_tracks"]["manual"]) == ["nan-TW", "zh-HK", "zh-TW"]


def test_download_drops_empty_tracks_and_falls_back(fake_ydl, tmp_path):  # noqa: ANN001
    fake_ydl.info = _subs_info("zh-TW", "zh-HK", "nan-TW")
    fake_ydl.contents = {"zh-TW": MUSIC_ONLY, "zh-HK": SPEECH, "nan-TW": SPEECH}
    res = yt.download("aaaaaaaaaaa", tmp_path, FO)
    assert fake_ydl.audio_downloads == ["aaaaaaaaaaa"]
    got = {s["key"]: s["primary"] for s in res.subtitles}
    assert got == {"nan-TW": False, "zh-HK": True}  # the next Chinese track becomes primary
    assert {k: v["status"] for k, v in res.caption_check.items()} == {"zh-TW": "empty", "nan-TW": "ok",
                                                                       "zh-HK": "ok"}
    assert not any(p.name.startswith("sub.zh-TW") for p in tmp_path.iterdir())


def test_download_empty_plus_network_error_is_retryable(fake_ydl, tmp_path):  # noqa: ANN001
    fake_ydl.info = _subs_info("zh-TW", "nan-TW")
    fake_ydl.contents = {"zh-TW": b"", "nan-TW": RuntimeError("Connection reset by peer")}
    with pytest.raises(yt.YouTubeError) as ei:
        yt.download("aaaaaaaaaaa", tmp_path, yt.FetchOptions(sleep_subtitles_sec=0))
    assert ei.value.code == "subtitle_unavailable" and ei.value.retryable and fake_ydl.audio_downloads == []


def test_download_empty_manual_with_auto(fake_ydl, tmp_path):  # noqa: ANN001
    auto = {"zh-TW-orig": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?lang=zh-TW-orig&kind=asr"}]}
    fake_ydl.info = _subs_info("zh-TW", auto=auto)
    fake_ydl.contents = {"zh-TW": MUSIC_ONLY, "zh-TW-orig": SPEECH}
    res = yt.download("aaaaaaaaaaa", tmp_path, FO, include_auto=True)
    assert [(s["kind"], s["primary"]) for s in res.subtitles] == [("auto", True)]
    assert res.caption_check["zh-TW"]["status"] == "empty" and fake_ydl.audio_downloads == ["aaaaaaaaaaa"]


# ───────────────────────────── handlers (test db, stubbed network) ─────────────────────────────

@pytest.fixture
def h(db, monkeypatch):  # noqa: ANN001
    from voti.models import Setting
    from voti.sources import handlers

    db.add(Setting(key="youtube", value={"sleep_between_requests_sec": 0, "sleep_between_downloads_sec": 0}))
    db.commit()
    monkeypatch.setattr("time.sleep", lambda s: None)  # sleep_subtitles_sec between verification fetches
    return handlers


def run(db, kind, **kw):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import enqueue, run_one

    job = enqueue(db, kind, **kw)
    db.commit()
    return run_job(db, job)


def run_job(db, job):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import run_one

    run_one(str(job.id), "test")
    db.expire_all()
    return db.get(Job, job.id)


def _info(vid, langs=("zh-TW", "nan-TW"), **kw):  # noqa: ANN001
    info = {"id": vid, "title": f"title {vid}", "duration": 61, "timestamp": 1581912003, "availability": "public",
            "license": "Creative Commons Attribution license (reuse allowed)", "live_status": "not_live",
            "media_type": "video", "channel_id": UC, "channel": "呆呆伙伴", "channel_url": yt.channel_url(UC),
            "extractor": "youtube", "formats": [], "automatic_captions": {},
            "subtitles": _subs_info(*langs)["subtitles"]}
    info.update(kw)
    return info


def test_detail_preferred_empty_next_ok(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Media
    m = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    db.add(m)
    db.commit()
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid, ("zh-TW", "zh-HK", "nan-TW")), []))
    calls = _stub_fetch(monkeypatch, {"zh-TW": MUSIC_ONLY, "zh-HK": SPEECH, "nan-TW": SPEECH})
    job = run(db, "media.detail", media_id=m.id)
    assert job.status == "completed", job.error_message
    assert [c[0] for c in calls] == ["zh-TW", "zh-HK"]
    db.refresh(m)
    assert m.status == "detailed" and m.has_manual_captions is True and m.captions_empty is False
    assert m.manual_caption_langs == ["zh-HK", "nan-TW"]
    cc = m.metadata_["caption_check"]
    assert cc["usable_langs"] == ["zh-HK"] and cc["tracks"]["zh-TW"]["status"] == "empty"
    assert job.result["caption_check"] == {"zh-TW": "empty", "zh-HK": "ok"}


def test_detail_all_empty_marks_captions_empty(db, h, monkeypatch, annotator_client):  # noqa: ANN001
    from voti.models import Media
    m = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    ok = Media(platform_media_id="bbbbbbbbbbb", status="listed")
    db.add_all([m, ok])
    db.commit()
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid, ("zh-TW",)), []))
    _stub_fetch(monkeypatch, {"zh-TW": WINDOW_ONLY.encode()})
    assert run(db, "media.detail", media_id=m.id).status == "completed"
    _stub_fetch(monkeypatch, {"zh-TW": SPEECH})
    assert run(db, "media.detail", media_id=ok.id).status == "completed"
    db.refresh(m)
    assert m.status == "detailed" and m.captions_empty is True and m.has_manual_captions is False
    assert m.manual_caption_langs == [] and m.metadata_["caption_tracks"]["manual"] == {"zh-TW": "zh-TW"}

    # API: summary fields, filters and facets
    r = annotator_client.get("/api/media")
    assert r.status_code == 200, r.text
    body = r.json()
    items = {i["platform_media_id"]: i for i in body["items"]}
    assert items["aaaaaaaaaaa"]["captions_empty"] is True and items["bbbbbbbbbbb"]["captions_empty"] is False
    assert items["aaaaaaaaaaa"]["caption_check"]["tracks"]["zh-TW"]["status"] == "empty"
    assert body["facets"]["captions"] == {"manual": 1, "auto_only": 0, "none": 1, "unknown": 0, "empty": 1}
    ids = lambda q: [i["platform_media_id"] for i in annotator_client.get(f"/api/media?{q}").json()["items"]]  # noqa: E731
    assert ids("captions=manual") == ["bbbbbbbbbbb"]
    assert ids("captions=none") == ["aaaaaaaaaaa"] and ids("captions=empty") == ["aaaaaaaaaaa"]
    assert ids("captions=manual&caption_lang=zh-TW") == ["bbbbbbbbbbb"]
    d = annotator_client.get(f"/api/media/{m.id}").json()
    assert d["captions_empty"] is True and d["caption_check"]["usable_langs"] == []


def test_detail_network_error_not_empty(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Media
    m = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    db.add(m)
    db.commit()
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid, ("zh-TW",)), []))
    _stub_fetch(monkeypatch, {"zh-TW": yt.classify_error("Read timed out")})
    job = run(db, "media.detail", media_id=m.id)
    assert job.status == "completed", job.error_message
    db.refresh(m)
    assert m.has_manual_captions is True and m.captions_empty is False and m.manual_caption_langs == ["zh-TW"]
    assert m.metadata_["caption_check"]["tracks"]["zh-TW"]["status"] == "error"


def test_detail_bot_check_during_verification_blocks(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Media
    m = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    db.add(m)
    db.commit()
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid, ("zh-TW",)), []))
    _stub_fetch(monkeypatch, {"zh-TW": yt.classify_error("Sign in to confirm you’re not a bot")})
    job = run(db, "media.detail", media_id=m.id)
    assert job.status == "blocked" and job.error_code == "bot_check"
    db.refresh(m)
    assert m.captions_empty is False


def test_verify_setting_off_skips_verification(db, h, monkeypatch, admin_client):  # noqa: ANN001
    from voti.models import Media
    r = admin_client.put("/api/settings/youtube", json={"verify_captions": False})
    assert r.status_code == 200 and r.json()["verify_captions"] is False
    assert admin_client.get("/api/settings").json()["youtube"]["verify_captions"] is False
    m = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    db.add(m)
    db.commit()
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid, ("zh-TW",)), []))
    calls = _stub_fetch(monkeypatch, {"zh-TW": b""})
    assert run(db, "media.detail", media_id=m.id).status == "completed"
    db.refresh(m)
    assert calls == [] and m.has_manual_captions is True and "caption_check" not in m.metadata_
    # an explicit re-check verifies anyway
    job = run(db, "media.detail", media_id=m.id, payload={"force": True, "verify": True})
    assert job.status == "completed" and len(calls) == 1
    db.refresh(m)
    assert m.captions_empty is True


def _empty_download(vid, workdir, fo, **kw):  # noqa: ANN001, ANN003
    e = yt.YouTubeError("empty_captions", f"{yt.EMPTY_CAPTIONS_MESSAGE}（zh-TW）", retryable=False)
    e.extra = {"caption_tracks": {"manual": {"zh-TW": "zh-TW"}, "auto": {}},
               "checked": {"zh-TW": {"status": "empty", "cues": 0, "speech_cues": 0, "chars": 0,
                                     "reason": "no_speech"}}}
    raise e


def test_download_guard_and_recheck_restores(db, h, monkeypatch, annotator_client):  # noqa: ANN001
    from voti.models import Asset, Job, Media
    m = Media(platform_media_id="aaaaaaaaaaa", status="detailed", has_manual_captions=True,
              manual_caption_langs=["zh-TW"], metadata_={"caption_tracks": {"manual": {"zh-TW": "zh-TW"}}})
    db.add(m)
    db.commit()
    monkeypatch.setattr(yt, "download", _empty_download)
    job = run(db, "media.download", media_id=m.id)
    assert job.status == "failed" and job.error_code == "empty_captions" and job.attempts == 1
    db.refresh(m)
    assert (m.status, m.failure_code) == ("skipped", "empty_captions")
    assert yt.EMPTY_CAPTIONS_MESSAGE in m.failure_message
    assert m.captions_empty is True and m.has_manual_captions is False
    assert m.metadata_["caption_check"]["source"] == "download"
    assert db.scalars(select(Asset).where(Asset.media_id == m.id, Asset.kind == "original_audio")).all() == []
    assert db.scalars(select(Job).where(Job.parent_id == job.id)).all() == []  # no pipeline follow-up

    # download endpoint skips it (and queues it only with include_auto when ASR exists)
    r = annotator_client.post("/api/media/download", json={"media_ids": [str(m.id)]})
    assert r.json()["queued"] == 0 and r.json()["skipped_reasons"] == {"empty_captions": 1}

    # still empty on re-check → stays skipped
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid, ("zh-TW",)), []))
    _stub_fetch(monkeypatch, {"zh-TW": MUSIC_ONLY})
    r = annotator_client.post(f"/api/media/{m.id}/recheck-captions")
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    j = db.get(Job, jid)
    assert j.kind == "media.detail" and j.payload == {"force": True, "verify": True}
    assert run_job(db, j).status == "completed"
    db.refresh(m)
    assert (m.status, m.failure_code, m.captions_empty) == ("skipped", "empty_captions", True)

    # the uploader fixed the captions → detailed again, downloadable
    _stub_fetch(monkeypatch, {"zh-TW": SPEECH})
    j = db.get(Job, annotator_client.post(f"/api/media/{m.id}/recheck-captions").json()["id"])
    assert run_job(db, j).status == "completed"
    db.refresh(m)
    assert (m.status, m.failure_code, m.failure_message) == ("detailed", None, None)
    assert m.captions_empty is False and m.has_manual_captions is True and m.manual_caption_langs == ["zh-TW"]


def test_recheck_never_regresses_processed_media(db, h, monkeypatch, annotator_client):  # noqa: ANN001
    from voti.models import Job, Media
    m = Media(platform_media_id="aaaaaaaaaaa", status="ready", has_manual_captions=True,
              manual_caption_langs=["zh-TW"])
    db.add(m)
    db.commit()
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid, ("zh-TW",)), []))
    _stub_fetch(monkeypatch, {"zh-TW": b""})
    j = db.get(Job, annotator_client.post(f"/api/media/{m.id}/recheck-captions").json()["id"])
    assert run_job(db, j).status == "completed"
    db.refresh(m)
    assert m.status == "ready" and m.captions_empty is True  # caption fields update, status does not


def test_recheck_upgrades_queued_plain_detail_job(db, h, annotator_client):  # noqa: ANN001
    from voti.models import Job, Media
    from voti.queue import enqueue
    m = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    db.add(m)
    db.commit()
    plain = enqueue(db, "media.detail", media_id=m.id)
    db.commit()
    r = annotator_client.post(f"/api/media/{m.id}/recheck-captions")
    assert r.json()["id"] == str(plain.id)
    db.expire_all()
    assert db.get(Job, plain.id).payload == {"force": True, "verify": True}


def test_channel_recheck_batches(db, h, monkeypatch, annotator_client, factory):  # noqa: ANN001
    from voti.models import Channel, Job, Media
    ch = factory.channel(platform_channel_id=UC, list_status="listed")
    claims = factory.media(channel_id=ch.id, platform_media_id="aaaaaaaaaaa", status="detailed",
                           has_manual_captions=True, manual_caption_langs=["zh-TW"])
    was_empty = factory.media(channel_id=ch.id, platform_media_id="bbbbbbbbbbb", status="skipped",
                              failure_code="empty_captions", has_manual_captions=False, captions_empty=True)
    factory.media(channel_id=ch.id, status="detailed", has_manual_captions=False)          # never claimed
    factory.media(channel_id=ch.id, status="ready", has_manual_captions=True)               # already downloaded
    factory.media(channel_id=ch.id, status="skipped", failure_code="members_only", has_manual_captions=True)
    other = factory.channel()
    factory.media(channel_id=other.id, status="detailed", has_manual_captions=True)
    r = annotator_client.post(f"/api/channels/{ch.id}/recheck-captions")
    assert r.status_code == 200 and r.json() == {"queued": 2}
    jobs = db.scalars(select(Job).where(Job.kind == "media.detail")).all()
    assert len(jobs) == 1 and jobs[0].media_id is None and jobs[0].channel_id == ch.id
    p = jobs[0].payload
    assert sorted(p["media_ids"]) == sorted([str(claims.id), str(was_empty.id)])
    assert p["force"] is True and p["verify"] is True and p["recheck"] is True and p["batch_size"] == 20
    # not queued twice while active
    assert annotator_client.post(f"/api/channels/{ch.id}/recheck-captions").json() == {"queued": 2}
    assert len(db.scalars(select(Job).where(Job.kind == "media.detail")).all()) == 1

    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid, ("zh-TW",)), []))
    _stub_fetch(monkeypatch, {"zh-TW": SPEECH})
    job = run_job(db, jobs[0])
    assert job.status == "completed" and job.result["detailed"] == 2, job.result
    db.expire_all()
    assert db.get(Media, was_empty.id).status == "detailed" and db.get(Media, was_empty.id).captions_empty is False
    assert db.get(Media, claims.id).status == "detailed"
    assert db.get(Channel, ch.id).list_status == "listed"


def test_recheck_batch_chains_and_does_not_hide_listing_sweep(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Channel, Job
    ch = Channel(platform_channel_id=UC, title="x")
    db.add(ch)
    db.commit()
    from voti.queue import enqueue
    enqueue(db, "media.detail", channel_id=ch.id, payload={"media_ids": ["x"], "recheck": True})
    db.commit()
    assert h._active_detail_sweep(db, ch.id) is False
    enqueue(db, "media.detail", channel_id=ch.id, payload={"batch_size": 20})
    db.commit()
    assert h._active_detail_sweep(db, ch.id) is True
    assert len(db.scalars(select(Job)).all()) == 2
