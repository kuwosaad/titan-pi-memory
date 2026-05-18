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

from .sessions import TRACES_DIR, ensure_dirs, read_json, write_json


LOGGER = logging.getLogger(__name__)

TRACE_FILE = TRACES_DIR / "trace_packets.json"
EVENT_LEDGER_FILE = TRACES_DIR / "events.jsonl"
EVENT_INDEX_FILE = TRACES_DIR / "event_index.json"
CHECKPOINT_FILE = TRACES_DIR / "checkpoints.json"
RETRY_QUEUE_FILE = TRACES_DIR / "retry_queue.jsonl"
SPOOL_CURSOR_FILE = TRACES_DIR / "spool_cursors.json"
PENDING_USER_MESSAGES_FILE = TRACES_DIR / "pending_user_messages.json"

_LOCK = threading.Lock()
_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "apikey", "auth", "authorization", "cookie")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk[-_][A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bntn_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bsecret_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}\b", re.IGNORECASE),
)


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
    return read_json(TRACE_FILE, [])


def append_trace(trace: Dict[str, Any]) -> None:
    ensure_dirs()
    traces = load_traces()
    traces.append({"ts": now_iso(), **sanitize_trace_value(trace)})
    write_json(TRACE_FILE, traces)


def _canonical_event_key(session_id: str, event_id: str) -> str:
    return f"{session_id}:{event_id}"


def load_event_index() -> Dict[str, int]:
    return read_json(EVENT_INDEX_FILE, {})


def save_event_index(index: Dict[str, int]) -> None:
    write_json(EVENT_INDEX_FILE, index)


def load_checkpoints() -> Dict[str, int]:
    return read_json(CHECKPOINT_FILE, {})


def save_checkpoints(checkpoints: Dict[str, int]) -> None:
    write_json(CHECKPOINT_FILE, checkpoints)


def get_session_checkpoint(session_id: str) -> int:
    checkpoints = load_checkpoints()
    return int(checkpoints.get(session_id, 0))


def update_session_checkpoint(session_id: str, seq: int) -> None:
    checkpoints = load_checkpoints()
    checkpoints[session_id] = max(int(checkpoints.get(session_id, 0)), int(seq))
    save_checkpoints(checkpoints)


def load_pending_user_messages() -> Dict[str, Dict[str, Any]]:
    payload = read_json(PENDING_USER_MESSAGES_FILE, {})
    return payload if isinstance(payload, dict) else {}


def get_pending_user_message(session_id: str) -> str:
    pending = load_pending_user_messages().get(session_id)
    if not isinstance(pending, dict):
        return ""
    return str(pending.get("content") or "").strip()


def set_pending_user_message(session_id: str, content: str, *, seq: int = 0, event_id: Optional[str] = None) -> None:
    content = str(content or "").strip()
    if not content:
        return
    pending = load_pending_user_messages()
    pending[session_id] = {
        "content": sanitize_trace_value(content),
        "seq": int(seq or 0),
        "event_id": event_id,
        "ts": now_iso(),
    }
    write_json(PENDING_USER_MESSAGES_FILE, pending)


def clear_pending_user_message(session_id: str) -> None:
    pending = load_pending_user_messages()
    if session_id not in pending:
        return
    pending.pop(session_id, None)
    write_json(PENDING_USER_MESSAGES_FILE, pending)


def _read_events() -> List[Dict[str, Any]]:
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
            EVENT_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
            with EVENT_LEDGER_FILE.open("a", encoding="utf-8") as handle:
                for record in records_to_write:
                    handle.write(json.dumps(record, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
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


def prune_processed_events(session_ids: List[str]) -> Dict[str, int]:
    """Remove ledger rows that have already become scenes/memories.

    The event ledger is temporary ingest scaffolding. Checkpoints and retry rows
    decide what must stay; processed rows can be dropped to prevent trace bloat.
    """
    wanted_sessions = {str(session_id) for session_id in session_ids if str(session_id).strip()}
    if not wanted_sessions or not EVENT_LEDGER_FILE.exists():
        return {"before": 0, "after": 0, "removed": 0}

    checkpoints = load_checkpoints()
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
            tmp_path = EVENT_LEDGER_FILE.with_suffix(".jsonl.tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                for event in kept:
                    handle.write(json.dumps(event, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, EVENT_LEDGER_FILE)
            save_event_index({_canonical_event_key(str(event.get("session_id") or ""), str(event.get("event_id") or "")): int(event.get("seq") or 0) for event in kept})

    return {"before": len(events), "after": len(kept), "removed": removed}


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
        RETRY_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with RETRY_QUEUE_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


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

        tmp_path = RETRY_QUEUE_FILE.with_suffix(".jsonl.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for row in kept:
                handle.write(json.dumps(row, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, RETRY_QUEUE_FILE)
        return removed


def get_retry_queue_size(session_id: Optional[str] = None) -> int:
    return len(load_retry_queue(session_id=session_id))


def _atomic_write_json(path: Path, data: Any) -> None:
    serialized = json.dumps(data, indent=2, default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def load_spool_cursors() -> Dict[str, Dict[str, Any]]:
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
