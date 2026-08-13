"""Migrations for scene evidence stored by older Titan versions.

This module deliberately owns the migration policy instead of teaching the
runtime scene repositories about one-off legacy repair rules.  The public
interface is :func:`backfill_scene_evidence`; it reads legacy scenes and the
surviving event ledger, then optionally persists a conservative partial
evidence record.

Legacy scenes are never promoted to complete by this migration.  A complete
scene requires the newer ingestion path to prove its full event lineage.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.runtime.context import get_runtime_context
from .sessions import read_json, write_json
from .sqlite_schema import ensure_memory_store_metadata, ensure_scene_readable_views


EVIDENCE_VERSION = 0
EVIDENCE_STATUS = "partial"
MIGRATION_SCHEMA_VERSION = 1
MIGRATION_TABLE = "scene_evidence_migrations"


@dataclass(frozen=True)
class SceneMigrationResult:
    """The stable, JSON-serializable result for one migrated scene."""

    scene_id: str
    session_id: str
    legacy: bool
    lineage_source: str
    recovered_event_ids: List[str] = field(default_factory=list)
    missing_source_event_ids: List[str] = field(default_factory=list)
    evidence_status: str = EVIDENCE_STATUS
    evidence_version: int = EVIDENCE_VERSION
    changed: bool = False
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "session_id": self.session_id,
            "legacy": self.legacy,
            "lineage_source": self.lineage_source,
            "recovered_event_ids": list(self.recovered_event_ids),
            "missing_source_event_ids": list(self.missing_source_event_ids),
            "evidence_status": self.evidence_status,
            "evidence_version": self.evidence_version,
            "changed": self.changed,
            "reason": self.reason,
        }


@dataclass
class _SceneRecord:
    """Backend-neutral scene payload plus its persistence identity."""

    payload: Dict[str, Any]
    row_id: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _as_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _scene_is_legacy(scene: Mapping[str, Any]) -> bool:
    """Return whether this command is allowed to touch the scene.

    Versioned evidence written by a newer runtime is outside this migration.
    A missing version/status pair is the normal shape of an old scene.  A
    version-0 partial scene remains eligible so a later run can recover newly
    surviving ledger events, while an existing complete scene is immutable.
    """

    status = str(scene.get("evidence_status") or "").strip().lower()
    version = _as_int(scene.get("evidence_version"))
    if status == "complete":
        return False
    if version is not None and version > EVIDENCE_VERSION:
        return False
    return True


def _scene_paths() -> Tuple[Path, Path, str]:
    context = get_runtime_context()
    memory_dir = context.base_dir / "out" / "memories"
    return memory_dir / "scenes.json", context.memory_db_path, context.memory_backend


def _trace_candidates() -> List[Path]:
    context = get_runtime_context()
    candidates = [context.trace_dir / "events.jsonl", context.base_dir / "out" / "traces" / "events.jsonl"]
    unique: List[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def _event_index_candidates() -> List[Path]:
    context = get_runtime_context()
    candidates = [context.trace_dir / "event_index.json", context.base_dir / "out" / "traces" / "event_index.json"]
    unique: List[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def _load_event_ledger() -> List[Dict[str, Any]]:
    for path in _trace_candidates():
        if not path.exists():
            continue
        events: List[Dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(event, dict):
                events.append(event)
        return events
    return []


def _load_event_index() -> Dict[str, int]:
    for path in _event_index_candidates():
        if not path.exists():
            continue
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            return {}
        result: Dict[str, int] = {}
        for key, value in payload.items():
            sequence = _as_int(value)
            if sequence is not None:
                result[str(key)] = sequence
        return result
    return {}


def _load_json_scenes(path: Path) -> List[_SceneRecord]:
    payload = read_json(path, [])
    if not isinstance(payload, list):
        return []
    records: List[_SceneRecord] = []
    for scene in payload:
        if isinstance(scene, dict):
            scene_id = str(scene.get("scene_id") or "").strip()
            if scene_id:
                records.append(_SceneRecord(dict(scene), scene_id))
    return records


def _decode_row(row: sqlite3.Row) -> Dict[str, Any]:
    keys = set(row.keys())

    def value(name: str, default: Any = None) -> Any:
        return row[name] if name in keys else default

    return {
        "scene_id": str(value("scene_id", "") or ""),
        "session_id": str(value("session_id", "") or ""),
        "turn": value("turn", 0),
        "kind": value("kind", "message_exchange"),
        "scene_seq": value("scene_seq"),
        "start_event_seq": value("start_event_seq"),
        "end_event_seq": value("end_event_seq"),
        "anchor_event_id": value("anchor_event_id"),
        "ts": value("ts"),
        "source_event_ids": _json_list(value("source_event_ids_json", "[]")),
        "raw_events": _json_list(value("raw_events_json", "[]")),
        "messages": _json_list(value("messages_json", "[]")),
        "tool_calls": _json_list(value("tool_calls_json", "[]")),
        "extraction_user_text": value("extraction_user_text", ""),
        "extraction_assistant_text": value("extraction_assistant_text", ""),
        "used_context_fallback": bool(value("used_context_fallback", 0)),
    }


def _load_sqlite_scenes(path: Path) -> List[_SceneRecord]:
    if not path.exists():
        return []
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='scenes'"
            ).fetchone()
            if table is None:
                return []
            rows = conn.execute("SELECT * FROM scenes ORDER BY ts ASC, scene_id ASC").fetchall()
    except (OSError, sqlite3.Error):
        return []
    records: List[_SceneRecord] = []
    for row in rows:
        payload = _decode_row(row)
        keys = set(row.keys())
        if "evidence_version" in keys:
            payload["evidence_version"] = row["evidence_version"]
        if "evidence_status" in keys:
            payload["evidence_status"] = row["evidence_status"]
        if "missing_source_event_ids_json" in keys:
            payload["missing_source_event_ids"] = _json_list(row["missing_source_event_ids_json"])
        scene_id = str(payload.get("scene_id") or "").strip()
        if scene_id:
            records.append(_SceneRecord(payload, scene_id))
    return records


def _load_scenes() -> Tuple[List[_SceneRecord], str, Path, Path]:
    json_path, sqlite_path, backend = _scene_paths()
    if backend == "json":
        return _load_json_scenes(json_path), "json", json_path, sqlite_path
    return _load_sqlite_scenes(sqlite_path), "sqlite", json_path, sqlite_path


def _event_id(event: Mapping[str, Any]) -> str:
    return str(event.get("event_id") or "").strip()


def _event_seq(event: Mapping[str, Any]) -> Optional[int]:
    return _as_int(event.get("seq"))


def _event_key(event: Mapping[str, Any]) -> Tuple[Optional[int], str]:
    return _event_seq(event), _event_id(event)


def _existing_event_ids(scene: Mapping[str, Any], session_id: str) -> List[str]:
    raw_ids: List[str] = []
    for item in _json_list(scene.get("raw_events")):
        if isinstance(item, dict) and str(item.get("session_id") or "") == session_id:
            raw_ids.extend([_event_id(item)])
    return _unique_strings(raw_ids)


def _embedded_lineage_ids(scene: Mapping[str, Any]) -> List[str]:
    embedded_ids: List[str] = []
    for item in _json_list(scene.get("raw_events")):
        if isinstance(item, dict):
            embedded_ids.extend([_event_id(item)])
    for item in _json_list(scene.get("messages")) + _json_list(scene.get("tool_calls")):
        if isinstance(item, dict):
            embedded_ids.extend([str(item.get("event_id") or "")])
    return _unique_strings(embedded_ids)


def _range_lineage_ids(scene: Mapping[str, Any], session_id: str, index: Mapping[str, int]) -> List[str]:
    start = _as_int(scene.get("start_event_seq"))
    end = _as_int(scene.get("end_event_seq"))
    if start is None or end is None or end < start:
        return []
    prefix = f"{session_id}:"
    by_seq: Dict[int, str] = {}
    for key, sequence in index.items():
        if not str(key).startswith(prefix):
            continue
        event_id = str(key)[len(prefix) :]
        if start <= sequence <= end and event_id:
            by_seq[sequence] = event_id
    return [by_seq[sequence] for sequence in sorted(by_seq)]


def _lineage_for_scene(scene: Mapping[str, Any], index: Mapping[str, int]) -> Tuple[List[str], str]:
    session_id = str(scene.get("session_id") or "")
    source_ids = _unique_strings(scene.get("source_event_ids") or [])
    if source_ids:
        return source_ids, "source_event_ids"
    indexed_ids = _range_lineage_ids(scene, session_id, index)
    if indexed_ids:
        return indexed_ids, "event_range"
    existing_ids = _embedded_lineage_ids(scene)
    if existing_ids:
        return existing_ids, "embedded_event_ids"
    return [], "unverifiable"


def _merge_raw_events(
    scene: Mapping[str, Any],
    recovered: Sequence[Dict[str, Any]],
    session_id: str,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    # Prefer surviving canonical ledger events over embedded legacy copies.
    for candidate in list(recovered) + list(_json_list(scene.get("raw_events"))):
        if not isinstance(candidate, dict):
            continue
        candidate_session = str(candidate.get("session_id") or "")
        if candidate_session and candidate_session != session_id:
            continue
        event_id = _event_id(candidate)
        sequence = _event_seq(candidate)
        if event_id and event_id in seen_ids:
            continue
        if sequence is not None and sequence in seen_sequences:
            continue
        if event_id:
            seen_ids.add(event_id)
        if sequence is not None:
            seen_sequences.add(sequence)
        merged.append(dict(candidate))
    merged.sort(key=lambda event: (0, _event_seq(event), _event_id(event)) if _event_seq(event) is not None else (1, 0, _event_id(event)))
    return merged


def _plan_scene(scene: Mapping[str, Any], events: Sequence[Dict[str, Any]], index: Mapping[str, int]) -> Tuple[Dict[str, Any], SceneMigrationResult]:
    scene_id = str(scene.get("scene_id") or "").strip()
    session_id = str(scene.get("session_id") or "").strip()
    if not _scene_is_legacy(scene):
        result = SceneMigrationResult(
            scene_id=scene_id,
            session_id=session_id,
            legacy=False,
            lineage_source="versioned",
            evidence_status=str(scene.get("evidence_status") or "complete"),
            evidence_version=_as_int(scene.get("evidence_version")) or 0,
            changed=False,
            reason="existing versioned scene was left unchanged",
        )
        return dict(scene), result

    expected_ids, lineage_source = _lineage_for_scene(scene, index)
    existing_ids = set(_existing_event_ids(scene, session_id))
    by_id = {
        _event_id(event): dict(event)
        for event in events
        if str(event.get("session_id") or "") == session_id and _event_id(event)
    }

    recovered: List[Dict[str, Any]] = []
    recovered_ids: List[str] = []
    present_ids: set[str] = set(existing_ids)
    for event_id in expected_ids:
        event = by_id.get(event_id)
        if event is None:
            continue
        if event_id in existing_ids:
            continue
        recovered.append(event)
        recovered_ids.append(event_id)
        present_ids.add(event_id)

    missing_ids = [event_id for event_id in expected_ids if event_id not in present_ids]
    updated = dict(scene)
    updated["raw_events"] = _merge_raw_events(scene, recovered, session_id)
    updated["source_event_ids"] = _unique_strings(
        list(scene.get("source_event_ids") or []) + [event_id for event_id in recovered_ids if event_id not in existing_ids]
    )
    updated["evidence_version"] = EVIDENCE_VERSION
    updated["evidence_status"] = EVIDENCE_STATUS
    updated["missing_source_event_ids"] = missing_ids

    changed = any(updated.get(key) != scene.get(key) for key in (
        "raw_events",
        "source_event_ids",
        "evidence_version",
        "evidence_status",
        "missing_source_event_ids",
    ))
    reason = None
    if lineage_source == "unverifiable":
        reason = "legacy scene has no source event IDs or recoverable event range"
    elif missing_ids:
        reason = f"{len(missing_ids)} source event(s) were pruned or unavailable"
    else:
        reason = "surviving events recovered; legacy scene remains partial"

    result = SceneMigrationResult(
        scene_id=scene_id,
        session_id=session_id,
        legacy=True,
        lineage_source=lineage_source,
        recovered_event_ids=recovered_ids,
        missing_source_event_ids=missing_ids,
        changed=changed,
        reason=reason,
    )
    return updated, result


def _write_json_scenes(path: Path, records: Sequence[_SceneRecord], updates: Mapping[str, Dict[str, Any]]) -> None:
    payload = read_json(path, [])
    if not isinstance(payload, list):
        raise ValueError(f"Scene file is not a JSON list: {path}")
    updated_payload: List[Any] = []
    for item in payload:
        if isinstance(item, dict):
            scene_id = str(item.get("scene_id") or "").strip()
            updated_payload.append(updates.get(scene_id, item))
        else:
            updated_payload.append(item)
    write_json(path, updated_payload)


def _ensure_sqlite_evidence_columns(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    additions = (
        ("source_event_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("raw_events_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("evidence_version", "INTEGER NOT NULL DEFAULT 0"),
        ("evidence_status", "TEXT NOT NULL DEFAULT 'partial'"),
        ("missing_source_event_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    )
    for name, definition in additions:
        if name not in columns:
            conn.execute(f"ALTER TABLE scenes ADD COLUMN {name} {definition}")


def _write_sqlite_scenes(path: Path, updates: Mapping[str, Dict[str, Any]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_sqlite_evidence_columns(conn)
        for scene_id, scene in updates.items():
            conn.execute(
                """
                UPDATE scenes
                SET source_event_ids_json = ?,
                    raw_events_json = ?,
                    evidence_version = ?,
                    evidence_status = ?,
                    missing_source_event_ids_json = ?
                WHERE scene_id = ?
                """,
                (
                    json.dumps(scene.get("source_event_ids") or []),
                    json.dumps(scene.get("raw_events") or [], default=str),
                    int(scene.get("evidence_version") or EVIDENCE_VERSION),
                    str(scene.get("evidence_status") or EVIDENCE_STATUS),
                    json.dumps(scene.get("missing_source_event_ids") or []),
                    scene_id,
                ),
            )
        ensure_memory_store_metadata(conn)
        ensure_scene_readable_views(conn)
        conn.commit()


def _write_migration_receipt(
    backend: str,
    json_path: Path,
    sqlite_path: Path,
    results: Sequence[SceneMigrationResult],
) -> None:
    """Persist a small audit receipt for operators and future migrations.

    Scene evidence itself is written into the active scene store.  The receipt
    only records what this command did, so it remains useful even while the
    public scene readers are still being upgraded to expose the new fields.
    """

    receipt = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "backend": backend,
        "scenes": {result.scene_id: result.as_dict() for result in results if result.legacy},
    }
    if backend == "json":
        write_json(json_path.with_name("scene_evidence_migrations.json"), receipt)
        return
    if not sqlite_path.exists():
        return
    with sqlite3.connect(sqlite_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
                scene_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                evidence_version INTEGER NOT NULL,
                evidence_status TEXT NOT NULL,
                missing_source_event_ids_json TEXT NOT NULL,
                recovered_event_ids_json TEXT NOT NULL,
                lineage_source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        for result in results:
            if not result.legacy:
                continue
            conn.execute(
                f"""
                INSERT INTO {MIGRATION_TABLE} (
                    scene_id, schema_version, evidence_version, evidence_status,
                    missing_source_event_ids_json, recovered_event_ids_json,
                    lineage_source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scene_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    evidence_version=excluded.evidence_version,
                    evidence_status=excluded.evidence_status,
                    missing_source_event_ids_json=excluded.missing_source_event_ids_json,
                    recovered_event_ids_json=excluded.recovered_event_ids_json,
                    lineage_source=excluded.lineage_source,
                    updated_at=excluded.updated_at
                """,
                (
                    result.scene_id,
                    MIGRATION_SCHEMA_VERSION,
                    result.evidence_version,
                    result.evidence_status,
                    json.dumps(result.missing_source_event_ids),
                    json.dumps(result.recovered_event_ids),
                    result.lineage_source,
                    _now_iso(),
                ),
            )
        conn.commit()


def _plan_dedupe_index_repair(
    scenes: Sequence[Mapping[str, Any]],
    index: Mapping[str, int],
) -> Dict[str, int]:
    """Recover permanent seen-event keys from durable scene lineage.

    A value of ``0`` means the original sequence was already pruned and cannot
    be recovered.  Deduplication depends on key presence, so the sentinel keeps
    the permanent seen-event invariant without inventing ordering information.
    """

    additions: Dict[str, int] = {}
    for scene in scenes:
        session_id = str(scene.get("session_id") or "").strip()
        if not session_id:
            continue
        raw_sequences = {
            _event_id(event): _event_seq(event)
            for event in _json_list(scene.get("raw_events"))
            if isinstance(event, dict)
            and str(event.get("session_id") or session_id) == session_id
            and _event_id(event)
            and (_event_seq(event) or 0) > 0
        }
        message_and_tool_ids = [
            str(item.get("event_id") or "")
            for item in _json_list(scene.get("messages")) + _json_list(scene.get("tool_calls"))
            if isinstance(item, dict)
        ]
        lineage_ids = _unique_strings(
            list(scene.get("source_event_ids") or [])
            + list(scene.get("missing_source_event_ids") or [])
            + _existing_event_ids(scene, session_id)
            + message_and_tool_ids
        )
        for event_id in lineage_ids:
            key = f"{session_id}:{event_id}"
            if key in index or key in additions:
                continue
            additions[key] = int(raw_sequences.get(event_id) or 0)
    return additions


def backfill_scene_evidence(*, apply: bool = False) -> Dict[str, Any]:
    """Inspect or conservatively backfill legacy scene evidence.

    ``apply=False`` is a read-only dry run.  It does not create storage
    directories, alter scene rows, add SQLite columns, or write a receipt.
    ``apply=True`` writes recovered events and explicit missing IDs, but keeps
    every legacy scene at evidence version 0 / partial status.
    """

    records, backend, json_path, sqlite_path = _load_scenes()
    events = _load_event_ledger()
    index = _load_event_index()
    planned_updates: Dict[str, Dict[str, Any]] = {}
    results: List[SceneMigrationResult] = []

    for record in records:
        updated, result = _plan_scene(record.payload, events, index)
        results.append(result)
        if result.legacy and result.changed:
            planned_updates[record.row_id] = updated

    planned_scenes = [planned_updates.get(record.row_id, record.payload) for record in records]
    index_additions = _plan_dedupe_index_repair(planned_scenes, index)

    if apply and planned_updates:
        if backend == "json":
            _write_json_scenes(json_path, records, planned_updates)
        else:
            _write_sqlite_scenes(sqlite_path, planned_updates)

    if apply and index_additions:
        repaired_index = dict(index)
        repaired_index.update(index_additions)
        write_json(_event_index_candidates()[0], repaired_index)

    if apply and (planned_updates or index_additions):
        _write_migration_receipt(backend, json_path, sqlite_path, results)

    legacy_results = [result for result in results if result.legacy]
    return {
        "apply": apply,
        "dry_run": not apply,
        "backend": backend,
        "scene_count": len(results),
        "legacy_scene_count": len(legacy_results),
        "changed_scene_count": sum(1 for result in legacy_results if result.changed),
        "recovered_event_count": sum(len(result.recovered_event_ids) for result in legacy_results),
        "missing_event_count": sum(len(result.missing_source_event_ids) for result in legacy_results),
        "dedupe_index_entries_added": len(index_additions),
        "unverifiable_scene_count": sum(1 for result in legacy_results if result.lineage_source == "unverifiable"),
        "scenes": [result.as_dict() for result in results],
    }


__all__ = ["backfill_scene_evidence", "SceneMigrationResult"]
