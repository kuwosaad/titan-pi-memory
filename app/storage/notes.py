from collections import defaultdict
import hashlib
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Dict, Iterable

from .sessions import OUT_DIR


NOTES_DIR = OUT_DIR / "memory_notes"
ROUGH_NOTES_DIR = NOTES_DIR / "rough"
LEARNINGS_NOTES_DIR = NOTES_DIR / "learnings"
LOW_SIGNAL_NOTE_MARKERS = (
    "captured and stored for memory processing",
    "event was captured and stored",
    "message.updated event",
    "message part update event",
    "message.part.updated",
    "session.updated event",
    "session.created event",
    "session status event",
)
_NOTES_LOCK = threading.RLock()
_MEMORY_MARKER_RE = re.compile(r"<!--\s*titan-memory-id:\s*([^\s]+)\s*-->")

_MEMORY_KIND_PRIORITY = {
    "relationship": 0,
    "user_preference": 1,
    "decision": 2,
    "commitment": 3,
    "task": 4,
    "outcome": 5,
    "user_fact": 6,
    "workflow": 7,
    "issue": 8,
}


def _classify_memory(text: str, memory_type: Any) -> tuple[str, str]:
    """Load extraction policy lazily to keep storage imports acyclic."""

    from app.save_pipeline.extraction.policy import classify_memory

    return classify_memory(text, memory_type)


def _is_hidden_metadata_memory(record: Dict[str, Any]) -> bool:
    from app.save_pipeline.extraction.policy import is_hidden_metadata_memory

    return is_hidden_metadata_memory(record)


def ensure_note_dirs() -> None:
    ROUGH_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    LEARNINGS_NOTES_DIR.mkdir(parents=True, exist_ok=True)


def append_memory_notes(records: Iterable[Dict[str, Any]]) -> None:
    by_target: Dict[Path, list[Dict[str, Any]]] = defaultdict(list)

    for record in records:
        session_id = str(record.get("session_id") or "default")
        stream = str(record.get("stream") or "rough")
        target = _notes_path(session_id, stream)
        by_target[target].append(record)

    if not by_target:
        return

    with _NOTES_LOCK:
        ensure_note_dirs()

        for target, target_records in by_target.items():
            target.touch(exist_ok=True)
            if target.stat().st_size == 0:
                header = f"# {target.stem} {target.parent.name} notes\n\n"
                target.write_text(header, encoding="utf-8")

            existing = target.read_text(encoding="utf-8")
            known_ids = set(_MEMORY_MARKER_RE.findall(existing))
            additions: list[str] = []
            ordered_records = sorted(
                target_records,
                key=lambda record: (
                    _MEMORY_KIND_PRIORITY.get(
                        str(record.get("memory_kind") or _classify_memory(str(record.get("text") or ""), record.get("type"))[1]),
                        99,
                    ),
                    str(record.get("ts") or ""),
                    int(record.get("turn") or 0),
                ),
            )
            for record in ordered_records:
                ts = str(record.get("ts") or "")
                turn = record.get("turn")
                text = str(record.get("text") or "").strip()
                if not text:
                    continue
                lowered = text.lower()
                if any(marker in lowered for marker in LOW_SIGNAL_NOTE_MARKERS) or _is_hidden_metadata_memory(record):
                    continue
                memory_id = str(record.get("id") or "").strip()
                if not memory_id:
                    # Legacy records may not have an ID. A stable content key
                    # still makes retries idempotent without changing their
                    # human-readable line.
                    digest = hashlib.sha256(
                        "\x1f".join((str(record.get(key) or "") for key in ("session_id", "stream", "ts", "turn", "text"))).encode("utf-8")
                    ).hexdigest()[:20]
                    memory_id = f"legacy-{digest}"
                rendered = f"- [{ts}] (turn {turn}) {text}"
                if memory_id in known_ids or rendered in existing:
                    known_ids.add(memory_id)
                    continue
                additions.append(f"{rendered} <!-- titan-memory-id: {memory_id} -->\n")
                known_ids.add(memory_id)

            if additions:
                # Replace atomically so a crash cannot leave a truncated note
                # projection. The marker is an HTML comment and therefore does
                # not disturb the readable Markdown content.
                payload = existing
                if payload and not payload.endswith("\n"):
                    payload += "\n"
                payload += "".join(additions)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
                    handle.write(payload)
                    temp_name = handle.name
                os.replace(temp_name, target)


def _notes_path(session_id: str, stream: str) -> Path:
    normalized = stream if stream in {"rough", "learnings"} else "rough"
    if normalized == "learnings":
        return LEARNINGS_NOTES_DIR / f"{session_id}.md"
    return ROUGH_NOTES_DIR / f"{session_id}.md"
