from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.sessions import MEMORIES_DIR, now_iso

LOGGER = logging.getLogger(__name__)

DEDUP_BUFFER_FILE = MEMORIES_DIR / "dedup_buffer.jsonl"
DEDUP_PROCESSING_FILE = MEMORIES_DIR / "dedup_buffer.processing.jsonl"

_LOCK = threading.RLock()


def _load_buffer() -> List[Dict[str, Any]]:
    if not DEDUP_BUFFER_FILE.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with _LOCK:
        try:
            raw = DEDUP_BUFFER_FILE.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            LOGGER.error("dedup_buffer: cannot read buffer file (Unicode error): %s", exc)
            return []
        except OSError as exc:
            LOGGER.error("dedup_buffer: cannot read buffer file (I/O error): %s", exc)
            return []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                LOGGER.warning("Skipping corrupted dedup buffer line")
    return entries


def _save_buffer(entries: List[Dict[str, Any]]) -> None:
    DEDUP_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        tmp = DEDUP_BUFFER_FILE.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
        tmp.replace(DEDUP_BUFFER_FILE)


def _drain_buffer() -> List[Dict[str, Any]]:
    """Atomically drain entries from the buffer into a processing file, then clear the buffer."""
    with _LOCK:
        if not DEDUP_BUFFER_FILE.exists():
            return []
        entries: List[Dict[str, Any]] = []
        try:
            raw = DEDUP_BUFFER_FILE.read_text(encoding="utf-8")
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    LOGGER.warning("Skipping corrupted line during drain")
        except OSError as exc:
            LOGGER.error("dedup_buffer: drain read error: %s", exc)
            return []

        if not entries:
            return []

        DEDUP_PROCESSING_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DEDUP_PROCESSING_FILE.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
        tmp.replace(DEDUP_PROCESSING_FILE)

        DEDUP_BUFFER_FILE.unlink(missing_ok=True)
        LOGGER.info("dedup_buffer: drained %d entries to processing file", len(entries))
        return entries


def _release_processing_file() -> None:
    """Called after successful dedup processing — removes the processing file."""
    with _LOCK:
        DEDUP_PROCESSING_FILE.unlink(missing_ok=True)


def _recover_processing_file() -> List[Dict[str, Any]]:
    """On startup, recover entries from a crashed processing run."""
    if not DEDUP_PROCESSING_FILE.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with _LOCK:
        try:
            raw = DEDUP_PROCESSING_FILE.read_text(encoding="utf-8")
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass
    if entries:
        LOGGER.info("dedup_buffer: recovered %d entries from previous processing run", len(entries))
    DEDUP_PROCESSING_FILE.unlink(missing_ok=True)
    return entries


def _append_to_buffer(records: List[Dict[str, Any]]) -> None:
    DEDUP_BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with DEDUP_BUFFER_FILE.open("a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, default=str) + "\n")
            f.flush()


def add_to_dedup_buffer(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not records:
        return []
    stamped: List[Dict[str, Any]] = []
    for record in records:
        record["_buffer_ts"] = now_iso()
        stamped.append(record)
    _append_to_buffer(stamped)
    LOGGER.info("dedup_buffer: added %d records", len(stamped))
    return stamped


def flush_dedup_buffer() -> List[Dict[str, Any]]:
    entries = _drain_buffer()
    return entries


def confirm_dedup_buffer_flush(entries: List[Dict[str, Any]]) -> None:
    _release_processing_file()
    LOGGER.info("dedup_buffer: confirmed flush of %d records", len(entries))


def recover_dedup_buffer() -> List[Dict[str, Any]]:
    return _recover_processing_file()


def peek_dedup_buffer(limit: int = 200, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    entries = _load_buffer()
    if session_id:
        entries = [e for e in entries if e.get("session_id") == session_id]
    return entries[-limit:]


def dedup_buffer_size() -> int:
    return len(_load_buffer())


def dedup_buffer_oldest_ts() -> Optional[str]:
    entries = _load_buffer()
    if not entries:
        return None
    return min(
        (e.get("_buffer_ts", "") for e in entries if e.get("_buffer_ts")),
        default=None,
    )


def dedup_buffer_pending_sessions() -> List[str]:
    entries = _load_buffer()
    seen = set()
    for e in entries:
        sid = e.get("session_id")
        if sid:
            seen.add(sid)
    return sorted(seen)
