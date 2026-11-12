#!/usr/bin/env python3
"""Measure SQLite delete/VACUUM space behavior using synthetic Titan-like rows only."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


def _make_db(path: Path, rows: int, text_bytes: int, embedding_dims: int, weight_bytes: int) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn INTEGER NOT NULL,
                scene_id TEXT, text TEXT NOT NULL, source_event_ids_json TEXT NOT NULL,
                provenance_user TEXT NOT NULL, provenance_assistant TEXT NOT NULL,
                embedding_blob BLOB, embedding_dim INTEGER, h REAL NOT NULL,
                tau REAL NOT NULL, outgoing_weights BLOB, incoming_weights BLOB
            );
            CREATE INDEX idx_memories_scene_id ON memories(scene_id);
            CREATE VIRTUAL TABLE memories_fts USING fts5(text, content='memories', content_rowid='rowid');
            CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
            END;
            CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
            END;
            """
        )
        text = "synthetic memory evidence " + ("x" * max(0, text_bytes - 24))
        vector = bytes(embedding_dims * 4)
        weight = bytes(weight_bytes)
        payload = [
            (f"synthetic-{i:08d}", f"session-{i % 10}", i, f"scene-{i}", text,
             json.dumps([f"event-{i}"]), "synthetic user evidence", "synthetic assistant evidence",
             vector, embedding_dims, 0.0, 0.5, weight, weight)
            for i in range(rows)
        ]
        conn.executemany(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload
        )


def _size(path: Path) -> int:
    return path.stat().st_size


def _measure_case(root: Path, name: str, rows: int, text_bytes: int,
                  embedding_dims: int, weight_bytes: int, action: str) -> dict[str, Any]:
    db = root / f"{name}.db"
    _make_db(db, rows, text_bytes, embedding_dims, weight_bytes)
    before = _size(db)
    with sqlite3.connect(db) as conn:
        if action == "delete_exact_duplicates":
            # Synthetic pairs share all content except ID, modeling reviewed
            # cross-ID duplicates after a canonical keeper has been chosen.
            conn.execute("DELETE FROM memories WHERE CAST(substr(id, 11) AS INTEGER) % 2 = 1")
            logical_rows_removed = rows // 2
        elif action == "null_lnn_weights":
            conn.execute("UPDATE memories SET outgoing_weights=NULL, incoming_weights=NULL")
            logical_rows_removed = 0
        else:  # pragma: no cover - internal call invariant
            raise ValueError(action)
    after_change = _size(db)
    with sqlite3.connect(db) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fts_rows = int(conn.execute("SELECT count(*) FROM memories_fts").fetchone()[0])
        conn.execute("VACUUM")
    after_vacuum = _size(db)
    return {
        "case": name,
        "action": action,
        "rows_before": rows,
        "logical_rows_removed": logical_rows_removed,
        "file_bytes_before": before,
        "file_bytes_after_change": after_change,
        "file_bytes_after_vacuum": after_vacuum,
        "bytes_reclaimed_by_change": before - after_change,
        "additional_bytes_reclaimed_by_vacuum": after_change - after_vacuum,
        "rows_after": fts_rows,
        "integrity_check": integrity,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--text-bytes", type=int, default=512)
    parser.add_argument("--embedding-dims", type=int, default=768)
    parser.add_argument("--weight-bytes", type=int, default=65536,
                        help="Bytes in each outgoing/incoming legacy weight blob.")
    args = parser.parse_args()
    if min(args.rows, args.text_bytes, args.embedding_dims, args.weight_bytes) < 1:
        parser.error("all sizes must be positive")
    with tempfile.TemporaryDirectory(prefix="titan-bulk-dedup-probe-") as tmp:
        root = Path(tmp)
        results = [
            _measure_case(root, "exact_duplicate_delete", args.rows, args.text_bytes,
                          args.embedding_dims, args.weight_bytes, "delete_exact_duplicates"),
            _measure_case(root, "legacy_weight_nulling", args.rows, args.text_bytes,
                          args.embedding_dims, args.weight_bytes, "null_lnn_weights"),
        ]
    print(json.dumps({
        "scope": "synthetic_only_temporary_directory",
        "live_store_access": False,
        "parameters": vars(args),
        "results": results,
        "note": "Exact-duplicate rows differ only by synthetic ID; the probe demonstrates SQLite mechanics, not real candidate eligibility or production savings.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
