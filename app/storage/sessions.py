import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .models import Session, Message
from app.runtime.context import get_runtime_context


_RUNTIME_CONTEXT = get_runtime_context()
BASE_DIR = _RUNTIME_CONTEXT.base_dir
OUT_DIR = BASE_DIR / "out"
SESSIONS_DIR = OUT_DIR / "sessions"
MEMORIES_DIR = OUT_DIR / "memories"
TRACES_DIR = _RUNTIME_CONTEXT.trace_dir
LEGACY_TRACES_DIR = OUT_DIR / "traces"
GRAPHS_DIR = OUT_DIR / "graphs"
_FILE_LOCK = threading.RLock()
_CONTEXT_BASE_DIR = BASE_DIR
_CONTEXT_TRACE_DIR = TRACES_DIR


def refresh_runtime_paths() -> None:
    """Refresh storage paths when a host selects a new runtime context."""

    global _RUNTIME_CONTEXT, _CONTEXT_BASE_DIR, _CONTEXT_TRACE_DIR
    global BASE_DIR, OUT_DIR, SESSIONS_DIR, MEMORIES_DIR, TRACES_DIR, LEGACY_TRACES_DIR, GRAPHS_DIR
    context = get_runtime_context()
    if context.base_dir == _CONTEXT_BASE_DIR and context.trace_dir == _CONTEXT_TRACE_DIR:
        return
    _RUNTIME_CONTEXT = context
    _CONTEXT_BASE_DIR = context.base_dir
    _CONTEXT_TRACE_DIR = context.trace_dir
    BASE_DIR = context.base_dir
    OUT_DIR = BASE_DIR / "out"
    SESSIONS_DIR = OUT_DIR / "sessions"
    MEMORIES_DIR = OUT_DIR / "memories"
    TRACES_DIR = context.trace_dir
    LEGACY_TRACES_DIR = OUT_DIR / "traces"
    GRAPHS_DIR = OUT_DIR / "graphs"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    with _FILE_LOCK:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # If a write was interrupted, callers get a safe default instead of crashing.
            return default


def write_json(path: Path, data: Any) -> None:
    serialized = json.dumps(data, indent=2, default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")

    with _FILE_LOCK:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)


def session_path(session_id: str) -> Path:
    refresh_runtime_paths()
    return SESSIONS_DIR / f"{session_id}.json"


def ensure_dirs() -> None:
    refresh_runtime_paths()
    # ``traces`` imports these paths by value for backwards-compatible patch
    # points; refresh them after the session context has been refreshed.
    try:
        from . import traces

        traces.refresh_trace_paths()
    except ImportError:
        # Avoid an import cycle during the initial module load.
        pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the historical repository layout available for older adapters and
    # readable views even when an explicit spool directory is configured.
    if LEGACY_TRACES_DIR != TRACES_DIR:
        LEGACY_TRACES_DIR.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_trace_files()
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_trace_files() -> None:
    """Copy old ``out/traces`` state into the active spool when absent."""

    names = (
        "trace_packets.json",
        "events.jsonl",
        "event_index.json",
        "checkpoints.json",
        "retry_queue.jsonl",
        "spool_cursors.json",
        "pending_user_messages.json",
    )
    for name in names:
        source = LEGACY_TRACES_DIR / name
        target = TRACES_DIR / name
        if source == target or not source.exists() or target.exists():
            continue
        temporary = target.with_suffix(f"{target.suffix}.migration.tmp")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        except OSError:
            # A read-only or concurrently removed legacy file can be retried
            # on the next invocation without taking down the active store.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            continue


def create_session() -> Session:
    ensure_dirs()
    session_id = uuid.uuid4().hex
    session = Session(
        id=session_id,
        created_at=now_iso(),
        messages=[]
    )
    save_session(session)
    return session


def load_session(session_id: str) -> Session:
    path = session_path(session_id)
    if path.exists():
        data = read_json(path, {})
        messages = [Message(**msg) for msg in data.get("messages", [])]
        return Session(
            id=session_id,
            created_at=data.get("created_at", now_iso()),
            messages=messages
        )
    return create_session()


def save_session(session: Session) -> None:
    ensure_dirs()
    data = {
        "id": session.id,
        "created_at": session.created_at,
        "messages": [msg.model_dump() for msg in session.messages]
    }
    write_json(session_path(session.id), data)


def get_next_turn(session: Session) -> int:
    return sum(1 for msg in session.messages if msg.role == "user") + 1


def add_message(session: Session, role: str, content: str, turn: int, ts: Optional[str] = None) -> None:
    timestamp = ts if ts is not None else now_iso()
    message = Message(role=role, content=content, ts=timestamp, turn=turn)
    session.messages.append(message)
    save_session(session)
