"""Read-only compatibility access to the retired save-time dedup buffer."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.sessions import MEMORIES_DIR

LOGGER = logging.getLogger(__name__)

DEDUP_BUFFER_FILE = MEMORIES_DIR / "dedup_buffer.jsonl"
DEDUP_PROCESSING_FILE = MEMORIES_DIR / "dedup_buffer.processing.jsonl"


def _read_entries(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        LOGGER.warning("dedup_buffer: cannot read %s: %s", path, exc)
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            LOGGER.warning("dedup_buffer: skipping corrupted line in %s", path)
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def peek_dedup_buffer(limit: int = 200, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Expose legacy pending records without starting workers or changing files."""
    if limit <= 0:
        return []
    entries = _read_entries(DEDUP_PROCESSING_FILE) + _read_entries(DEDUP_BUFFER_FILE)
    if session_id:
        entries = [entry for entry in entries if entry.get("session_id") == session_id]
    return entries[-limit:]
