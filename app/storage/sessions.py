import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .models import Session, Message


_BASE_DIR_OVERRIDE = os.getenv("TITAN_BASE_DIR")
BASE_DIR = Path(_BASE_DIR_OVERRIDE).expanduser().resolve() if _BASE_DIR_OVERRIDE else Path(__file__).resolve().parents[2]
OUT_DIR = BASE_DIR / "out"
SESSIONS_DIR = OUT_DIR / "sessions"
MEMORIES_DIR = OUT_DIR / "memories"
TRACES_DIR = OUT_DIR / "traces"
GRAPHS_DIR = OUT_DIR / "graphs"
_FILE_LOCK = threading.RLock()


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
    return SESSIONS_DIR / f"{session_id}.json"


def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)


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
