from __future__ import annotations

import json
import threading
import sqlite3
from pathlib import Path
from typing import List, Optional, Sequence

from app.storage.sqlite_schema import ensure_pattern_tables

from .errors import PatternStorageUnavailable, PatternValidation
from .models import Pattern, PatternApplication, PatternEvidence, now_iso
from .storage import connect_pattern_db

_PATTERN_KINDS = {"codebase", "workflow", "failure", "preference", "product", "distribution", "other"}
_PATTERN_SCOPES = {"user", "repo", "team", "agent", "global"}
_PATTERN_STATUSES = {"candidate", "accepted", "rejected", "superseded"}
_EVIDENCE_ROLES = {"support", "contradict", "bridge", "central"}


class PatternValidationError(PatternValidation):
    """Raised when a pattern card is not evidence-backed enough to store."""


class PatternStore:
    """SQLite repository for durable pattern cards and their evidence."""

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

    def create_pattern(
        self,
        pattern: Pattern,
        evidence: Sequence[PatternEvidence],
        *,
        validate_memory_ids: bool = True,
        min_support_evidence: int = 1,
    ) -> Pattern:
        """Insert a new pattern and its evidence.

        The store enforces the basic evidence-backed shape. API layers can pass a
        higher ``min_support_evidence`` for product policy, while tests and local
        support tools can still create small synthetic fixtures.
        """

        evidence = list(evidence)
        self._validate_pattern(pattern)
        self._validate_evidence(pattern.id, evidence, min_support_evidence=min_support_evidence)
        with self._lock, self._connect() as conn:
            if validate_memory_ids:
                self._validate_memory_ids(conn, evidence)
            self._insert_pattern(conn, pattern)
            self._replace_evidence(conn, pattern.id, evidence)
            conn.commit()
        return pattern

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
        return self._row_to_pattern(row) if row else None

    def list_patterns(
        self,
        *,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 50,
    ) -> List[Pattern]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            if status not in _PATTERN_STATUSES:
                raise PatternValidationError(f"Invalid pattern status: {status}")
            clauses.append("status = ?")
            params.append(status)
        if scope:
            if scope not in _PATTERN_SCOPES:
                raise PatternValidationError(f"Invalid pattern scope: {scope}")
            clauses.append("scope = ?")
            params.append(scope)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM patterns {where} ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._row_to_pattern(row) for row in rows]

    def update_status(self, pattern_id: str, status: str) -> Pattern:
        if status not in _PATTERN_STATUSES:
            raise PatternValidationError(f"Invalid pattern status: {status}")
        updated_at = now_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE patterns SET status = ?, updated_at = ? WHERE id = ?",
                (status, updated_at, pattern_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Pattern not found: {pattern_id}")
            row = conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
            conn.commit()
        pattern = self._row_to_pattern(row) if row else None
        if pattern is None:
            raise KeyError(f"Pattern not found: {pattern_id}")
        return pattern

    def list_evidence(self, pattern_id: str, *, role: Optional[str] = None) -> List[PatternEvidence]:
        params: list[object] = [pattern_id]
        where = "pattern_id = ?"
        if role:
            if role not in _EVIDENCE_ROLES:
                raise PatternValidationError(f"Invalid evidence role: {role}")
            where += " AND role = ?"
            params.append(role)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM pattern_evidence WHERE {where} ORDER BY score DESC, memory_id ASC",
                tuple(params),
            ).fetchall()
        return [self._row_to_evidence(row) for row in rows]

    def record_application(self, application: PatternApplication) -> PatternApplication:
        with self._lock, self._connect() as conn:
            if not self._pattern_exists(conn, application.pattern_id):
                raise PatternValidationError(f"Pattern does not exist: {application.pattern_id}")
            conn.execute(
                """
                INSERT INTO pattern_applications (
                    id, pattern_id, query, task_id, retrieved_at, was_used, outcome, feedback
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application.id,
                    application.pattern_id,
                    application.query,
                    application.task_id,
                    application.retrieved_at,
                    None if application.was_used is None else int(bool(application.was_used)),
                    application.outcome,
                    application.feedback,
                ),
            )
            conn.execute(
                "UPDATE patterns SET last_applied_at = ?, updated_at = ? WHERE id = ?",
                (application.retrieved_at, now_iso(), application.pattern_id),
            )
            conn.commit()
        return application

    def _insert_pattern(self, conn: sqlite3.Connection, pattern: Pattern) -> None:
        conn.execute(
            """
            INSERT INTO patterns (
                id, title, kind, scope, status, summary, recommended_behavior,
                applies_when, does_not_apply_when, trigger_terms_json,
                confidence, actionability, retrieval_value, canonical_key, mined_run_id,
                last_refreshed_at, last_applied_at, created_at, updated_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern.id,
                pattern.title.strip(),
                pattern.kind,
                pattern.scope,
                pattern.status,
                pattern.summary.strip(),
                pattern.recommended_behavior.strip(),
                pattern.applies_when,
                pattern.does_not_apply_when,
                json.dumps([term.strip() for term in pattern.trigger_terms if term.strip()]),
                float(pattern.confidence),
                float(pattern.actionability),
                float(pattern.retrieval_value),
                pattern.canonical_key,
                pattern.mined_run_id,
                pattern.last_refreshed_at,
                pattern.last_applied_at,
                pattern.created_at,
                pattern.updated_at,
                pattern.source,
            ),
        )

    def _replace_evidence(self, conn: sqlite3.Connection, pattern_id: str, evidence: Sequence[PatternEvidence]) -> None:
        conn.execute("DELETE FROM pattern_evidence WHERE pattern_id = ?", (pattern_id,))
        conn.executemany(
            """
            INSERT INTO pattern_evidence (pattern_id, memory_id, scene_id, role, score)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (item.pattern_id, item.memory_id, item.scene_id, item.role, float(item.score))
                for item in evidence
            ],
        )

    def _validate_pattern(self, pattern: Pattern) -> None:
        if pattern.kind not in _PATTERN_KINDS:
            raise PatternValidationError(f"Invalid pattern kind: {pattern.kind}")
        if pattern.scope not in _PATTERN_SCOPES:
            raise PatternValidationError(f"Invalid pattern scope: {pattern.scope}")
        if pattern.status not in _PATTERN_STATUSES:
            raise PatternValidationError(f"Invalid pattern status: {pattern.status}")
        for field_name in ("title", "summary", "recommended_behavior"):
            if not str(getattr(pattern, field_name, "") or "").strip():
                raise PatternValidationError(f"Pattern {field_name} is required")
        if not [term for term in pattern.trigger_terms if term.strip()]:
            raise PatternValidationError("Pattern trigger_terms are required")
        for field_name in ("confidence", "actionability", "retrieval_value"):
            value = float(getattr(pattern, field_name))
            if value < 0.0 or value > 1.0:
                raise PatternValidationError(f"Pattern {field_name} must be between 0.0 and 1.0")

    def _validate_evidence(
        self,
        pattern_id: str,
        evidence: Sequence[PatternEvidence],
        *,
        min_support_evidence: int,
    ) -> None:
        support_count = 0
        for item in evidence:
            if item.pattern_id != pattern_id:
                raise PatternValidationError("Evidence pattern_id must match the pattern id")
            if item.role not in _EVIDENCE_ROLES:
                raise PatternValidationError(f"Invalid evidence role: {item.role}")
            if not item.memory_id.strip():
                raise PatternValidationError("Evidence memory_id is required")
            if float(item.score) < 0.0 or float(item.score) > 1.0:
                raise PatternValidationError("Evidence score must be between 0.0 and 1.0")
            if item.role == "support":
                support_count += 1
        if support_count < min_support_evidence:
            raise PatternValidationError(
                f"Pattern requires at least {min_support_evidence} support evidence item(s)"
            )

    def _validate_memory_ids(self, conn: sqlite3.Connection, evidence: Sequence[PatternEvidence]) -> None:
        memory_ids = sorted({item.memory_id for item in evidence})
        if not memory_ids:
            return
        memories_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
        ).fetchone()
        if memories_table is None:
            raise PatternValidationError("Cannot validate evidence because memories table does not exist")
        placeholders = ",".join("?" for _ in memory_ids)
        rows = conn.execute(f"SELECT id FROM memories WHERE id IN ({placeholders})", tuple(memory_ids)).fetchall()
        found = {row["id"] for row in rows}
        missing = [memory_id for memory_id in memory_ids if memory_id not in found]
        if missing:
            raise PatternValidationError(f"Evidence memory ids do not exist: {', '.join(missing)}")

    def _pattern_exists(self, conn: sqlite3.Connection, pattern_id: str) -> bool:
        row = conn.execute("SELECT 1 FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
        return row is not None

    def _row_to_pattern(self, row: sqlite3.Row) -> Pattern:
        return Pattern(
            id=row["id"],
            title=row["title"],
            kind=row["kind"],
            scope=row["scope"],
            status=row["status"],
            summary=row["summary"],
            recommended_behavior=row["recommended_behavior"],
            applies_when=row["applies_when"],
            does_not_apply_when=row["does_not_apply_when"],
            trigger_terms=json.loads(row["trigger_terms_json"] or "[]"),
            confidence=float(row["confidence"]),
            actionability=float(row["actionability"]),
            retrieval_value=float(row["retrieval_value"]),
            canonical_key=row["canonical_key"],
            mined_run_id=row["mined_run_id"],
            last_refreshed_at=row["last_refreshed_at"],
            last_applied_at=row["last_applied_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            source=row["source"],
        )

    def _row_to_evidence(self, row: sqlite3.Row) -> PatternEvidence:
        return PatternEvidence(
            pattern_id=row["pattern_id"],
            memory_id=row["memory_id"],
            scene_id=row["scene_id"],
            role=row["role"],
            score=float(row["score"]),
        )
