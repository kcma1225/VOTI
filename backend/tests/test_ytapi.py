"""YouTube Data API v3 fast pass (voti.sources.ytapi, job media.api_detail) and the key settings endpoints.

Every HTTP call goes to an httpx.MockTransport (no network, no real key).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from voti.sources import ytapi

UC = "UChaNiPEqVcO-iE1NIg3wuHQ"
KEY_A = "AIzaSyA-test-key-number-one-0000000001"
KEY_B = "AIzaSyB-test-key-number-two-0000000002"
KEY_C = "AIzaSyC-test-key-number-three-00000003"


def _item(vid: str, caption: bool = False, **kw) -> dict:
    it = {"id": vid,
          "snippet": {"publishedAt": "2023-05-06T07:08:09Z", "channelId": UC, "title": f"api {vid}",
                      "description": "api description", "tags": ["台語", "教育"], "categoryId": "27",
                      "channelTitle": "呆呆伙伴", "defaultAudioLanguage": "zh-TW", "liveBroadcastContent": "none",
                      "thumbnails": {"high": {"url": "https://i.ytimg.com/h.jpg"},
                                     "maxres": {"url": "https://i.ytimg.com/maxres.jpg"}}},
          "contentDetails": {"duration": "PT1H2M3S", "caption": "true" if caption else "false",
                             "licensedContent": False, "contentRating": {},
                             "regionRestriction": {"blocked": ["CN"]}},
          "status": {"uploadStatus": "processed", "privacyStatus": "public", "license": "creativeCommon",
                     "embeddable": True, "madeForKids": False},
          "statistics": {"viewCount": "1234", "likeCount": "56", "commentCount": "7"}}
    for k, v in kw.items():
        it[k] = {**it.get(k, {}), **v} if isinstance(v, dict) else v
    return it


class FakeApi:
    """httpx transport: per-key behaviour ("ok" | "quota" | "invalid" | "disabled" | "error"), items by id."""

    def __init__(self, items: dict[str, dict] | None = None, behaviour: dict[str, str] | None = None):
        self.items = items or {}
        self.behaviour = behaviour or {}
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.headers.get("x-goog-api-key")
        assert key, "the key travels in the X-Goog-Api-Key header"
        assert "key=" not in str(request.url), "the key must never be in the URL"
        ids = request.url.params.get("id", "").split(",")
        self.calls.append({"key": key, "ids": ids, "part": request.url.params.get("part")})
        b = self.behaviour.get(key, "ok")
        if b == "quota":
            return httpx.Response(403, json={"error": {"code": 403, "message": "The request cannot be completed "
                                                       "because you have exceeded your quota.",
                                                       "errors": [{"reason": "quotaExceeded",
                                                                   "domain": "youtube.quota"}]}})
        if b == "invalid":
            return httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid. Please pass a "
                                                       "valid API key.", "errors": [{"reason": "badRequest"}],
                                                       "status": "INVALID_ARGUMENT",
                                                       "details": [{"reason": "API_KEY_INVALID"}]}})
        if b == "disabled":
            return httpx.Response(403, json={"error": {"code": 403, "message": "YouTube Data API v3 has not been "
                                                       "used in project 1 before or it is disabled.",
                                                       "errors": [{"reason": "accessNotConfigured"}]}})
        if b == "error":
            return httpx.Response(503, json={"error": {"code": 503, "message": "backend error"}})
        return httpx.Response(200, json={"kind": "youtube#videoListResponse",
                                         "items": [self.items[i] for i in ids if i in self.items]})


@pytest.fixture
def api(monkeypatch):  # noqa: ANN001
    fake = FakeApi()
    monkeypatch.setattr(ytapi, "_TRANSPORT", httpx.MockTransport(fake))
    return fake


def set_keys(db, *keys: str) -> None:  # noqa: ANN001
    from voti.models import Setting
    row = db.get(Setting, ytapi.SETTING_KEY) or Setting(key=ytapi.SETTING_KEY, value={})
    row.value = {"keys": [{"id": ytapi.key_id(k), "key": k, "added_at": "2026-10-28T00:00:00+00:00"} for k in keys]}
    db.add(row)
    db.commit()


@pytest.fixture
def h(db):  # noqa: ANN001
    from voti.models import Setting
    from voti.sources import handlers
    db.add(Setting(key="youtube", value={"sleep_between_requests_sec": 0, "sleep_between_downloads_sec": 0}))
    db.commit()
    return handlers


def run(db, kind, **kw):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import enqueue, run_one
    job = enqueue(db, kind, **kw)
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    job = db.get(Job, job.id)
    return job, db.scalars(select(Job).where(Job.parent_id == job.id)).all()


# ───────────────────────────── pure helpers ─────────────────────────────

def test_mask_and_id():
    assert ytapi.mask(KEY_A) == "AIza…0001"
    assert KEY_A not in ytapi.mask(KEY_A)
    assert ytapi.key_id(KEY_A) == ytapi.key_id(KEY_A) and ytapi.key_id(KEY_A) != ytapi.key_id(KEY_B)
    assert ytapi.normalize_key("  " + KEY_A + "\n") == KEY_A
    assert ytapi.normalize_key("not a key!") is None


def test_quota_reset_is_next_pacific_midnight():
    # 2026-10-28 10:00 UTC = 03:00 PDT → next reset 2026-10-29 00:00 PDT = 07:00 UTC
    assert ytapi.next_quota_reset(datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 29, 7, 0, tzinfo=timezone.utc)
    # winter (PST, UTC-8): 2026-12-01 09:00 UTC = 01:00 PST → 2026-12-02 08:00 UTC
    assert ytapi.next_quota_reset(datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)) == \
        datetime(2026, 12, 2, 8, 0, tzinfo=timezone.utc)


def test_summarize_item():
    d = ytapi.summarize_item(_item("aaaaaaaaaaa", caption=True))
    assert d["duration_sec"] == 3723 and d["license"] == "creative_commons" and d["caption"] is True
    assert d["published_at"] == datetime(2023, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
    assert (d["view_count"], d["like_count"], d["comment_count"]) == (1234, 56, 7)
    assert d["categories"] == ["Education"] and d["language"] == "zh-TW" and d["availability"] == "public"
    assert d["thumbnail_url"] == "https://i.ytimg.com/maxres.jpg"
    assert d["extras"]["region_restriction"] == {"blocked": ["CN"]} and d["extras"]["embeddable"] is True
    live = ytapi.summarize_item(_item("b", liveStreamingDetails={"actualStartTime": "x", "actualEndTime": "y"},
                                      status={"license": "youtube"}))
    assert live["was_live"] is True and live["live_status"] == "was_live" and live["license"] == "youtube"
    assert ytapi.parse_duration("P1DT2S") == 86402 and ytapi.parse_duration("P0D") == 0
    assert ytapi.parse_duration("") is None


def test_classify_errors():
    e = ytapi.classify_error(403, {"error": {"errors": [{"reason": "dailyLimitExceeded"}], "message": "x"}})
    assert isinstance(e, ytapi.ApiKeyError) and e.kind == "quota"
    e = ytapi.classify_error(403, {"error": {"errors": [{"reason": "rateLimitExceeded"}]}})
    assert e.kind == "quota"
    e = ytapi.classify_error(400, {"error": {"errors": [{"reason": "keyInvalid"}], "message": "Bad Request"}})
    assert e.kind == "invalid"
    e = ytapi.classify_error(403, {"error": {"errors": [{"reason": "accessNotConfigured"}]}})
    assert e.kind == "invalid"
    assert isinstance(ytapi.classify_error(503, None), ytapi.ApiUnavailable)


# ───────────────────────────── rotation / quota ─────────────────────────────

def test_round_robin_and_quota_rotation(db, api):  # noqa: ANN001
    set_keys(db, KEY_A, KEY_B, KEY_C)
    api.items = {"aaaaaaaaaaa": _item("aaaaaaaaaaa")}
    used = [ytapi.videos_list(["aaaaaaaaaaa"])[1] for _ in range(6)]
    assert set(used) == {ytapi.key_id(k) for k in (KEY_A, KEY_B, KEY_C)}  # round-robin spreads the calls
    assert all(used.count(k) == 2 for k in set(used))

    api.behaviour = {KEY_A: "quota", KEY_B: "invalid"}
    api.calls.clear()
    for _ in range(4):
        items, kid = ytapi.videos_list(["aaaaaaaaaaa"])
        assert kid == ytapi.key_id(KEY_C) and "aaaaaaaaaaa" in items
    # the bad keys were tried once each, then skipped
    assert sum(c["key"] == KEY_A for c in api.calls) == 1 and sum(c["key"] == KEY_B for c in api.calls) == 1
    sa = ytapi.key_status(ytapi.key_id(KEY_A))
    assert sa["status"] == "quota_exceeded" and sa["until"] == ytapi.next_quota_reset()
    assert ytapi.key_status(ytapi.key_id(KEY_B))["status"] == "invalid"
    assert ytapi.key_status(ytapi.key_id(KEY_C))["units_today"] >= 4

    api.behaviour[KEY_C] = "quota"
    with pytest.raises(ytapi.NoUsableKey):
        ytapi.videos_list(["aaaaaaaaaaa"])
    assert not ytapi.has_usable_key()
    # after the reset time the quota-exhausted keys are usable again; invalid stays invalid
    later = ytapi.next_quota_reset().timestamp() + 1
    assert ytapi.key_status(ytapi.key_id(KEY_A), now=later)["status"] == "ok"
    assert ytapi.key_status(ytapi.key_id(KEY_B), now=later)["status"] == "invalid"


def test_env_key_is_an_extra_key(db, api, monkeypatch):  # noqa: ANN001
    from voti.config import get_config
    monkeypatch.setattr(get_config(), "youtube_api_key", KEY_C)
    set_keys(db, KEY_A)
    keys = ytapi.all_keys()
    assert [(k.key, k.source) for k in keys] == [(KEY_A, "settings"), (KEY_C, "env")]
    set_keys(db, KEY_C)  # stored too: listed once
    assert [(k.key, k.source) for k in ytapi.all_keys()] == [(KEY_C, "settings")]


def test_server_error_is_transient_and_not_blamed_on_key(db, api):  # noqa: ANN001
    set_keys(db, KEY_A)
    api.behaviour = {KEY_A: "error"}
    with pytest.raises(ytapi.ApiUnavailable):
        ytapi.videos_list(["aaaaaaaaaaa"])
    assert ytapi.has_usable_key()


# ───────────────────────────── settings endpoints (secrets) ─────────────────────────────

def _no_full_key(text: str) -> None:
    for k in (KEY_A, KEY_B, KEY_C):
        assert k not in text
        assert k[4:-4] not in text


def test_keys_endpoints_mask_add_keep_remove(admin_client, db, api, monkeypatch):  # noqa: ANN001
    from voti.config import get_config
    from voti.models import AuditLog, Job
    monkeypatch.setattr(get_config(), "youtube_api_key", KEY_C)
    r = admin_client.put("/api/settings/youtube-api/keys", json={"keys": [{"key": KEY_A, "label": "主帳號"},
                                                                         {"key": " " + KEY_B + " "}]})
    assert r.status_code == 200, r.text
    _no_full_key(r.text)
    out = r.json()
    assert [(k["masked"], k["label"], k["source"]) for k in out["keys"]] == [
        ("AIza…0001", "主帳號", "settings"), ("AIza…0002", "", "settings"), ("AIza…0003", "YOUTUBE_API_KEY", "env")]
    assert out["usable"] == 3 and all(k["status"] == "untested" for k in out["keys"])
    ida, idb, idc = (k["id"] for k in out["keys"])

    g = admin_client.get("/api/settings")
    assert g.status_code == 200
    _no_full_key(g.text)
    assert len(g.json()["youtube_api"]["keys"]) == 3
    # the generic setting endpoints never touch the key row
    assert admin_client.put("/api/settings/youtube_api", json={"keys": []}).status_code == 404
    assert "api_keys" not in g.json()["youtube"]

    # keep A (new label), drop B, the env key id is accepted and ignored, add nothing
    r = admin_client.put("/api/settings/youtube-api/keys", json={"keys": [{"id": ida, "label": "x"}, {"id": idc}]})
    assert r.status_code == 200, r.text
    assert [(k["id"], k["label"]) for k in r.json()["keys"]] == [(ida, "x"), (idc, "YOUTUBE_API_KEY")]
    assert [k["key"] for k in ytapi.stored_keys(db)] == [KEY_A]
    assert admin_client.put("/api/settings/youtube-api/keys", json={"keys": [{"id": idb}]}).status_code == 422
    assert admin_client.put("/api/settings/youtube-api/keys", json={"keys": [{"key": "short"}]}).status_code == 422
    # audit rows only hold masked keys; jobs never carry keys
    for a in db.scalars(select(AuditLog).where(AuditLog.action.like("settings.youtube_api%"))):
        _no_full_key(json.dumps([a.before, a.after]))
    for j in db.scalars(select(Job)):
        _no_full_key(json.dumps([j.payload, j.result]))


def test_annotator_cannot_touch_keys(annotator_client):  # noqa: ANN001
    assert annotator_client.get("/api/settings/youtube-api").status_code == 403
    assert annotator_client.put("/api/settings/youtube-api/keys", json={"keys": []}).status_code == 403


def test_test_key_endpoint(admin_client, db, api):  # noqa: ANN001
    set_keys(db, KEY_A, KEY_B)
    api.behaviour = {KEY_B: "disabled"}
    r = admin_client.post("/api/settings/youtube-api/test", json={"id": ytapi.key_id(KEY_A)})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["key"]["status"] == "ok"
    assert r.json()["key"]["units_today"] == 1 and api.calls[-1]["part"] == "id"
    r = admin_client.post("/api/settings/youtube-api/test", json={"id": ytapi.key_id(KEY_B)})
    body = r.json()
    assert body["ok"] is False and body["status"] == "invalid" and body["key"]["status"] == "invalid"
    assert "accessnotconfigured" in body["reason"]
    _no_full_key(r.text)
    # an unsaved key can be tried before adding it (status not stored)
    api.behaviour[KEY_C] = "invalid"
    r = admin_client.post("/api/settings/youtube-api/test", json={"key": KEY_C})
    assert r.json()["status"] == "invalid" and r.json()["key"] is None
    _no_full_key(r.text)
    # a successful re-test clears the invalid mark
    api.behaviour[KEY_B] = "ok"
    r = admin_client.post("/api/settings/youtube-api/test", json={"id": ytapi.key_id(KEY_B)})
    assert r.json()["key"]["status"] == "ok"


def test_key_never_logged(db, api, h, factory, caplog):  # noqa: ANN001
    caplog.set_level(logging.DEBUG)
    set_keys(db, KEY_A, KEY_B)
    api.behaviour = {KEY_A: "quota"}
    ch = factory.channel(platform_channel_id=UC)
    m = factory.media(channel_id=ch.id, platform_media_id="aaaaaaaaaaa", status="listed")
    api.items = {"aaaaaaaaaaa": _item("aaaaaaaaaaa")}
    job, _ = run(db, "media.api_detail", channel_id=ch.id)
    assert job.status == "completed", job.error_message
    _no_full_key(caplog.text)
    db.refresh(m)
    _no_full_key(json.dumps(m.metadata_))


# ───────────────────────────── media.api_detail ─────────────────────────────

def test_batch_fill_and_caption_branching(db, api, h, factory):  # noqa: ANN001
    from voti.models import Job, Media
    set_keys(db, KEY_A)
    ch = factory.channel(platform_channel_id=UC)
    no_cc = factory.media(channel_id=ch.id, platform_media_id="nocaptions1", status="listed", kind="video",
                          metadata_={"published_approximate": True})
    cc = factory.media(channel_id=ch.id, platform_media_id="hascaption1", status="listed")
    gone = factory.media(channel_id=ch.id, platform_media_id="deletedvid1", status="listed")
    done = factory.media(channel_id=ch.id, platform_media_id="detailedvid", status="detailed",
                         detailed_at=datetime(2026, 1, 1, tzinfo=timezone.utc), has_manual_captions=True,
                         manual_caption_langs=["zh-TW"])
    api.items = {"nocaptions1": _item("nocaptions1"), "hascaption1": _item("hascaption1", caption=True),
                 "detailedvid": _item("detailedvid")}
    job, follow = run(db, "media.api_detail", channel_id=ch.id)
    assert job.status == "completed", job.error_message
    assert job.result["no_captions"] == 1 and job.result["captions"] == 1 and job.result["unavailable"] == 1
    assert len(api.calls) == 1 and sorted(api.calls[0]["ids"]) == ["deletedvid1", "hascaption1", "nocaptions1"]
    assert api.calls[0]["part"] == ytapi.PARTS
    for m in (no_cc, cc, gone, done):
        db.refresh(m)
    # caption false: fully detailed, no yt-dlp needed
    assert no_cc.status == "detailed" and no_cc.detailed_at is not None and no_cc.api_checked_at is not None
    assert no_cc.has_manual_captions is False and no_cc.has_auto_captions is None and no_cc.manual_caption_langs == []
    assert no_cc.published_at == datetime(2023, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
    assert no_cc.metadata_["published_approximate"] is False and no_cc.metadata_["detail_source"] == "ytapi"
    assert (no_cc.duration_sec, no_cc.license, no_cc.view_count, no_cc.like_count, no_cc.comment_count) == \
        (3723, "creative_commons", 1234, 56, 7)
    assert no_cc.title == "api nocaptions1" and no_cc.tags == ["台語", "教育"] and no_cc.categories == ["Education"]
    assert no_cc.language == "zh-TW" and no_cc.availability == "public"
    assert no_cc.thumbnail_url == "https://i.ytimg.com/maxres.jpg"
    assert no_cc.metadata_["ytapi"]["caption"] is False and no_cc.metadata_["ytapi"]["item"]["id"] == "nocaptions1"
    # caption true: CC known, languages still pending → yt-dlp sweep queued for the channel
    assert cc.has_manual_captions is True and cc.manual_caption_langs == [] and cc.detailed_at is None
    assert cc.status == "listed" and cc.api_checked_at is not None
    sweeps = [f for f in follow if f.kind == "media.detail"]
    assert len(sweeps) == 1 and sweeps[0].channel_id == ch.id and sweeps[0].media_id is None
    # missing from the answer: unavailable like the yt-dlp path
    assert gone.status == "failed" and gone.failure_code == "unavailable" and gone.api_checked_at is not None
    # already detailed by yt-dlp: untouched
    assert done.api_checked_at is None and done.manual_caption_langs == ["zh-TW"]
    # nothing left for the API: re-running is a no-op
    job, _ = run(db, "media.api_detail", channel_id=ch.id)
    assert job.result["calls"] == 0 and len(api.calls) == 1
    assert db.scalars(select(Job).where(Job.kind == "media.api_detail", Job.status == "queued")).all() == []
    assert db.scalar(select(Media).where(Media.platform_media_id == "nocaptions1")).status == "detailed"


def test_fifty_ids_per_call_and_chaining(db, api, h, factory, monkeypatch):  # noqa: ANN001
    set_keys(db, KEY_A)
    monkeypatch.setattr(h, "API_CALLS_PER_RUN", 2)
    ch = factory.channel(platform_channel_id=UC)
    vids = [f"v{i:010d}" for i in range(120)]
    for v in vids:
        factory.media(channel_id=ch.id, platform_media_id=v, status="listed")
    api.items = {v: _item(v) for v in vids}
    job, follow = run(db, "media.api_detail", payload={"all": True})
    assert job.status == "completed", job.error_message
    assert [len(c["ids"]) for c in api.calls] == [50, 50] and job.result["more"] is True
    nxt = [f for f in follow if f.kind == "media.api_detail"]
    assert len(nxt) == 1 and nxt[0].payload == {"all": True}
    from voti.queue import run_one
    run_one(str(nxt[0].id), "test")
    assert [len(c["ids"]) for c in api.calls] == [50, 50, 20]
    assert len({i for c in api.calls for i in c["ids"]}) == 120


def test_replaces_queued_detail_jobs(db, api, h, factory):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import enqueue
    set_keys(db, KEY_A)
    ch = factory.channel(platform_channel_id=UC)
    a = factory.media(channel_id=ch.id, platform_media_id="aaaaaaaaaaa", status="listed")
    b = factory.media(channel_id=ch.id, platform_media_id="bbbbbbbbbbb", status="listed")
    c = factory.media(channel_id=ch.id, platform_media_id="ccccccccccc", status="listed")
    plain = enqueue(db, "media.detail", media_id=a.id, channel_id=ch.id)
    recheck = enqueue(db, "media.detail", media_id=b.id, channel_id=ch.id, payload={"force": True, "verify": True})
    with_cc = enqueue(db, "media.detail", media_id=c.id, channel_id=ch.id)
    db.commit()
    api.items = {"aaaaaaaaaaa": _item("aaaaaaaaaaa"), "bbbbbbbbbbb": _item("bbbbbbbbbbb"),
                 "ccccccccccc": _item("ccccccccccc", caption=True)}
    job, _ = run(db, "media.api_detail", payload={"media_ids": [str(a.id), str(b.id), str(c.id)]})
    assert job.status == "completed", job.error_message
    assert job.result["replaced"] == 1
    db.expire_all()
    assert db.get(Job, plain.id).status == "cancelled" and db.get(Job, plain.id).error_code == "replaced"
    assert db.get(Job, recheck.id).status == "queued"       # caption re-checks are kept
    assert db.get(Job, with_cc.id).status == "queued"       # uploader captions: yt-dlp still needed


def test_detail_sweep_and_batches_skip_api_detailed(db, api, h, factory, monkeypatch):  # noqa: ANN001
    from voti.sources import youtube as yt
    set_keys(db, KEY_A)
    ch = factory.channel(platform_channel_id=UC)
    seen_cc = factory.media(channel_id=ch.id, platform_media_id="hascaption1", status="listed",
                            api_checked_at=datetime(2026, 10, 28, tzinfo=timezone.utc), has_manual_captions=True)
    unseen = factory.media(channel_id=ch.id, platform_media_id="notseenyet1", status="listed")
    api_done = factory.media(channel_id=ch.id, platform_media_id="nocaptions1", status="detailed",
                             detailed_at=datetime(2026, 10, 28, tzinfo=timezone.utc), has_manual_captions=False,
                             metadata_={"detail_source": "ytapi"})
    fetched = []

    def fake_detail(vid, fo):
        fetched.append(vid)
        return {"id": vid, "title": vid, "channel_id": UC, "subtitles": {}, "automatic_captions": {}}, []
    monkeypatch.setattr(yt, "fetch_detail", fake_detail)
    # channel sweep with a usable key: only media the API has seen (the others get the API pass first)
    job, follow = run(db, "media.detail", channel_id=ch.id, payload={"batch_size": 20})
    assert job.status == "completed", job.error_message
    assert fetched == ["hascaption1"]
    assert [f.kind for f in follow] == ["media.api_detail"]  # the unseen media will get the API pass
    # an explicit batch (playlist) skips media the API already detailed
    fetched.clear()
    job, _ = run(db, "media.detail", payload={"media_ids": [str(api_done.id), str(unseen.id)]})
    assert fetched == ["notseenyet1"] and job.result["skipped_api"] == 1
    db.refresh(seen_cc)
    assert seen_cc.detailed_at is not None and seen_cc.metadata_["detail_source"] == "yt-dlp"


def test_fallback_without_usable_keys(db, api, h, factory, monkeypatch):  # noqa: ANN001
    from voti.models import Job
    from voti.sources import youtube as yt
    set_keys(db, KEY_A)
    api.behaviour = {KEY_A: "quota"}
    ch = factory.channel(platform_channel_id=UC)
    m = factory.media(channel_id=ch.id, platform_media_id="aaaaaaaaaaa", status="listed")
    job, follow = run(db, "media.api_detail", channel_id=ch.id)
    assert job.status == "completed" and job.result["fallback"] is True
    assert [(f.kind, f.channel_id) for f in follow] == [("media.detail", ch.id)]
    db.refresh(m)
    assert m.api_checked_at is None and m.status == "listed"
    # the yt-dlp sweep now takes every undetailed media (unchanged path)
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: ({"id": vid, "title": vid, "channel_id": UC,
                                                              "subtitles": {}, "automatic_captions": {}}, []))
    job, _ = run(db, "media.detail", channel_id=ch.id)
    db.refresh(m)
    assert m.detailed_at is not None
    assert not db.scalars(select(Job).where(Job.kind == "media.api_detail", Job.status == "queued")).all()


def test_transient_api_error_retries(db, api, h, factory):  # noqa: ANN001
    set_keys(db, KEY_A)
    api.behaviour = {KEY_A: "error"}
    ch = factory.channel(platform_channel_id=UC)
    factory.media(channel_id=ch.id, platform_media_id="aaaaaaaaaaa", status="listed")
    job, _ = run(db, "media.api_detail", channel_id=ch.id)
    assert job.status == "queued" and job.phase == "retry_wait" and job.error_code == "ytapi_unavailable"


# ───────────────────────────── pipeline integration ─────────────────────────────

def _entry(vid):  # noqa: ANN001, ANN202
    from voti.sources import youtube as yt
    return {"platform_media_id": vid, "kind": "video", "url": yt.video_url(vid), "title": vid, "description": "",
            "duration_sec": 60.0, "view_count": 5, "thumbnail_url": "t",
            "published_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "published_approximate": True,
            "channel_id": None, "live_status": None, "availability": None}


def test_listing_queues_api_pass_instead_of_ytdlp(db, api, h, factory, monkeypatch):  # noqa: ANN001
    from voti.sources import youtube as yt
    ch = factory.channel(platform_channel_id=UC)
    monkeypatch.setattr(yt, "list_channel", lambda cid, fo, tabs, limit_per_tab=None, progress=None: {
        "channel": None, "tabs": {"videos": 2}, "entries": [_entry("aaaaaaaaaaa"), _entry("bbbbbbbbbbb")],
        "warnings": []})
    # no key: the yt-dlp sweep as before
    job, follow = run(db, "channel.list", channel_id=ch.id)
    assert job.result["caption_info"] == "detail" and [f.kind for f in follow] == ["media.detail"]
    from voti.models import Job
    db.execute(Job.__table__.delete().where(Job.kind == "media.detail"))
    db.commit()
    set_keys(db, KEY_A)
    job, follow = run(db, "channel.list", channel_id=ch.id, payload={"scheduled": True, "detail": True})
    assert job.result["caption_info"] == "api" and [f.kind for f in follow] == ["media.api_detail"]
    api.items = {"aaaaaaaaaaa": _item("aaaaaaaaaaa"), "bbbbbbbbbbb": _item("bbbbbbbbbbb", caption=True)}
    job, follow = run(db, "media.api_detail", channel_id=ch.id)
    assert job.result["no_captions"] == 1 and [f.kind for f in follow] == ["media.detail"]


def test_resolve_single_video_uses_api(db, api, h):  # noqa: ANN001
    set_keys(db, KEY_A)
    job, follow = run(db, "source.resolve", payload={"url": "https://youtu.be/aaaaaaaaaaa"})
    assert [f.kind for f in follow] == ["media.api_detail"]
    api.items = {"aaaaaaaaaaa": _item("aaaaaaaaaaa", caption=True)}
    from voti.models import Channel, Media
    from voti.queue import run_one
    run_one(str(follow[0].id), "test")
    db.expire_all()
    m = db.scalar(select(Media))
    assert m.has_manual_captions is True and m.channel_id is not None
    # its channel was added (auto) and a single yt-dlp detail for the languages follows
    from voti.models import Job
    assert db.scalar(select(Channel)).platform_channel_id == UC
    assert db.scalar(select(Job).where(Job.kind == "media.detail", Job.media_id == m.id)) is not None


def test_backfill_endpoint_and_auto_start(admin_client, db, api, factory):  # noqa: ANN001
    from voti.models import Job
    ch = factory.channel(platform_channel_id=UC)
    factory.media(channel_id=ch.id, platform_media_id="aaaaaaaaaaa", status="listed")
    factory.media(channel_id=ch.id, platform_media_id="bbbbbbbbbbb", status="detailed",
                  detailed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert admin_client.post("/api/settings/youtube-api/backfill").status_code == 409  # no key
    r = admin_client.put("/api/settings/youtube-api/keys", json={"keys": [{"key": KEY_A}]})
    assert r.json()["backfill_queued"] is True and r.json()["backfill"] == {
        "pending": 1, "active": True, "job_id": r.json()["backfill"]["job_id"], "langs_pending": 0}
    jobs = db.scalars(select(Job).where(Job.kind == "media.api_detail")).all()
    assert len(jobs) == 1 and jobs[0].payload["all"] is True and jobs[0].channel_id is None
    # adding a second key later does not start another one; the button reports the active job
    r = admin_client.put("/api/settings/youtube-api/keys", json={"keys": [{"id": ytapi.key_id(KEY_A)},
                                                                         {"key": KEY_B}]})
    assert r.json()["backfill_queued"] is False
    r = admin_client.post("/api/settings/youtube-api/backfill")
    assert r.status_code == 200 and r.json()["already_active"] is True and r.json()["queued"] == 1
    # channel progress counts the global backfill as active for channels it still has media for
    c = admin_client.get(f"/api/channels/{ch.id}").json()
    assert c["detail_progress"]["active"] is True


def test_channel_progress_and_download_with_unknown_langs(admin_client, db, factory):  # noqa: ANN001
    ch = factory.channel(platform_channel_id=UC)
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    m1 = factory.media(channel_id=ch.id, platform_media_id="aaaaaaaaaaa", status="listed", api_checked_at=now,
                       has_manual_captions=True)
    factory.media(channel_id=ch.id, platform_media_id="bbbbbbbbbbb", status="detailed", detailed_at=now,
                  api_checked_at=now, has_manual_captions=False)
    factory.media(channel_id=ch.id, platform_media_id="ccccccccccc", status="listed")
    dp = admin_client.get(f"/api/channels/{ch.id}").json()["detail_progress"]
    assert (dp["detailed"], dp["total"], dp["langs_pending"]) == (2, 3, 1)
    # facets: CC known before the languages; no-CC from the API counts as none
    f = admin_client.get("/api/media", params={"channel_id": str(ch.id)}).json()["facets"]["captions"]
    assert (f["manual"], f["none"], f["unknown"]) == (1, 1, 1)
    sel = admin_client.post("/api/media/selection-captions", json={"media_ids": [str(m1.id)]}).json()
    assert sel["with_manual"] == 1 and sel["langs_unknown"] == 1 and sel["langs"] == {}
    # a language choice cannot exclude it yet: the download worker picks the tracks
    r = admin_client.post("/api/media/download", json={"media_ids": [str(m1.id)], "caption_langs": ["zh-TW"],
                                                       "ocr": False})
    assert r.json()["queued"] == 1, r.json()
