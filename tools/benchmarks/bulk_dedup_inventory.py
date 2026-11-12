#!/usr/bin/env python3
"""Read-only SQLite inventory of exact normalized-text duplicate candidates.

This reports candidates only. It never writes to the source database or
declares records safe to remove.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _inventory(database: Path, batch_size: int, include_groups: bool) -> Dict[str, Any]:
    resolved = database.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"database does not exist or is not a file: {resolved}")

    uri = f"{resolved.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "memories" not in tables:
            raise ValueError("database has no memories table")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memories)")}
        required = {"id", "text", "embedding_blob"}
        if not required.issubset(columns):
            raise ValueError("memories table is missing required columns: " + ", ".join(sorted(required - columns)))

        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        row_count = 0

        # Scratch contains only text digests and byte counts. IDs are omitted
        # unless --groups was explicitly requested; normalized text is never
        # persisted, even temporarily.
        with tempfile.TemporaryDirectory(prefix="titan-dedup-inventory-") as temp_dir:
            scratch_path = Path(temp_dir) / "inventory.sqlite"
            scratch = sqlite3.connect(scratch_path)
            try:
                scratch.execute("PRAGMA journal_mode = OFF")
                scratch.execute("PRAGMA synchronous = OFF")
                scratch.execute(
                    "CREATE TABLE candidates (text_digest TEXT NOT NULL, memory_id TEXT, text_bytes INTEGER NOT NULL, vector_bytes INTEGER NOT NULL)"
                )
                scratch.execute("CREATE INDEX candidate_digest ON candidates(text_digest, memory_id)")

                cursor = connection.execute(
                    "SELECT id, text, length(embedding_blob) FROM memories"
                )
                insert_sql = "INSERT INTO candidates VALUES (?, ?, ?, ?)"
                while True:
                    batch = cursor.fetchmany(batch_size)
                    if not batch:
                        break
                    staged = []
                    for memory_id, text, vector_bytes in batch:
                        row_count += 1
                        original_text = str(text or "")
                        normalized = _normalize_text(original_text)
                        if not normalized:
                            continue
                        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                        stored_id = str(memory_id or "") if include_groups else None
                        staged.append((
                            digest,
                            stored_id,
                            len(original_text.encode("utf-8")),
                            max(0, int(vector_bytes or 0)),
                        ))
                    if staged:
                        scratch.executemany(insert_sql, staged)
                        scratch.commit()

                duplicate_groups = duplicate_rows = duplicate_text_bytes = duplicate_vector_bytes = 0
                groups: List[Dict[str, Any]] = []
                group_rows = scratch.execute(
                    """SELECT text_digest, COUNT(*), SUM(text_bytes), SUM(vector_bytes)
                       FROM candidates GROUP BY text_digest HAVING COUNT(*) > 1
                       ORDER BY text_digest"""
                )
                for digest, count, text_sum, vector_sum in group_rows:
                    duplicate_groups += 1
                    excess_rows = int(count) - 1
                    duplicate_rows += excess_rows
                    duplicate_text_bytes += int(text_sum)
                    duplicate_vector_bytes += int(vector_sum)
                    if include_groups:
                        ids = [
                            row[0]
                            for row in scratch.execute(
                                "SELECT memory_id FROM candidates WHERE text_digest=? ORDER BY memory_id",
                                (digest,),
                            )
                        ]
                        groups.append({
                            "normalized_text_sha256": digest,
                            "count": int(count),
                            "memory_ids": ids,
                            "group_text_bytes": int(text_sum),
                            "group_vector_bytes": int(vector_sum),
                        })
            finally:
                scratch.close()

        result: Dict[str, Any] = {
            "backend": "sqlite",
            "database": str(resolved),
            "rows_scanned": row_count,
            "exact_text_duplicate_groups": duplicate_groups,
            "duplicate_excess_rows": duplicate_rows,
            "duplicate_group_text_bytes": duplicate_text_bytes,
            "duplicate_group_vector_bytes": duplicate_vector_bytes,
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "note": "Candidate payload bytes are inventory statistics, not reclaimable-space estimates. No changes made.",
        }
        if include_groups:
            result["groups"] = groups
        return result
    finally:
        connection.close()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Explicit path to one namespace's SQLite memory database.")
    parser.add_argument("--batch-size", type=_positive_int, default=2000, help="Rows read per bounded batch (default: 2000).")
    parser.add_argument("--groups", action="store_true", help="Include duplicate group hashes and memory IDs for review.")
    args = parser.parse_args(argv)
    try:
        report = _inventory(args.db, args.batch_size, args.groups)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
