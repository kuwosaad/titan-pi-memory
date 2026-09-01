from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from .models import Scene, validate_scene_evidence_payload
from .sessions import BASE_DIR, MEMORIES_DIR, read_json, write_json
from . import sessions as _sessions
from .sqlite import connect_sqlite
from .sqlite_schema import ensure_memory_store_metadata, ensure_scene_readable_views


LOGGER = logging.getLogger(__name__)
SCENES_FILE = MEMORIES_DIR / "scenes.json"
DEFAULT_SQLITE_FILE = MEMORIES_DIR / "memory_store.db"
_SCENE_ROOT = MEMORIES_DIR
_SCENES_LOCK = threading.RLock()
_REPO_CACHE: Optional["SceneRepository"] = None
_REPO_CACHE_KEY: Optional[tuple[str, str]] = None


def _refresh_json_paths() -> None:
    """Keep JSON scene compatibility storage aligned with runtime context."""

    global SCENES_FILE, DEFAULT_SQLITE_FILE, _SCENE_ROOT
    _sessions.refresh_runtime_paths()
    current_root = _sessions.MEMORIES_DIR
    if current_root == _SCENE_ROOT:
        return
    if SCENES_FILE == _SCENE_ROOT / "scenes.json":
        SCENES_FILE = current_root / "scenes.json"
    if DEFAULT_SQLITE_FILE == _SCENE_ROOT / "memory_store.db":
        DEFAULT_SQLITE_FILE = current_root / "memory_store.db"
    _SCENE_ROOT = current_root


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_sqlite_path() -> Path:
    from app.runtime.context import get_runtime_context
    return get_runtime_context().memory_db_path


def _resolve_backend() -> str:
    from app.runtime.context import get_runtime_context
    return get_runtime_context().memory_backend


def _resolve_read_fallback() -> str:
    from app.runtime.context import get_runtime_context
    return get_runtime_context().read_fallback


def _normalize_scene(scene: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(scene)
    normalized.setdefault("kind", "message_exchange")
    normalized.setdefault("scene_seq", None)
    normalized.setdefault("start_event_seq", None)
    normalized.setdefault("end_event_seq", None)
    normalized.setdefault("anchor_event_id", None)
    normalized.setdefault("source_event_ids", [])
    normalized.setdefault("raw_events", [])
    normalized.setdefault("evidence_version", 0)
    normalized.setdefault("evidence_status", "partial")
    normalized.setdefault("missing_source_event_ids", [])
    normalized.setdefault("messages", [])
    normalized.setdefault("tool_calls", [])
    normalized.setdefault("extraction_user_text", "")
    normalized.setdefault("extraction_assistant_text", "")
    normalized.setdefault("used_context_fallback", False)
    normalized.setdefault("ts", now_iso())

    claims_versioned_evidence = int(normalized.get("evidence_version") or 0) >= 1
    if claims_versioned_evidence:
        for field in ("messages", "tool_calls", "raw_events"):
            values = normalized.get(field)
            if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                raise ValueError(f"versioned {field} must be a list of objects")
        for message in normalized.get("messages") or []:
            if str(message.get("role") or "") not in {"user", "assistant", "system"}:
                raise ValueError("versioned messages require a valid role")
            if not str(message.get("content") or "").strip():
                raise ValueError("versioned messages cannot be empty")
        for tool_call in normalized.get("tool_calls") or []:
            if not str(tool_call.get("name") or "").strip():
                raise ValueError("versioned tool calls require a name")

    messages: List[Dict[str, Any]] = []
    for item in normalized.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "system")
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        messages.append(
            {
                "role": role if role in {"user", "assistant", "system"} else "system",
                "content": content,
                "message_id": item.get("message_id"),
                "event_id": item.get("event_id"),
            }
        )
    normalized["messages"] = messages
    tool_calls: List[Dict[str, Any]] = []
    for item in normalized.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "unknown").strip() or "unknown"
        file_paths = [str(path) for path in item.get("file_paths") or [] if str(path).strip()]
        tool_calls.append(
            {
                "name": name,
                "call_id": item.get("call_id"),
                "status": str(item.get("status") or "unknown"),
                "summary": str(item.get("summary") or ""),
                "file_paths": file_paths,
                "excerpt": item.get("excerpt"),
                "event_id": item.get("event_id"),
            }
        )
    normalized["tool_calls"] = tool_calls
    normalized["source_event_ids"] = [str(item) for item in normalized.get("source_event_ids") or [] if str(item).strip()]
    try:
        normalized["evidence_version"] = int(normalized.get("evidence_version") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence_version must be an integer") from exc
    normalized["evidence_status"] = str(normalized.get("evidence_status") or "partial")
    if normalized["evidence_status"] not in {"complete", "partial"}:
        raise ValueError("evidence_status must be 'complete' or 'partial'")
    if normalized["evidence_version"] == 0:
        normalized["evidence_status"] = "partial"
    missing_source_event_ids: List[str] = []
    for item in normalized.get("missing_source_event_ids") or []:
        value = str(item).strip()
        if value and value not in missing_source_event_ids:
            missing_source_event_ids.append(value)
    normalized["missing_source_event_ids"] = missing_source_event_ids
    raw_events: List[Dict[str, Any]] = []
    for item in normalized.get("raw_events") or []:
        if isinstance(item, dict):
            raw_events.append(item)
    normalized["raw_events"] = raw_events
    normalized["scene_id"] = str(normalized.get("scene_id") or "")
    normalized["session_id"] = str(normalized.get("session_id") or "")
    normalized["turn"] = int(normalized.get("turn") or 0)
    for key in ("scene_seq", "start_event_seq", "end_event_seq"):
        value = normalized.get(key)
        normalized[key] = int(value) if value not in (None, "") else None
    normalized["used_context_fallback"] = bool(normalized.get("used_context_fallback", False))
    validate_scene_evidence_payload(normalized)
    return normalized


def _is_complete_scene(scene: Dict[str, Any]) -> bool:
    return str(scene.get("evidence_status") or "partial") == "complete" and int(scene.get("evidence_version") or 0) == 1


class SceneRepository(Protocol):
    def append_scenes(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ...

    def get_scene(self, scene_id: str) -> Optional[Dict[str, Any]]:
        ...

    def get_scenes(self, scene_ids: List[str]) -> List[Dict[str, Any]]:
        ...

    def get_scene_references(self, scene_ids: List[str]) -> List[Dict[str, Any]]:
        ...

    def get_recent_scenes(self, limit: int = 8, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        ...

    def get_session_scenes(self, session_id: str) -> List[Dict[str, Any]]:
        ...


class JsonSceneRepository:
    def __init__(self, scenes_file: Optional[Path] = None) -> None:
        # Optional explicit paths make namespace reads testable without
        # changing process-wide runtime context.
        self.scenes_file = scenes_file

    @property
    def _path(self) -> Path:
        return self.scenes_file or SCENES_FILE

    def load_all_scenes(self) -> List[Dict[str, Any]]:
        with _SCENES_LOCK:
            return [_normalize_scene(scene) for scene in read_json(self._path, [])]

    def append_scenes(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not scenes:
            return []
        normalized_scenes = [_normalize_scene(scene) for scene in scenes]
        with _SCENES_LOCK:
            existing = {
                str(item.get("scene_id") or ""): _normalize_scene(item)
                for item in read_json(self._path, [])
                if isinstance(item, dict)
            }
            for normalized in normalized_scenes:
                current = existing.get(normalized["scene_id"])
                if current is not None and _is_complete_scene(current) and not _is_complete_scene(normalized):
                    continue
                existing[normalized["scene_id"]] = normalized
            ordered = sorted(existing.values(), key=lambda item: str(item.get("ts") or ""))
            write_json(self._path, ordered)
        return scenes

    def get_scene(self, scene_id: str) -> Optional[Dict[str, Any]]:
        scene_id = str(scene_id or "").strip()
        if not scene_id:
            return None
        for scene in self.load_all_scenes():
            if scene.get("scene_id") == scene_id:
                return scene
        return None

    def get_scenes(self, scene_ids: List[str]) -> List[Dict[str, Any]]:
        wanted = {str(scene_id).strip() for scene_id in scene_ids if str(scene_id).strip()}
        if not wanted:
            return []
        by_id = {scene.get("scene_id"): scene for scene in self.load_all_scenes()}
        return [by_id[scene_id] for scene_id in scene_ids if scene_id in by_id]

    def get_scene_references(self, scene_ids: List[str]) -> List[Dict[str, Any]]:
        wanted = [str(scene_id).strip() for scene_id in scene_ids if str(scene_id).strip()]
        if not wanted:
            return []
        by_id = {scene.get("scene_id"): scene for scene in self.load_all_scenes()}
        return [_scene_reference_payload(by_id[scene_id]) for scene_id in wanted if scene_id in by_id]

    def get_recent_scenes(self, limit: int = 8, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        scenes = self.load_all_scenes()
        if session_id:
            scenes = [scene for scene in scenes if scene.get("session_id") == session_id]
        scenes = sorted(scenes, key=lambda item: str(item.get("ts") or ""), reverse=True)
        return scenes[:limit]

    def get_session_scenes(self, session_id: str) -> List[Dict[str, Any]]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        scenes = [scene for scene in self.load_all_scenes() if scene.get("session_id") == session_id]
        return sorted(scenes, key=lambda item: (item.get("scene_seq") is None, item.get("scene_seq") or 0, str(item.get("ts") or "")))


class SqliteSceneRepository:
    def __init__(self, db_path: Path, *, initialize: bool = True) -> None:
        self.db_path = db_path
        # ``initialize=False`` is the federation read path; construction must
        # not create directories for a namespace that is not present.
        if initialize:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._read_only = not initialize
        self._lock = threading.RLock()
        if initialize:
            self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.db_path, read_only=self._read_only)

    def _init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS scenes (
            scene_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn INTEGER NOT NULL,
            kind TEXT NOT NULL,
            scene_seq INTEGER,
            start_event_seq INTEGER,
            end_event_seq INTEGER,
            anchor_event_id TEXT,
            ts TEXT NOT NULL,
            source_event_ids_json TEXT NOT NULL,
            raw_events_json TEXT NOT NULL DEFAULT '[]',
            evidence_version INTEGER NOT NULL DEFAULT 0,
            evidence_status TEXT NOT NULL DEFAULT 'partial',
            missing_source_event_ids_json TEXT NOT NULL DEFAULT '[]',
            messages_json TEXT NOT NULL,
            tool_calls_json TEXT NOT NULL DEFAULT '[]',
            extraction_user_text TEXT NOT NULL,
            extraction_assistant_text TEXT NOT NULL,
            used_context_fallback INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scenes_session_ts ON scenes(session_id, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_scenes_turn ON scenes(session_id, turn DESC);
        """
        with self._lock, self._connect() as conn:
            conn.executescript(ddl)
            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scenes)").fetchall()}
            if "scene_seq" not in existing_columns:
                conn.execute("ALTER TABLE scenes ADD COLUMN scene_seq INTEGER")
            if "start_event_seq" not in existing_columns:
                conn.execute("ALTER TABLE scenes ADD COLUMN start_event_seq INTEGER")
            if "end_event_seq" not in existing_columns:
                conn.execute("ALTER TABLE scenes ADD COLUMN end_event_seq INTEGER")
            if "raw_events_json" not in existing_columns:
                conn.execute("ALTER TABLE scenes ADD COLUMN raw_events_json TEXT NOT NULL DEFAULT '[]'")
            if "tool_calls_json" not in existing_columns:
                conn.execute("ALTER TABLE scenes ADD COLUMN tool_calls_json TEXT NOT NULL DEFAULT '[]'")
            if "evidence_version" not in existing_columns:
                conn.execute("ALTER TABLE scenes ADD COLUMN evidence_version INTEGER NOT NULL DEFAULT 0")
            if "evidence_status" not in existing_columns:
                conn.execute("ALTER TABLE scenes ADD COLUMN evidence_status TEXT NOT NULL DEFAULT 'partial'")
            if "missing_source_event_ids_json" not in existing_columns:
                conn.execute("ALTER TABLE scenes ADD COLUMN missing_source_event_ids_json TEXT NOT NULL DEFAULT '[]'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scenes_session_seq ON scenes(session_id, scene_seq)")
            ensure_memory_store_metadata(conn)
            ensure_scene_readable_views(conn)
            conn.commit()

    def _scene_to_row(self, scene: Dict[str, Any]) -> Dict[str, Any]:
        normalized = _normalize_scene(scene)
        return {
            "scene_id": normalized["scene_id"],
            "session_id": normalized["session_id"],
            "turn": normalized["turn"],
            "kind": str(normalized.get("kind") or "message_exchange"),
            "scene_seq": normalized.get("scene_seq"),
            "start_event_seq": normalized.get("start_event_seq"),
            "end_event_seq": normalized.get("end_event_seq"),
            "anchor_event_id": normalized.get("anchor_event_id"),
            "ts": str(normalized.get("ts") or now_iso()),
            "source_event_ids_json": json.dumps(normalized.get("source_event_ids") or []),
            "raw_events_json": json.dumps(normalized.get("raw_events") or [], default=str),
            "evidence_version": normalized["evidence_version"],
            "evidence_status": normalized["evidence_status"],
            "missing_source_event_ids_json": json.dumps(normalized.get("missing_source_event_ids") or []),
            "messages_json": json.dumps(normalized.get("messages") or []),
            "tool_calls_json": json.dumps(normalized.get("tool_calls") or [], default=str),
            "extraction_user_text": str(normalized.get("extraction_user_text") or ""),
            "extraction_assistant_text": str(normalized.get("extraction_assistant_text") or ""),
            "used_context_fallback": 1 if normalized.get("used_context_fallback") else 0,
        }

    def _row_to_scene(self, row: sqlite3.Row) -> Dict[str, Any]:
        return _normalize_scene(
            {
                "scene_id": row["scene_id"],
                "session_id": row["session_id"],
                "turn": row["turn"],
                "kind": row["kind"],
                "scene_seq": row["scene_seq"] if "scene_seq" in row.keys() else None,
                "start_event_seq": row["start_event_seq"] if "start_event_seq" in row.keys() else None,
                "end_event_seq": row["end_event_seq"] if "end_event_seq" in row.keys() else None,
                "anchor_event_id": row["anchor_event_id"],
                "ts": row["ts"],
                "source_event_ids": json.loads(row["source_event_ids_json"] or "[]"),
                "raw_events": json.loads(row["raw_events_json"] or "[]") if "raw_events_json" in row.keys() else [],
                "evidence_version": row["evidence_version"] if "evidence_version" in row.keys() else 0,
                "evidence_status": row["evidence_status"] if "evidence_status" in row.keys() else "partial",
                "missing_source_event_ids": json.loads(row["missing_source_event_ids_json"] or "[]")
                if "missing_source_event_ids_json" in row.keys()
                else [],
                "messages": json.loads(row["messages_json"] or "[]"),
                "tool_calls": json.loads(row["tool_calls_json"] or "[]") if "tool_calls_json" in row.keys() else [],
                "extraction_user_text": row["extraction_user_text"],
                "extraction_assistant_text": row["extraction_assistant_text"],
                "used_context_fallback": bool(row["used_context_fallback"]),
            }
        )

    def append_scenes(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not scenes:
            return []
        sql = """
        INSERT INTO scenes (
            scene_id, session_id, turn, kind, scene_seq, start_event_seq, end_event_seq,
            anchor_event_id, ts, source_event_ids_json, raw_events_json, evidence_version, evidence_status,
            missing_source_event_ids_json, messages_json, tool_calls_json, extraction_user_text,
            extraction_assistant_text, used_context_fallback
        )
        VALUES (
            :scene_id, :session_id, :turn, :kind, :scene_seq, :start_event_seq, :end_event_seq,
            :anchor_event_id, :ts, :source_event_ids_json, :raw_events_json, :evidence_version, :evidence_status,
            :missing_source_event_ids_json, :messages_json, :tool_calls_json, :extraction_user_text,
            :extraction_assistant_text, :used_context_fallback
        )
        ON CONFLICT(scene_id) DO UPDATE SET
            session_id=excluded.session_id,
            turn=excluded.turn,
            kind=excluded.kind,
            scene_seq=excluded.scene_seq,
            start_event_seq=excluded.start_event_seq,
            end_event_seq=excluded.end_event_seq,
            anchor_event_id=excluded.anchor_event_id,
            ts=excluded.ts,
            source_event_ids_json=excluded.source_event_ids_json,
            raw_events_json=excluded.raw_events_json,
            evidence_version=excluded.evidence_version,
            evidence_status=excluded.evidence_status,
            missing_source_event_ids_json=excluded.missing_source_event_ids_json,
            messages_json=excluded.messages_json,
            tool_calls_json=excluded.tool_calls_json,
            extraction_user_text=excluded.extraction_user_text,
            extraction_assistant_text=excluded.extraction_assistant_text,
            used_context_fallback=excluded.used_context_fallback
        WHERE scenes.evidence_status <> 'complete' OR excluded.evidence_status = 'complete'
        """
        rows = [self._scene_to_row(scene) for scene in scenes]
        with self._lock, self._connect() as conn:
            conn.executemany(sql, rows)
            conn.commit()
        return scenes

    def get_scene(self, scene_id: str) -> Optional[Dict[str, Any]]:
        rows = self.get_scenes([scene_id])
        return rows[0] if rows else None

    def get_scenes(self, scene_ids: List[str]) -> List[Dict[str, Any]]:
        normalized_ids = [str(scene_id).strip() for scene_id in scene_ids if str(scene_id).strip()]
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        query = f"SELECT * FROM scenes WHERE scene_id IN ({placeholders})"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, normalized_ids).fetchall()
        by_id = {row["scene_id"]: self._row_to_scene(row) for row in rows}
        return [by_id[scene_id] for scene_id in normalized_ids if scene_id in by_id]

    def get_scene_references(self, scene_ids: List[str]) -> List[Dict[str, Any]]:
        normalized_ids = [str(scene_id).strip() for scene_id in scene_ids if str(scene_id).strip()]
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        query = f"""
        SELECT scene_id, evidence_version, evidence_status, missing_source_event_ids_json
        FROM scenes WHERE scene_id IN ({placeholders})
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, normalized_ids).fetchall()
        by_id = {
            row["scene_id"]: {
                "scene_id": row["scene_id"],
                "evidence_version": int(row["evidence_version"] or 0),
                "evidence_status": str(row["evidence_status"] or "partial"),
                "missing_source_event_ids": json.loads(row["missing_source_event_ids_json"] or "[]"),
            }
            for row in rows
        }
        return [by_id[scene_id] for scene_id in normalized_ids if scene_id in by_id]

    def get_recent_scenes(self, limit: int = 8, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        where = ""
        params: List[Any] = []
        if session_id:
            where = "WHERE session_id = ?"
            params.append(session_id)
        query = f"SELECT * FROM scenes {where} ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_scene(row) for row in rows]

    def get_session_scenes(self, session_id: str) -> List[Dict[str, Any]]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        query = """
        SELECT * FROM scenes
        WHERE session_id = ?
        ORDER BY scene_seq IS NULL, scene_seq ASC, ts ASC
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, (session_id,)).fetchall()
        return [self._row_to_scene(row) for row in rows]


def get_scene_repository() -> SceneRepository:
    global _REPO_CACHE, _REPO_CACHE_KEY

    _refresh_json_paths()
    backend = _resolve_backend()
    sqlite_path = _resolve_sqlite_path()
    cache_key = (backend, str(sqlite_path))
    if _REPO_CACHE is not None and _REPO_CACHE_KEY == cache_key:
        return _REPO_CACHE

    if backend == "json":
        _REPO_CACHE = JsonSceneRepository()
        _REPO_CACHE_KEY = cache_key
        return _REPO_CACHE

    try:
        _REPO_CACHE = SqliteSceneRepository(sqlite_path)
    except Exception as exc:  # pragma: no cover
        fallback = _resolve_read_fallback()
        if fallback == "json":
            LOGGER.warning("SQLite scene store unavailable (%s). Falling back to JSON backend.", exc)
            _REPO_CACHE = JsonSceneRepository()
        else:
            raise
    _REPO_CACHE_KEY = cache_key
    return _REPO_CACHE


def get_scene_write_repository() -> SceneRepository:
    """Return the configured scene store without applying read fallbacks."""

    global _REPO_CACHE, _REPO_CACHE_KEY
    _refresh_json_paths()
    backend = _resolve_backend()
    sqlite_path = _resolve_sqlite_path()
    cache_key = (backend, str(sqlite_path))
    if backend == "json":
        if not isinstance(_REPO_CACHE, JsonSceneRepository) or _REPO_CACHE_KEY != cache_key:
            _REPO_CACHE = JsonSceneRepository()
            _REPO_CACHE_KEY = cache_key
        return _REPO_CACHE
    if isinstance(_REPO_CACHE, SqliteSceneRepository) and _REPO_CACHE_KEY == cache_key:
        return _REPO_CACHE
    _REPO_CACHE = SqliteSceneRepository(sqlite_path)
    _REPO_CACHE_KEY = cache_key
    return _REPO_CACHE


def append_scene(scene: Dict[str, Any] | Scene) -> Dict[str, Any]:
    payload = scene.model_dump() if isinstance(scene, Scene) else dict(scene)
    repository = get_scene_write_repository()
    repository.append_scenes([payload])
    return repository.get_scene(str(payload.get("scene_id") or "")) or payload


def _scene_reference_payload(scene: Dict[str, Any]) -> Dict[str, Any]:
    status = str(scene.get("evidence_status") or "partial")
    if status not in {"complete", "partial"}:
        status = "partial"
    try:
        version = int(scene.get("evidence_version") or 0)
    except (TypeError, ValueError):
        version = 0
    return {
        "scene_id": str(scene.get("scene_id") or ""),
        "evidence_status": status,
        "evidence_version": version,
        "missing_source_event_ids": [
            str(event_id)
            for event_id in scene.get("missing_source_event_ids") or []
            if str(event_id).strip()
        ],
    }


def append_scenes(scenes: List[Dict[str, Any] | Scene]) -> List[Dict[str, Any]]:
    payload = [scene.model_dump() if isinstance(scene, Scene) else dict(scene) for scene in scenes]
    return get_scene_write_repository().append_scenes(payload)


def get_scene(scene_id: str) -> Optional[Scene]:
    scene = get_scene_repository().get_scene(scene_id)
    return Scene(**scene) if scene else None


def get_scenes(scene_ids: List[str]) -> List[Scene]:
    return [Scene(**scene) for scene in get_scene_repository().get_scenes(scene_ids)]


def get_scene_references(scene_ids: List[str]) -> List[Dict[str, Any]]:
    return get_scene_repository().get_scene_references(scene_ids)


def get_recent_scenes(limit: int = 8, session_id: Optional[str] = None) -> List[Scene]:
    return [Scene(**scene) for scene in get_scene_repository().get_recent_scenes(limit=limit, session_id=session_id)]


def get_session_scenes(session_id: str) -> List[Scene]:
    return [Scene(**scene) for scene in get_scene_repository().get_session_scenes(session_id=session_id)]
