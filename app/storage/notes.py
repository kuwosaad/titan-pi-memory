from collections import defaultdict
from pathlib import Path
import threading
from typing import Any, Dict, Iterable

from app.save_pipeline.extraction.extractor import classify_memory, is_hidden_metadata_memory

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

            with target.open("a", encoding="utf-8") as handle:
                ordered_records = sorted(
                    target_records,
                    key=lambda record: (
                        _MEMORY_KIND_PRIORITY.get(
                            str(record.get("memory_kind") or classify_memory(str(record.get("text") or ""), record.get("type"))[1]),
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
                    if any(marker in lowered for marker in LOW_SIGNAL_NOTE_MARKERS) or is_hidden_metadata_memory(record):
                        continue
                    handle.write(f"- [{ts}] (turn {turn}) {text}\n")


def _notes_path(session_id: str, stream: str) -> Path:
    normalized = stream if stream in {"rough", "learnings"} else "rough"
    if normalized == "learnings":
        return LEARNINGS_NOTES_DIR / f"{session_id}.md"
    return ROUGH_NOTES_DIR / f"{session_id}.md"
