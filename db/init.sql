-- twcorpus schema. Postgres runs this file once, when the data volume is first created
-- (docker-entrypoint-initdb.d). There are no migrations: to change the schema, edit this file and
-- recreate the database (`docker compose down -v && docker compose up -d`).
--
-- Timestamps are ISO-8601 UTC text and *_json columns are JSON text; the application writes and parses them,
-- so they sort and compare as plain strings.
CREATE TABLE sources (
 id TEXT PRIMARY KEY, platform TEXT NOT NULL, video_id TEXT, url TEXT,
 title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', channel_id TEXT,
 channel_name TEXT, published_date TEXT, acquired_at TEXT, inspected_at TEXT,
 duration_sec DOUBLE PRECISION, audio_language TEXT NOT NULL DEFAULT 'zh', subtitle_language TEXT,
 subtitle_kind TEXT NOT NULL DEFAULT 'unknown', speaker_count TEXT NOT NULL DEFAULT 'unknown',
 taiwan_basis TEXT NOT NULL DEFAULT 'unknown', domain TEXT NOT NULL DEFAULT 'unknown', license_label TEXT NOT NULL DEFAULT 'unknown',
 license_evidence TEXT NOT NULL DEFAULT '', license_checked_at TEXT,
 status TEXT NOT NULL DEFAULT 'candidate', failure_reason TEXT,
 metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
 UNIQUE(platform, video_id)
);
CREATE INDEX sources_domain ON sources(domain);
CREATE TABLE assets (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
 kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes BIGINT NOT NULL,
 created_at TEXT NOT NULL, tool_version TEXT, params_json TEXT NOT NULL DEFAULT '{}',
 UNIQUE(source_id,kind,path)
);
CREATE INDEX assets_hash ON assets(sha256);
CREATE TABLE tracks (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
 language TEXT NOT NULL, kind TEXT NOT NULL, format TEXT NOT NULL,
 asset_id TEXT REFERENCES assets(id), created_at TEXT NOT NULL,
 UNIQUE(source_id,asset_id)
);
CREATE TABLE cues (
 id TEXT PRIMARY KEY, track_id TEXT NOT NULL REFERENCES tracks(id), ordinal INTEGER NOT NULL,
 original_id TEXT, start_sec DOUBLE PRECISION, end_sec DOUBLE PRECISION, raw_text TEXT NOT NULL,
 cleaned_text TEXT NOT NULL, clean_version TEXT NOT NULL DEFAULT 'v1',
 flags_json TEXT NOT NULL DEFAULT '[]', UNIQUE(track_id,ordinal)
);
CREATE TABLE segments (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), track_id TEXT NOT NULL REFERENCES tracks(id),
 cue_ids_json TEXT NOT NULL, start_sec DOUBLE PRECISION NOT NULL, end_sec DOUBLE PRECISION NOT NULL,
 audio_asset_id TEXT NOT NULL REFERENCES assets(id), revision INTEGER NOT NULL DEFAULT 1,
 original_text TEXT NOT NULL, corrected_text TEXT, language TEXT NOT NULL DEFAULT 'zh',
 review_status TEXT NOT NULL DEFAULT 'unchecked', review_level TEXT NOT NULL DEFAULT 'none',
 review_note TEXT NOT NULL DEFAULT '', quality_flags_json TEXT NOT NULL DEFAULT '[]',
 manual_labels_json TEXT NOT NULL DEFAULT '[]', local_speaker_id TEXT,
 speaker_state TEXT NOT NULL DEFAULT 'unknown', embedding_status TEXT NOT NULL DEFAULT 'pending',
 active INTEGER NOT NULL DEFAULT 1, params_json TEXT NOT NULL DEFAULT '{}',
 job_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX segments_filter ON segments(source_id, active, review_status);
CREATE TABLE segment_versions (
 segment_id TEXT NOT NULL REFERENCES segments(id), revision INTEGER NOT NULL,
 start_sec DOUBLE PRECISION NOT NULL, end_sec DOUBLE PRECISION NOT NULL, text TEXT,
 audio_asset_id TEXT NOT NULL REFERENCES assets(id), changed_at TEXT NOT NULL,
 PRIMARY KEY(segment_id,revision)
);
CREATE TABLE embeddings (
 id TEXT PRIMARY KEY, segment_id TEXT NOT NULL REFERENCES segments(id), segment_revision INTEGER NOT NULL,
 model_id TEXT NOT NULL, model_revision TEXT NOT NULL DEFAULT 'unknown',
 vector BYTEA NOT NULL, dimensions INTEGER NOT NULL, dtype TEXT NOT NULL,
 device TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(segment_id,segment_revision,model_id)
);
CREATE TABLE cluster_runs (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, model_id TEXT NOT NULL,
 algorithm TEXT NOT NULL, threshold DOUBLE PRECISION NOT NULL, subset_json TEXT NOT NULL,
 params_json TEXT NOT NULL
);
CREATE TABLE cluster_assignments (
 run_id TEXT NOT NULL REFERENCES cluster_runs(id), segment_id TEXT NOT NULL REFERENCES segments(id),
 cluster_id TEXT NOT NULL, PRIMARY KEY(run_id,segment_id)
);
CREATE TABLE cluster_overrides (
 segment_id TEXT PRIMARY KEY REFERENCES segments(id), group_id TEXT NOT NULL,
 updated_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT REFERENCES sources(id),
 status TEXT NOT NULL DEFAULT 'queued', phase TEXT NOT NULL DEFAULT 'imported',
 progress_current INTEGER NOT NULL DEFAULT 0, progress_total INTEGER NOT NULL DEFAULT 0,
 payload_json TEXT NOT NULL DEFAULT '{}', error_code TEXT, error_message TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
 claimed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX jobs_claim ON jobs(status,created_at);
CREATE UNIQUE INDEX jobs_unique_active ON jobs(kind,source_id)
 WHERE status IN ('queued','running') AND source_id IS NOT NULL;
CREATE TABLE review_history (
 id TEXT PRIMARY KEY, segment_id TEXT NOT NULL REFERENCES segments(id),
 action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 changed_at TEXT NOT NULL
);
-- Word-level timing from forced alignment. Untimed units (spaces, punctuation, digits) keep NULL times so the
-- units of one cue concatenate back to its cleaned_text exactly.
CREATE TABLE word_alignments (
 id TEXT PRIMARY KEY, track_id TEXT NOT NULL REFERENCES tracks(id), cue_id TEXT NOT NULL REFERENCES cues(id),
 ordinal INTEGER NOT NULL, word TEXT NOT NULL, romanized TEXT, start_sec DOUBLE PRECISION, end_sec DOUBLE PRECISION, score DOUBLE PRECISION,
 method TEXT NOT NULL, model_id TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(cue_id,model_id,ordinal)
);
CREATE INDEX word_alignments_track ON word_alignments(track_id,model_id);
CREATE TABLE exports (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, filters_json TEXT NOT NULL,
 manifest_path TEXT NOT NULL, snapshot_json TEXT NOT NULL, count INTEGER NOT NULL
);
