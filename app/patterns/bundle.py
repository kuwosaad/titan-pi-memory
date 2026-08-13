from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pydantic import ValidationError

from app.storage.sqlite_schema import ensure_pattern_tables

from .models import Pattern, PatternEvidence
from .storage import resolve_pattern_db_path
from .store import PatternStore, PatternValidationError

PATTERN_BUNDLE_SCHEMA = "titan.pattern_bundle.v1"
_SECRET_PATTERNS = [
    ("api_key", re.compile(r"\b(?:sk|pk|AIza|ghp|github_pat|xox[baprs])-?[A-Za-z0-9_\-]{16,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b", re.IGNORECASE)),
    ("env_secret", re.compile(r"\b[A-Z][A-Z0-9_]{2,}_(?:API_)?KEY\s*=\s*[^\s]+")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
]


def resolve_pattern_bundle_db_path() -> Path:
    return resolve_pattern_db_path()


def export_pattern_bundle(
    *,
    statuses: Optional[Sequence[str]] = None,
    scopes: Optional[Sequence[str]] = None,
    include_memory_summaries: bool = True,
    include_progress: bool = True,
    limit: int = 500,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Export shareable pattern cards with evidence summaries, not raw scenes."""

    path = Path(db_path) if db_path is not None else resolve_pattern_bundle_db_path()
    store = PatternStore(path)
    safe_limit = max(1, min(int(limit or 500), 5000))
    selected_statuses = list(statuses or ["accepted"])
    selected_scopes = set(scopes or [])

    patterns: list[Pattern] = []
    seen: set[str] = set()
    for status in selected_statuses:
        for pattern in store.list_patterns(status=status, limit=safe_limit):
            if pattern.id in seen:
                continue
            if selected_scopes and pattern.scope not in selected_scopes:
                continue
            patterns.append(pattern)
            seen.add(pattern.id)
            if len(patterns) >= safe_limit:
                break
        if len(patterns) >= safe_limit:
            break

    evidence: list[PatternEvidence] = []
    for pattern in patterns:
        evidence.extend(store.list_evidence(pattern.id))

    memory_ids = sorted({item.memory_id for item in evidence})
    redaction_counts: Dict[str, int] = {}
    memory_summaries = _memory_summaries(path, memory_ids, redaction_counts) if include_memory_summaries else []
    progress = _export_progress(path, memory_ids, [pattern.id for pattern in patterns]) if include_progress else {
        "mining_runs": [],
        "memory_processing": [],
    }

    redactions = [
        {"kind": kind, "field": "memory_summaries.summary", "count": count}
        for kind, count in sorted(redaction_counts.items())
        if count > 0
    ]
    redactions.append(
        {
            "kind": "raw_scene_text_omitted",
            "field": "scenes",
            "count": len({item.scene_id for item in evidence if item.scene_id}),
        }
    )

    return {
        "schema": PATTERN_BUNDLE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "titan",
        "defaults": {
            "statuses": selected_statuses,
            "include_memory_summaries": include_memory_summaries,
            "include_progress": include_progress,
        },
        "patterns": [_redact_pattern_payload(_dump_model(pattern), redaction_counts) for pattern in patterns],
        "evidence": [_dump_model(item) for item in evidence],
        "memory_summaries": memory_summaries,
        "progress": progress,
        "redactions": redactions,
    }


def import_pattern_bundle(
    bundle: Dict[str, Any],
    *,
    overwrite: bool = False,
    import_progress: bool = True,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Import pattern cards from a bundle without requiring local raw memories."""

    if bundle.get("schema") != PATTERN_BUNDLE_SCHEMA:
        raise ValueError(f"Unsupported pattern bundle schema: {bundle.get('schema')}")

    path = Path(db_path) if db_path is not None else resolve_pattern_bundle_db_path()
    store = PatternStore(path)
    raw_patterns = bundle.get("patterns") if isinstance(bundle.get("patterns"), list) else []
    raw_evidence = bundle.get("evidence") if isinstance(bundle.get("evidence"), list) else []
    evidence_by_pattern: Dict[str, list[PatternEvidence]] = {}
    skipped_evidence = 0
    for raw in raw_evidence:
        if not isinstance(raw, dict):
            skipped_evidence += 1
            continue
        try:
            item = PatternEvidence(**raw)
        except ValidationError:
            skipped_evidence += 1
            continue
        evidence_by_pattern.setdefault(item.pattern_id, []).append(item)

    imported = 0
    skipped_existing = 0
    failed: list[dict[str, str]] = []
    for raw in raw_patterns:
        if not isinstance(raw, dict):
            failed.append({"id": "", "error": "pattern entry is not an object"})
            continue
        try:
            pattern = Pattern(**raw)
        except ValidationError as exc:
            failed.append({"id": str(raw.get("id") or ""), "error": str(exc)})
            continue
        if store.get_pattern(pattern.id) is not None:
            if not overwrite:
                skipped_existing += 1
                continue
            _delete_pattern(path, pattern.id)
        try:
            store.create_pattern(
                pattern,
                evidence_by_pattern.get(pattern.id, []),
                validate_memory_ids=False,
                min_support_evidence=0,
            )
        except (PatternValidationError, ValueError) as exc:
            failed.append({"id": pattern.id, "error": str(exc)})
            continue
        imported += 1

    progress_imported = 0
    if import_progress:
        progress_imported = _import_progress(path, bundle.get("progress") if isinstance(bundle.get("progress"), dict) else {})

    return {
        "schema": PATTERN_BUNDLE_SCHEMA,
        "imported_patterns": imported,
        "skipped_existing_patterns": skipped_existing,
        "failed_patterns": failed,
        "skipped_evidence": skipped_evidence,
        "imported_progress_records": progress_imported,
    }


def _memory_summaries(path: Path, memory_ids: Sequence[str], redaction_counts: Dict[str, int]) -> List[Dict[str, Any]]:
    if not memory_ids:
        return []
    placeholders = ",".join("?" for _ in memory_ids)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "memories"):
            return []
        rows = conn.execute(
            f"""
            SELECT id, scene_id, stream, COALESCE(memory_kind, type, '') AS memory_kind, text, ts
            FROM memories
            WHERE id IN ({placeholders})
            ORDER BY ts ASC, id ASC
            """,
            tuple(memory_ids),
        ).fetchall()
    summaries: list[dict[str, Any]] = []
    for row in rows:
        summary = " ".join(str(row["text"] or "").split())[:500]
        redacted = _redact(summary, redaction_counts)
        summaries.append(
            {
                "memory_id": row["id"],
                "scene_id": row["scene_id"],
                "stream": row["stream"],
                "kind": row["memory_kind"],
                "summary": redacted,
                "created_at": row["ts"],
            }
        )
    return summaries


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _redact(text: str, redaction_counts: Dict[str, int]) -> str:
    redacted = text
    for kind, pattern in _SECRET_PATTERNS:
        redacted, count = pattern.subn(f"[{kind.upper()}_REDACTED]", redacted)
        if count:
            redaction_counts[kind] = redaction_counts.get(kind, 0) + count
    return redacted


def _redact_pattern_payload(payload: Dict[str, Any], redaction_counts: Dict[str, int]) -> Dict[str, Any]:
    redacted: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and key not in {"id", "kind", "scope", "status", "created_at", "updated_at"}:
            redacted[key] = _redact(value, redaction_counts)
        elif isinstance(value, list):
            redacted[key] = [_redact(item, redaction_counts) if isinstance(item, str) else item for item in value]
        else:
            redacted[key] = value
    return redacted


def _export_progress(path: Path, memory_ids: Sequence[str], pattern_ids: Sequence[str]) -> Dict[str, Any]:
    if not memory_ids and not pattern_ids:
        return {"mining_runs": [], "memory_processing": []}
    processing_rows: list[dict[str, Any]] = []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_pattern_tables(conn)
        clauses: list[str] = []
        params: list[Any] = []
        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            clauses.append(f"memory_id IN ({placeholders})")
            params.extend(memory_ids)
        if pattern_ids:
            # SQLite JSON1 is not guaranteed in all bundled runtimes. Fetch rows
            # that mention any pattern and do exact JSON membership filtering in
            # Python to avoid substring collisions such as pattern:abc matching
            # pattern:abcd.
            clauses.append("pattern_ids_json != '[]'")
        where = " OR ".join(clauses) if clauses else "1 = 0"
        rows = conn.execute(
            f"""
            SELECT * FROM pattern_memory_processing
            WHERE {where}
            ORDER BY processed_at DESC
            LIMIT 5000
            """,
            tuple(params),
        ).fetchall()
        selected_pattern_ids = set(pattern_ids)
        selected_memory_ids = set(memory_ids)
        processing_rows = []
        for row in rows:
            record = _processing_row_to_dict(row)
            if record["memory_id"] in selected_memory_ids or selected_pattern_ids.intersection(record.get("pattern_ids") or []):
                processing_rows.append(record)
        run_ids = sorted({row["run_id"] for row in processing_rows if row.get("run_id")})
        mining_runs = _load_runs(conn, run_ids)
    return {"mining_runs": mining_runs, "memory_processing": processing_rows}


def _load_runs(conn: sqlite3.Connection, run_ids: Sequence[str]) -> List[Dict[str, Any]]:
    if not run_ids:
        return []
    placeholders = ",".join("?" for _ in run_ids)
    rows = conn.execute(f"SELECT * FROM pattern_mining_runs WHERE id IN ({placeholders})", tuple(run_ids)).fetchall()
    return [dict(row) for row in rows]


def _import_progress(path: Path, progress: Dict[str, Any]) -> int:
    runs = progress.get("mining_runs") if isinstance(progress.get("mining_runs"), list) else []
    records = progress.get("memory_processing") if isinstance(progress.get("memory_processing"), list) else []
    imported = 0
    with sqlite3.connect(path) as conn:
        ensure_pattern_tables(conn)
        for run in runs:
            if not isinstance(run, dict) or not run.get("id"):
                continue
            conn.execute(
                """
                INSERT INTO pattern_mining_runs (
                    id, started_at, finished_at, status, processor_version, processor_config_hash,
                    mode, memory_count, candidate_count, accepted_count, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    finished_at = excluded.finished_at,
                    status = excluded.status,
                    memory_count = excluded.memory_count,
                    candidate_count = excluded.candidate_count,
                    accepted_count = excluded.accepted_count,
                    error = excluded.error
                """,
                (
                    run.get("id"),
                    run.get("started_at") or datetime.now(timezone.utc).isoformat(),
                    run.get("finished_at"),
                    run.get("status") or "completed",
                    run.get("processor_version") or "imported",
                    run.get("processor_config_hash") or "imported",
                    run.get("mode") or "imported",
                    int(run.get("memory_count") or 0),
                    int(run.get("candidate_count") or 0),
                    int(run.get("accepted_count") or 0),
                    run.get("error"),
                ),
            )
        existing_runs = {row[0] for row in conn.execute("SELECT id FROM pattern_mining_runs").fetchall()}
        for record in records:
            if not isinstance(record, dict) or not record.get("memory_id") or not record.get("run_id"):
                continue
            if record.get("run_id") not in existing_runs:
                continue
            pattern_ids = record.get("pattern_ids") if isinstance(record.get("pattern_ids"), list) else []
            conn.execute(
                """
                INSERT INTO pattern_memory_processing (
                    memory_id, processor_version, processor_config_hash, processed_at,
                    run_id, status, pattern_ids_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id, processor_version, processor_config_hash)
                DO UPDATE SET
                    processed_at = excluded.processed_at,
                    run_id = excluded.run_id,
                    status = excluded.status,
                    pattern_ids_json = excluded.pattern_ids_json,
                    error = excluded.error
                """,
                (
                    record.get("memory_id"),
                    record.get("processor_version") or "imported",
                    record.get("processor_config_hash") or "imported",
                    record.get("processed_at") or datetime.now(timezone.utc).isoformat(),
                    record.get("run_id"),
                    record.get("status") or "processed",
                    json.dumps(pattern_ids),
                    record.get("error"),
                ),
            )
            imported += 1
        conn.commit()
    return imported


def _processing_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        pattern_ids = json.loads(row["pattern_ids_json"] or "[]")
    except json.JSONDecodeError:
        pattern_ids = []
    return {
        "memory_id": row["memory_id"],
        "processor_version": row["processor_version"],
        "processor_config_hash": row["processor_config_hash"],
        "processed_at": row["processed_at"],
        "run_id": row["run_id"],
        "status": row["status"],
        "pattern_ids": pattern_ids,
        "error": row["error"],
    }


def _delete_pattern(path: Path, pattern_id: str) -> None:
    with sqlite3.connect(path) as conn:
        ensure_pattern_tables(conn)
        conn.execute("DELETE FROM patterns WHERE id = ?", (pattern_id,))
        conn.commit()


def _dump_model(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
