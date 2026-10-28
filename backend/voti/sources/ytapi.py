"""YouTube Data API v3: fast per-video metadata (job `media.api_detail`) and the API key store / rotation.

Keys
  * Stored in the `settings` row `youtube_api` = {"keys": [{"id", "key", "label", "added_at"}]} (its own row, not
    part of the `youtube` setting: the generic PUT /settings/{key} rewrites a whole row and audits it in full,
    which must never happen to secrets). The legacy env `YOUTUBE_API_KEY` (config.youtube_api_key) is an extra
    key (source "env", not removable in the UI).
  * A key's id is "k" + sha256(key)[:10] (stable, reveals nothing usable); the UI only ever sees `mask(key)`.
  * Keys travel in the `X-Goog-Api-Key` header, never in a URL, so no request log / exception text contains one.
    Nothing in this module logs a key.

Status (Redis, shared by every worker; `voti:ytapi:*`)
  * quota: quotaExceeded / dailyLimitExceeded / rateLimitExceeded / userRateLimitExceeded → the key is skipped
    until the next midnight America/Los_Angeles (when YouTube resets the daily quota).
  * invalid: keyInvalid / keyExpired / accessNotConfigured (API not enabled) / referrer- or IP-blocked keys → skipped
    until a successful test (POST /settings/youtube-api/test) or until the key is removed and added again.
  * per key: last_used, last_ok, last error, units used today (Pacific day; videos.list costs 1 unit per call
    whatever the number of ids ≤ 50). Redis down → every key counts as usable.
  Round-robin: a Redis counter picks the first key of each call; a key that fails with quota/invalid is marked and
  the next one is tried. No usable key → NoUsableKey (callers fall back to the yt-dlp `media.detail` path).
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from ..config import get_config
from ..models import Setting

log = logging.getLogger("voti.sources.ytapi")

API_URL = "https://www.googleapis.com/youtube/v3/videos"
PARTS = "snippet,contentDetails,status,statistics,liveStreamingDetails"
SETTING_KEY = "youtube_api"
MAX_IDS = 50                 # videos.list accepts ≤ 50 ids per call
UNITS_PER_CALL = 1           # videos.list quota cost (any parts)
DAILY_QUOTA = 10_000         # default per Google Cloud project
TEST_VIDEO_ID = "jNQXAC9IVRw"  # any id works: unknown ids just come back empty (1 unit)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
PACIFIC = ZoneInfo("America/Los_Angeles")
KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{20,100}$")

QUOTA_REASONS = {"quotaexceeded", "dailylimitexceeded", "ratelimitexceeded", "userratelimitexceeded",
                 "dailylimitexceededunreg", "rate_limit_exceeded", "resource_exhausted"}
INVALID_REASONS = {"keyinvalid", "keyexpired", "accessnotconfigured", "iprefererblocked", "forbidden",
                   "api_key_invalid", "api_key_expired", "service_disabled", "api_key_service_blocked",
                   "api_key_http_referrer_blocked", "api_key_ip_address_blocked", "api_key_android_app_blocked",
                   "api_key_ios_app_blocked", "permission_denied", "unauthenticated"}

# tests replace this with an httpx.MockTransport (no network)
_TRANSPORT: httpx.BaseTransport | None = None


# ───────────────────────────── errors ─────────────────────────────

class ApiKeyError(Exception):
    """The key cannot be used: kind "quota" (until the Pacific midnight) or "invalid"."""

    def __init__(self, kind: str, reason: str, message: str):
        super().__init__(message)
        self.kind, self.reason, self.message = kind, reason, message


class ApiUnavailable(Exception):
    """Network error / timeout / 5xx / unexpected answer — not the key's fault (job retries later)."""


class NoUsableKey(Exception):
    pass


# ───────────────────────────── keys ─────────────────────────────

@dataclass
class ApiKey:
    id: str
    key: str
    source: str = "settings"   # settings | env
    label: str = ""
    added_at: str | None = None


def key_id(key: str) -> str:
    return "k" + hashlib.sha256(key.encode()).hexdigest()[:10]


def mask(key: str) -> str:
    key = key or ""
    if len(key) < 12:
        return "…" + key[-2:] if key else "…"
    return f"{key[:4]}…{key[-4:]}"


def normalize_key(raw: str) -> str | None:
    k = (raw or "").strip()
    return k if KEY_RE.match(k) else None


def stored_keys(s) -> list[dict]:  # noqa: ANN001
    row = s.get(Setting, SETTING_KEY)
    keys = (row.value or {}).get("keys") if row is not None else None
    return [k for k in (keys or []) if isinstance(k, dict) and k.get("key")]


def all_keys(s=None) -> list[ApiKey]:  # noqa: ANN001
    """Settings keys (in their order) + the env key when set and not also stored."""
    if s is None:
        from ..db import session_scope
        with session_scope() as s2:
            return all_keys(s2)
    out = [ApiKey(id=k.get("id") or key_id(k["key"]), key=k["key"], label=k.get("label") or "",
                  added_at=k.get("added_at")) for k in stored_keys(s)]
    env = normalize_key(get_config().youtube_api_key or "")
    if env and all(k.key != env for k in out):
        out.append(ApiKey(id=key_id(env), key=env, source="env", label="YOUTUBE_API_KEY"))
    return out


# ───────────────────────────── status (Redis) ─────────────────────────────

def _r():  # noqa: ANN202
    from ..queue import redis_client
    return redis_client()


def _st_key(kid: str) -> str:
    return f"voti:ytapi:st:{kid}"


def _units_key(kid: str, day: str) -> str:
    return f"voti:ytapi:units:{kid}:{day}"


def pacific_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(PACIFIC).strftime("%Y-%m-%d")


def next_quota_reset(now: datetime | None = None) -> datetime:
    """The next midnight America/Los_Angeles (YouTube's daily quota reset), as UTC."""
    local = (now or datetime.now(timezone.utc)).astimezone(PACIFIC)
    nxt = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=PACIFIC)
    return nxt.astimezone(timezone.utc)


def _dec(v) -> str:  # noqa: ANN001
    return v.decode() if isinstance(v, bytes) else str(v)


def raw_status(kid: str) -> dict:
    try:
        return {_dec(k): _dec(v) for k, v in (_r().hgetall(_st_key(kid)) or {}).items()}
    except Exception:  # noqa: BLE001  (redis down: nothing known)
        return {}


def key_status(kid: str, now: float | None = None) -> dict:
    """→ {status: ok|untested|quota_exceeded|invalid, until: datetime|None, last_used_at, last_ok_at, last_error,
    units_today}"""
    now = now or time.time()
    st = raw_status(kid)
    status = "ok" if st.get("last_ok") else "untested"
    until = None
    if st.get("state") == "invalid":
        status = "invalid"
    elif st.get("state") == "quota" and float(st.get("until") or 0) > now:
        status = "quota_exceeded"
        until = datetime.fromtimestamp(float(st["until"]), tz=timezone.utc)
    try:
        units = int(_r().get(_units_key(kid, pacific_day())) or 0)
    except Exception:  # noqa: BLE001
        units = 0

    def ts(k: str) -> datetime | None:
        return datetime.fromtimestamp(float(st[k]), tz=timezone.utc) if st.get(k) else None
    return {"status": status, "until": until, "last_used_at": ts("last_used"), "last_ok_at": ts("last_ok"),
            "last_error": st.get("error") or None, "units_today": units}


def usable(kid: str, now: float | None = None) -> bool:
    return key_status(kid, now)["status"] in ("ok", "untested")


def usable_keys(s=None) -> list[ApiKey]:  # noqa: ANN001
    return [k for k in all_keys(s) if usable(k.id)]


def has_usable_key(s=None) -> bool:  # noqa: ANN001
    return bool(usable_keys(s))


def mark_used(kid: str, ok: bool = True, units: int = UNITS_PER_CALL) -> None:
    try:
        r = _r()
        now = time.time()
        fields = {"last_used": now}
        if ok:
            fields["last_ok"] = now
        r.hset(_st_key(kid), mapping=fields)
        if ok:
            r.hdel(_st_key(kid), "state", "until", "error")
        uk = _units_key(kid, pacific_day())
        r.incrby(uk, units)
        r.expire(uk, 3 * 86400)
    except Exception:  # noqa: BLE001
        pass


def mark_bad(kid: str, err: ApiKeyError) -> None:
    """quota → skipped until the next Pacific midnight; invalid → until a successful test / removal."""
    fields: dict = {"state": err.kind, "error": f"{err.reason}: {err.message}"[:300], "last_used": time.time()}
    if err.kind == "quota":
        fields["until"] = next_quota_reset().timestamp()
    log.warning("YouTube API key %s marked %s (%s)", kid, err.kind, err.reason)
    try:
        _r().hset(_st_key(kid), mapping=fields)
    except Exception:  # noqa: BLE001
        pass


def forget(kid: str) -> None:
    try:
        r = _r()
        r.delete(_st_key(kid))
        for k in r.scan_iter(f"voti:ytapi:units:{kid}:*"):
            r.delete(k)
    except Exception:  # noqa: BLE001
        pass


def _rotated(keys: list[ApiKey]) -> list[ApiKey]:
    if len(keys) < 2:
        return keys
    try:
        start = int(_r().incr("voti:ytapi:rr"))
    except Exception:  # noqa: BLE001
        start = int(time.time())
    i = start % len(keys)
    return keys[i:] + keys[:i]


# ───────────────────────────── HTTP ─────────────────────────────

def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=_TRANSPORT, follow_redirects=False)


def classify_error(status: int, body: dict | None) -> Exception:
    """Map a non-200 videos.list answer to ApiKeyError (quota / invalid) or ApiUnavailable."""
    err = (body or {}).get("error") or {}
    reasons = [str(e.get("reason") or "") for e in err.get("errors") or [] if isinstance(e, dict)]
    reasons += [str(d.get("reason") or "") for d in err.get("details") or [] if isinstance(d, dict)]
    reasons.append(str(err.get("status") or ""))
    low = [x.lower() for x in reasons if x]
    message = str(err.get("message") or f"HTTP {status}")[:300]
    mlow = message.lower()
    quota = next((x for x in low if x in QUOTA_REASONS), None)
    if quota or status == 429:
        return ApiKeyError("quota", quota or "http_429", message)
    invalid = next((x for x in low if x in INVALID_REASONS), None)
    if invalid or "api key not valid" in mlow or "api key expired" in mlow or "has not been used in project" in mlow \
            or status in (401, 403):
        return ApiKeyError("invalid", invalid or f"http_{status}", message)
    if status >= 500:
        return ApiUnavailable(f"YouTube API HTTP {status}: {message}")
    return ApiUnavailable(f"YouTube API HTTP {status}: {message}")


def _get(key: str, ids: list[str], parts: str = PARTS) -> dict:
    try:
        with _client() as c:
            resp = c.get(API_URL, params={"part": parts, "id": ",".join(ids), "maxResults": MAX_IDS},
                         headers={"X-Goog-Api-Key": key, "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise ApiUnavailable(f"YouTube API 連線失敗：{type(e).__name__}") from None
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code == 200 and isinstance(body, dict):
        return body
    raise classify_error(resp.status_code, body if isinstance(body, dict) else None)


def videos_list(ids: list[str], s=None) -> tuple[dict[str, dict], str]:  # noqa: ANN001
    """One videos.list call (≤ 50 ids) with key rotation. → ({video id: item}, key id used).
    Raises NoUsableKey (every key exhausted / invalid / none configured) or ApiUnavailable."""
    if len(ids) > MAX_IDS:
        raise ValueError("videos.list takes at most 50 ids")
    keys = _rotated(usable_keys(s))
    if not keys:
        raise NoUsableKey()
    for k in keys:
        try:
            body = _get(k.key, ids)
        except ApiKeyError as e:
            mark_bad(k.id, e)
            continue
        mark_used(k.id)
        return {str(it.get("id")): it for it in body.get("items") or [] if isinstance(it, dict)}, k.id
    raise NoUsableKey()


def test_key(key: str, kid: str | None = None) -> dict:
    """One cheap videos.list call. → {ok, status: ok|invalid|quota_exceeded|unavailable, message}; updates the
    key's stored status when `kid` is given."""
    try:
        _get(key, [TEST_VIDEO_ID], parts="id")
    except ApiKeyError as e:
        if kid:
            mark_bad(kid, e)
        status = "quota_exceeded" if e.kind == "quota" else "invalid"
        return {"ok": status == "quota_exceeded", "status": status, "reason": e.reason, "message": e.message}
    except ApiUnavailable as e:
        return {"ok": False, "status": "unavailable", "reason": "unavailable", "message": str(e)}
    if kid:
        mark_used(kid)
    return {"ok": True, "status": "ok", "reason": None, "message": ""}


# ───────────────────────────── item → media values ─────────────────────────────

_DUR_RE = re.compile(r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")

# videoCategories (stable ids; yt-dlp reports the names)
CATEGORIES = {"1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals",
              "17": "Sports", "18": "Short Movies", "19": "Travel & Events", "20": "Gaming", "21": "Videoblogging",
              "22": "People & Blogs", "23": "Comedy", "24": "Entertainment", "25": "News & Politics",
              "26": "Howto & Style", "27": "Education", "28": "Science & Technology",
              "29": "Nonprofits & Activism", "30": "Movies", "31": "Anime/Animation", "32": "Action/Adventure",
              "33": "Classics", "34": "Comedy", "35": "Documentary", "36": "Drama", "37": "Family", "38": "Foreign",
              "39": "Horror", "40": "Sci-Fi/Fantasy", "41": "Thriller", "42": "Shorts", "43": "Shows",
              "44": "Trailers"}


def parse_duration(v: str | None) -> float | None:
    m = _DUR_RE.match(v or "")
    if not m or not v or v in ("P", "PT"):
        return None
    w, d, h, mi, s = m.groups()
    return float(w or 0) * 604800 + float(d or 0) * 86400 + float(h or 0) * 3600 + float(mi or 0) * 60 + \
        float(s or 0)


def parse_time(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _int(v):  # noqa: ANN001, ANN202
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def best_thumbnail(thumbs: dict | None) -> str | None:
    for k in ("maxres", "standard", "high", "medium", "default"):
        t = (thumbs or {}).get(k)
        if isinstance(t, dict) and t.get("url"):
            return t["url"]
    return None


def summarize_item(it: dict) -> dict:
    """videos.list item → Media column values + `caption` (uploader captions exist) + extras."""
    sn = it.get("snippet") or {}
    cd = it.get("contentDetails") or {}
    st = it.get("status") or {}
    stats = it.get("statistics") or {}
    live = it.get("liveStreamingDetails") or None
    lbc = sn.get("liveBroadcastContent") or "none"
    if lbc == "upcoming":
        live_status = "is_upcoming"
    elif lbc == "live":
        live_status = "is_live"
    elif live:
        live_status = "was_live" if live.get("actualEndTime") or live.get("actualStartTime") else "not_live"
    else:
        live_status = "not_live"
    rating = cd.get("contentRating") or {}
    cat = str(sn.get("categoryId") or "")
    lang = sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or None
    return {
        "title": sn.get("title") or "",
        "description": sn.get("description") or "",
        "tags": list(sn.get("tags") or []),
        "categories": [CATEGORIES.get(cat, cat)] if cat else [],
        "published_at": parse_time(sn.get("publishedAt")),
        "duration_sec": parse_duration(cd.get("duration")),
        "license": "creative_commons" if st.get("license") == "creativeCommon" else "youtube",
        "view_count": _int(stats.get("viewCount")),
        "like_count": _int(stats.get("likeCount")),
        "comment_count": _int(stats.get("commentCount")),
        "language": str(lang)[:16] if lang else None,
        "availability": st.get("privacyStatus") or None,   # public | unlisted | private (yt-dlp vocabulary)
        "age_limit": 18 if rating.get("ytRating") == "ytAgeRestricted" else 0,
        "was_live": live_status == "was_live",
        "live_status": live_status,
        "thumbnail_url": best_thumbnail(sn.get("thumbnails")),
        "caption": str(cd.get("caption") or "").lower() == "true",
        "channel_platform_id": sn.get("channelId") or None,
        "channel_title": sn.get("channelTitle") or None,
        "upload_status": st.get("uploadStatus"),
        "extras": {k: v for k, v in {
            "embeddable": st.get("embeddable"), "made_for_kids": st.get("madeForKids"),
            "self_declared_made_for_kids": st.get("selfDeclaredMadeForKids"),
            "public_stats_viewable": st.get("publicStatsViewable"), "upload_status": st.get("uploadStatus"),
            "failure_reason": st.get("failureReason"), "rejection_reason": st.get("rejectionReason"),
            "region_restriction": cd.get("regionRestriction"), "definition": cd.get("definition"),
            "dimension": cd.get("dimension"), "projection": cd.get("projection"),
            "licensed_content": cd.get("licensedContent"), "category_id": cat or None,
            "default_language": sn.get("defaultLanguage"), "default_audio_language": sn.get("defaultAudioLanguage"),
            "live_broadcast_content": lbc, "live_status": live_status,
            "age_restricted": rating.get("ytRating") == "ytAgeRestricted" or None,
        }.items() if v is not None},
    }


# upload states for which the video cannot be fetched (deleted / failed / rejected uploads)
GONE_UPLOAD_STATES = ("deleted", "failed", "rejected")
UNAVAILABLE_MESSAGE = "影片／頻道不存在或已被移除。（YouTube Data API 查無此影片：已刪除、私人或不公開存取）"
