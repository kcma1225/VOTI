"""Persistent single-worker queue. Claim is atomic; stale running jobs can be retried."""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .core import (add_subtitles, adopt_track_language, asset_path, create_source, dumps, ensure_normalized, ensure_playback, import_local,
                   new_id, segment_source, store_asset, try_playback, update_source)
from .db import connect, init_db, now, rows, transaction
from .media import sha256
from .speakers import MODEL_ID, ModelUnavailable, cluster, extract_embedding, top_k


KINDS = ("youtube_inspect", "youtube_acquire", "youtube_subtitles", "embedding", "local_import", "segment", "alignment",
         "playback", "reprocess")
ACTIVE = ("queued", "running")
# Model jobs can run for minutes; they get their own lane so a caption fetch never waits behind them.
HEAVY_KINDS = ("embedding", "alignment")
# Weighted jobs report on a 0–1000 scale so ffmpeg/yt-dlp fractions keep their resolution.
SCALE = 1000


def enqueue(settings: Settings, kind: str, source_id: str | None = None, payload: dict | None = None) -> str:
    if kind not in KINDS:
        raise ValueError("不支援的工作類型")
    init_db(settings.database)
    with connect(settings.database) as db, transaction(db):
        db.execute("SELECT pg_advisory_xact_lock(hashtext('twcorpus.enqueue'))")  # serialize the check-then-insert
        if source_id:
            existing = db.execute("SELECT id FROM jobs WHERE kind=? AND source_id=? AND status IN ('queued','running')",
                                  (kind, source_id)).fetchone()
        else:
            # Source-less jobs are deduplicated by kind and payload, so repeat clicks reuse the open job.
            existing = db.execute("SELECT id FROM jobs WHERE kind=? AND source_id IS NULL AND payload_json=? "
                                  "AND status IN ('queued','running')", (kind, dumps(payload or {}))).fetchone()
        if existing:
            return existing["id"]
        job_id = new_id()
        db.execute("INSERT INTO jobs(id,kind,source_id,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                   (job_id, kind, source_id, "queued", dumps(payload or {}), now(), now()))
        return job_id


def list_jobs(settings: Settings, limit: int = 200) -> list[dict]:
    # Active jobs are always included, however many finished jobs came after them.
    with connect(settings.database) as db:
        return rows(db, "SELECT * FROM jobs WHERE status IN ('queued','running') OR id IN "
                    "(SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?) ORDER BY created_at DESC", (limit,))


def estimate(job: dict, at: datetime | None = None) -> dict:
    """Elapsed time and a linear ETA from the job's own progress. None when there is too little to go on."""
    at = at or datetime.now(timezone.utc)
    total, current = job["progress_total"] or 0, job["progress_current"] or 0
    fraction = min(1.0, current / total) if total else None
    elapsed = eta = None
    if job["status"] == "running" and job["claimed_at"]:
        elapsed = max(0.0, (at - datetime.fromisoformat(job["claimed_at"])).total_seconds())
        if fraction and fraction >= 0.02 and elapsed >= 2:
            eta = elapsed * (1 - fraction) / fraction
    return {"fraction": fraction, "elapsed_sec": elapsed, "eta_sec": eta}


class Reporter:
    """Writes progress to the job row at most every 0.7 s; every write is also a cancellation point."""

    def __init__(self, settings: Settings, job_id: str, total: int = SCALE):
        self.settings, self.job_id, self.total = settings, job_id, total
        self.value, self.phase, self.last = -1, None, 0.0
        _update(settings, job_id, progress_total=total, progress_current=0)

    def set(self, phase: str, value: float, force: bool = False):
        value = int(max(0, min(self.total, value)))
        moment = time.monotonic()
        if force or phase != self.phase or (value != self.value and moment - self.last >= 0.7):
            _check_cancel(self.settings, self.job_id)
            _update(self.settings, self.job_id, phase=phase, progress_current=value)
            self.value, self.phase, self.last = value, phase, moment

    def span(self, phase: str, start: float, end: float):
        """Callback mapping a 0–1 fraction onto [start, end] of the job scale."""
        return lambda fraction: self.set(phase, start + (end - start) * max(0.0, min(1.0, fraction)))


def retry(settings: Settings, job_id: str) -> None:
    with connect(settings.database) as db, transaction(db):
        job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["status"] not in ("failed", "blocked", "cancelled", "interrupted"):
            raise ValueError("工作並非可重試狀態")
        db.execute("UPDATE jobs SET status='queued',error_code=NULL,error_message=NULL,cancel_requested=0,updated_at=? WHERE id=?",
                   (now(), job_id))


def request_cancel(settings: Settings, job_id: str) -> None:
    with connect(settings.database) as db, transaction(db):
        db.execute("UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=? AND status IN ('queued','running')", (now(), job_id))


def recover(settings: Settings, after_minutes: int = 20) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=after_minutes)).isoformat(timespec="seconds")
    with connect(settings.database) as db, transaction(db):
        cursor = db.execute("UPDATE jobs SET status='interrupted',error_code='worker_interrupted',"
                            "error_message='工作程序中斷；可重試',updated_at=? "
                            "WHERE status='running' AND claimed_at<?", (now(), cutoff))
        return cursor.rowcount


def _claim(settings: Settings, lane: str = "all") -> dict | None:
    marks = ",".join("?" * len(HEAVY_KINDS))
    clause = {"all": "", "heavy": f" AND kind IN ({marks})", "light": f" AND kind NOT IN ({marks})"}[lane]
    with connect(settings.database) as db, transaction(db):
        row = db.execute(f"SELECT * FROM jobs WHERE status='queued'{clause} ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED",
                         HEAVY_KINDS if lane != "all" else ()).fetchone()
        if not row:
            return None
        if row["cancel_requested"]:
            db.execute("UPDATE jobs SET status='cancelled',updated_at=? WHERE id=?", (now(), row["id"]))
            return None
        db.execute("UPDATE jobs SET status='running',claimed_at=?,attempts=attempts+1,updated_at=? WHERE id=?",
                   (now(), now(), row["id"]))
        return dict(row)


def _update(settings: Settings, job_id: str, **values):
    values["updated_at"] = now()
    with connect(settings.database) as db, transaction(db):
        db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                   (*values.values(), job_id))


def _check_cancel(settings: Settings, job_id: str):
    with connect(settings.database) as db:
        row = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row and row["cancel_requested"]:
        raise InterruptedError("使用者取消工作")


def run_one(settings: Settings, lane: str = "all") -> str | None:
    job = _claim(settings, lane)
    if job is None:
        return None
    job_id = job["id"]
    try:
        _check_cancel(settings, job_id)
        payload = json.loads(job["payload_json"])
        if job["kind"] == "youtube_inspect":
            _youtube_inspect(settings, job_id, payload)
        elif job["kind"] == "youtube_acquire":
            _youtube_acquire(settings, job_id, job["source_id"], payload)
        elif job["kind"] == "youtube_subtitles":
            _youtube_subtitles(settings, job_id, job["source_id"], payload)
        elif job["kind"] == "playback":
            _playback(settings, job_id, job["source_id"])
        elif job["kind"] == "reprocess":
            _reprocess(settings, job_id, job["source_id"], payload)
        elif job["kind"] == "embedding":
            _embed(settings, job_id, payload)
        elif job["kind"] == "local_import":
            _local_import(settings, job_id, payload)
        elif job["kind"] == "segment":
            _segment(settings, job_id, job["source_id"], payload)
        elif job["kind"] == "alignment":
            _align(settings, job_id, job["source_id"], payload)
        with connect(settings.database) as db, transaction(db):
            db.execute("UPDATE jobs SET status='completed',phase='completed',progress_current=progress_total,"
                       "updated_at=? WHERE id=?", (now(), job_id))
    except InterruptedError as exc:
        _update(settings, job_id, status="cancelled", error_code="cancelled", error_message=str(exc))
    except Exception as exc:
        from .youtube import YouTubeError
        code = exc.code if isinstance(exc, YouTubeError) else "processing_error"
        state = "blocked" if code in ("missing_dependency", "missing_api_key", "access_restricted", "subtitle_unavailable") or isinstance(exc, ModelUnavailable) else "failed"
        _update(settings, job_id, status=state, error_code=code, error_message=str(exc)[:500])
        if job["source_id"] and job["kind"] in ("youtube_acquire", "local_import"):
            update_source(settings, job["source_id"], status=state, failure_reason=str(exc)[:400])
    return job_id


def get_source_status(settings: Settings, sid: str) -> str | None:
    with connect(settings.database) as db:
        row = db.execute("SELECT status FROM sources WHERE id=?", (sid,)).fetchone()
    return row["status"] if row else None

def _apply_inspection(settings: Settings, info: dict) -> str:
    sid = create_source(settings, "youtube", info["id"], info["url"], info["title"],
                        info["language"] or "unknown", metadata={"tracks": info["tracks"], **info["metadata"]})
    # Refreshing the caption list must not downgrade a source that already has audio or slices.
    keep = get_source_status(settings, sid) in ("acquired", "normalized", "segmented")
    from .web.labels import YOUTUBE_DOMAINS
    guess = next((YOUTUBE_DOMAINS[c] for c in info.get("categories", []) if c in YOUTUBE_DOMAINS), None)
    with connect(settings.database) as db:
        current = db.execute("SELECT domain FROM sources WHERE id=?", (sid,)).fetchone()["domain"]
    if guess and current == "unknown":  # never overwrite a domain someone chose by hand
        update_source(settings, sid, domain=guess)
    update_source(settings, sid, description=info["description"], channel_id=info["channel_id"],
                  channel_name=info["channel_name"], published_date=info["published_date"],
                  duration_sec=info["duration_sec"], inspected_at=now(), **({} if keep else {"status": "inspected"}),
                  metadata_json=dumps({"tracks": info["tracks"], **info["metadata"]}))
    return sid


def _youtube_inspect(settings: Settings, job_id: str, payload: dict):
    from .youtube import inspect_video
    urls = payload.get("urls", [])[:20]
    _update(settings, job_id, progress_total=len(urls), phase="inspecting")
    failures = []
    for i, url in enumerate(urls):
        _check_cancel(settings, job_id)
        try:
            info = inspect_video(url)
            _apply_inspection(settings, info)
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)[:200]})
        _update(settings, job_id, progress_current=i+1)
    if failures:
        _update(settings, job_id, error_code="partial_inspection", error_message=dumps(failures)[:500])
        if len(failures) == len(urls):
            raise ValueError("全部影片檢查失敗：" + dumps(failures)[:350])


def _youtube_acquire(settings: Settings, job_id: str, sid: str, payload: dict):
    from .youtube import download_video
    with connect(settings.database) as db:
        source = db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not source or source["platform"] != "youtube":
        raise ValueError("來源不是已檢查的 YouTube 影片")
    import tempfile
    report = Reporter(settings, job_id)
    with tempfile.TemporaryDirectory(dir=settings.data_dir) as td:
        report.set("downloading", 0, force=True)
        try:
            retrieved = download_video(source["url"], Path(td), payload["subtitle_language"],
                                       payload["subtitle_kind"], progress=report.span("downloading", 0, 400))
        except Exception:
            _check_cancel(settings, job_id)  # a cancel raised inside the yt-dlp hook arrives wrapped
            raise
        report.set("storing", 400, force=True)
        store_asset(settings, sid, retrieved["audio"], "original_audio")
        store_asset(settings, sid, retrieved["metadata"], "source_metadata", ".bin")
        add_subtitles(settings, sid, retrieved["subtitle"], payload["subtitle_language"], payload["subtitle_kind"])
        adopt_track_language(settings, sid, payload["subtitle_language"])
        update_source(settings, sid, acquired_at=now(), status="acquired")
        report.set("playback", 450, force=True)
        notes = []
        try_playback(settings, sid, report.span("playback", 450, 600), notes)
        report.set("normalizing", 600, force=True)
        ensure_normalized(settings, sid, progress=report.span("normalizing", 600, 800))
        report.set("segmenting", 800, force=True)
        segment_source(settings, sid, job_id=job_id,
                       progress=lambda i, n: report.set("segmenting", 800 + 200 * (i / n if n else 1)))
        if notes:
            _update(settings, job_id, error_code="playback_unavailable", error_message="；".join(notes)[:500])

def _youtube_subtitles(settings: Settings, job_id: str, sid: str, payload: dict):
    """Fetch only the video's own caption track (no audio) and store it as a track of the source."""
    from .youtube import download_subtitles
    with connect(settings.database) as db:
        source = db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not source or source["platform"] != "youtube":
        raise ValueError("來源不是已檢查的 YouTube 影片")
    import tempfile
    report = Reporter(settings, job_id)
    with tempfile.TemporaryDirectory(dir=settings.data_dir) as td:
        report.set("subtitles", 0, force=True)
        subtitle = download_subtitles(source["url"], Path(td), payload["subtitle_language"], payload["subtitle_kind"])
        report.set("storing", 600, force=True)
        add_subtitles(settings, sid, subtitle, payload["subtitle_language"], payload["subtitle_kind"])
        adopt_track_language(settings, sid, payload["subtitle_language"])
    if source["status"] in ("candidate", "inspected"):
        update_source(settings, sid, acquired_at=now())

def _reprocess(settings: Settings, job_id: str, sid: str, payload: dict):
    """Run a stored source through the whole pipeline again.

    YouTube: download audio and the SRT afresh, then rebuild MP3, WAV and slices.
    Local: the original file is all there is, so only the derived files and slices are rebuilt.
    Nothing existing is removed until the new files have been retrieved and verified."""
    with connect(settings.database) as db:
        source = db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not source:
        raise ValueError("找不到來源")
    report = Reporter(settings, job_id)
    notes: list[str] = []
    track_id = None
    if source["platform"] == "youtube":
        from .youtube import download_video
        import tempfile
        with tempfile.TemporaryDirectory(dir=settings.data_dir) as td:
            report.set("downloading", 0, force=True)
            try:
                retrieved = download_video(source["url"], Path(td), payload["subtitle_language"], payload["subtitle_kind"],
                                           progress=report.span("downloading", 0, 400))
            except Exception:
                _check_cancel(settings, job_id)
                raise
            report.set("storing", 400, force=True)
            _apply_inspection(settings, retrieved["info"])  # refreshes the caption list from the same fetch
            store_asset(settings, sid, retrieved["audio"], "original_audio")
            store_asset(settings, sid, retrieved["metadata"], "source_metadata", ".bin")
            track_id = add_subtitles(settings, sid, retrieved["subtitle"], payload["subtitle_language"], payload["subtitle_kind"])
            adopt_track_language(settings, sid, payload["subtitle_language"])
        update_source(settings, sid, acquired_at=now(), status="acquired", failure_reason="")
        start = 450
    else:
        start = 0
    report.set("playback", start, force=True)
    try_playback(settings, sid, report.span("playback", start, start + 150), notes)
    report.set("normalizing", start + 150, force=True)
    ensure_normalized(settings, sid, progress=report.span("normalizing", start + 150, start + 350))
    with connect(settings.database) as db:
        has_track = db.execute("SELECT 1 FROM tracks WHERE source_id=?", (sid,)).fetchone()
    if has_track:
        first = start + 350
        report.set("segmenting", first, force=True)
        segment_source(settings, sid, track_id=track_id, job_id=job_id,
                       progress=lambda i, n: report.set("segmenting", first + (SCALE - first) * (i / n if n else 1)))
    else:
        notes.append("尚無字幕，未切片")
    if notes:
        _update(settings, job_id, error_code="reprocess_notes", error_message="；".join(notes)[:500])


def _playback(settings: Settings, job_id: str, sid: str):
    report = Reporter(settings, job_id)
    report.set("playback", 0, force=True)
    ensure_playback(settings, sid, progress=report.span("playback", 0, SCALE))


def _local_import(settings: Settings, job_id: str, payload: dict):
    """Uploads wait in data/uploads/<token>/ until the worker imports them; the folder is removed either way."""
    uploads = (settings.data_dir / "uploads").resolve()
    folder = asset_path(settings, payload["upload_dir"])
    if folder.parent != uploads:
        raise ValueError("上傳暫存路徑無效")
    try:
        audio = folder / payload["audio"]
        if not audio.is_file():
            raise ValueError("上傳暫存已清除；請重新上傳")
        subtitle = folder / payload["subtitle"] if payload.get("subtitle") else None
        report = Reporter(settings, job_id)
        stages = {"stored": ("storing", 0, 100), "playback": ("playback", 100, 300),
                  "normalize": ("normalizing", 300, 750), "segment": ("segmenting", 750, 1000)}
        report.set("storing", 0, force=True)

        def progress(stage: str, fraction: float):
            phase, start, end = stages[stage]
            report.set(phase, start + (end - start) * fraction)
        notes = []
        sid = import_local(settings, audio, subtitle, progress=progress, notes=notes, **payload["options"])
        _update(settings, job_id, source_id=sid)
        if notes:
            _update(settings, job_id, error_code="playback_unavailable", error_message="；".join(notes)[:500])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _segment(settings: Settings, job_id: str, sid: str, payload: dict):
    report = Reporter(settings, job_id)
    report.set("normalizing", 0, force=True)
    converted = []

    def normalizing(fraction: float):
        converted.append(fraction)
        report.set("normalizing", 600 * fraction)
    ensure_normalized(settings, sid, progress=normalizing)
    # Already-normalized audio skips straight to slicing, which then owns the whole bar (keeps the ETA honest).
    start = 600 if converted else 0
    segment_source(settings, sid, track_id=payload.get("track_id"), job_id=job_id,
                   progress=lambda i, n: report.set("segmenting", start + (SCALE - start) * (i / n if n else 1)))


def _align(settings: Settings, job_id: str, sid: str, payload: dict):
    from .align import align_track, weights_cached
    report = Reporter(settings, job_id)
    cached = weights_cached(settings)
    download, load = (0, 0) if cached else (0, 400), (0, 50) if cached else (400, 450)
    spans = {"model_download": report.span("model_download", *download),
             "model_loading": report.span("model_loading", *load),
             "aligning": report.span("aligning", load[1], SCALE)}
    report.set("model_loading" if cached else "model_download", 0, force=True)
    result = align_track(settings, payload["track_id"], source_id=sid,
                         progress=lambda phase, fraction: spans[phase](fraction))
    if result["failed"]:
        _update(settings, job_id, error_code="partial_alignment",
                error_message=f"{result['aligned']} 句完成，{len(result['failed'])} 句未對齊：" +
                "；".join(result["failed"])[:420])


def _embed(settings: Settings, job_id: str, payload: dict):
    model_id = payload.get("model_id") or MODEL_ID
    device = payload.get("device") or settings.speaker_device
    with connect(settings.database) as db:
        candidates = rows(db, "SELECT s.*,a.path FROM segments s JOIN assets a ON s.audio_asset_id=a.id "
                          "WHERE s.active=1 AND s.embedding_status IN ('pending','failed') ORDER BY s.id")
    _update(settings, job_id, progress_total=len(candidates), phase="embedding")
    failures = []
    for i, seg in enumerate(candidates):
        _check_cancel(settings, job_id)
        if seg["speaker_state"] in ("multiple", "overlap"):
            failures.append(f"{seg['id']}: 多語者或重疊音段")
            _update(settings, job_id, progress_current=i+1)
            continue
        from .core import asset_path
        try:
            vector = extract_embedding(asset_path(settings, seg["path"]), settings.data_dir / "models" / "speechbrain", model_id, device)
            with connect(settings.database) as db, transaction(db):
                current = db.execute("SELECT revision FROM segments WHERE id=?", (seg["id"],)).fetchone()
                if current["revision"] != seg["revision"]:
                    continue
                db.execute("INSERT INTO embeddings(id,segment_id,segment_revision,model_id,vector,dimensions,dtype,device,created_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", (new_id(), seg["id"], seg["revision"], model_id,
                            vector.tobytes(), int(vector.size), "float32", device, now()))
                db.execute("UPDATE segments SET embedding_status='ready' WHERE id=?", (seg["id"],))
        except ModelUnavailable:
            raise
        except Exception as exc:
            failures.append(f"{seg['id']}: {str(exc)[:100]}")
            with connect(settings.database) as db, transaction(db):
                db.execute("UPDATE segments SET embedding_status='failed' WHERE id=?", (seg["id"],))
        _update(settings, job_id, progress_current=i+1)
    if failures:
        _update(settings, job_id, error_code="partial_embedding", error_message="；".join(failures)[:500])


def vectors(settings: Settings, model_id: str = MODEL_ID) -> dict:
    import numpy as np
    with connect(settings.database) as db:
        records = rows(db, "SELECT e.* FROM embeddings e JOIN segments s ON e.segment_id=s.id "
                       "WHERE e.model_id=? AND e.segment_revision=s.revision AND s.active=1 AND s.speaker_state NOT IN ('multiple','overlap')", (model_id,))
    return {r["segment_id"]: np.frombuffer(r["vector"], dtype="float32").copy() for r in records}


def similar(settings: Settings, segment_id: str, count: int = 5) -> list[tuple[str, float]]:
    values = vectors(settings)
    if segment_id not in values:
        raise ValueError("此音段缺少目前版本向量")
    return top_k(values, segment_id, count)


def run_clustering(settings: Settings, distance_threshold: float = 0.35) -> str:
    values = vectors(settings)
    if not values:
        raise ValueError("尚無真實可用 embedding；請先完成模型工作")
    assignments = cluster(values, distance_threshold)
    rid = new_id()
    with connect(settings.database) as db, transaction(db):
        db.execute("INSERT INTO cluster_runs(id,created_at,model_id,algorithm,threshold,subset_json,params_json) "
                   "VALUES(?,?,?,?,?,?,?)", (rid, now(), MODEL_ID, "average-linkage-cosine", distance_threshold,
                   dumps(sorted(values)), dumps({"l2_normalized": True, "random_seed": None})))
        for sid, label in assignments.items():
            db.execute("INSERT INTO cluster_assignments(run_id,segment_id,cluster_id) VALUES(?,?,?)", (rid, sid, label))
    return rid


def override_group(settings: Settings, segment_id: str, group_id: str, note: str = "") -> None:
    if not group_id or len(group_id) > 80:
        raise ValueError("群組名稱須為 1–80 字元")
    with connect(settings.database) as db, transaction(db):
        before = db.execute("SELECT group_id,note FROM cluster_overrides WHERE segment_id=?", (segment_id,)).fetchone()
        db.execute("INSERT INTO cluster_overrides(segment_id,group_id,updated_at,note) VALUES(?,?,?,?) "
                   "ON CONFLICT(segment_id) DO UPDATE SET group_id=excluded.group_id,updated_at=excluded.updated_at,note=excluded.note",
                   (segment_id, group_id, now(), note))
        db.execute("INSERT INTO review_history(id,segment_id,action,before_json,after_json,changed_at) VALUES(?,?,?,?,?,?)",
                   (new_id(), segment_id, "group_override", dumps(dict(before) if before else {}),
                    dumps({"group_id": group_id, "note": note}), now()))
