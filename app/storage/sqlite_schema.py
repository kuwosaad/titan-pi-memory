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
