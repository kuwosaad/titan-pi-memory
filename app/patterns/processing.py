from __future__ import annotations

import json
import threading
import sqlite3
from pathlib import Path
from typing import List, Optional, Sequence

from app.storage.sqlite_schema import ensure_pattern_tables

from .models import PatternMiningRun, PatternMiningStatus, now_iso
from .storage import connect_pattern_db

_PROCESSING_STATUSES = {"processed", "skipped", "failed"}
_RUN_STATUSES = {"running", "completed", "failed"}


class PatternProcessingLedger:
    """Versioned ledger for memories inspected by the pattern miner."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return connect_pattern_db(self.db_path)

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            ensure_pattern_tables(conn)
            conn.commit()

    def start_run(self, *, processor_version: str, processor_config_hash: str, mode: str) -> PatternMiningRun:
        run = PatternMiningRun(
            processor_version=processor_version,
            processor_config_hash=processor_config_hash,
            mode=mode,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pattern_mining_runs (
                    id, started_at, finished_at, status, processor_version,
                    processor_config_hash, mode, memory_count, candidate_count,
                    accepted_count, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.started_at,
                    run.finished_at,
                    run.status,
                    run.processor_version,
                    run.processor_config_hash,
                    run.mode,
                    run.memory_count,
                    run.candidate_count,
                    run.accepted_count,
                    run.error,
                ),
            )
            conn.commit()
        return run

    def finish_run(
        self,
        run_id: str,
        *,
        status: str = "completed",
        memory_count: int = 0,
        candidate_count: int = 0,
        accepted_count: int = 0,
        error: Optional[str] = None,
    ) -> PatternMiningRun:
        if status not in _RUN_STATUSES:
            raise ValueError(f"Invalid pattern mining run status: {status}")
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE pattern_mining_runs
                SET finished_at = ?, status = ?, memory_count = ?, candidate_count = ?, accepted_count = ?, error = ?
                WHERE id = ?
                """,
                (now_iso(), status, int(memory_count), int(candidate_count), int(accepted_count), error, run_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Pattern mining run not found: {run_id}")
            row = conn.execute("SELECT * FROM pattern_mining_runs WHERE id = ?", (run_id,)).fetchone()
            conn.commit()
        return self._row_to_run(row)

    def mark_processed(
        self,
        memory_ids: Sequence[str],
        *,
        processor_version: str,
        processor_config_hash: str,
        run_id: str,
        status: str = "processed",
        pattern_ids: Optional[Sequence[str]] = None,
        error: Optional[str] = None,
    ) -> int:
        if status not in _PROCESSING_STATUSES:
            raise ValueError(f"Invalid pattern memory processing status: {status}")
        unique_memory_ids = sorted({memory_id for memory_id in memory_ids if memory_id})
        if not unique_memory_ids:
            return 0
        pattern_ids_json = json.dumps(list(pattern_ids or []))
        processed_at = now_iso()
        with self._lock, self._connect() as conn:
            if self._run_exists(conn, run_id) is False:
                raise KeyError(f"Pattern mining run not found: {run_id}")
            conn.executemany(
                """
                INSERT INTO pattern_memory_processing (
                    memory_id, processor_version, processor_config_hash,
                    processed_at, run_id, status, pattern_ids_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id, processor_version, processor_config_hash)
                DO UPDATE SET
                    processed_at = excluded.processed_at,
                    run_id = excluded.run_id,
                    status = excluded.status,
                    pattern_ids_json = excluded.pattern_ids_json,
                    error = excluded.error
                """,
                [
                    (
                        memory_id,
                        processor_version,
                        processor_config_hash,
                        processed_at,
                        run_id,
                        status,
                        pattern_ids_json,
                        error,
                    )
                    for memory_id in unique_memory_ids
                ],
            )
            conn.commit()
        return len(unique_memory_ids)

    def processed_memory_ids(self, *, processor_version: str, processor_config_hash: str) -> set[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT memory_id FROM pattern_memory_processing
                WHERE processor_version = ? AND processor_config_hash = ?
                """,
                (processor_version, processor_config_hash),
            ).fetchall()
        return {row["memory_id"] for row in rows}

    def list_unprocessed_memory_ids(
        self,
        *,
        processor_version: str,
        processor_config_hash: str,
        limit: int = 100,
    ) -> List[str]:
        with self._lock, self._connect() as conn:
            if not self._table_exists(conn, "memories"):
                return []
            rows = conn.execute(
                """
                SELECT m.id
                FROM memories m
                LEFT JOIN pattern_memory_processing p
                    ON p.memory_id = m.id
                    AND p.processor_version = ?
                    AND p.processor_config_hash = ?
                WHERE p.memory_id IS NULL
                ORDER BY m.ts ASC, m.id ASC
                LIMIT ?
                """,
                (processor_version, processor_config_hash, int(limit)),
            ).fetchall()
        return [row["id"] for row in rows]

    def status(self, *, processor_version: str, processor_config_hash: str) -> PatternMiningStatus:
        with self._lock, self._connect() as conn:
            memories_total = self._count_table(conn, "memories")
            processed_current = conn.execute(
                """
                SELECT COUNT(*) AS c FROM pattern_memory_processing
                WHERE processor_version = ? AND processor_config_hash = ?
                """,
                (processor_version, processor_config_hash),
            ).fetchone()["c"]
            candidate_patterns = conn.execute(
                "SELECT COUNT(*) AS c FROM patterns WHERE status = 'candidate'"
            ).fetchone()["c"]
            accepted_patterns = conn.execute(
                "SELECT COUNT(*) AS c FROM patterns WHERE status = 'accepted'"
            ).fetchone()["c"]
            last_run_row = conn.execute(
                """
                SELECT * FROM pattern_mining_runs
                WHERE processor_version = ? AND processor_config_hash = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (processor_version, processor_config_hash),
            ).fetchone()
        processed_int = int(processed_current)
        total_int = int(memories_total)
        return PatternMiningStatus(
            memories_total=total_int,
            processed_current=processed_int,
            unprocessed=max(0, total_int - processed_int),
            candidate_patterns=int(candidate_patterns),
            accepted_patterns=int(accepted_patterns),
            last_run=self._row_to_run(last_run_row) if last_run_row else None,
            processor_version=processor_version,
            processor_config_hash=processor_config_hash,
        )

    def _run_exists(self, conn: sqlite3.Connection, run_id: str) -> bool:
        row = conn.execute("SELECT 1 FROM pattern_mining_runs WHERE id = ?", (run_id,)).fetchone()
        return row is not None

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _count_table(self, conn: sqlite3.Connection, table_name: str) -> int:
        if not self._table_exists(conn, table_name):
            return 0
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()["c"])

    def _row_to_run(self, row: sqlite3.Row) -> PatternMiningRun:
        return PatternMiningRun(
            id=row["id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            processor_version=row["processor_version"],
            processor_config_hash=row["processor_config_hash"],
            mode=row["mode"],
            memory_count=int(row["memory_count"]),
            candidate_count=int(row["candidate_count"]),
            accepted_count=int(row["accepted_count"]),
            error=row["error"],
        )
