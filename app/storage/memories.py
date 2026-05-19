from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .models import Memory
from .repository import CandidateFilters, MemoryRepository
from .sessions import BASE_DIR, MEMORIES_DIR, read_json, write_json
from .sqlite_schema import ensure_memory_readable_views, ensure_memory_store_metadata


LOGGER = logging.getLogger(__name__)
MEMORIES_FILE = MEMORIES_DIR / "memories.json"
DEFAULT_SQLITE_FILE = MEMORIES_DIR / "memory_store.db"
SQLITE_TIMEOUT_SECONDS = 30.0
_MEMORIES_LOCK = threading.RLock()
_REPO_CACHE: Optional[MemoryRepository] = None
_REPO_CACHE_KEY: Optional[Tuple[str, str]] = None
_ALLOWED_MEMORY_KINDS = {
    "user_fact",
    "user_preference",
    "task",
    "decision",
    "commitment",
    "outcome",
    "relationship",
    "workflow",
    "issue",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pack_embedding(vec: Any) -> Tuple[Optional[bytes], Optional[int], Optional[str]]:
    if vec is None:
        return None, None, None
    array = np.asarray(vec, dtype=np.float32).reshape(-1)
    if array.size == 0:
        return None, None, None
    return np.ascontiguousarray(array).tobytes(), int(array.size), "f32"


def unpack_embedding(blob: Optional[bytes], dim: Optional[int], dtype: Optional[str]) -> Optional[np.ndarray]:
    if not blob or not dim:
        return None
    if dtype not in (None, "f32"):
        raise ValueError(f"Unsupported embedding dtype: {dtype}")
    array = np.frombuffer(blob, dtype=np.float32, count=int(dim))
    if array.size != int(dim):
        raise ValueError("Embedding size mismatch while decoding blob")
    return array


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalize_memory_kind(mem: Dict[str, Any]) -> Optional[str]:
    raw_kind = mem.get("memory_kind")
    if raw_kind is None:
        return None

    kind = str(raw_kind).strip().lower()
    if not kind:
        return None
    if kind in _ALLOWED_MEMORY_KINDS:
        return kind

    from app.save_pipeline.extraction.extractor import classify_memory

    _speaker_focus, normalized_kind = classify_memory(str(mem.get("text") or ""), mem.get("type"))
    LOGGER.info("Normalizing legacy memory_kind '%s' -> '%s' for memory id=%s", kind, normalized_kind, mem.get("id"))
    return normalized_kind if normalized_kind in _ALLOWED_MEMORY_KINDS else None


def _normalize_memory(mem: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(mem)
    normalized.setdefault("stream", "rough")
    normalized.setdefault("scene_id", None)
    normalized.setdefault("source_event_ids", [])
    if not normalized.get("source_type"):
        normalized["source_type"] = "legacy"
    if normalized.get("source_reliability") is None:
        normalized["source_reliability"] = 0.3
    normalized.setdefault("verification_status", "unverified")
    normalized.setdefault("fallback_generated", False)
    normalized.setdefault("speaker_focus", None)
    normalized["memory_kind"] = _normalize_memory_kind(normalized)
    provenance = normalized.get("provenance")
    if not isinstance(provenance, dict):
        normalized["provenance"] = {"user": "", "assistant": ""}
    else:
        normalized["provenance"] = {
            "user": str(provenance.get("user") or ""),
            "assistant": str(provenance.get("assistant") or ""),
        }
    return normalized


def _normalize_stream(value: Any) -> str:
    stream = str(value or "rough").strip().lower()
    return stream if stream in {"rough", "learnings"} else "rough"


def _resolve_sqlite_path() -> Path:
    from app.retrieval_pipeline.config import load_settings

    settings = load_settings()
    configured = str(settings.get("memory_store_sqlite_path") or "").strip()
    if not configured:
        return DEFAULT_SQLITE_FILE
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _resolve_backend() -> str:
    from app.retrieval_pipeline.config import load_settings

    settings = load_settings()
    return str(settings.get("memory_store_backend", "sqlite")).strip().lower() or "sqlite"


def _resolve_read_fallback() -> str:
    from app.retrieval_pipeline.config import load_settings

    settings = load_settings()
    return str(settings.get("memory_store_read_fallback", "json")).strip().lower() or "json"


def _is_database_locked_error(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower()


class JsonMemoryRepository:
    def load_all_memories(self) -> List[Dict[str, Any]]:
        with _MEMORIES_LOCK:
            return [_normalize_memory(mem) for mem in read_json(MEMORIES_FILE, [])]

    def save_all_memories(self, memories: List[Dict[str, Any]]) -> None:
        with _MEMORIES_LOCK:
            write_json(MEMORIES_FILE, memories)

    def append_memories(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        with _MEMORIES_LOCK:
            all_memories = read_json(MEMORIES_FILE, [])
            all_memories.extend(records)
            write_json(MEMORIES_FILE, all_memories)
        return records

    def get_recent_memories(self, limit: int = 8, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        all_memories = self.load_all_memories()
        if session_id:
            all_memories = [mem for mem in all_memories if mem.get("session_id") == session_id]
        sliced = all_memories[-limit:]
        return list(reversed(sliced))

    def get_memory_count(self, session_id: Optional[str] = None) -> int:
        all_memories = self.load_all_memories()
        if session_id:
            all_memories = [mem for mem in all_memories if mem.get("session_id") == session_id]
        return len(all_memories)

    def list_memory_session_ids(self, limit: int = 100) -> List[str]:
        all_memories = self.load_all_memories()
        seen: set[str] = set()
        result: list[str] = []
        for mem in all_memories:
            sid = mem.get("session_id")
            if sid and sid not in seen:
                seen.add(sid)
                result.append(sid)
                if len(result) >= limit:
                    break
        return result

    def query_candidates(self, filters: CandidateFilters) -> List[Dict[str, Any]]:
        memories = self.load_all_memories()
        if filters.recency_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=filters.recency_days)
            memories = [mem for mem in memories if (_parse_timestamp(mem.get("ts")) or cutoff) >= cutoff]

        if filters.date_from:
            date_from_dt = _parse_timestamp(filters.date_from)
            if date_from_dt:
                memories = [
                    mem for mem in memories
                    if (mem_ts := _parse_timestamp(mem.get("ts"))) is not None
                    and mem_ts >= date_from_dt
                ]

        if filters.date_to:
            date_to_dt = _parse_timestamp(filters.date_to)
            if date_to_dt:
                memories = [
                    mem for mem in memories
                    if (mem_ts := _parse_timestamp(mem.get("ts"))) is not None
                    and mem_ts <= date_to_dt
                ]

        if filters.memory_types:
            allowed = {item.lower() for item in filters.memory_types}
            memories = [mem for mem in memories if str(mem.get("type", "")).lower() in allowed]

        if filters.mode in {"rough", "learnings"}:
            memories = [mem for mem in memories if str(mem.get("stream", "rough")) == filters.mode]

        memories = [mem for mem in memories if float(mem.get("source_reliability", 0.0)) >= filters.min_reliability]

        if filters.session_id and filters.session_bias:
            session_memories = [mem for mem in memories if mem.get("session_id") == filters.session_id]
            if session_memories:
                memories = session_memories
        return memories

    def query_by_ids(self, memory_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        memories = self.load_all_memories()
        by_id = {mem["id"]: mem for mem in memories if mem.get("id") in set(memory_ids)}
        return by_id

    def get_strong_neighbors(self, memory_id: str, min_weight: float = 0.35, max_neighbors: int = 8) -> List[Tuple[str, float, float]]:
        memories = self.load_all_memories()
        neighbors: List[Tuple[str, float, float]] = []

        source = next((m for m in memories if m.get("id") == memory_id), None)
        if source:
            outgoing = source.get("outgoing_weights") or {}
            if isinstance(outgoing, dict):
                for tid, w in outgoing.items():
                    w = float(w)
                    if w >= min_weight and tid != memory_id:
                        target = next((m for m in memories if m.get("id") == tid), None)
                        tau = float(target["tau"]) if target else 0.3
                        neighbors.append((tid, w, tau))

            for other in memories:
                if other.get("id") == memory_id:
                    continue
                rev_outgoing = other.get("outgoing_weights") or {}
                if isinstance(rev_outgoing, dict) and memory_id in rev_outgoing:
                    w = float(rev_outgoing[memory_id])
                    if w >= min_weight:
                        tau = float(other["tau"])
                        neighbors.append((other["id"], w, tau))

        seen: set[str] = set()
        deduped: List[Tuple[str, float, float]] = []
        for nid, w, t in neighbors:
            if nid in seen:
                continue
            seen.add(nid)
            deduped.append((nid, w, t))
        deduped.sort(key=lambda x: x[1], reverse=True)
        return deduped[:max_neighbors]

    def update_lnn_state(self, memory_id: str, h: Optional[float] = None, tau: Optional[float] = None,
                         outgoing_weights: Optional[Dict[str, float]] = None,
                         incoming_weights: Optional[Dict[str, float]] = None) -> None:
        with _MEMORIES_LOCK:
            all_memories = read_json(MEMORIES_FILE, [])
            for mem in all_memories:
                if mem.get("id") == memory_id:
                    if h is not None:
                        mem["h"] = float(h)
                    if tau is not None:
                        mem["tau"] = min(0.95, max(0.05, float(tau)))
                    if outgoing_weights is not None:
                        mem["outgoing_weights"] = outgoing_weights
                    if incoming_weights is not None:
                        mem["incoming_weights"] = incoming_weights
                    break
            write_json(MEMORIES_FILE, all_memories)

    def batch_update_weights(self, weight_deltas: List[Tuple[str, str, float]]) -> None:
        if not weight_deltas:
            return
        by_source: Dict[str, Dict[str, float]] = {}
        for source_id, target_id, delta in weight_deltas:
            if source_id not in by_source:
                by_source[source_id] = {}
            by_source[source_id][target_id] = by_source[source_id].get(target_id, 0.0) + delta
        with _MEMORIES_LOCK:
            all_memories = read_json(MEMORIES_FILE, [])
            for mem in all_memories:
                mid = mem.get("id")
                if mid in by_source:
                    current = mem.get("outgoing_weights") or {}
                    if isinstance(current, list):
                        current = {}
                    current = dict(current)
                    for tid, delta in by_source[mid].items():
                        current[tid] = min(current.get(tid, 0.0) + delta, 1.0)
                    current = {k: v for k, v in current.items() if abs(v) > 0.001}
                    mem["outgoing_weights"] = current if current else None
            write_json(MEMORIES_FILE, all_memories)

    def decay_all_activations(self, tau_disuse_decay: float, dt_minutes: float) -> None:
        pass

    def decay_all_tau(self, tau_disuse_decay: float, dt_minutes: float) -> None:
        pass

    def decay_all_weights(self, weight_decay: float) -> None:
        pass


class SqliteMemoryRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=SQLITE_TIMEOUT_SECONDS)
        conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_TIMEOUT_SECONDS * 1000)}")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn INTEGER NOT NULL,
            scene_id TEXT,
            idx INTEGER NOT NULL,
            text TEXT NOT NULL,
            type TEXT,
            stream TEXT NOT NULL CHECK(stream IN ('rough','learnings')),
            ts TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_reliability REAL NOT NULL,
            verification_status TEXT NOT NULL,
            fallback_generated INTEGER NOT NULL,
            source_event_ids_json TEXT NOT NULL,
            provenance_user TEXT NOT NULL,
            provenance_assistant TEXT NOT NULL,
            speaker_focus TEXT,
            memory_kind TEXT,
            embedding_blob BLOB,
            embedding_dim INTEGER,
            embedding_dtype TEXT CHECK(embedding_dtype IN ('f32') OR embedding_dtype IS NULL),
            h REAL NOT NULL DEFAULT 0.0,
            tau REAL NOT NULL DEFAULT 0.5,
            outgoing_weights BLOB,
            incoming_weights BLOB
        );
        CREATE INDEX IF NOT EXISTS idx_memories_session_ts ON memories(session_id, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_scene_id ON memories(scene_id);
        CREATE INDEX IF NOT EXISTS idx_memories_stream_ts ON memories(stream, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_type_ts ON memories(type, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_reliability ON memories(source_reliability);
        CREATE INDEX IF NOT EXISTS idx_memories_ts ON memories(ts DESC);
        """
        with self._lock, self._connect() as conn:
            conn.executescript(ddl)
            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
            if "scene_id" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN scene_id TEXT")
            if "speaker_focus" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN speaker_focus TEXT")
            if "memory_kind" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN memory_kind TEXT")
            if "h" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN h REAL NOT NULL DEFAULT 0.0")
            if "tau" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN tau REAL NOT NULL DEFAULT 0.5")
            if "outgoing_weights" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN outgoing_weights BLOB")
            if "incoming_weights" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN incoming_weights BLOB")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_scene_id ON memories(scene_id)")
            ensure_memory_store_metadata(conn)
            ensure_memory_readable_views(conn)
            conn.commit()

    def _record_to_row(self, record: Dict[str, Any]) -> Dict[str, Any]:
        normalized = _normalize_memory(record)
        idx = int(str(normalized.get("id", "0:0:0")).split(":")[-1] or 0)
        blob, dim, dtype = pack_embedding(normalized.get("embedding"))
        source_reliability = normalized.get("source_reliability")
        if source_reliability is None:
            source_reliability = 0.5
        outgoing_weights = normalized.get("outgoing_weights")
        incoming_weights = normalized.get("incoming_weights")
        outgoing_blob = json.dumps(outgoing_weights).encode() if isinstance(outgoing_weights, dict) else None
        incoming_blob = json.dumps(incoming_weights).encode() if isinstance(incoming_weights, dict) else None
        return {
            "id": str(normalized.get("id")),
            "session_id": str(normalized.get("session_id")),
            "turn": int(normalized.get("turn", 0)),
            "scene_id": normalized.get("scene_id"),
            "idx": idx,
            "text": str(normalized.get("text", "")),
            "type": normalized.get("type"),
            "stream": _normalize_stream(normalized.get("stream")),
            "ts": str(normalized.get("ts") or now_iso()),
            "source_type": str(normalized.get("source_type", "unknown")),
            "source_reliability": float(source_reliability),
            "verification_status": str(normalized.get("verification_status", "unverified")),
            "fallback_generated": 1 if bool(normalized.get("fallback_generated", False)) else 0,
            "source_event_ids_json": json.dumps(list(normalized.get("source_event_ids", []))),
            "provenance_user": str((normalized.get("provenance") or {}).get("user", "")),
            "provenance_assistant": str((normalized.get("provenance") or {}).get("assistant", "")),
            "speaker_focus": normalized.get("speaker_focus"),
            "memory_kind": normalized.get("memory_kind"),
            "h": float(normalized.get("h", 0.0)),
            "tau": float(normalized.get("tau", 0.5)),
            "outgoing_weights": outgoing_blob,
            "incoming_weights": incoming_blob,
            "embedding_blob": blob,
            "embedding_dim": dim,
            "embedding_dtype": dtype,
        }

    def _row_to_memory(self, row: sqlite3.Row, decode_embedding: bool, include_blob: bool = False) -> Dict[str, Any]:
        memory: Dict[str, Any] = {
            "id": row["id"],
            "text": row["text"],
            "type": row["type"],
            "stream": row["stream"],
            "ts": row["ts"],
            "session_id": row["session_id"],
            "turn": row["turn"],
            "scene_id": row["scene_id"] if "scene_id" in row.keys() else None,
            "provenance": {
                "user": row["provenance_user"] or "",
                "assistant": row["provenance_assistant"] or "",
            },
            "source_event_ids": json.loads(row["source_event_ids_json"] or "[]"),
            "source_type": row["source_type"],
            "source_reliability": float(row["source_reliability"]),
            "verification_status": row["verification_status"],
            "fallback_generated": bool(row["fallback_generated"]),
            "speaker_focus": row["speaker_focus"] if "speaker_focus" in row.keys() else None,
            "memory_kind": row["memory_kind"] if "memory_kind" in row.keys() else None,
            "h": float(row["h"]) if "h" in row.keys() else 0.0,
            "tau": float(row["tau"]) if "tau" in row.keys() else 0.5,
            "embedding": None,
        }
        memory["memory_kind"] = _normalize_memory_kind(memory)

        outgoing_blob = row["outgoing_weights"] if "outgoing_weights" in row.keys() else None
        incoming_blob = row["incoming_weights"] if "incoming_weights" in row.keys() else None
        if outgoing_blob:
            try:
                memory["outgoing_weights"] = json.loads(outgoing_blob)
            except (json.JSONDecodeError, TypeError):
                memory["outgoing_weights"] = None
        if incoming_blob:
            try:
                memory["incoming_weights"] = json.loads(incoming_blob)
            except (json.JSONDecodeError, TypeError):
                memory["incoming_weights"] = None

        blob = row["embedding_blob"]
        dim = row["embedding_dim"]
        dtype = row["embedding_dtype"]
        if decode_embedding and blob and dim:
            try:
                vector = unpack_embedding(blob, int(dim), dtype)
                memory["embedding"] = vector.tolist() if vector is not None else None
            except ValueError:
                LOGGER.warning("Failed to decode embedding blob for memory id=%s", row["id"])

        if include_blob:
            memory["_embedding_blob"] = blob
            memory["_embedding_dim"] = dim
            memory["_embedding_dtype"] = dtype
        return memory

    def append_memories(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not records:
            return []

        sql = """
        INSERT INTO memories (
            id, session_id, turn, scene_id, idx, text, type, stream, ts, source_type,
            source_reliability, verification_status, fallback_generated,
            source_event_ids_json, provenance_user, provenance_assistant, speaker_focus, memory_kind,
            h, tau, outgoing_weights, incoming_weights,
            embedding_blob, embedding_dim, embedding_dtype
        )
        VALUES (
            :id, :session_id, :turn, :scene_id, :idx, :text, :type, :stream, :ts, :source_type,
            :source_reliability, :verification_status, :fallback_generated,
            :source_event_ids_json, :provenance_user, :provenance_assistant, :speaker_focus, :memory_kind,
            :h, :tau, :outgoing_weights, :incoming_weights,
            :embedding_blob, :embedding_dim, :embedding_dtype
        )
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            turn=excluded.turn,
            scene_id=excluded.scene_id,
            idx=excluded.idx,
            text=excluded.text,
            type=excluded.type,
            stream=excluded.stream,
            ts=excluded.ts,
            source_type=excluded.source_type,
            source_reliability=excluded.source_reliability,
            verification_status=excluded.verification_status,
            fallback_generated=excluded.fallback_generated,
            source_event_ids_json=excluded.source_event_ids_json,
            provenance_user=excluded.provenance_user,
            provenance_assistant=excluded.provenance_assistant,
            speaker_focus=excluded.speaker_focus,
            memory_kind=excluded.memory_kind,
            h=excluded.h,
            tau=excluded.tau,
            outgoing_weights=excluded.outgoing_weights,
            incoming_weights=excluded.incoming_weights,
            embedding_blob=excluded.embedding_blob,
            embedding_dim=excluded.embedding_dim,
            embedding_dtype=excluded.embedding_dtype
        """
        rows = [self._record_to_row(record) for record in records]
        with self._lock, self._connect() as conn:
            conn.executemany(sql, rows)
            conn.commit()
        return records

    def load_all_memories(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM memories ORDER BY ts ASC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [self._row_to_memory(row, decode_embedding=True) for row in rows]

    def get_recent_memories(self, limit: int = 8, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        where = ""
        params: List[Any] = []
        if session_id:
            where = "WHERE session_id = ?"
            params.append(session_id)

        query = f"SELECT * FROM memories {where} ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_memory(row, decode_embedding=True) for row in rows]

    def get_memory_count(self, session_id: Optional[str] = None) -> int:
        where = ""
        params: List[Any] = []
        if session_id:
            where = "WHERE session_id = ?"
            params.append(session_id)
        query = f"SELECT COUNT(*) AS c FROM memories {where}"
        with self._lock, self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return int(row["c"] if row else 0)

    def list_memory_session_ids(self, limit: int = 100) -> List[str]:
        query = "SELECT DISTINCT session_id FROM memories ORDER BY ts DESC LIMIT ?"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, (int(limit),)).fetchall()
        return [row["session_id"] for row in rows if row["session_id"]]

    def _filtered_rows(self, filters: CandidateFilters, session_id: Optional[str] = None) -> List[sqlite3.Row]:
        clauses = ["source_reliability >= ?"]
        params: List[Any] = [float(filters.min_reliability)]

        if filters.recency_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=filters.recency_days)
            clauses.append("ts >= ?")
            params.append(cutoff.isoformat())

        if filters.date_from:
            clauses.append("ts >= ?")
            params.append(filters.date_from)

        if filters.date_to:
            clauses.append("ts <= ?")
            params.append(filters.date_to)

        if filters.memory_types:
            placeholders = ",".join("?" for _ in filters.memory_types)
            clauses.append(f"LOWER(COALESCE(type, '')) IN ({placeholders})")
            params.extend([item.lower() for item in filters.memory_types])

        if filters.mode in {"rough", "learnings"}:
            clauses.append("stream = ?")
            params.append(filters.mode)

        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)

        where = " AND ".join(clauses) if clauses else "1=1"
        query = f"SELECT * FROM memories WHERE {where} ORDER BY ts DESC"
        with self._lock, self._connect() as conn:
            return conn.execute(query, params).fetchall()

    def query_candidates(self, filters: CandidateFilters) -> List[Dict[str, Any]]:
        if filters.session_id and filters.session_bias:
            rows = self._filtered_rows(filters, session_id=filters.session_id)
            if not rows:
                rows = self._filtered_rows(filters, session_id=None)
        else:
            rows = self._filtered_rows(filters, session_id=None)
        return [self._row_to_memory(row, decode_embedding=False, include_blob=True) for row in rows]

    def update_lnn_state(self, memory_id: str, h: Optional[float] = None, tau: Optional[float] = None,
                         outgoing_weights: Optional[Dict[str, float]] = None,
                         incoming_weights: Optional[Dict[str, float]] = None) -> None:
        set_clauses: List[str] = []
        params: List[Any] = []
        if h is not None:
            set_clauses.append("h = ?")
            params.append(float(h))
        if tau is not None:
            tau_val = min(0.95, max(0.05, float(tau)))
            set_clauses.append("tau = ?")
            params.append(tau_val)
        if outgoing_weights is not None:
            set_clauses.append("outgoing_weights = ?")
            params.append(json.dumps(outgoing_weights).encode())
        if incoming_weights is not None:
            set_clauses.append("incoming_weights = ?")
            params.append(json.dumps(incoming_weights).encode())
        if not set_clauses:
            return
        params.append(memory_id)
        sql = f"UPDATE memories SET {', '.join(set_clauses)} WHERE id = ?"
        with self._lock, self._connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def batch_update_weights(self, weight_deltas: List[Tuple[str, str, float]]) -> None:
        if not weight_deltas:
            return
        by_source: Dict[str, Dict[str, float]] = {}
        for source_id, target_id, delta in weight_deltas:
            if source_id not in by_source:
                by_source[source_id] = {}
            by_source[source_id][target_id] = by_source[source_id].get(target_id, 0.0) + delta
        with self._lock, self._connect() as conn:
            for source_id, updates in by_source.items():
                row = conn.execute("SELECT outgoing_weights FROM memories WHERE id = ?", (source_id,)).fetchone()
                current: Dict[str, float] = {}
                if row and row["outgoing_weights"]:
                    try:
                        current = json.loads(row["outgoing_weights"])
                    except (json.JSONDecodeError, TypeError):
                        current = {}
                for target_id, delta in updates.items():
                    current[target_id] = min(current.get(target_id, 0.0) + delta, 1.0)
                current = {k: v for k, v in current.items() if abs(v) > 0.001}
                conn.execute(
                    "UPDATE memories SET outgoing_weights = ? WHERE id = ?",
                    (json.dumps(current).encode() if current else None, source_id),
                )
            conn.commit()

    def decay_all_activations(self, tau_disuse_decay: float, dt_minutes: float) -> None:
        rate = float(tau_disuse_decay) * float(dt_minutes)
        if rate <= 0:
            return
        sql = "UPDATE memories SET h = h * EXP(-?) WHERE h IS NOT NULL AND h > 0.001"
        with self._lock, self._connect() as conn:
            conn.execute(sql, (rate,))
            conn.commit()

    def decay_all_tau(self, tau_disuse_decay: float, dt_minutes: float) -> None:
        rate = float(tau_disuse_decay) * float(dt_minutes)
        if rate <= 0:
            return
        sql = "UPDATE memories SET tau = MAX(0.05, tau * (1.0 - ?)) WHERE tau > 0.05"
        with self._lock, self._connect() as conn:
            conn.execute(sql, (rate,))
            conn.commit()

    def decay_all_weights(self, weight_decay: float) -> None:
        all_ids_query = "SELECT id, outgoing_weights FROM memories WHERE outgoing_weights IS NOT NULL"
        with self._lock, self._connect() as conn:
            rows = conn.execute(all_ids_query).fetchall()
            for row in rows:
                try:
                    weights = json.loads(row["outgoing_weights"])
                except (json.JSONDecodeError, TypeError):
                    continue
                pruned = {}
                for target_id, w in weights.items():
                    w = float(w) * (1.0 - float(weight_decay))
                    if abs(w) > 0.001:
                        pruned[target_id] = w
                conn.execute(
                    "UPDATE memories SET outgoing_weights = ? WHERE id = ?",
                    (json.dumps(pruned).encode() if pruned else None, row["id"]),
                )
            conn.commit()

    def query_by_ids(self, memory_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not memory_ids:
            return {}
        placeholders = ",".join("?" for _ in memory_ids)
        query = f"SELECT * FROM memories WHERE id IN ({placeholders})"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, memory_ids).fetchall()
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            record = self._row_to_memory(row, decode_embedding=True, include_blob=True)
            result[row["id"]] = record
        return result

    def get_strong_neighbors(self, memory_id: str, min_weight: float = 0.35, max_neighbors: int = 8) -> List[Tuple[str, float, float]]:
        neighbors: List[Tuple[str, float, float]] = []

        with self._lock, self._connect() as conn:
            source_row = conn.execute("SELECT outgoing_weights, tau FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if source_row and source_row["outgoing_weights"]:
                try:
                    outgoing = json.loads(source_row["outgoing_weights"])
                except (json.JSONDecodeError, TypeError):
                    outgoing = {}
                for target_id, weight in outgoing.items():
                    weight = float(weight)
                    if weight >= min_weight:
                        target_row = conn.execute("SELECT tau FROM memories WHERE id = ?", (target_id,)).fetchone()
                        tau = float(target_row["tau"]) if target_row else 0.3
                        neighbors.append((target_id, weight, tau))

            rev_query = "SELECT id, outgoing_weights, tau FROM memories WHERE outgoing_weights IS NOT NULL AND id != ?"
            rev_rows = conn.execute(rev_query, (memory_id,)).fetchall()
            for rev_row in rev_rows:
                try:
                    rev_outgoing = json.loads(rev_row["outgoing_weights"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if memory_id in rev_outgoing:
                    weight = float(rev_outgoing[memory_id])
                    if weight >= min_weight:
                        tau = float(rev_row["tau"])
                        neighbors.append((rev_row["id"], weight, tau))

        seen: set[str] = set()
        deduped: List[Tuple[str, float, float]] = []
        for nid, w, t in neighbors:
            if nid == memory_id or nid in seen:
                continue
            seen.add(nid)
            deduped.append((nid, w, t))

        deduped.sort(key=lambda x: x[1], reverse=True)
        return deduped[:max_neighbors]


def get_memory_repository() -> MemoryRepository:
    global _REPO_CACHE, _REPO_CACHE_KEY

    backend = _resolve_backend()
    sqlite_path = _resolve_sqlite_path()
    cache_key = (backend, str(sqlite_path))
    if _REPO_CACHE is not None and _REPO_CACHE_KEY == cache_key:
        return _REPO_CACHE

    if backend == "json":
        _REPO_CACHE = JsonMemoryRepository()
        _REPO_CACHE_KEY = cache_key
        return _REPO_CACHE

    try:
        _REPO_CACHE = SqliteMemoryRepository(sqlite_path)
    except Exception as exc:  # pragma: no cover
        if _is_database_locked_error(exc):
            raise
        fallback = _resolve_read_fallback()
        if fallback == "json":
            LOGGER.warning("SQLite backend unavailable (%s). Falling back to JSON backend.", exc)
            _REPO_CACHE = JsonMemoryRepository()
        else:
            raise
    _REPO_CACHE_KEY = cache_key
    return _REPO_CACHE


def load_all_memories() -> List[Dict[str, Any]]:
    LOGGER.warning(
        "load_all_memories() loads the full memory store into RAM; prefer repository.query_candidates() for retrieval paths."
    )
    return get_memory_repository().load_all_memories()


def save_all_memories(memories: List[Dict[str, Any]]) -> None:
    repo = get_memory_repository()
    if isinstance(repo, JsonMemoryRepository):
        repo.save_all_memories(memories)
        return

    # SQLite-backed stores do not support bulk replacement safely in place.
    LOGGER.warning("save_all_memories() is JSON-only. Skipping operation for backend '%s'.", _resolve_backend())


def append_memories(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return get_memory_repository().append_memories(records)


def create_memory_record(
    session_id: str,
    turn: int,
    index: int,
    text: str,
    user_text: str,
    assistant_text: str,
    scene_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    stream: str = "rough",
    embedding: Optional[List[float]] = None,
    source_event_ids: Optional[List[str]] = None,
    source_type: str = "unknown",
    source_reliability: float = 0.5,
    verification_status: str = "unverified",
    fallback_generated: bool = False,
    speaker_focus: Optional[str] = None,
    memory_kind: Optional[str] = None,
) -> Dict[str, Any]:
    tau = _resolve_tau_initial(memory_kind, source_reliability, verification_status, stream)
    return {
        "id": f"{session_id}:{turn}:{index}",
        "text": text,
        "type": memory_type,
        "stream": stream,
        "embedding": embedding,
        "ts": now_iso(),
        "session_id": session_id,
        "turn": turn,
        "scene_id": scene_id,
        "provenance": {"user": user_text, "assistant": assistant_text},
        "source_event_ids": list(source_event_ids or []),
        "source_type": source_type,
        "source_reliability": source_reliability,
        "verification_status": verification_status,
        "fallback_generated": bool(fallback_generated),
        "speaker_focus": speaker_focus,
        "memory_kind": memory_kind,
        "h": 0.0,
        "tau": tau,
    }


def _resolve_tau_initial(
    memory_kind: Optional[str],
    source_reliability: Optional[float],
    verification_status: str,
    stream: str,
) -> float:
    kind_tau = {
        "decision": 0.90,
        "commitment": 0.85,
        "user_preference": 0.80,
        "outcome": 0.70,
        "workflow": 0.60,
        "user_fact": 0.50,
        "task": 0.40,
        "issue": 0.35,
    }
    tau = kind_tau.get(str(memory_kind or "").strip().lower(), 0.30)
    reliability = float(source_reliability) if source_reliability is not None else 0.5
    if reliability >= 0.8 or verification_status == "verified":
        tau += 0.10
    if stream == "learnings":
        tau += 0.05
    return min(0.95, max(0.05, tau))


def get_memories_for_session(session_id: str) -> List[Memory]:
    all_memories = get_memory_repository().load_all_memories()
    session_memories = [Memory(**_normalize_memory(mem)) for mem in all_memories if mem.get("session_id") == session_id]
    return session_memories


def get_recent_memories(limit: int = 8, session_id: Optional[str] = None) -> List[Memory]:
    memories = get_memory_repository().get_recent_memories(limit=limit, session_id=session_id)

    try:
        from app.save_pipeline.dedup_buffer import peek_dedup_buffer
        buf_limit = max(limit // 2, 2)
        buffer_entries = peek_dedup_buffer(limit=buf_limit, session_id=session_id)
        seen_ids = {mem.get("id") for mem in memories}
        for buf in buffer_entries:
            entry = dict(buf)
            entry.pop("_buffer_ts", None)
            if entry.get("id") not in seen_ids:
                memories.append(entry)
                seen_ids.add(entry.get("id"))
    except Exception:
        pass

    memories.sort(key=lambda m: m.get("ts", ""), reverse=True)
    return [Memory(**_normalize_memory(mem)) for mem in memories[:limit]]


def get_memory_count(session_id: Optional[str] = None) -> int:
    return get_memory_repository().get_memory_count(session_id=session_id)


def list_memory_session_ids(limit: int = 100) -> list[str]:
    return get_memory_repository().list_memory_session_ids(limit=limit)


def migrate_legacy_memories() -> int:
    """
    Add source and stream fields to legacy JSON memories created before v2.
    This migration intentionally targets memories.json for backward compatibility.
    """
    all_memories = read_json(MEMORIES_FILE, [])
    migrated = 0
    for mem in all_memories:
        changed = False
        if "source_type" not in mem:
            mem["source_type"] = "legacy"
            mem["source_reliability"] = 0.3
            mem["verification_status"] = "unverified"
            changed = True
        if "stream" not in mem:
            mem["stream"] = "rough"
            changed = True
        if "source_event_ids" not in mem:
            mem["source_event_ids"] = []
            changed = True
        if changed:
            migrated += 1

    if migrated > 0:
        write_json(MEMORIES_FILE, all_memories)
    return migrated


def query_memory_candidates(filters: CandidateFilters) -> List[Dict[str, Any]]:
    return get_memory_repository().query_candidates(filters)


def migrate_json_to_sqlite(sqlite_path: Optional[Path] = None) -> Dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    source = read_json(MEMORIES_FILE, [])
    db_path = sqlite_path or _resolve_sqlite_path()
    repo = SqliteMemoryRepository(db_path)

    skipped = 0
    normalized_records: List[Dict[str, Any]] = []

    with repo._lock, repo._connect() as conn:
        existing_ids = {row["id"] for row in conn.execute("SELECT id FROM memories").fetchall()}

    for mem in source:
        normalized = _normalize_memory(mem)
        memory_id = str(normalized.get("id") or "")
        if not memory_id:
            skipped += 1
            continue
        normalized_records.append(normalized)

    try:
        repo.append_memories(normalized_records)
    except Exception:
        # Fall back to per-record writes so malformed rows do not block migration.
        saved_records: List[Dict[str, Any]] = []
        for record in normalized_records:
            try:
                repo.append_memories([record])
                saved_records.append(record)
            except Exception:
                skipped += 1
        normalized_records = saved_records

    migrated_ids = {str(record.get("id")) for record in normalized_records}
    updated = len(migrated_ids & existing_ids)
    inserted = len(migrated_ids - existing_ids)

    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    report = {
        "source_count": len(source),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "duration_ms": duration_ms,
        "sqlite_path": str(db_path),
    }
    return report
