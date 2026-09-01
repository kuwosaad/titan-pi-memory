from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .sessions import TRACES_DIR, atomic_write_text, ensure_dirs, interprocess_lock, read_json, write_json


LOGGER = logging.getLogger(__name__)

TRACE_FILE = TRACES_DIR / "trace_packets.json"
EVENT_LEDGER_FILE = TRACES_DIR / "events.jsonl"
EVENT_INDEX_FILE = TRACES_DIR / "event_index.json"
CHECKPOINT_FILE = TRACES_DIR / "checkpoints.json"
SCENE_CHECKPOINT_FILE = TRACES_DIR / "scene_checkpoints.json"
# Compatibility alias for callers that describe the second checkpoint as the
# committed checkpoint.  ``SCENE_CHECKPOINT_FILE`` remains the canonical
# patch point and filename.
COMMITTED_CHECKPOINT_FILE = SCENE_CHECKPOINT_FILE
RETRY_QUEUE_FILE = TRACES_DIR / "retry_queue.jsonl"
SPOOL_CURSOR_FILE = TRACES_DIR / "spool_cursors.json"
PENDING_USER_MESSAGES_FILE = TRACES_DIR / "pending_user_messages.json"
_TRACE_ROOT = TRACES_DIR

_LOCK = threading.RLock()
_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "apikey", "auth", "authorization", "cookie")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk[-_][A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bntn_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bsecret_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}\b", re.IGNORECASE),
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(
        r"\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*\s*=\s*(?:[^\s'\"]+|'[^']*'|\"[^\"]*\")",
        re.IGNORECASE,
    ),
)


def refresh_trace_paths() -> None:
    """Refresh ledger paths after the active runtime context changes.

    Tests and integrations historically patch the module constants directly;
    only values still pointing at the previous root are refreshed.
    """

    global _TRACE_ROOT, TRACE_FILE, EVENT_LEDGER_FILE, EVENT_INDEX_FILE
    global CHECKPOINT_FILE, SCENE_CHECKPOINT_FILE, COMMITTED_CHECKPOINT_FILE
    global RETRY_QUEUE_FILE, SPOOL_CURSOR_FILE, PENDING_USER_MESSAGES_FILE
    from . import sessions

    sessions.refresh_runtime_paths()
    current_root = sessions.TRACES_DIR

    previous_root = _TRACE_ROOT
    if current_root == previous_root:
        return
    names = (
        ("TRACE_FILE", "trace_packets.json"),
        ("EVENT_LEDGER_FILE", "events.jsonl"),
        ("EVENT_INDEX_FILE", "event_index.json"),
        ("CHECKPOINT_FILE", "checkpoints.json"),
        ("SCENE_CHECKPOINT_FILE", "scene_checkpoints.json"),
        ("COMMITTED_CHECKPOINT_FILE", "scene_checkpoints.json"),
        ("RETRY_QUEUE_FILE", "retry_queue.jsonl"),
        ("SPOOL_CURSOR_FILE", "spool_cursors.json"),
        ("PENDING_USER_MESSAGES_FILE", "pending_user_messages.json"),
    )
    for variable, filename in names:
        if globals()[variable] == previous_root / filename:
            globals()[variable] = current_root / filename
    _TRACE_ROOT = current_root


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _redact_sensitive_string(value: str, key_hint: Optional[str] = None) -> str:
    if key_hint and _looks_sensitive_key(key_hint):
        return "[redacted]"

    redacted = value
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def sanitize_trace_value(value: Any, key_hint: Optional[str] = None) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, nested in value.items():
            if _looks_sensitive_key(str(key)):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_trace_value(nested, key_hint=str(key))
        return sanitized
    if isinstance(value, list):
        return [sanitize_trace_value(item, key_hint=key_hint) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_string(value, key_hint=key_hint)
    return value


def load_traces() -> List[Dict[str, Any]]:
    refresh_trace_paths()
    ensure_dirs()
    return read_json(TRACE_FILE, [])


def append_trace(trace: Dict[str, Any]) -> None:
    refresh_trace_paths()
    ensure_dirs()
    traces = load_traces()
    traces.append({"ts": now_iso(), **sanitize_trace_value(trace)})
    write_json(TRACE_FILE, traces)


def _canonical_event_key(session_id: str, event_id: str) -> str:
    return f"{session_id}:{event_id}"


def load_event_index() -> Dict[str, int]:
    refresh_trace_paths()
    ensure_dirs()
    return read_json(EVENT_INDEX_FILE, {})


def save_event_index(index: Dict[str, int]) -> None:
    refresh_trace_paths()
    write_json(EVENT_INDEX_FILE, index)


def load_checkpoints() -> Dict[str, int]:
    refresh_trace_paths()
    ensure_dirs()
    return read_json(CHECKPOINT_FILE, {})


def save_checkpoints(checkpoints: Dict[str, int]) -> None:
    refresh_trace_paths()
    write_json(CHECKPOINT_FILE, checkpoints)


def get_session_checkpoint(session_id: str) -> int:
    checkpoints = load_checkpoints()
    return int(checkpoints.get(session_id, 0))


def update_session_checkpoint(session_id: str, seq: int) -> None:
    with _LOCK:
        checkpoints = load_checkpoints()
        checkpoints[session_id] = max(int(checkpoints.get(session_id, 0)), int(seq))
        save_checkpoints(checkpoints)


def _scene_checkpoint_path() -> Path:
    """Return the active path while supporting either public patch point.

    ``SCENE_CHECKPOINT_FILE`` is the canonical name.  The alias is accepted so
    integrations that called this the committed checkpoint can patch that
    constant without silently writing to the real runtime directory.
    """

    default_path = _TRACE_ROOT / "scene_checkpoints.json"
    if SCENE_CHECKPOINT_FILE != default_path:
        return SCENE_CHECKPOINT_FILE
    if COMMITTED_CHECKPOINT_FILE != default_path:
        return COMMITTED_CHECKPOINT_FILE
    return SCENE_CHECKPOINT_FILE


def _load_scene_checkpoint_state() -> Tuple[Dict[str, int], Dict[str, Set[int]]]:
    """Load committed checkpoints and optional finalized sequence metadata.

    The public checkpoint view stays a simple ``session_id -> seq`` mapping.
    Finalized sequences are kept under a reserved key so callers can persist
    events that are ready to commit before a contiguous checkpoint is raised.
    Older flat files remain valid and need no migration.
    """

    refresh_trace_paths()
    ensure_dirs()
    raw = read_json(_scene_checkpoint_path(), {})
    if not isinstance(raw, dict):
        return {}, {}

    checkpoints: Dict[str, int] = {}
    for session_id, value in raw.items():
        if str(session_id).startswith("_"):
            continue
        try:
            checkpoints[str(session_id)] = int(value)
        except (TypeError, ValueError):
            continue

    finalized_payload = raw.get("_finalized")
    finalized: Dict[str, Set[int]] = {}
    if isinstance(finalized_payload, dict):
        for session_id, values in finalized_payload.items():
            if not isinstance(values, list):
                continue
            parsed: Set[int] = set()
            for value in values:
                try:
                    seq = int(value)
                except (TypeError, ValueError):
                    continue
                if seq > 0:
                    parsed.add(seq)
            if parsed:
                finalized[str(session_id)] = parsed
    return checkpoints, finalized


def _save_scene_checkpoint_state(
    checkpoints: Dict[str, int],
    finalized: Optional[Dict[str, Set[int]]] = None,
) -> None:
    payload: Dict[str, Any] = {
        str(session_id): int(seq)
        for session_id, seq in checkpoints.items()
        if int(seq) > 0
    }
    finalized_payload = {
        str(session_id): sorted(int(seq) for seq in values if int(seq) > 0)
        for session_id, values in (finalized or {}).items()
        if values
    }
    if finalized_payload:
        payload["_finalized"] = finalized_payload
    write_json(_scene_checkpoint_path(), payload)


def load_scene_checkpoints() -> Dict[str, int]:
    """Load durable scene/committed checkpoints by session."""

    checkpoints, _ = _load_scene_checkpoint_state()
    return checkpoints


def save_scene_checkpoints(checkpoints: Dict[str, int]) -> None:
    """Persist scene/committed checkpoints while preserving finalization state."""

    with _LOCK:
        _, finalized = _load_scene_checkpoint_state()
        _save_scene_checkpoint_state(checkpoints, finalized)


def load_committed_checkpoints() -> Dict[str, int]:
    """Compatibility alias for ``load_scene_checkpoints``."""

    return load_scene_checkpoints()


def save_committed_checkpoints(checkpoints: Dict[str, int]) -> None:
    """Compatibility alias for ``save_scene_checkpoints``."""

    save_scene_checkpoints(checkpoints)


def get_scene_checkpoint(session_id: str) -> int:
    checkpoints = load_scene_checkpoints()
    return int(checkpoints.get(session_id, 0))


def get_committed_checkpoint(session_id: str) -> int:
    """Alias for the scene checkpoint used by pruning and future pipeline code."""

    return get_scene_checkpoint(session_id)


def update_scene_checkpoint(session_id: str, seq: int) -> None:
    """Monotonically update the committed checkpoint.

    This compatibility accessor is intentionally explicit: callers should use
    ``mark_scene_events_finalized`` when they need contiguous advancement.
    """

    mark_scene_events_finalized(session_id, [seq])


def update_committed_checkpoint(session_id: str, seq: int) -> None:
    """Compatibility alias for ``update_scene_checkpoint``."""

    update_scene_checkpoint(session_id, seq)


def _session_event_sequences(session_id: str) -> Set[int]:
    prefix = f"{session_id}:"
    sequences: Set[int] = set()
    for key, value in load_event_index().items():
        if not str(key).startswith(prefix):
            continue
        try:
            seq = int(value)
        except (TypeError, ValueError):
            continue
        if seq > 0:
            sequences.add(seq)
    return sequences


def mark_scene_events_finalized(session_id: str, seqs: List[int]) -> int:
    """Record finalized events and advance the committed checkpoint contiguously.

    Contiguity is evaluated over the session's admitted event sequences, not
    every global sequence number, because the ledger sequence is shared by
    multiple sessions.  This lets future pipeline code commit a scene spanning
    several events without skipping an earlier unresolved event in that same
    session.
    """

    with _LOCK:
        checkpoints, finalized = _load_scene_checkpoint_state()
        current = int(checkpoints.get(session_id, 0))
        ready = finalized.setdefault(session_id, set())
        for seq in seqs:
            try:
                parsed_seq = int(seq)
            except (TypeError, ValueError):
                continue
            if parsed_seq > current:
                ready.add(parsed_seq)

        known = _session_event_sequences(session_id)
        known.update(ready)
        candidate_sequences = sorted(seq for seq in known if seq > current)
        for seq in candidate_sequences:
            if seq not in ready:
                break
            current = seq
            ready.discard(seq)

        checkpoints[session_id] = current
        if not ready:
            finalized.pop(session_id, None)
        _save_scene_checkpoint_state(checkpoints, finalized)
        return current


def mark_events_committed(session_id: str, seqs: List[int]) -> int:
    """Compatibility alias for ``mark_scene_events_finalized``."""

    return mark_scene_events_finalized(session_id, seqs)


def advance_scene_checkpoint(session_id: str, seqs: Any) -> int:
    """Advance the committed checkpoint over a finalized sequence or batch."""

    if isinstance(seqs, (str, bytes)):
        seqs = [seqs]
    elif isinstance(seqs, int):
        seqs = [seqs]
    return mark_scene_events_finalized(session_id, list(seqs or []))


def load_pending_user_messages() -> Dict[str, Dict[str, Any]]:
    refresh_trace_paths()
    ensure_dirs()
    payload = read_json(PENDING_USER_MESSAGES_FILE, {})
    return payload if isinstance(payload, dict) else {}


def get_pending_user_message(session_id: str) -> str:
    pending = load_pending_user_messages().get(session_id)
    if not isinstance(pending, dict):
        return ""
    return str(pending.get("content") or "").strip()


def get_pending_user_message_seq(session_id: str) -> int:
    """Return the ledger sequence associated with pending user context.

    The sequence lets intake distinguish context carried over from a previous
    batch from stale state that belongs to the events currently being tested or
    replayed.  Older pending files without a sequence remain compatible.
    """

    pending = load_pending_user_messages().get(session_id)
    if not isinstance(pending, dict):
        return 0
    try:
        return int(pending.get("seq") or 0)
    except (TypeError, ValueError):
        return 0


def set_pending_user_message(session_id: str, content: str, *, seq: int = 0, event_id: Optional[str] = None) -> None:
    content = str(content or "").strip()
    if not content:
        return
    with _LOCK, interprocess_lock(PENDING_USER_MESSAGES_FILE.with_name(f".{PENDING_USER_MESSAGES_FILE.name}.lock")):
        pending = load_pending_user_messages()
        record = pending.get(session_id)
        if not isinstance(record, dict):
            record = {}
        record.update(
            {
                "content": sanitize_trace_value(content),
                "seq": int(seq or 0),
                "event_id": event_id,
                "ts": now_iso(),
            }
        )
        pending[session_id] = record
        write_json(PENDING_USER_MESSAGES_FILE, pending)


def set_pending_scene_events(session_id: str, events: List[Dict[str, Any]]) -> None:
    """Atomically replace one session's durable evidence assembly."""

    with _LOCK, interprocess_lock(PENDING_USER_MESSAGES_FILE.with_name(f".{PENDING_USER_MESSAGES_FILE.name}.lock")):
        pending = load_pending_user_messages()
        record = pending.get(session_id)
        if not isinstance(record, dict):
            record = {}
        if events:
            record["scene_evidence"] = {"events": events, "updated_at": now_iso()}
        else:
            record.pop("scene_evidence", None)
        if record:
            pending[session_id] = record
        else:
            pending.pop(session_id, None)
        write_json(PENDING_USER_MESSAGES_FILE, pending)


def clear_pending_user_message(session_id: str) -> None:
    with _LOCK, interprocess_lock(PENDING_USER_MESSAGES_FILE.with_name(f".{PENDING_USER_MESSAGES_FILE.name}.lock")):
        pending = load_pending_user_messages()
        if session_id not in pending:
            return
        pending.pop(session_id, None)
        write_json(PENDING_USER_MESSAGES_FILE, pending)


def _read_events() -> List[Dict[str, Any]]:
    refresh_trace_paths()
    ensure_dirs()
    if not EVENT_LEDGER_FILE.exists():
        return []

    events: List[Dict[str, Any]] = []
    for line in EVENT_LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def load_events_for_session(session_id: str) -> List[Dict[str, Any]]:
    events = [event for event in _read_events() if event.get("session_id") == session_id]
    events.sort(key=lambda item: int(item.get("seq", 0)))
    return events


def _normalize_event_record(event: Dict[str, Any], seq: int) -> Dict[str, Any]:
    session_id = str(event.get("session_id") or "")
    event_id = str(event.get("event_id") or "")
    if not session_id or not event_id:
        raise ValueError("session_id and event_id are required for event ingest")
    return {
        "seq": seq,
        "ts": event.get("ts") or now_iso(),
        "session_id": session_id,
        "event_id": event_id,
        "event_type": event.get("event_type") or "unknown",
        "payload": sanitize_trace_value(event.get("payload") or {}),
        "schema_version": event.get("schema_version") or "v1",
    }


def _repair_jsonl_tail_for_append(path: Path) -> None:
    """Make an interrupted JSONL tail safe before appending another record."""

    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r+b") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        handle.seek(end - 1)
        if handle.read(1) == b"\n":
            return

        start = 0
        cursor = end - 1
        while cursor >= 0:
            handle.seek(cursor)
            if handle.read(1) == b"\n":
                start = cursor + 1
                break
            cursor -= 1
        handle.seek(start)
        fragment = handle.read(end - start)
        try:
            json.loads(fragment.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            handle.truncate(start)
        else:
            handle.seek(0, os.SEEK_END)
            handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_jsonl_records(path: Path, records: List[Dict[str, Any]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _repair_jsonl_tail_for_append(path)
    payload = b"".join(
        (json.dumps(record, default=str) + "\n").encode("utf-8")
        for record in records
    )
    with path.open("ab+") as handle:
        handle.seek(0, os.SEEK_END)
        start = handle.tell()
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.seek(start)
        persisted = handle.read(len(payload))
    parsed = [json.loads(line) for line in persisted.decode("utf-8").splitlines() if line.strip()]
    if len(parsed) != len(records):
        raise OSError(f"JSONL append verification failed for {path}")


def append_events_batch(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Batch idempotent append for event-first ingest.
    Keeps one lock/index load/index save per batch for throughput.
    """
    ensure_dirs()
    if not events:
        return {"ingested": 0, "duplicate": 0, "invalid": 0, "item_results": [], "sessions_touched": []}

    ingested = 0
    duplicate = 0
    invalid = 0
    item_results: List[Dict[str, Any]] = []
    sessions_touched: Set[str] = set()

    with _LOCK:
        index = load_event_index()
        index_changed = False
        # The ledger is fsynced before the permanent dedupe index. If a process
        # dies in that narrow window, recover only missing entries from retained
        # payloads. Never rebuild or delete existing registry entries.
        for admitted in _read_events():
            admitted_session = str(admitted.get("session_id") or "")
            admitted_event = str(admitted.get("event_id") or "")
            admitted_seq = int(admitted.get("seq") or 0)
            if not admitted_session or not admitted_event or admitted_seq <= 0:
                continue
            admitted_key = _canonical_event_key(admitted_session, admitted_event)
            if admitted_key not in index:
                index[admitted_key] = admitted_seq
                index_changed = True
        checkpoints = load_checkpoints()
        # Checkpoints may refer to event seq values that were later pruned from
        # the temporary ledger/index. New records must still advance past those
        # checkpoints, otherwise per-session processing treats fresh events as
        # already processed.
        index_high_water = max(index.values()) if index else 0
        checkpoint_high_water = max((int(value) for value in checkpoints.values()), default=0)
        next_seq = max(index_high_water, checkpoint_high_water) + 1
        records_to_write: List[Dict[str, Any]] = []

        for event in events:
            try:
                session_id = str(event.get("session_id") or "")
                event_id = str(event.get("event_id") or "")
                if not session_id or not event_id:
                    raise ValueError("session_id and event_id are required for event ingest")

                key = _canonical_event_key(session_id, event_id)
                if key in index:
                    duplicate += 1
                    item_results.append({"status": "duplicate", "seq": None, "session_id": session_id, "event_id": event_id})
                    continue

                record = _normalize_event_record(event, next_seq)
                records_to_write.append(record)
                index[key] = next_seq
                sessions_touched.add(session_id)
                item_results.append({"status": "ingested", "seq": next_seq, "session_id": session_id, "event_id": event_id})
                next_seq += 1
                ingested += 1
            except Exception:
                invalid += 1
                item_results.append({"status": "invalid", "seq": None, "session_id": "", "event_id": ""})

        if records_to_write:
            _append_jsonl_records(EVENT_LEDGER_FILE, records_to_write)
        if records_to_write or index_changed:
            save_event_index(index)

    return {
        "ingested": ingested,
        "duplicate": duplicate,
        "invalid": invalid,
        "item_results": item_results,
        "sessions_touched": sorted(sessions_touched),
    }


def append_event(event: Dict[str, Any]) -> Tuple[str, Optional[int]]:
    """
    Idempotent event append.
    Returns:
      - ("duplicate", None) if already present.
      - ("ingested", seq) when appended.
    """
    result = append_events_batch([event])
    item = result.get("item_results", [{}])[0]
    status = str(item.get("status") or "invalid")
    if status == "invalid":
        raise ValueError("session_id and event_id are required for event ingest")
    if status == "duplicate":
        return ("duplicate", None)
    return ("ingested", int(item.get("seq") or 0))


def get_next_trace_turn(session_id: str) -> int:
    traces = load_traces()
    count = sum(1 for trace in traces if trace.get("session_id") == session_id)

    # Include event ledger checkpoints so event-first ingest increments turns too.
    checkpoint = get_session_checkpoint(session_id)
    return max(count, checkpoint) + 1


def load_unprocessed_events(session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    checkpoint = get_session_checkpoint(session_id)
    events = [event for event in _read_events() if event.get("session_id") == session_id and int(event.get("seq", 0)) > checkpoint]
    events.sort(key=lambda item: int(item.get("seq", 0)))
    if limit is not None:
        return events[:limit]
    return events


def prune_committed_events(session_ids: List[str]) -> Dict[str, int]:
    """Remove ledger rows whose scenes have been durably committed.

    The processed checkpoint only says that an event has been classified. The
    committed scene checkpoint is the safety boundary for deleting its payload.
    ``event_index.json`` is deliberately left untouched so admitted event IDs
    remain duplicates even after their ledger rows are pruned.
    """
    wanted_sessions = {str(session_id) for session_id in session_ids if str(session_id).strip()}
    if not wanted_sessions or not EVENT_LEDGER_FILE.exists():
        return {"before": 0, "after": 0, "removed": 0}

    checkpoints = load_scene_checkpoints()
    retry_keys = {f"{item.get('session_id')}:{item.get('event_id')}" for item in load_retry_queue()}

    with _LOCK:
        events = _read_events()
        kept: List[Dict[str, Any]] = []
        removed = 0
        for event in events:
            session_id = str(event.get("session_id") or "")
            event_id = str(event.get("event_id") or "")
            seq = int(event.get("seq") or 0)
            checkpoint = int(checkpoints.get(session_id, 0))
            key = _canonical_event_key(session_id, event_id)
            if session_id in wanted_sessions and seq <= checkpoint and key not in retry_keys:
                removed += 1
                continue
            kept.append(event)

        if removed:
            EVENT_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                EVENT_LEDGER_FILE,
                "".join(json.dumps(event, default=str) + "\n" for event in kept),
            )

    return {"before": len(events), "after": len(kept), "removed": removed}


def prune_processed_events(session_ids: List[str]) -> Dict[str, int]:
    """Compatibility wrapper using the durable scene checkpoint."""

    return prune_committed_events(session_ids)


def _extract_message_updated_text(body: Dict[str, Any]) -> Optional[str]:
    properties = body.get("properties") or {}
    info = properties.get("info") or {}
    candidates = [
        info.get("summary"),
        info.get("text"),
        info.get("content"),
        properties.get("text"),
        properties.get("content"),
        body.get("text"),
        body.get("content"),
    ]
    for value in candidates:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def _first_non_empty_string(*values: Any) -> Optional[str]:
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def resolve_event_session_id(event: Dict[str, Any], fallback_session_id: Optional[str] = None) -> str:
    payload = event.get("payload") if isinstance(event, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    body = payload.get("body")
    if not isinstance(body, dict):
        body = {}
    properties = body.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    info = properties.get("info")
    if not isinstance(info, dict):
        info = {}
    part = properties.get("part")
    if not isinstance(part, dict):
        part = {}
    status = properties.get("status")
    if not isinstance(status, dict):
        status = {}

    raw_type = str(payload.get("raw_type") or "")
    session_info_id = info.get("id") if raw_type.startswith("session.") else None
    nested_resolved = _first_non_empty_string(
        properties.get("sessionID"),
        properties.get("sessionId"),
        info.get("sessionID"),
        info.get("sessionId"),
        part.get("sessionID"),
        part.get("sessionId"),
        status.get("sessionID"),
        status.get("sessionId"),
        session_info_id,
    )
    if nested_resolved:
        return nested_resolved

    top_level_resolved = _first_non_empty_string(
        event.get("session_id"),
        event.get("sessionID"),
        event.get("sessionId"),
        fallback_session_id,
    )
    return top_level_resolved or "default"


def load_message_context(session_id: str) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """
    Build latest known message metadata for a session from the full event ledger.
    Returns:
      - role_by_message_id
      - parent_by_message_id
      - latest_text_by_message_id
    """
    role_by_message_id: Dict[str, str] = {}
    parent_by_message_id: Dict[str, str] = {}
    latest_text_by_message_id: Dict[str, str] = {}

    events = load_events_for_session(session_id)

    for event in events:
        payload = event.get("payload") or {}
        raw_type = str(payload.get("raw_type") or "")
        body = payload.get("body") or {}

        if raw_type == "message.updated":
            info = (((body.get("properties") or {}).get("info") or {}))
            message_id = info.get("id")
            role = info.get("role")
            parent_id = info.get("parentID")
            if isinstance(message_id, str) and isinstance(role, str):
                role_by_message_id[message_id] = role
                if isinstance(parent_id, str):
                    parent_by_message_id[message_id] = parent_id
                if role == "user":
                    text = _extract_message_updated_text(body)
                    if text:
                        latest_text_by_message_id[message_id] = text
            continue

        if raw_type == "message.part.updated":
            part = (((body.get("properties") or {}).get("part") or {}))
            if part.get("type") != "text":
                continue
            message_id = part.get("messageID")
            text = part.get("text")
            if isinstance(message_id, str) and isinstance(text, str):
                cleaned = text.strip()
                if cleaned:
                    latest_text_by_message_id[message_id] = cleaned

    return role_by_message_id, parent_by_message_id, latest_text_by_message_id


def load_retry_queue(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    refresh_trace_paths()
    ensure_dirs()
    if not RETRY_QUEUE_FILE.exists():
        return []

    entries: List[Dict[str, Any]] = []
    for line in RETRY_QUEUE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if session_id and row.get("session_id") != session_id:
            continue
        entries.append(row)
    entries.sort(key=lambda item: int(item.get("seq", 0)))
    return entries


def append_retry_entry(entry: Dict[str, Any]) -> None:
    refresh_trace_paths()
    ensure_dirs()
    record = {
        "ts": entry.get("ts") or now_iso(),
        "session_id": str(entry.get("session_id") or ""),
        "event_id": str(entry.get("event_id") or ""),
        "seq": int(entry.get("seq") or 0),
        "reason": str(entry.get("reason") or "unknown"),
    }
    if not record["session_id"] or not record["event_id"] or int(record["seq"]) <= 0:
        raise ValueError("retry entry requires session_id, event_id, and seq")

    with _LOCK:
        existing_keys = {f"{item.get('session_id')}:{item.get('event_id')}" for item in load_retry_queue()}
        key = f"{record['session_id']}:{record['event_id']}"
        if key in existing_keys:
            return
        _append_jsonl_records(RETRY_QUEUE_FILE, [record])


def remove_retry_entries(session_id: str, event_ids: Set[str]) -> int:
    if not event_ids or not RETRY_QUEUE_FILE.exists():
        return 0

    with _LOCK:
        removed = 0
        rows = load_retry_queue()
        kept: List[Dict[str, Any]] = []
        for row in rows:
            same_session = row.get("session_id") == session_id
            same_event = str(row.get("event_id") or "") in event_ids
            if same_session and same_event:
                removed += 1
                continue
            kept.append(row)

        if removed == 0:
            return 0

        atomic_write_text(
            RETRY_QUEUE_FILE,
            "".join(json.dumps(row, default=str) + "\n" for row in kept),
        )
        return removed


def get_retry_queue_size(session_id: Optional[str] = None) -> int:
    return len(load_retry_queue(session_id=session_id))


def _atomic_write_json(path: Path, data: Any) -> None:
    serialized = json.dumps(data, indent=2, default=str)
    atomic_write_text(path, serialized)


def load_spool_cursors() -> Dict[str, Dict[str, Any]]:
    refresh_trace_paths()
    ensure_dirs()
    if not SPOOL_CURSOR_FILE.exists():
        return {}
    try:
        payload = json.loads(SPOOL_CURSOR_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        LOGGER.warning("Invalid JSON in spool cursor file: %s. Rebuilding cursors from scratch.", SPOOL_CURSOR_FILE)
        return {}
    LOGGER.warning("Unexpected spool cursor payload type in %s. Rebuilding cursors from scratch.", SPOOL_CURSOR_FILE)
    return {}


def save_spool_cursors(cursors: Dict[str, Dict[str, Any]]) -> None:
    refresh_trace_paths()
    ensure_dirs()
    _atomic_write_json(SPOOL_CURSOR_FILE, cursors)


def _compute_head_hash(path: Path, num_bytes: int = 256) -> Tuple[str, int]:
    digest = hashlib.sha256()
    sample = b""
    with path.open("rb") as handle:
        sample = handle.read(num_bytes)
    digest.update(sample)
    return digest.hexdigest(), len(sample)


def _load_ingest_settings() -> Dict[str, Any]:
    from app.retrieval_pipeline.config import load_settings

    settings = load_settings()
    mode = str(settings.get("ingest_spool_mode", "incremental") or "incremental").strip().lower()
    max_lines = int(settings.get("ingest_spool_max_lines_per_pass", 20000) or 20000)
    return {"mode": mode, "max_lines_per_pass": max(1, max_lines)}


def _cursor_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def _read_incremental_lines(spool_file: Path, start_offset: int, max_lines: int) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    invalid = 0
    processed_lines = 0
    hit_cap = False
    partial_line = False
    last_committed_offset = start_offset

    with spool_file.open("rb") as handle:
        handle.seek(start_offset)
        while processed_lines < max_lines:
            line_start = handle.tell()
            raw = handle.readline()
            if raw == b"":
                break
            if not raw.endswith(b"\n"):
                partial_line = True
                # Keep cursor at the beginning of this incomplete line.
                last_committed_offset = line_start
                break

            processed_lines += 1
            last_committed_offset = handle.tell()
            line = raw.strip()
            if not line:
                continue

            try:
                parsed = json.loads(line.decode("utf-8"))
                if isinstance(parsed, dict):
                    events.append(parsed)
                else:
                    invalid += 1
            except Exception:
                invalid += 1

        if processed_lines >= max_lines:
            hit_cap = True

    return {
        "events": events,
        "invalid": invalid,
        "end_offset": last_committed_offset,
        "processed_lines": processed_lines,
        "hit_cap": hit_cap,
        "partial_line": partial_line,
    }


def _ingest_spool_replay(session_id: str, spool_file: Path) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    invalid = 0

    for line in spool_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                invalid += 1
                continue
            payload["session_id"] = resolve_event_session_id(payload, fallback_session_id=session_id)
            events.append(payload)
        except Exception:
            invalid += 1

    batch = append_events_batch(events)
    return {
        "ingested": int(batch.get("ingested", 0)),
        "duplicate": int(batch.get("duplicate", 0)),
        "invalid": invalid + int(batch.get("invalid", 0)),
        "sessions_touched": list(batch.get("sessions_touched", [])),
        "start_offset": 0,
        "end_offset": spool_file.stat().st_size,
        "bytes_read": spool_file.stat().st_size,
        "processed_lines": len(events),
        "hit_cap": False,
        "partial_line": False,
    }


def ingest_spool_file(session_id: str, spool_dir: Path) -> Dict[str, Any]:
    """
    Ingest events from plugin spool file:
    .opencode/titan/traces/<session_id>.jsonl
    """
    ensure_dirs()
    spool_file = spool_dir / f"{session_id}.jsonl"
    if not spool_file.exists():
        return {"ingested": 0, "duplicate": 0, "invalid": 0, "sessions_touched": []}

    settings = _load_ingest_settings()
    mode = settings["mode"]
    max_lines = settings["max_lines_per_pass"]
    if mode != "incremental":
        return _ingest_spool_replay(session_id=session_id, spool_file=spool_file)

    file_stat = spool_file.stat()
    file_size = int(file_stat.st_size)
    file_mtime_ns = int(file_stat.st_mtime_ns)
    head_hash, head_size = _compute_head_hash(spool_file)

    key = _cursor_key(spool_file)
    cursors = load_spool_cursors()
    cursor = cursors.get(key) or {}
    start_offset = int(cursor.get("offset") or 0)
    previous_head_hash = str(cursor.get("head_hash_256") or "")
    previous_head_size = int(cursor.get("head_size") or 0)

    if start_offset > file_size:
        start_offset = 0
    elif previous_head_hash and previous_head_size > 0 and file_size >= previous_head_size:
        current_prefix_hash, _ = _compute_head_hash(spool_file, num_bytes=previous_head_size)
        if current_prefix_hash != previous_head_hash:
            # File content changed in-place or recreated under same name.
            start_offset = 0

    read_result = _read_incremental_lines(spool_file=spool_file, start_offset=start_offset, max_lines=max_lines)
    parsed_events: List[Dict[str, Any]] = []
    invalid = int(read_result.get("invalid", 0))

    for event in read_result["events"]:
        try:
            event["session_id"] = resolve_event_session_id(event, fallback_session_id=session_id)
            parsed_events.append(event)
        except Exception:
            invalid += 1

    batch = append_events_batch(parsed_events)
    end_offset = int(read_result.get("end_offset", start_offset))

    latest_stat = spool_file.stat()
    latest_head_hash, latest_head_size = _compute_head_hash(spool_file)
    cursors[key] = {
        "offset": end_offset,
        "size": int(latest_stat.st_size),
        "mtime_ns": int(latest_stat.st_mtime_ns),
        "head_hash_256": latest_head_hash,
        "head_size": latest_head_size,
        "updated_at": now_iso(),
    }
    save_spool_cursors(cursors)

    return {
        "ingested": int(batch.get("ingested", 0)),
        "duplicate": int(batch.get("duplicate", 0)),
        "invalid": invalid + int(batch.get("invalid", 0)),
        "sessions_touched": list(batch.get("sessions_touched", [])),
        "start_offset": start_offset,
        "end_offset": end_offset,
        "bytes_read": max(0, end_offset - start_offset),
        "processed_lines": int(read_result.get("processed_lines", 0)),
        "hit_cap": bool(read_result.get("hit_cap", False)),
        "partial_line": bool(read_result.get("partial_line", False)),
        "spool_size": int(latest_stat.st_size),
        "spool_mtime_ns": int(latest_stat.st_mtime_ns),
        "spool_head_hash_256": head_hash,
        "spool_head_size": head_size,
    }


def cleanup_processed_spool_file(spool_file: Path, ingest_counts: Dict[str, Any], allow_delete: bool = True) -> Dict[str, Any]:
    if not allow_delete or not spool_file.exists():
        return {"deleted": False, "reason": "disabled_or_missing"}
    if bool(ingest_counts.get("hit_cap")) or bool(ingest_counts.get("partial_line")):
        return {"deleted": False, "reason": "more_data_pending"}

    end_offset = int(ingest_counts.get("end_offset") or 0)
    latest_size = int(spool_file.stat().st_size)
    if end_offset < latest_size:
        return {"deleted": False, "reason": "unread_tail", "end_offset": end_offset, "size": latest_size}

    key = _cursor_key(spool_file)
    with _LOCK:
        try:
            spool_file.unlink()
        except FileNotFoundError:
            pass
        cursors = load_spool_cursors()
        if key in cursors:
            cursors.pop(key, None)
            save_spool_cursors(cursors)
    return {"deleted": True, "reason": "processed", "bytes_removed": latest_size}


def get_spool_cursor(spool_file: Path) -> Optional[Dict[str, Any]]:
    key = _cursor_key(spool_file)
    return load_spool_cursors().get(key)


def _tail_lines(path: Path, max_lines: int) -> List[str]:
    if not path.exists():
        return []
    ring: deque[str] = deque(maxlen=max_lines)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            ring.append(line)
    return list(ring)


def get_spool_latest_ts(session_id: str, spool_file: Path, max_scan_lines: int = 1000) -> Optional[str]:
    if not spool_file.exists():
        return None

    for line in reversed(_tail_lines(spool_file, max_scan_lines)):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        resolved = resolve_event_session_id(payload, fallback_session_id=session_id)
        if resolved != session_id:
            continue
        ts = payload.get("ts")
        if isinstance(ts, str) and ts.strip():
            return ts.strip()
    return None


def get_ledger_latest_ts(session_id: str, max_scan_lines: int = 2000) -> Optional[str]:
    refresh_trace_paths()
    if not EVENT_LEDGER_FILE.exists():
        return None

    for line in reversed(_tail_lines(EVENT_LEDGER_FILE, max_scan_lines)):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if payload.get("session_id") != session_id:
            continue
        ts = payload.get("ts")
        if isinstance(ts, str) and ts.strip():
            return ts.strip()
    return None
