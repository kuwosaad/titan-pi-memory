from __future__ import annotations

import sqlite3


def ensure_memory_store_metadata(conn: sqlite3.Connection) -> None:
    """Create human-readable metadata for portable Titan memory DBs."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    rows = {
        "schema_name": "titan_memory_store",
        "schema_version": "2",
        "storage_model": "scene_first",
        "portable_unit": "memory_store.db",
        "description": "Ordered lossless scenes are the source of truth; memories are extracted from scenes.",
    }
    for key, value in rows.items():
        conn.execute(
            """
            INSERT INTO metadata (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (key, value),
        )


def ensure_memory_readable_views(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW IF EXISTS readable_memories")
    conn.execute(
        """
        CREATE VIEW readable_memories AS
        SELECT
            id AS memory_id,
            session_id AS conversation_id,
            turn,
            scene_id,
            stream,
            COALESCE(memory_kind, type, '') AS memory_kind,
            text AS memory_text,
            source_type,
            source_reliability,
            verification_status,
            ts AS created_at
        FROM memories
        """
    )


def ensure_pattern_tables(conn: sqlite3.Connection) -> None:
    """Create durable pattern, evidence, application, and mining ledger tables."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS patterns (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            summary TEXT NOT NULL,
            recommended_behavior TEXT NOT NULL,
            applies_when TEXT NOT NULL DEFAULT '',
            does_not_apply_when TEXT NOT NULL DEFAULT '',
            trigger_terms_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.0,
            actionability REAL NOT NULL DEFAULT 0.0,
            retrieval_value REAL NOT NULL DEFAULT 0.0,
            canonical_key TEXT,
            mined_run_id TEXT,
            last_refreshed_at TEXT,
            last_applied_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'agent'
        );

        CREATE INDEX IF NOT EXISTS idx_patterns_status_updated ON patterns(status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_patterns_scope_status ON patterns(scope, status);
        CREATE INDEX IF NOT EXISTS idx_patterns_canonical_key ON patterns(canonical_key);

        CREATE TABLE IF NOT EXISTS pattern_evidence (
            pattern_id TEXT NOT NULL,
            memory_id TEXT NOT NULL,
            scene_id TEXT,
            role TEXT NOT NULL DEFAULT 'support',
            score REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY (pattern_id, memory_id, role),
            FOREIGN KEY (pattern_id) REFERENCES patterns(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_pattern_evidence_memory ON pattern_evidence(memory_id);
        CREATE INDEX IF NOT EXISTS idx_pattern_evidence_pattern_role ON pattern_evidence(pattern_id, role);

        CREATE TABLE IF NOT EXISTS pattern_applications (
            id TEXT PRIMARY KEY,
            pattern_id TEXT NOT NULL,
            query TEXT NOT NULL,
            task_id TEXT,
            retrieved_at TEXT NOT NULL,
            was_used INTEGER,
            outcome TEXT,
            feedback TEXT,
            FOREIGN KEY (pattern_id) REFERENCES patterns(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_pattern_applications_pattern_time ON pattern_applications(pattern_id, retrieved_at DESC);

        CREATE TABLE IF NOT EXISTS pattern_mining_runs (
            id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            processor_version TEXT NOT NULL,
            processor_config_hash TEXT NOT NULL,
            mode TEXT NOT NULL,
            memory_count INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_pattern_mining_runs_processor ON pattern_mining_runs(processor_version, processor_config_hash, started_at DESC);

        CREATE TABLE IF NOT EXISTS pattern_memory_processing (
            memory_id TEXT NOT NULL,
            processor_version TEXT NOT NULL,
            processor_config_hash TEXT NOT NULL,
            processed_at TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            pattern_ids_json TEXT NOT NULL DEFAULT '[]',
            error TEXT,
            PRIMARY KEY (memory_id, processor_version, processor_config_hash),
            FOREIGN KEY (run_id) REFERENCES pattern_mining_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_pattern_memory_processing_run ON pattern_memory_processing(run_id);
        CREATE INDEX IF NOT EXISTS idx_pattern_memory_processing_processor ON pattern_memory_processing(processor_version, processor_config_hash, processed_at DESC);
        """
    )


def ensure_scene_readable_views(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW IF EXISTS readable_scenes")
    conn.execute(
        """
        CREATE VIEW readable_scenes AS
        SELECT
            scene_id,
            session_id AS conversation_id,
            scene_seq,
            start_event_seq,
            end_event_seq,
            kind,
            ts AS created_at,
            anchor_event_id,
            substr(extraction_user_text, 1, 500) AS user_text,
            substr(extraction_assistant_text, 1, 500) AS assistant_text,
            tool_calls_json,
            length(tool_calls_json) AS tool_call_bytes,
            length(raw_events_json) AS raw_event_bytes,
            raw_events_json
        FROM scenes
        """
    )
    conn.execute("DROP VIEW IF EXISTS conversation_timeline")
    conn.execute(
        """
        CREATE VIEW conversation_timeline AS
        SELECT
            session_id AS conversation_id,
            scene_seq,
            scene_id,
            kind,
            ts AS created_at,
            CASE
                WHEN extraction_user_text != '' AND extraction_assistant_text != '' THEN 'message_exchange'
                WHEN extraction_user_text != '' THEN 'user_text'
                WHEN extraction_assistant_text != '' THEN 'assistant_text'
                ELSE 'raw_event'
            END AS content_kind,
            length(tool_calls_json) AS tool_call_bytes,
            substr(trim(extraction_user_text || ' ' || extraction_assistant_text), 1, 700) AS text_preview
        FROM scenes
        ORDER BY session_id, scene_seq IS NULL, scene_seq ASC, ts ASC
        """
    )
