"""Import, segmentation, review, query and reproducible export operations."""
from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from . import __version__
from .config import Settings
from .db import connect, init_db, now, rows, transaction
from .media import MediaError, basic_audio_flags, normalize, probe, sha256, slice_audio, to_mp3
from .subtitles import group_cues, parse_file


def new_id() -> str:
    return uuid.uuid4().hex


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def asset_path(settings: Settings, stored: str) -> Path:
    path = (settings.data_dir / stored).resolve()
    if not path.is_relative_to(settings.data_dir.resolve()):
        raise ValueError("資產路徑超出資料目錄")
    return path


def relative(settings: Settings, path: Path) -> str:
    return str(path.resolve().relative_to(settings.data_dir.resolve()))


def store_asset(settings: Settings, source_id: str, original: Path, kind: str, suffix: str | None = None,
                params: dict | None = None) -> dict:
    """Copy into content-addressed store before recording in DB. No original overwrite."""
    if not original.is_file():
        raise FileNotFoundError(original)
    digest = sha256(original)
    extension = suffix or original.suffix.lower() or ".bin"
    if extension not in (".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".opus", ".webm", ".aac", ".srt", ".vtt", ".mkv", ".mov", ".bin"):
        extension = ".bin"
    target = settings.data_dir / "assets" / digest[:2] / f"{digest}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as f:
            staging = Path(f.name)
        try:
            shutil.copyfile(original, staging)
            if sha256(staging) != digest:
                raise IOError("複製後檔案指紋不一致")
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
    with connect(settings.database) as db, transaction(db):
        db.execute("INSERT INTO assets(id,source_id,kind,path,sha256,bytes,created_at,tool_version,params_json) "
                   "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,kind,path) DO NOTHING",
                   (new_id(), source_id, kind, relative(settings, target), digest,
                    target.stat().st_size, now(), __version__, dumps(params or {})))
        return dict(db.execute("SELECT * FROM assets WHERE source_id=? AND kind=? AND path=?",
                               (source_id, kind, relative(settings, target))).fetchone())


def create_source(settings: Settings, platform: str, video_id: str, url: str, title: str,
                  language: str = "zh", license_label: str = "unknown", license_evidence: str = "",
                  speaker_count: str = "unknown", subtitle_kind: str = "unknown",
                  subtitle_language: str | None = None, metadata: dict | None = None,
                  taiwan_basis: str = "unknown", domain: str = "unknown") -> str:
    init_db(settings.database)
    if not license_evidence.strip() and license_label != "unknown":
        raise ValueError("授權標示需附上觀察證據")
    with connect(settings.database) as db, transaction(db):
        sid = new_id()
        db.execute("INSERT INTO sources(id,platform,video_id,url,title,audio_language,subtitle_language,"
                   "subtitle_kind,speaker_count,taiwan_basis,domain,license_label,license_evidence,license_checked_at,"
                   "metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,video_id) DO NOTHING",
                   (sid, platform, video_id, url, title, language, subtitle_language,
                    subtitle_kind, speaker_count, taiwan_basis, domain, license_label, license_evidence,
                    now(), dumps(metadata or {}), now()))
        return db.execute("SELECT id FROM sources WHERE platform=? AND video_id=?", (platform, video_id)).fetchone()["id"]


def update_source(settings: Settings, source_id: str, **changes) -> None:
    allowed = {"title", "description", "channel_id", "channel_name", "published_date", "duration_sec",
               "audio_language", "subtitle_language", "subtitle_kind", "license_label", "license_evidence",
               "speaker_count", "taiwan_basis", "domain", "status", "failure_reason", "metadata_json", "inspected_at", "acquired_at"}
    if not changes.keys() <= allowed:
        raise ValueError("來源欄位不允許修改")
    if not changes:
        return
    with connect(settings.database) as db, transaction(db):
        if "license_label" in changes or "license_evidence" in changes:
            changes["license_checked_at"] = now()
        fields = ", ".join(f"{key}=?" for key in changes)
        db.execute(f"UPDATE sources SET {fields} WHERE id=?", (*changes.values(), source_id))


def delete_source(settings: Settings, source_id: str) -> int:
    """Remove a source with its tracks, segments, review history, embeddings, jobs and stored files.

    Refused while a job is queued or running for it. Assets are content-addressed, so a file is only
    unlinked once no remaining asset row points at it. Returns the number of files removed."""
    with connect(settings.database) as db, transaction(db):
        if not db.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
            raise ValueError("找不到來源")
        if db.execute("SELECT 1 FROM jobs WHERE source_id=? AND status IN ('queued','running')", (source_id,)).fetchone():
            raise ValueError("此來源仍有排隊或執行中的工作，請先到「處理任務」取消")
        paths = {r["path"] for r in db.execute("SELECT path FROM assets WHERE source_id=?", (source_id,))}
        by_segment = "(SELECT id FROM segments WHERE source_id=?)"
        by_track = "(SELECT id FROM tracks WHERE source_id=?)"
        for table, column, scope in (("cluster_assignments", "segment_id", by_segment),
                                     ("cluster_overrides", "segment_id", by_segment),
                                     ("embeddings", "segment_id", by_segment),
                                     ("review_history", "segment_id", by_segment),
                                     ("segment_versions", "segment_id", by_segment),
                                     ("word_alignments", "track_id", by_track),
                                     ("cues", "track_id", by_track)):
            db.execute(f"DELETE FROM {table} WHERE {column} IN {scope}", (source_id,))
        for table in ("segments", "tracks", "jobs", "assets"):
            db.execute(f"DELETE FROM {table} WHERE source_id=?", (source_id,))
        db.execute("DELETE FROM sources WHERE id=?", (source_id,))
        shared = {r["path"] for r in db.execute("SELECT DISTINCT path FROM assets")}
    removed = 0
    for stored in paths - shared:
        try:
            target = asset_path(settings, stored)
        except ValueError:
            continue
        if target.is_file():
            target.unlink()
            removed += 1
    return removed


def try_playback(settings: Settings, source_id: str, progress=None, notes: list | None = None) -> dict | None:
    """ensure_playback that records a failure instead of raising; imports must not fail over a listening copy."""
    try:
        return ensure_playback(settings, source_id, progress)
    except MediaError as exc:
        if notes is not None:
            notes.append(f"MP3 轉檔未完成，改用 WAV 播放：{exc}")
        return None


def import_local(settings: Settings, audio: Path, subtitles: Path | None = None, *,
                 language: str = "zh", subtitle_language: str | None = None,
                 subtitle_kind: str = "unknown", license_label: str = "unknown",
                 license_evidence: str = "", title: str | None = None,
                 speaker_count: str = "unknown", taiwan_basis: str = "unknown", domain: str = "unknown",
                 progress=None, notes: list | None = None) -> str:
    """Import real file, preserve raw data, process only when paired subtitles exist.

    progress(stage, fraction) reports real work: "stored", "playback" (MP3 copy), "normalize" (ffmpeg time),
    "segment" (groups). A failed MP3 copy is not fatal (the WAV still plays); it is appended to notes."""
    report = progress or (lambda stage, fraction: None)
    audio = Path(audio)
    if subtitles is not None:
        subtitles = Path(subtitles)
    info = probe(audio)
    if subtitles is not None:
        if not subtitles.is_file():
            raise FileNotFoundError(subtitles)
        if subtitles.suffix.lower() not in (".srt", ".vtt"):
            raise ValueError("字幕須為 SRT 或 VTT")
    audio_hash = sha256(audio)
    sid = create_source(settings, "local", audio_hash, "", title or audio.name, language,
                        license_label, license_evidence, speaker_count, subtitle_kind,
                        subtitle_language or language if subtitles else None, taiwan_basis=taiwan_basis,
                        domain=domain)
    try:
        store_asset(settings, sid, audio, "original_audio")
        update_source(settings, sid, duration_sec=info["duration_sec"], status="acquired", acquired_at=now())
        report("stored", 1.0)
        try_playback(settings, sid, lambda f: report("playback", f), notes)
        if subtitles is not None:
            add_subtitles(settings, sid, subtitles, subtitle_language or language, subtitle_kind)
            ensure_normalized(settings, sid, progress=lambda f: report("normalize", f))
            segment_source(settings, sid, progress=lambda i, n: report("segment", i / n if n else 1.0))
    except Exception as exc:
        update_source(settings, sid, status="failed", failure_reason=str(exc)[:400])
        raise
    return sid


def add_subtitles(settings: Settings, source_id: str, subtitles: Path,
                  language: str = "zh", kind: str = "unknown") -> str:
    if kind not in ("uploader_provided", "auto_generated", "translated", "unknown", "local_provided"):
        raise ValueError("字幕類型無效")
    with connect(settings.database) as db:
        source = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        raise ValueError("找不到來源")
    cues = parse_file(Path(subtitles), source["duration_sec"])
    if not cues:
        raise ValueError("字幕沒有可辨識 cue")
    asset = store_asset(settings, source_id, Path(subtitles), "original_subtitle")
    with connect(settings.database) as db, transaction(db):
        old = db.execute("SELECT id,language,kind FROM tracks WHERE source_id=? AND asset_id=?", (source_id, asset["id"])).fetchone()
        if old:
            if old["language"] != language or old["kind"] != kind:
                raise ValueError("相同字幕檔已有不同語言／類型標記；請先核對既有軌")
            return old["id"]
        tid = new_id()
        db.execute("INSERT INTO tracks(id,source_id,language,kind,format,asset_id,created_at) VALUES(?,?,?,?,?,?,?)",
                   (tid, source_id, language, kind, Path(subtitles).suffix.lower()[1:], asset["id"], now()))
        for cue in cues:
            db.execute("INSERT INTO cues(id,track_id,ordinal,original_id,start_sec,end_sec,raw_text,cleaned_text,flags_json) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (new_id(), tid, cue.ordinal, cue.original_id,
                        cue.start_sec, cue.end_sec, cue.raw_text, cue.cleaned_text, dumps(cue.flags)))
    update_source(settings, source_id, subtitle_kind=kind, subtitle_language=language)
    return tid


def ensure_normalized(settings: Settings, source_id: str, progress=None) -> dict:
    with connect(settings.database) as db:
        raw = db.execute("SELECT * FROM assets WHERE source_id=? AND kind='original_audio' ORDER BY created_at DESC LIMIT 1", (source_id,)).fetchone()
        old = db.execute("SELECT * FROM assets WHERE source_id=? AND kind='normalized_audio' ORDER BY created_at DESC LIMIT 1", (source_id,)).fetchone()
        if old and raw and asset_path(settings, old["path"]).is_file():
            recorded_source = json.loads(old["params_json"]).get("source_asset_id")
            if recorded_source == raw["id"] and sha256(asset_path(settings, old["path"])) == old["sha256"]:
                return dict(old)
    if not raw:
        raise ValueError("缺少原始音訊；請先補件")
    if not asset_path(settings, raw["path"]).is_file() or sha256(asset_path(settings, raw["path"])) != raw["sha256"]:
        raise MediaError("原始音訊缺失或指紋不符")
    with tempfile.TemporaryDirectory(dir=settings.data_dir) as td:
        tmp = Path(td) / "normalized.wav"
        info = normalize(asset_path(settings, raw["path"]), tmp, progress=progress)
        asset = store_asset(settings, source_id, tmp, "normalized_audio", ".wav",
                            {"source_asset_id": raw["id"], **info})
    update_source(settings, source_id, status="normalized")
    return asset


def ensure_playback(settings: Settings, source_id: str, progress=None) -> dict:
    """MP3 copy of the original audio for the browser player; rebuilt when the original changes."""
    with connect(settings.database) as db:
        raw = db.execute("SELECT * FROM assets WHERE source_id=? AND kind='original_audio' ORDER BY created_at DESC LIMIT 1", (source_id,)).fetchone()
        old = db.execute("SELECT * FROM assets WHERE source_id=? AND kind='playback_audio' ORDER BY created_at DESC LIMIT 1", (source_id,)).fetchone()
    if not raw:
        raise ValueError("缺少原始音訊；請先取得或匯入")
    if old and asset_path(settings, old["path"]).is_file() \
            and json.loads(old["params_json"]).get("source_asset_id") == raw["id"] \
            and sha256(asset_path(settings, old["path"])) == old["sha256"]:
        return dict(old)
    if not asset_path(settings, raw["path"]).is_file() or sha256(asset_path(settings, raw["path"])) != raw["sha256"]:
        raise MediaError("原始音訊缺失或指紋不符")
    with tempfile.TemporaryDirectory(dir=settings.data_dir) as td:
        tmp = Path(td) / "playback.mp3"
        info = to_mp3(asset_path(settings, raw["path"]), tmp, progress=progress)
        return store_asset(settings, source_id, tmp, "playback_audio", ".mp3", {"source_asset_id": raw["id"], **info})

def base_language(language: str | None) -> str:
    """"zh-TW" and "zh_Hant" are both "zh"; subtitle tags are finer than the audio-language choices."""
    return (language or "").lower().replace("_", "-").split("-")[0]


def adopt_track_language(settings: Settings, source_id: str, language: str) -> None:
    """A fetched caption track that is not a translation tells which language the audio is in,
    when nobody has said so yet."""
    code = base_language(language)
    with connect(settings.database) as db:
        row = db.execute("SELECT audio_language FROM sources WHERE id=?", (source_id,)).fetchone()
    if row and row["audio_language"] in ("", "unknown") and code in ("zh", "nan", "hak", "en"):
        update_source(settings, source_id, audio_language=code)


def segment_source(settings: Settings, source_id: str, track_id: str | None = None,
                   padding: float | None = None, target_sec: float = 8.0, job_id: str | None = None,
                   progress=None) -> int:
    """progress(done, total) is called after each segment group."""
    with connect(settings.database) as db:
        source = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if track_id:
            track = db.execute("SELECT * FROM tracks WHERE id=? AND source_id=?", (track_id, source_id)).fetchone()
        else:
            track = db.execute("SELECT * FROM tracks WHERE source_id=? ORDER BY created_at DESC LIMIT 1", (source_id,)).fetchone()
        if not track:
            raise ValueError("缺少字幕；來源保留為待補字幕")
        cue_rows = rows(db, "SELECT * FROM cues WHERE track_id=? ORDER BY ordinal", (track["id"],))
    from .subtitles import Cue
    if track["kind"] == "translated" or base_language(track["language"]) != base_language(source["audio_language"]):
        raise ValueError("音訊與字幕不是同語言轉寫；翻譯或語言未知時不可切為 ASR 配對")
    normalized = ensure_normalized(settings, source_id)
    cues = [Cue(c["ordinal"], c["original_id"], c["start_sec"], c["end_sec"],
                c["raw_text"], c["cleaned_text"], json.loads(c["flags_json"])) for c in cue_rows]
    groups = group_cues(cues, source["duration_sec"], padding if padding is not None else settings.edge_padding_sec,
                        target_sec=target_sec)
    cue_ids = {c["ordinal"]: c["id"] for c in cue_rows}
    count = 0
    expected_ids = []
    for index, group in enumerate(groups):
        if progress:
            progress(index, len(groups))
        linked = [cue_ids[c.ordinal] for c in group["cues"]]
        key = uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{track['id']}:{dumps(linked)}:{group['start_sec']}:{group['end_sec']}").hex
        expected_ids.append(key)
        with connect(settings.database) as db:
            exists = db.execute("SELECT id FROM segments WHERE id=?", (key,)).fetchone()
        if exists:
            continue
        duration = group["end_sec"] - group["start_sec"]
        with tempfile.TemporaryDirectory(dir=settings.data_dir) as td:
            tmp = Path(td) / "segment.wav"
            slice_info = slice_audio(asset_path(settings, normalized["path"]), tmp,
                                     group["start_sec"], group["end_sec"])
            flags = sorted({flag for c in group["cues"] for flag in c.flags})
            if duration < settings.min_segment_sec:
                flags.append("short_segment")
            if duration > settings.max_segment_sec:
                flags.append("long_segment")
            if duration > target_sec:
                flags.append("long_cue_not_split")
            flags.extend(basic_audio_flags(tmp))
            segment_asset = store_asset(settings, source_id, tmp, "segment_audio", ".wav",
                                        {"normalized_asset_id": normalized["id"], "cue_ids": linked,
                                         "start_sec": group["start_sec"], "end_sec": group["end_sec"], **slice_info})
        timestamp = now()
        with connect(settings.database) as db, transaction(db):
            inserted = db.execute("INSERT INTO segments(id,source_id,track_id,cue_ids_json,start_sec,end_sec,audio_asset_id,"
                       "original_text,language,quality_flags_json,params_json,job_id,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                       (key, source_id, track["id"], dumps(linked), group["start_sec"], group["end_sec"],
                        segment_asset["id"], group["text"], track["language"], dumps(sorted(set(flags))),
                        dumps({"padding_sec": padding if padding is not None else settings.edge_padding_sec,
                               "target_sec": target_sec, "normalized_asset_id": normalized["id"],
                               "segmenter_version": "v1"}), job_id, timestamp, timestamp))
            count += inserted.rowcount
    with connect(settings.database) as db, transaction(db):
        if expected_ids:
            db.execute("UPDATE segments SET active=0,updated_at=? WHERE source_id=? AND track_id=? AND active=1 "
                       "AND NOT (id = ANY(?))", (now(), source_id, track["id"], list(expected_ids)))
    if progress:
        progress(len(groups), len(groups))
    update_source(settings, source_id, status="segmented")
    return count


def list_sources(settings: Settings) -> list[dict]:
    with connect(settings.database) as db:
        return rows(db, "SELECT * FROM sources ORDER BY created_at DESC")


def get_source(settings: Settings, source_id: str) -> dict | None:
    with connect(settings.database) as db:
        found = rows(db, "SELECT * FROM sources WHERE id=?", (source_id,))
    return found[0] if found else None


def list_tracks(settings: Settings, source_id: str) -> list[dict]:
    with connect(settings.database) as db:
        return rows(db, "SELECT t.*,(SELECT COUNT(*) FROM cues c WHERE c.track_id=t.id) AS cue_count,"
                    "(SELECT COUNT(*) FROM segments s WHERE s.track_id=t.id AND s.active=1) AS segment_count "
                    "FROM tracks t WHERE t.source_id=? ORDER BY t.created_at DESC", (source_id,))


def list_exports(settings: Settings, limit: int = 30) -> list[dict]:
    with connect(settings.database) as db:
        return rows(db, "SELECT id,created_at,filters_json,count FROM exports ORDER BY created_at DESC LIMIT ?", (limit,))


def list_segments(settings: Settings, *, segment_id: str | None = None, source_id: str | None = None, search: str = "",
                  review_status: str | None = None, language: str | None = None,
                  subtitle_kind: str | None = None, group_id: str | None = None,
                  quality_flag: str | None = None, limit: int = 500) -> list[dict]:
    sql = ("SELECT s.*, a.path AS audio_path,a.sha256 AS audio_sha256,src.title AS source_title,src.url AS source_url,"
           "src.license_label,src.license_evidence,t.kind AS track_kind,t.language AS track_language,"
           "co.group_id AS manual_group,"
           "(SELECT ca.cluster_id FROM cluster_assignments ca JOIN cluster_runs cr ON ca.run_id=cr.id "
           "WHERE ca.segment_id=s.id ORDER BY cr.created_at DESC,cr.id DESC LIMIT 1) AS candidate_group "
           "FROM segments s JOIN assets a ON s.audio_asset_id=a.id JOIN sources src ON s.source_id=src.id "
           "JOIN tracks t ON s.track_id=t.id LEFT JOIN cluster_overrides co ON s.id=co.segment_id WHERE s.active=1")
    args: list = []
    for column, value in (("s.id", segment_id), ("s.source_id", source_id), ("s.review_status", review_status),
                          ("s.language", language), ("t.kind", subtitle_kind)):
        if value:
            sql += f" AND {column}=?"
            args.append(value)
    if search:
        sql += " AND (s.original_text ILIKE ? OR s.corrected_text ILIKE ? OR src.title ILIKE ?)"
        args.extend([f"%{search}%"] * 3)
    if group_id:
        sql += " AND (co.group_id=? OR (co.group_id IS NULL AND EXISTS "
        sql += "(SELECT 1 FROM cluster_assignments ca JOIN cluster_runs cr ON ca.run_id=cr.id WHERE ca.segment_id=s.id AND ca.cluster_id=? "
        sql += "AND cr.id=(SELECT id FROM cluster_runs ORDER BY created_at DESC,id DESC LIMIT 1))))"
        args.extend([group_id, group_id])
    if quality_flag:
        sql += " AND (s.quality_flags_json LIKE ? OR s.manual_labels_json LIKE ?)"
        args.extend([f'%"{quality_flag}"%', f'%"{quality_flag}"%'])
    sql += " ORDER BY s.created_at DESC,s.id LIMIT ?"
    args.append(min(max(int(limit), 1), 10000))
    with connect(settings.database) as db:
        return rows(db, sql, tuple(args))


def review_segment(settings: Settings, segment_id: str, *, corrected_text: str | None = None,
                   note: str | None = None, language: str | None = None, review_status: str | None = None,
                   review_level: str | None = None, manual_labels: list[str] | None = None,
                   speaker_state: str | None = None, local_speaker_id: str | None = None,
                   start_sec: float | None = None, end_sec: float | None = None) -> None:
    if review_status and review_status not in ("unchecked", "passed", "needs_fix", "excluded"):
        raise ValueError("審查狀態無效")
    if review_level and review_level not in ("none", "sampled", "fully_corrected"):
        raise ValueError("校對程度無效")
    if speaker_state and speaker_state not in ("unknown", "single", "multiple", "overlap"):
        raise ValueError("語者狀態無效")
    with connect(settings.database) as db:
        previous = db.execute("SELECT * FROM segments WHERE id=? AND active=1", (segment_id,)).fetchone()
        if not previous:
            raise ValueError("找不到音段")
        previous = dict(previous)
        source = db.execute("SELECT * FROM sources WHERE id=?", (previous["source_id"],)).fetchone()
    updates = {}
    for key, value in (("corrected_text", corrected_text), ("review_note", note), ("language", language),
                       ("review_status", review_status), ("review_level", review_level),
                       ("speaker_state", speaker_state), ("local_speaker_id", local_speaker_id)):
        if value is not None and previous[key] != value:
            updates[key] = value
    if manual_labels is not None:
        updates["manual_labels_json"] = dumps(manual_labels)
    boundary_changed = (start_sec is not None and start_sec != previous["start_sec"]) or (end_sec is not None and end_sec != previous["end_sec"])
    if boundary_changed:
        start = float(start_sec if start_sec is not None else previous["start_sec"])
        end = float(end_sec if end_sec is not None else previous["end_sec"])
        if start < 0 or end <= start or end > source["duration_sec"] + 0.001:
            raise ValueError("邊界須落在來源音訊時間範圍")
        normalized = ensure_normalized(settings, previous["source_id"])
        with tempfile.TemporaryDirectory(dir=settings.data_dir) as td:
            tmp = Path(td) / "revised.wav"
            slice_info = slice_audio(asset_path(settings, normalized["path"]), tmp, start, end)
            segment_asset = store_asset(settings, previous["source_id"], tmp, "segment_audio", ".wav",
                                        {"normalized_asset_id": normalized["id"], "manual": True,
                                         "start_sec": start, "end_sec": end, **slice_info})
            flags = json.loads(previous["quality_flags_json"])
            flags = [x for x in flags if x not in ("short_segment", "long_segment", "low_volume_or_silence", "possible_clipping")]
            if end - start < settings.min_segment_sec:
                flags.append("short_segment")
            if end - start > settings.max_segment_sec:
                flags.append("long_segment")
            flags.extend(basic_audio_flags(tmp))
        updates.update(start_sec=start, end_sec=end, audio_asset_id=segment_asset["id"],
                       revision=previous["revision"] + 1, quality_flags_json=dumps(sorted(set(flags))),
                       embedding_status="pending")
    if not updates:
        return
    with connect(settings.database) as db, transaction(db):
        # Reject stale edits from other processes; UI can reload and retry.
        current = db.execute("SELECT revision,updated_at FROM segments WHERE id=? FOR UPDATE", (segment_id,)).fetchone()
        if current["revision"] != previous["revision"] or current["updated_at"] != previous["updated_at"]:
            raise RuntimeError("音段已被另一程序修改，請重新整理")
        if boundary_changed:
            db.execute("INSERT INTO segment_versions(segment_id,revision,start_sec,end_sec,text,audio_asset_id,changed_at) "
                       "VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", (segment_id, previous["revision"], previous["start_sec"], previous["end_sec"],
                         previous["corrected_text"], previous["audio_asset_id"], now()))
        updates["updated_at"] = now()
        db.execute("UPDATE segments SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=?",
                   (*updates.values(), segment_id))
        if boundary_changed:
            db.execute("INSERT INTO segment_versions(segment_id,revision,start_sec,end_sec,text,audio_asset_id,changed_at) "
                       "VALUES(?,?,?,?,?,?,?)", (segment_id, updates["revision"], updates["start_sec"], updates["end_sec"],
                         updates.get("corrected_text", previous["corrected_text"]), updates["audio_asset_id"], now()))
        db.execute("INSERT INTO review_history(id,segment_id,action,before_json,after_json,changed_at) "
                   "VALUES(?,?,?,?,?,?)", (new_id(), segment_id, "boundary" if boundary_changed else "review",
                    dumps({k: previous.get(k) for k in updates}), dumps(updates), now()))


def _coverage(intervals: list[tuple[float, float]]) -> float:
    total = end = 0.0
    for start, finish in sorted(intervals):
        if start >= end:
            total += finish - start
            end = finish
        elif finish > end:
            total += finish - end
            end = finish
    return total


def statistics(settings: Settings) -> dict:
    with connect(settings.database) as db:
        sources = rows(db, "SELECT * FROM sources")
        segments = rows(db, "SELECT s.*,t.kind AS subtitle_kind FROM segments s JOIN tracks t ON s.track_id=t.id WHERE s.active=1")
        group_count = db.execute("SELECT COUNT(DISTINCT cluster_id) FROM cluster_assignments "
                                 "WHERE run_id=(SELECT id FROM cluster_runs ORDER BY created_at DESC,id DESC LIMIT 1)").fetchone()[0]
    acquired = [s for s in sources if s["acquired_at"]]
    usable = [s for s in segments if s["review_status"] == "passed"]
    covers: dict[str, list[tuple[float, float]]] = {}
    for seg in segments:
        covers.setdefault(seg["source_id"], []).append((seg["start_sec"], seg["end_sec"]))
    distribution = {}
    for key in ("source_id", "language", "subtitle_kind", "review_status"):
        vals = {}
        for seg in segments:
            value = seg[key] or "unknown"
            vals[value] = vals.get(value, 0) + 1
        distribution[key] = vals
    return {
        "candidate_sources": len(sources), "acquired_sources": len(acquired),
        "failed_sources": sum(s["status"] == "failed" for s in sources),
        "candidate_hours": sum((s["duration_sec"] or 0) for s in sources) / 3600,
        "acquired_hours": sum((s["duration_sec"] or 0) for s in acquired) / 3600,
        "segments": len(segments), "segment_hours_sum": sum(s["end_sec"] - s["start_sec"] for s in segments) / 3600,
        "source_coverage_hours": sum(_coverage(spans) for spans in covers.values()) / 3600,
        "reviewed_hours_sum": sum(s["end_sec"] - s["start_sec"] for s in usable) / 3600,
        "excluded_segments": sum(s["review_status"] == "excluded" for s in segments),
        "pending_segments": sum(s["review_status"] == "unchecked" for s in segments),
        "candidate_clusters": group_count, "distribution": distribution,
    }


def export_corpus(settings: Settings, *, source_id: str | None = None, language: str | None = None,
                  review_status: str | None = None, subtitle_kind: str | None = None,
                  group_id: str | None = None, corrected_only: bool = False) -> Path:
    filters = dict(source_id=source_id, language=language, review_status=review_status,
                   subtitle_kind=subtitle_kind, group_id=group_id, corrected_only=corrected_only)
    segments = list_segments(settings, source_id=source_id, language=language, review_status=review_status,
                             subtitle_kind=subtitle_kind, group_id=group_id, limit=10000)
    if corrected_only:
        segments = [s for s in segments if s["review_level"] == "fully_corrected"]
    export_id = new_id()
    staging = settings.data_dir / "exports" / f".{export_id}.staging"
    target = settings.data_dir / "exports" / export_id
    staging.mkdir(parents=True)
    manifest = []
    try:
        (staging / "audio").mkdir()
        for s in segments:
            source_audio = asset_path(settings, s["audio_path"])
            if not source_audio.is_file() or sha256(source_audio) != s["audio_sha256"]:
                raise MediaError(f"音段檔案不存在或指紋不符：{s['id']}")
            if not 0 <= s["start_sec"] < s["end_sec"]:
                raise ValueError(f"音段時間無效：{s['id']}")
            audio_name = f"audio/{s['id']}-r{s['revision']}.wav"
            shutil.copyfile(source_audio, staging / audio_name)
            text = s["corrected_text"] if s["corrected_text"] is not None else s["original_text"]
            manifest.append({
                "segment_id": s["id"], "audio_path": audio_name,
                "audio_sha256": s["audio_sha256"], "duration_sec": s["end_sec"] - s["start_sec"],
                "text": text, "text_source": "human_revision" if s["corrected_text"] is not None else s["track_kind"],
                "original_text": s["original_text"], "corrected_text": s["corrected_text"],
                "language": s["language"], "source_id": s["source_id"], "source_url": s["source_url"],
                "start_sec": s["start_sec"], "end_sec": s["end_sec"],
                "license_label": s["license_label"], "license_evidence": s["license_evidence"],
                "review_status": s["review_status"], "review_level": s["review_level"],
                "subtitle_kind": s["track_kind"], "candidate_group": s["candidate_group"],
                "manual_group": s["manual_group"], "segment_revision": s["revision"],
                "track_id": s["track_id"], "cue_ids": json.loads(s["cue_ids_json"]),
                "processing_params": json.loads(s["params_json"]),
                "publication_rights_confirmed": False,
            })
        with (staging / "manifest.jsonl").open("w", encoding="utf-8", newline="") as f:
            for item in manifest:
                f.write(dumps(item) + "\n")
        fieldnames = list(manifest[0]) if manifest else ["segment_id", "audio_path", "text"]
        with (staging / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in manifest:
                writer.writerow({k: dumps(v) if isinstance(v, (dict, list)) else v for k, v in item.items()})
        (staging / "README.txt").write_text("本地研究匯出；授權未經法律審核，不代表可公開發布。\n"
                                             "review_level=fully_corrected 才代表逐段人工校對；其他文字請勿當作 ground truth。\n", encoding="utf-8")
        staging.replace(target)
        with connect(settings.database) as db, transaction(db):
            db.execute("INSERT INTO exports(id,created_at,filters_json,manifest_path,snapshot_json,count) VALUES(?,?,?,?,?,?)",
                       (export_id, now(), dumps(filters), relative(settings, target / "manifest.jsonl"),
                        dumps(manifest), len(manifest)))
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if target.exists():
            shutil.rmtree(target)
        raise
    return target
