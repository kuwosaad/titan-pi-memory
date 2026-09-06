"""Bounded, read-only data bridge for the native BB Titan explorer.

This module intentionally does not import Titan's runtime bootstrap, model
providers, retrieval pipeline, or graph renderer.  It reads durable memory
stores directly through read-only SQLite connections (or the legacy JSON
fallback), then emits one bounded JSON response for a single CLI request.

The public entrypoint is::

    python3 -m app.graph.explorer [--home /path/to/.titan] < request.json

``--home`` and ``TITAN_HOME`` mean the shared Titan home containing an
``agents/`` directory.  The bridge never creates that directory or any
namespace beneath it.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
from typing import Any, Iterable, Optional


_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]*")

MAX_SOURCES = 64
MAX_CATALOG_TYPES = 256
MAX_SNAPSHOT_NODES = 150
MAX_SNAPSHOT_EDGES = 450
MAX_SNAPSHOT_BYTES = 512 * 1024
MAX_NODE_TEXT = 240
MAX_DETAIL_TEXT = 16 * 1024
MAX_SEARCH_PAGE = 50
MAX_SEARCH_QUERY = 512
MAX_FILTER_VALUE = 256
MAX_MEMORY_ID = 256
MAX_SOURCE_AGENT = 128
MAX_SESSION_ID = 256
MAX_MEMORY_TYPE = 128
MAX_STREAM = 64
MAX_TIMESTAMP = 128
MAX_NODE_ID = 512
MAX_VECTOR_DIM = 4096
SQLITE_TIMEOUT_SECONDS = 2.0
MAX_STDIN_BYTES = 1024 * 1024
TOP_K = 2
MIN_SIMILARITY = 0.35


class ExplorerError(Exception):
    """A bounded, user-visible bridge error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        self.message = str(message)[:500]
        super().__init__(self.message)


class WarningCollector:
    """Keep partial-read warnings bounded and deterministic."""

    def __init__(self) -> None:
        self._items: list[str] = []
        self._seen: set[str] = set()

    def add(self, message: str) -> None:
        value = " ".join(str(message).split())[:240]
        if not value or value in self._seen or len(self._items) >= 32:
            return
        self._seen.add(value)
        self._items.append(value)

    def extend(self, values: Iterable[str]) -> None:
        for value in values:
            self.add(value)

    def as_list(self) -> list[str]:
        return list(self._items)


@dataclass(frozen=True)
class ExplorerFilters:
    agent: Optional[str] = None
    type: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    date_to_exclusive: bool = False
    query: Optional[str] = None


@dataclass
class MemoryRecord:
    source_agent: str
    memory_id: str
    text: str
    memory_type: Optional[str]
    stream: Optional[str]
    timestamp: Optional[str]
    session_id: Optional[str]
    turn: Optional[int]
    scene_id: Optional[str]
    source_type: Optional[str]
    source_reliability: Optional[float]
    verification_status: Optional[str]
    fallback_generated: Optional[bool]
    speaker_focus: Optional[str]
    memory_kind: Optional[str]
    embedding: Optional[tuple[float, ...]]

    @property
    def has_embedding(self) -> bool:
        return bool(self.embedding)


@dataclass(frozen=True)
class Source:
    agent: str
    db_path: Path
    json_path: Path


@dataclass(frozen=True)
class Cursor:
    timestamp: Optional[str]
    source_agent: str
    memory_id: str


@dataclass(frozen=True)
class StorageCandidate:
    source_agent: str
    memory_id: str
    timestamp: Optional[str]
    rowid: Optional[int] = None
    record: Optional[MemoryRecord] = None


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _bounded_metadata(value: Any, limit: int) -> Optional[str]:
    text = _optional_string(value)
    return _bounded_text(text, limit) if text is not None else None


def _timestamp_value(value: Any) -> Optional[int]:
    text = _optional_string(value)
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        utc = parsed.astimezone(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = utc - epoch
        return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key] if key in row.keys() else default
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _valid_vector(values: Any) -> Optional[tuple[float, ...]]:
    if not isinstance(values, (list, tuple)) or not values:
        return None
    if len(values) > MAX_VECTOR_DIM:
        return None
    result: list[float] = []
    try:
        for value in values:
            number = float(value)
            if not math.isfinite(number):
                return None
            result.append(number)
    except (TypeError, ValueError):
        return None
    if not result or not any(number != 0.0 for number in result):
        return None
    norm = math.sqrt(sum(number * number for number in result))
    if not math.isfinite(norm) or norm == 0.0:
        return None
    return tuple(number / norm for number in result)


def _decode_sqlite_vector(row: Any) -> Optional[tuple[float, ...]]:
    blob = _row_value(row, "embedding_blob")
    dim_value = _row_value(row, "embedding_dim")
    dtype = _row_value(row, "embedding_dtype")
    if not blob or dim_value in (None, ""):
        return None
    if dtype not in (None, "f32"):
        return None
    try:
        dimension = int(dim_value)
    except (TypeError, ValueError):
        return None
    if dimension <= 0 or dimension > MAX_VECTOR_DIM:
        return None
    raw = bytes(blob)
    expected = dimension * 4
    if len(raw) < expected:
        return None
    try:
        values = struct.unpack("<" + ("f" * dimension), raw[:expected])
    except (struct.error, ValueError):
        return None
    return _valid_vector(values)


def _record_from_row(
    source_agent: str,
    row: Any,
    *,
    text_limit: int = MAX_DETAIL_TEXT,
    warnings: Optional[WarningCollector] = None,
) -> Optional[MemoryRecord]:
    memory_id = _optional_string(_row_value(row, "id"))
    if not memory_id:
        if warnings:
            warnings.add(f"Skipped a {source_agent} memory with an empty id")
        return None
    if len(memory_id) > MAX_MEMORY_ID:
        if warnings:
            warnings.add(f"Skipped a {source_agent} memory with an oversized id")
        return None
    timestamp = _optional_string(_row_value(row, "ts"))
    if timestamp is not None and len(timestamp) > MAX_TIMESTAMP:
        if warnings:
            warnings.add(f"Skipped {source_agent} memory {memory_id[:64]} with an oversized timestamp")
        return None
    raw_text = _row_value(row, "text", "")
    text = _bounded_text(raw_text, text_limit)
    embedding = _decode_sqlite_vector(row)
    if embedding is None:
        embedding_value = _row_value(row, "embedding")
        embedding = _valid_vector(embedding_value)
    return MemoryRecord(
        source_agent=source_agent,
        memory_id=memory_id,
        text=text,
        memory_type=_bounded_metadata(_row_value(row, "type"), MAX_MEMORY_TYPE),
        stream=_bounded_metadata(_row_value(row, "stream"), MAX_STREAM),
        timestamp=timestamp,
        session_id=_bounded_metadata(_row_value(row, "session_id"), MAX_SESSION_ID),
        turn=_optional_int(_row_value(row, "turn")),
        scene_id=_bounded_metadata(_row_value(row, "scene_id"), MAX_FILTER_VALUE),
        source_type=_bounded_metadata(_row_value(row, "source_type"), MAX_FILTER_VALUE),
        source_reliability=_optional_float(_row_value(row, "source_reliability")),
        verification_status=_bounded_metadata(_row_value(row, "verification_status"), MAX_FILTER_VALUE),
        fallback_generated=_optional_bool(_row_value(row, "fallback_generated")),
        speaker_focus=_bounded_metadata(_row_value(row, "speaker_focus"), MAX_FILTER_VALUE),
        memory_kind=_bounded_metadata(_row_value(row, "memory_kind"), MAX_FILTER_VALUE),
        embedding=embedding,
    )


def _record_from_json(
    source_agent: str,
    raw: Any,
    *,
    text_limit: int = MAX_DETAIL_TEXT,
    warnings: Optional[WarningCollector] = None,
) -> Optional[MemoryRecord]:
    if not isinstance(raw, dict):
        return None
    memory_id = _optional_string(raw.get("id"))
    if not memory_id:
        if warnings:
            warnings.add(f"Skipped a {source_agent} memory with an empty id")
        return None
    if len(memory_id) > MAX_MEMORY_ID:
        if warnings:
            warnings.add(f"Skipped a {source_agent} memory with an oversized id")
        return None
    timestamp = _optional_string(raw.get("ts"))
    if timestamp is not None and len(timestamp) > MAX_TIMESTAMP:
        if warnings:
            warnings.add(f"Skipped {source_agent} memory {memory_id[:64]} with an oversized timestamp")
        return None
    return MemoryRecord(
        source_agent=source_agent,
        memory_id=memory_id,
        text=_bounded_text(raw.get("text", ""), text_limit),
        memory_type=_bounded_metadata(raw.get("type"), MAX_MEMORY_TYPE),
        stream=_bounded_metadata(raw.get("stream"), MAX_STREAM),
        timestamp=timestamp,
        session_id=_bounded_metadata(raw.get("session_id"), MAX_SESSION_ID),
        turn=_optional_int(raw.get("turn")),
        scene_id=_bounded_metadata(raw.get("scene_id"), MAX_FILTER_VALUE),
        source_type=_bounded_metadata(raw.get("source_type"), MAX_FILTER_VALUE),
        source_reliability=_optional_float(raw.get("source_reliability")),
        verification_status=_bounded_metadata(raw.get("verification_status"), MAX_FILTER_VALUE),
        fallback_generated=_optional_bool(raw.get("fallback_generated")),
        speaker_focus=_bounded_metadata(raw.get("speaker_focus"), MAX_FILTER_VALUE),
        memory_kind=_bounded_metadata(raw.get("memory_kind"), MAX_FILTER_VALUE),
        embedding=_valid_vector(raw.get("embedding")),
    )


def _record_key(record: MemoryRecord) -> tuple[int, str, str]:
    # SQLite and JSON both order by the parsed instant, not the raw offset
    # spelling. This keeps pagination stable for equivalent timestamps such as
    # 00:00Z and 01:00+01:00.
    timestamp = _timestamp_value(record.timestamp)
    return timestamp if timestamp is not None else -(10**30), record.source_agent, record.memory_id


def _node_id(record: MemoryRecord) -> str:
    # Agent names cannot contain ':', so this qualified form cannot collide
    # across namespaces even when memory IDs are identical.
    return f"{record.source_agent}::{record.memory_id}"


def _summary(record: MemoryRecord, *, text_limit: int = MAX_NODE_TEXT) -> dict[str, Any]:
    return {
        "nodeId": _node_id(record),
        "sourceAgent": record.source_agent,
        "memoryId": record.memory_id,
        "text": _bounded_text(record.text, text_limit),
        "type": record.memory_type,
        "stream": record.stream,
        "timestamp": record.timestamp,
        "sessionId": record.session_id,
        "hasEmbedding": record.has_embedding,
    }


def _detail_metadata(record: MemoryRecord) -> dict[str, str | int | float | bool | None]:
    return {
        "type": record.memory_type,
        "stream": record.stream,
        "memory_kind": record.memory_kind,
        "timestamp": record.timestamp,
        "session_id": record.session_id,
        "turn": record.turn,
        "scene_id": record.scene_id,
        "source_type": record.source_type,
        "source_reliability": record.source_reliability,
        "verification_status": record.verification_status,
        "fallback_generated": record.fallback_generated,
        "speaker_focus": record.speaker_focus,
    }


def _validate_agent(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _AGENT_NAME_RE.fullmatch(value):
        raise ExplorerError("invalid_input", "agent must be a valid Titan namespace name")
    return value


def _validate_bounded_string(value: Any, name: str, limit: int = MAX_FILTER_VALUE) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ExplorerError("invalid_input", f"{name} must be a string")
    if len(value) > limit:
        raise ExplorerError("invalid_input", f"{name} is too long")
    return value


def _is_date_only(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"


def _validate_date(value: Any, name: str) -> Optional[str]:
    result = _validate_bounded_string(value, name)
    if result is None:
        return None
    try:
        if _is_date_only(result):
            parsed_date = datetime.fromisoformat(result)
            if name == "dateTo":
                # Native date inputs represent a whole UTC calendar day. Use
                # an exclusive next-day boundary so 23:59:59.999999 is kept.
                try:
                    iso_value = (parsed_date + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00+00:00")
                except OverflowError as exc:
                    raise ExplorerError("invalid_input", f"{name} date is out of range") from exc
            else:
                iso_value = result + "T00:00:00+00:00"
        else:
            iso_value = result[:-1] + "+00:00" if result.endswith(("Z", "z")) else result
        datetime.fromisoformat(iso_value)
    except ExplorerError:
        raise
    except (OverflowError, ValueError) as exc:
        raise ExplorerError("invalid_input", f"{name} must be an ISO timestamp") from exc
    return iso_value


def parse_filters(raw: Any) -> ExplorerFilters:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ExplorerError("invalid_input", "filters must be an object")
    allowed = {"agent", "type", "dateFrom", "dateTo", "query"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ExplorerError("invalid_input", f"unsupported filter: {unknown[0]}")
    raw_date_to = raw.get("dateTo")
    return ExplorerFilters(
        agent=_validate_agent(raw.get("agent")),
        type=_validate_bounded_string(raw.get("type"), "type"),
        date_from=_validate_date(raw.get("dateFrom"), "dateFrom"),
        date_to=_validate_date(raw_date_to, "dateTo"),
        date_to_exclusive=_is_date_only(raw_date_to),
        query=_validate_bounded_string(raw.get("query"), "query", MAX_SEARCH_QUERY),
    )


def resolve_home(explicit_home: Optional[str] = None) -> Path:
    value = explicit_home or os.environ.get("TITAN_HOME") or str(Path.home() / ".titan")
    return Path(value).expanduser().resolve()


def _safe_agent_dir(home: Path, agent: str) -> Optional[Path]:
    if not _AGENT_NAME_RE.fullmatch(agent):
        return None
    agents_root = (home / "agents").resolve()
    candidate = (agents_root / agent).resolve()
    if agents_root not in candidate.parents:
        return None
    return candidate


def _safe_store_path(directory: Path, filename: str) -> Optional[Path]:
    candidate = directory / "out" / "memories" / filename
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if directory not in resolved.parents and resolved != directory:
        return None
    return resolved


def discover_agent_names(home: Path, agent: Optional[str], warnings: WarningCollector) -> list[str]:
    agents_root = home / "agents"
    if agent is not None:
        return [agent]
    names: list[str] = []
    if agents_root.is_dir():
        try:
            for entry in sorted(agents_root.iterdir(), key=lambda path: path.name):
                if not entry.is_dir() or not _AGENT_NAME_RE.fullmatch(entry.name):
                    continue
                if _safe_agent_dir(home, entry.name) is None:
                    warnings.add(f"Skipped unsafe namespace {entry.name}")
                    continue
                names.append(entry.name)
        except OSError as exc:
            warnings.add(f"Could not inspect Titan namespaces: {exc}")
    if len(names) > MAX_SOURCES:
        warnings.add(f"Namespace list limited to {MAX_SOURCES} sources")
        names = names[:MAX_SOURCES]
    return names


def _source_for_agent(home: Path, name: str, warnings: WarningCollector) -> Optional[Source]:
    directory = _safe_agent_dir(home, name)
    if directory is None:
        raise ExplorerError("invalid_input", "agent namespace escaped Titan home")
    db_path = _safe_store_path(directory, "memory_store.db")
    json_path = _safe_store_path(directory, "memories.json")
    if db_path is None or not db_path.is_file():
        db_path = directory / ".missing-memory-store"
    if json_path is None or not json_path.is_file():
        json_path = directory / ".missing-memory-store"
    if not db_path.is_file() and not json_path.is_file():
        warnings.add(f"Namespace {name} has no readable memory store")
        return None
    return Source(name, db_path, json_path)


def discover_sources(home: Path, agent: Optional[str], warnings: WarningCollector) -> list[Source]:
    sources: list[Source] = []
    for name in discover_agent_names(home, agent, warnings):
        source = _source_for_agent(home, name, warnings)
        if source is not None:
            sources.append(source)
    return sources


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ExplorerError("not_found", "memory store not found")
    uri = f"{path.expanduser().resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_SECONDS)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ExplorerError("database_busy", "memory store is busy") from exc
        raise ExplorerError("read_error", "could not open memory store") from exc
    connection.row_factory = sqlite3.Row
    # SQLite's julianday/strftime paths round fractional seconds. This
    # deterministic scalar gives SQL the same exact UTC-microsecond key used
    # by Python ordering, without schema/index mutations.
    connection.create_function("titan_ts_us", 1, _timestamp_value, deterministic=True)
    connection.execute("PRAGMA busy_timeout = 2000")
    connection.execute("PRAGMA query_only = ON")
    return connection


def _sqlite_columns(connection: sqlite3.Connection) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute("PRAGMA table_info(memories)").fetchall()}
    except sqlite3.DatabaseError as exc:
        raise ExplorerError("read_error", "could not inspect memory schema") from exc


def _sqlite_projection(columns: set[str], *, text_limit: int = MAX_DETAIL_TEXT) -> str:
    # Bound untrusted text before it reaches Python. The extra byte lets the
    # detail response detect truncation without loading a giant source field.
    fields = [
        f"substr(id, 1, {MAX_MEMORY_ID + 1}) AS id",
        f"substr(text, 1, {text_limit}) AS text",
    ]
    bounded_fields = {
        "type": MAX_MEMORY_TYPE + 1,
        "stream": MAX_STREAM + 1,
        "ts": MAX_TIMESTAMP + 1,
        "session_id": MAX_SESSION_ID + 1,
        "scene_id": MAX_FILTER_VALUE + 1,
        "source_type": MAX_FILTER_VALUE + 1,
        "verification_status": MAX_FILTER_VALUE + 1,
        "speaker_focus": MAX_FILTER_VALUE + 1,
        "memory_kind": MAX_FILTER_VALUE + 1,
    }
    for name, limit in bounded_fields.items():
        if name in columns:
            fields.append(f"substr({name}, 1, {limit}) AS {name}")
    for name in (
        "turn",
        "source_reliability",
        "fallback_generated",
        "embedding_blob",
        "embedding_dim",
        "embedding_dtype",
    ):
        if name in columns:
            fields.append(name)
    return ", ".join(fields)


def _fts_expression(query: Optional[str]) -> Optional[str]:
    if not query or not query.strip():
        return None
    tokens = _FTS_TOKEN_RE.findall(query)
    if not tokens:
        return None
    # Quote every token so user text cannot inject FTS operators. AND keeps
    # search deterministic and, importantly, never falls back to all rows.
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _cursor_after_sql(agent: str, cursor: Optional[Cursor]) -> tuple[str, list[Any]]:
    if cursor is None:
        return "", []
    instant = "titan_ts_us(ts)"
    if agent < cursor.source_agent:
        # Global order is timestamp DESC, source DESC, id DESC. A lower
        # source name is therefore later when instants tie.
        return f" AND ({instant} < titan_ts_us(?) OR {instant} = titan_ts_us(?))", [cursor.timestamp, cursor.timestamp]
    if agent == cursor.source_agent:
        return (
            f" AND ({instant} < titan_ts_us(?) OR ({instant} = titan_ts_us(?) AND id < ?))",
            [cursor.timestamp, cursor.timestamp, cursor.memory_id],
        )
    return f" AND {instant} < titan_ts_us(?)", [cursor.timestamp]


def _sqlite_filter_parts(
    connection: sqlite3.Connection,
    source: Source,
    filters: ExplorerFilters,
    cursor: Optional[Cursor],
    warnings: WarningCollector,
    *,
    force_like: bool = False,
) -> tuple[set[str], str, list[Any], Optional[str], str, str]:
    columns = _sqlite_columns(connection)
    if "id" not in columns or "text" not in columns:
        raise ExplorerError("read_error", "memory table is missing required columns")

    clauses: list[str] = [f"length(COALESCE(id, '')) BETWEEN 1 AND {MAX_MEMORY_ID}"]
    params: list[Any] = []
    if filters.type is not None:
        if "type" not in columns:
            clauses.append("0 = 1")
        else:
            clauses.append("LOWER(COALESCE(type, '')) = LOWER(?)")
            params.append(filters.type)
    if "ts" in columns:
        clauses.append(f"length(COALESCE(ts, '')) <= {MAX_TIMESTAMP}")
    if filters.date_from is not None:
        if "ts" not in columns:
            clauses.append("0 = 1")
        else:
            clauses.append("titan_ts_us(ts) >= titan_ts_us(?)")
            params.append(filters.date_from)
    if filters.date_to is not None:
        if "ts" not in columns:
            clauses.append("0 = 1")
        else:
            operator = "<" if filters.date_to_exclusive else "<="
            clauses.append(f"titan_ts_us(ts) {operator} titan_ts_us(?)")
            params.append(filters.date_to)
    cursor_sql, cursor_params = _cursor_after_sql(source.agent, cursor) if "ts" in columns else ("", [])
    if cursor is not None and not cursor_sql:
        clauses.append("0 = 1")
    if cursor_sql:
        clauses.append(cursor_sql.removeprefix(" AND "))
        params.extend(cursor_params)

    expression = _fts_expression(filters.query)
    query_mode = "like" if force_like else "fts"
    if filters.query and filters.query.strip() and expression is None:
        query_mode = "like"
    if expression is not None and not force_like:
        try:
            fts_available = (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories_fts'"
                ).fetchone()
                is not None
            )
        except sqlite3.DatabaseError:
            fts_available = False
        if fts_available:
            clauses.append("m.rowid IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)")
            params.append(expression)
        else:
            query_mode = "like"
    if filters.query and filters.query.strip() and query_mode == "like":
        escaped_query = (
            filters.query.casefold()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append("LOWER(COALESCE(text, '')) LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped_query}%")

    where = " AND ".join(clauses)
    order_by = "titan_ts_us(ts) DESC, id DESC" if "ts" in columns else "rowid DESC"
    return columns, where, params, expression, query_mode, order_by


def _warn_like(source: Source, expression: Optional[str], query_mode: str, warnings: WarningCollector) -> None:
    if expression is not None and query_mode == "like":
        warnings.add(f"Namespace {source.agent} has no FTS index; used bounded text search")


def _query_sqlite(
    source: Source,
    filters: ExplorerFilters,
    cursor: Optional[Cursor],
    limit: int,
    warnings: WarningCollector,
    *,
    force_like: bool = False,
) -> tuple[list[MemoryRecord], bool]:
    connection = _open_read_only(source.db_path)
    try:
        columns, where, params, expression, query_mode, order_by = _sqlite_filter_parts(
            connection, source, filters, cursor, warnings, force_like=force_like
        )
        projection = _sqlite_projection(columns)
        candidate_sql = f"SELECT rowid FROM memories m WHERE {where} ORDER BY {order_by} LIMIT ?"
        sql = f"SELECT {projection} FROM memories WHERE rowid IN ({candidate_sql})"
        rows = connection.execute(sql, [*params, limit + 1]).fetchall()
        _warn_like(source, expression, query_mode, warnings)
        records: list[MemoryRecord] = []
        for row in rows:
            record = _record_from_row(source.agent, row, warnings=warnings)
            if record is not None:
                records.append(record)
        records.sort(key=_record_key, reverse=True)
        if len(records) < limit + 1:
            oversized = connection.execute(
                f"SELECT 1 FROM memories WHERE length(id) > {MAX_MEMORY_ID} LIMIT 1"
            ).fetchone()
            if oversized:
                warnings.add(f"Skipped oversized id values in namespace {source.agent}")
        return records[:limit], len(records) > limit
    except ExplorerError:
        raise
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ExplorerError("database_busy", "memory store is busy") from exc
        if filters.query and "fts" in message and not force_like:
            warnings.add(f"Namespace {source.agent} FTS search unavailable; used bounded text search")
            return _query_sqlite(source, filters, cursor, limit, warnings, force_like=True)
        raise ExplorerError("read_error", "could not query memory store") from exc
    except sqlite3.DatabaseError as exc:
        raise ExplorerError("read_error", "could not query memory store") from exc
    finally:
        connection.close()


def _sqlite_count(
    source: Source,
    filters: ExplorerFilters,
    warnings: WarningCollector,
    *,
    force_like: bool = False,
) -> int:
    connection = _open_read_only(source.db_path)
    try:
        _columns, where, params, expression, query_mode, _order_by = _sqlite_filter_parts(
            connection, source, filters, None, warnings, force_like=force_like
        )
        row = connection.execute(f"SELECT COUNT(*) FROM memories m WHERE {where}", params).fetchone()
        _warn_like(source, expression, query_mode, warnings)
        return int(row[0] if row else 0)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ExplorerError("database_busy", "memory store is busy") from exc
        if filters.query and "fts" in message:
            warnings.add(f"Namespace {source.agent} FTS search unavailable; used bounded text search")
            return _sqlite_count(source, filters, warnings, force_like=True)
        raise ExplorerError("read_error", "could not count memory store") from exc
    except sqlite3.DatabaseError as exc:
        raise ExplorerError("read_error", "could not count memory store") from exc
    finally:
        connection.close()


def _sqlite_candidates(
    source: Source,
    filters: ExplorerFilters,
    limit: int,
    warnings: WarningCollector,
    *,
    force_like: bool = False,
) -> list[StorageCandidate]:
    connection = _open_read_only(source.db_path)
    try:
        columns, where, params, expression, query_mode, order_by = _sqlite_filter_parts(
            connection, source, filters, None, warnings, force_like=force_like
        )
        ts_expression = "substr(ts, 1, 129)" if "ts" in columns else "NULL"
        sql = f"SELECT rowid, substr(id, 1, 257), {ts_expression} FROM memories m WHERE {where} ORDER BY {order_by} LIMIT ?"
        rows = connection.execute(sql, [*params, limit]).fetchall()
        _warn_like(source, expression, query_mode, warnings)
        return [StorageCandidate(source.agent, str(row[1]), row[2], int(row[0])) for row in rows]
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ExplorerError("database_busy", "memory store is busy") from exc
        if filters.query and "fts" in message and not force_like:
            warnings.add(f"Namespace {source.agent} FTS search unavailable; used bounded text search")
            return _sqlite_candidates(source, filters, limit, warnings, force_like=True)
        raise ExplorerError("read_error", "could not query memory store") from exc
    except sqlite3.DatabaseError as exc:
        raise ExplorerError("read_error", "could not query memory store") from exc
    finally:
        connection.close()


def _fetch_sqlite_records(
    source: Source,
    rowids: list[int],
    warnings: WarningCollector,
) -> list[MemoryRecord]:
    if not rowids:
        return []
    connection = _open_read_only(source.db_path)
    try:
        columns = _sqlite_columns(connection)
        projection = _sqlite_projection(columns)
        placeholders = ", ".join("?" for _ in rowids)
        rows = connection.execute(
            f"SELECT {projection} FROM memories WHERE rowid IN ({placeholders})",
            rowids,
        ).fetchall()
        records: list[MemoryRecord] = []
        for row in rows:
            record = _record_from_row(source.agent, row, warnings=warnings)
            if record is not None:
                records.append(record)
        records.sort(key=_record_key, reverse=True)
        return records
    except sqlite3.DatabaseError as exc:
        raise ExplorerError("read_error", "could not fetch memory page") from exc
    finally:
        connection.close()


def _load_json(path: Path) -> list[dict[str, Any]]:
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ExplorerError("store_too_large", "legacy JSON memory store exceeds the read limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ExplorerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExplorerError("read_error", "could not read legacy memory store") from exc
    if not isinstance(payload, list):
        raise ExplorerError("read_error", "legacy memory store is not a memory list")
    return [item for item in payload if isinstance(item, dict)]


def _record_matches(record: MemoryRecord, filters: ExplorerFilters) -> bool:
    if filters.type is not None and (record.memory_type or "").casefold() != filters.type.casefold():
        return False
    timestamp = _timestamp_value(record.timestamp)
    if filters.date_from is not None:
        lower = _timestamp_value(filters.date_from)
        if timestamp is None or lower is None or timestamp < lower:
            return False
    if filters.date_to is not None:
        upper = _timestamp_value(filters.date_to)
        if upper is None or timestamp is None:
            return False
        if filters.date_to_exclusive and timestamp >= upper:
            return False
        if not filters.date_to_exclusive and timestamp > upper:
            return False
    if filters.query:
        return filters.query.casefold() in record.text.casefold()
    return True


def _after_cursor(record: MemoryRecord, cursor: Optional[Cursor]) -> bool:
    if cursor is None:
        return True
    # Global order is timestamp DESC, source DESC, id DESC. Spell out the
    # comparison because a normal tuple ``<`` would reverse the source/id
    # tiebreakers incorrectly.
    left_timestamp = _timestamp_value(record.timestamp)
    right_timestamp = _timestamp_value(cursor.timestamp)
    if left_timestamp is None or right_timestamp is None:
        return False
    if left_timestamp != right_timestamp:
        return left_timestamp < right_timestamp
    if record.source_agent != cursor.source_agent:
        return record.source_agent < cursor.source_agent
    return record.memory_id < cursor.memory_id


def _filtered_json_records(
    source: Source,
    filters: ExplorerFilters,
    warnings: WarningCollector,
) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for raw in _load_json(source.json_path):
        record = _record_from_json(source.agent, raw, warnings=warnings)
        if record is not None and _record_matches(record, filters):
            records.append(record)
    records.sort(key=_record_key, reverse=True)
    return records


def _query_json(
    source: Source,
    filters: ExplorerFilters,
    cursor: Optional[Cursor],
    limit: int,
    warnings: WarningCollector,
) -> tuple[list[MemoryRecord], bool]:
    records = [record for record in _filtered_json_records(source, filters, warnings) if _after_cursor(record, cursor)]
    return records[:limit], len(records) > limit


def _candidate_key(candidate: StorageCandidate) -> tuple[int, str, str]:
    timestamp = _timestamp_value(candidate.timestamp)
    return timestamp if timestamp is not None else -(10**30), candidate.source_agent, candidate.memory_id


def _json_page_candidates(
    source: Source,
    filters: ExplorerFilters,
    limit: int,
    warnings: WarningCollector,
) -> tuple[int, list[StorageCandidate]]:
    records = _filtered_json_records(source, filters, warnings)
    return len(records), [
        StorageCandidate(record.source_agent, record.memory_id, record.timestamp, record=record)
        for record in records[:limit]
    ]


def _catalog_sqlite(source: Source, warnings: WarningCollector) -> tuple[int, set[str]]:
    connection = _open_read_only(source.db_path)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
        ).fetchone()
        if table is None:
            return 0, set()
        count_row = connection.execute("SELECT COUNT(*) FROM memories").fetchone()
        type_rows = connection.execute(
            f"SELECT DISTINCT substr(type, 1, {MAX_MEMORY_TYPE}) FROM memories "
            "WHERE type IS NOT NULL AND type != ''"
        ).fetchall()
        types = {str(row[0]) for row in type_rows if row[0]}
        return int(count_row[0] if count_row else 0), types
    except sqlite3.DatabaseError as exc:
        warnings.add(f"Skipped catalog data for {source.agent}: {exc}")
        return 0, set()
    finally:
        connection.close()


def catalog(home: Path) -> dict[str, Any]:
    warnings = WarningCollector()
    agents: list[dict[str, Any]] = []
    types: set[str] = set()
    for name in discover_agent_names(home, None, warnings):
        source = _source_for_agent(home, name, warnings)
        if source is None:
            agents.append({"id": name, "count": 0})
            continue
        try:
            if source.db_path.is_file():
                count, source_types = _catalog_sqlite(source, warnings)
            else:
                records = _load_json(source.json_path)
                count = sum(1 for record in records if isinstance(record, dict))
                source_types = {
                    bounded
                    for record in records
                    if isinstance(record, dict)
                    for bounded in [_bounded_metadata(record.get("type"), MAX_MEMORY_TYPE)]
                    if bounded
                }
            agents.append({"id": name, "count": count})
            types.update(source_types)
        except ExplorerError as exc:
            warnings.add(f"Skipped catalog data for {name}: {exc.message}")
            agents.append({"id": name, "count": 0})
    ordered_types = sorted(types)
    if len(ordered_types) > MAX_CATALOG_TYPES:
        warnings.add(f"Catalog types limited to {MAX_CATALOG_TYPES}")
        ordered_types = ordered_types[:MAX_CATALOG_TYPES]
    return {"agents": agents, "types": ordered_types, "warnings": warnings.as_list()}


def _validate_page(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 1_000_000:
        raise ExplorerError("invalid_input", "page must be a positive integer")
    return value


def search_page(home: Path, filters: ExplorerFilters, page_value: Any) -> dict[str, Any]:
    page = _validate_page(page_value)
    warnings = WarningCollector()
    sources = discover_sources(home, filters.agent, warnings)
    counts: dict[str, int] = {}
    total = 0
    for source in sources:
        try:
            if source.db_path.is_file():
                count = _sqlite_count(source, filters, warnings)
            else:
                count = len(_filtered_json_records(source, filters, warnings))
            counts[source.agent] = count
            total += count
        except ExplorerError as exc:
            warnings.add(f"Skipped namespace {source.agent}: {exc.message}")
            counts[source.agent] = 0

    total_pages = (total + MAX_SEARCH_PAGE - 1) // MAX_SEARCH_PAGE
    if total == 0 or page > total_pages:
        return {
            "items": [],
            "nextCursor": None,
            "warnings": warnings.as_list(),
            "page": page,
            "totalItems": total,
            "totalPages": total_pages,
        }

    offset = (page - 1) * MAX_SEARCH_PAGE
    candidate_limit = offset + MAX_SEARCH_PAGE
    candidates: list[StorageCandidate] = []
    for source in sources:
        if counts.get(source.agent, 0) == 0:
            continue
        try:
            if source.db_path.is_file():
                candidates.extend(_sqlite_candidates(source, filters, candidate_limit, warnings))
            else:
                _count, source_candidates = _json_page_candidates(source, filters, candidate_limit, warnings)
                candidates.extend(source_candidates)
        except ExplorerError as exc:
            warnings.add(f"Skipped namespace {source.agent}: {exc.message}")
    candidates.sort(key=_candidate_key, reverse=True)
    selected = candidates[offset : offset + MAX_SEARCH_PAGE]

    selected_rowids: dict[str, list[int]] = {}
    for candidate in selected:
        if candidate.rowid is not None:
            selected_rowids.setdefault(candidate.source_agent, []).append(candidate.rowid)
    fetched: dict[tuple[str, str], MemoryRecord] = {}
    for source in sources:
        rowids = selected_rowids.get(source.agent, [])
        if not rowids:
            continue
        try:
            for record in _fetch_sqlite_records(source, rowids, warnings):
                fetched[(record.source_agent, record.memory_id)] = record
        except ExplorerError as exc:
            warnings.add(f"Skipped namespace {source.agent}: {exc.message}")

    items: list[dict[str, Any]] = []
    for candidate in selected:
        record = candidate.record or fetched.get((candidate.source_agent, candidate.memory_id))
        if record is not None:
            items.append(_summary(record))
    return {
        "items": items,
        "nextCursor": None,
        "warnings": warnings.as_list(),
        "page": page,
        "totalItems": total,
        "totalPages": total_pages,
    }


def _decode_cursor(value: Any) -> Optional[Cursor]:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ExplorerError("invalid_input", "cursor is invalid")
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii") + b"===")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        source_value = payload.get("sourceAgent")
        memory_id = payload.get("memoryId")
        timestamp = payload.get("timestamp")
        if not isinstance(source_value, str) or not source_value:
            raise ValueError
        if not isinstance(memory_id, str) or not memory_id or len(memory_id) > MAX_MEMORY_ID:
            raise ValueError
        if not isinstance(timestamp, str) or not timestamp or _timestamp_value(timestamp) is None:
            raise ValueError
        source_agent = _validate_agent(source_value)
        if source_agent is None:
            raise ValueError
        return Cursor(timestamp, source_agent, memory_id)
    except (ExplorerError, ValueError, TypeError, UnicodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ExplorerError("invalid_input", "cursor is invalid") from exc


def _encode_cursor(record: MemoryRecord) -> Optional[str]:
    if not record.timestamp or _timestamp_value(record.timestamp) is None:
        return None
    payload = {
        "timestamp": record.timestamp,
        "sourceAgent": record.source_agent,
        "memoryId": record.memory_id,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _read_page(
    home: Path,
    filters: ExplorerFilters,
    cursor: Optional[Cursor],
    limit: int,
    warnings: WarningCollector,
) -> tuple[list[MemoryRecord], bool]:
    records: list[MemoryRecord] = []
    more = False
    for source in discover_sources(home, filters.agent, warnings):
        try:
            if source.db_path.is_file():
                source_records, source_more = _query_sqlite(source, filters, cursor, limit, warnings)
            elif source.json_path.is_file():
                source_records, source_more = _query_json(source, filters, cursor, limit, warnings)
            else:
                warnings.add(f"Namespace {source.agent} has no readable memory store")
                continue
            records.extend(source_records)
            more = more or source_more
        except ExplorerError as exc:
            warnings.add(f"Skipped namespace {source.agent}: {exc.message}")
    records.sort(key=_record_key, reverse=True)
    # The per-source SQL cursor is conservative; apply it again after merging
    # so JSON and SQLite sources have identical page semantics.
    records = [record for record in records if _after_cursor(record, cursor)]
    if len(records) > limit:
        more = True
    page = records[:limit]
    return page, more


def _similarity_edges(records: list[MemoryRecord], warnings: WarningCollector) -> list[dict[str, Any]]:
    valid = [record for record in records if record.embedding is not None]
    by_dimension: dict[int, list[MemoryRecord]] = {}
    for record in valid:
        by_dimension.setdefault(len(record.embedding or ()), []).append(record)
    if len(by_dimension) > 1:
        warnings.add("Some stored vectors use different dimensions; cross-dimension edges were skipped")

    # Score each unordered pair once. Each endpoint keeps the same candidate
    # ordering used by the old directed loop: score DESC, then other memory
    # ID DESC, with stable input order for exact ties.
    candidates_by_node: dict[str, list[tuple[float, str, str]]] = {}
    for group in by_dimension.values():
        for left_index, left in enumerate(group):
            left_vector = left.embedding or ()
            left_node = _node_id(left)
            for right in group[left_index + 1 :]:
                right_vector = right.embedding or ()
                score = sum(a * b for a, b in zip(left_vector, right_vector))
                if not math.isfinite(score) or score < MIN_SIMILARITY:
                    continue
                right_node = _node_id(right)
                candidates_by_node.setdefault(left_node, []).append((score, right.memory_id, right_node))
                candidates_by_node.setdefault(right_node, []).append((score, left.memory_id, left_node))

    best: dict[tuple[str, str], float] = {}
    for node, node_candidates in candidates_by_node.items():
        node_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for score, _memory_id, other_node in node_candidates[:TOP_K]:
            source, target = sorted((node, other_node))
            best[(source, target)] = max(best.get((source, target), 0.0), float(score))

    edges = [
        {"source": source, "target": target, "kind": "similarity", "weight": round(weight, 6)}
        for (source, target), weight in best.items()
    ]
    edges.sort(key=lambda item: (item["source"], item["target"]))
    if len(edges) > MAX_SNAPSHOT_EDGES:
        warnings.add(f"Graph edges limited to {MAX_SNAPSHOT_EDGES}")
        edges = edges[:MAX_SNAPSHOT_EDGES]
    return edges


def _fit_snapshot_payload(payload: dict[str, Any], warnings: WarningCollector) -> dict[str, Any]:
    payload["partial"] = bool(payload.get("partial"))
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) <= MAX_SNAPSHOT_BYTES:
        return payload

    warnings.add("Graph payload exceeded the byte limit; previews were shortened")
    payload["warnings"] = warnings.as_list()
    for node in payload["nodes"]:
        node["text"] = _bounded_text(node.get("text"), 64)
    payload["partial"] = True
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    while len(encoded) > MAX_SNAPSHOT_BYTES and len(payload["nodes"]) > 1:
        payload["nodes"].pop()
        node_ids = {node["nodeId"] for node in payload["nodes"]}
        payload["edges"] = [
            edge for edge in payload["edges"] if edge["source"] in node_ids and edge["target"] in node_ids
        ]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ExplorerError("payload_too_large", "graph payload exceeds the response limit")
    payload["warnings"] = warnings.as_list()
    return payload


def snapshot(home: Path, filters: ExplorerFilters) -> dict[str, Any]:
    warnings = WarningCollector()
    records, more = _read_page(home, filters, None, MAX_SNAPSHOT_NODES + 1, warnings)
    records = records[:MAX_SNAPSHOT_NODES]
    if more:
        warnings.add(f"Graph limited to {MAX_SNAPSHOT_NODES} memories")
    edges = _similarity_edges(records, warnings)
    payload: dict[str, Any] = {
        "nodes": [_summary(record) for record in records],
        "edges": edges,
        "partial": bool(more or warnings.as_list()),
        "warnings": warnings.as_list(),
    }
    return _fit_snapshot_payload(payload, warnings)


def search(home: Path, filters: ExplorerFilters, cursor_value: Any, page_value: Any = None) -> dict[str, Any]:
    if cursor_value not in (None, "") and page_value is not None:
        raise ExplorerError("invalid_input", "cursor and page are mutually exclusive")
    if page_value is not None:
        return search_page(home, filters, page_value)
    warnings = WarningCollector()
    cursor = _decode_cursor(cursor_value)
    records, more = _read_page(home, filters, cursor, MAX_SEARCH_PAGE, warnings)
    next_cursor = _encode_cursor(records[-1]) if more and records else None
    if more and records and next_cursor is None:
        warnings.add("Search pagination stopped at a record with no valid timestamp")
    return {
        "items": [_summary(record) for record in records],
        "nextCursor": next_cursor,
        "warnings": warnings.as_list(),
    }


def _detail_from_sqlite(source: Source, memory_id: str) -> tuple[MemoryRecord, str]:
    connection = _open_read_only(source.db_path)
    try:
        columns = _sqlite_columns(connection)
        projection = _sqlite_projection(columns, text_limit=MAX_DETAIL_TEXT + 1)
        row = connection.execute(
            f"SELECT {projection} FROM memories WHERE id = ? LIMIT 1",
            (memory_id,),
        ).fetchone()
        if row is None:
            raise ExplorerError("not_found", "memory not found")
        record = _record_from_row(source.agent, row)
        if record is None:
            raise ExplorerError("read_error", "memory record is invalid")
        raw_text = _row_value(row, "text", "")
        return record, str(raw_text or "")
    except ExplorerError:
        raise
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ExplorerError("database_busy", "memory store is busy") from exc
        raise ExplorerError("read_error", "could not read memory detail") from exc
    except sqlite3.DatabaseError as exc:
        raise ExplorerError("read_error", "could not read memory detail") from exc
    finally:
        connection.close()


def _detail_from_json(source: Source, memory_id: str) -> tuple[MemoryRecord, str]:
    for raw in _load_json(source.json_path):
        if str(raw.get("id") or "") != memory_id:
            continue
        record = _record_from_json(source.agent, raw)
        if record is None:
            raise ExplorerError("read_error", "memory record is invalid")
        return record, str(raw.get("text") or "")
    raise ExplorerError("not_found", "memory not found")


def detail(home: Path, source_agent: Any, memory_id: Any) -> dict[str, Any]:
    agent = _validate_agent(source_agent)
    if agent is None:
        raise ExplorerError("invalid_input", "sourceAgent is required")
    if not isinstance(memory_id, str) or not memory_id or len(memory_id) > MAX_MEMORY_ID or "\x00" in memory_id:
        raise ExplorerError("invalid_input", "memoryId is invalid")
    warnings = WarningCollector()
    sources = discover_sources(home, agent, warnings)
    if not sources:
        raise ExplorerError("not_found", "memory namespace not found")
    source = sources[0]
    if source.db_path.is_file():
        record, full_text = _detail_from_sqlite(source, memory_id)
    elif source.json_path.is_file():
        record, full_text = _detail_from_json(source, memory_id)
    else:
        raise ExplorerError("not_found", "memory namespace not found")
    bounded_text, truncated = _truncate_utf8(full_text, MAX_DETAIL_TEXT)
    return {
        "memory": _summary(record),
        "text": bounded_text,
        "metadata": _detail_metadata(record),
        "truncated": truncated,
    }


def handle_request(request: Any, home: Path) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ExplorerError("invalid_input", "request must be an object")
    method = request.get("method")
    if method not in {"catalog", "snapshot", "search", "detail"}:
        raise ExplorerError("invalid_input", "method must be catalog, snapshot, search, or detail")
    if method == "catalog":
        return catalog(home)
    if method == "snapshot":
        return snapshot(home, parse_filters(request.get("filters")))
    if method == "search":
        return search(
            home,
            parse_filters(request.get("filters")),
            request.get("cursor"),
            request.get("page"),
        )
    return detail(home, request.get("sourceAgent"), request.get("memoryId"))


def _error_payload(exc: ExplorerError) -> dict[str, Any]:
    return {"error": {"code": exc.code, "message": exc.message}}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Titan BB explorer data bridge")
    parser.add_argument(
        "--home",
        help="Shared Titan home containing agents/ (defaults to TITAN_HOME or ~/.titan)",
    )
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) > MAX_STDIN_BYTES:
            raise ExplorerError("invalid_input", "request exceeds the input limit")
        request = json.loads(raw.decode("utf-8"))
        result = handle_request(request, resolve_home(args.home))
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        sys.stdout.write("\n")
        return 0
    except ExplorerError as exc:
        sys.stdout.write(json.dumps(_error_payload(exc), ensure_ascii=False, separators=(",", ":")))
        sys.stdout.write("\n")
        return 1
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        error = _error_payload(ExplorerError("invalid_input", "stdin is not valid JSON"))
        sys.stdout.write(json.dumps(error, ensure_ascii=False, separators=(",", ":")))
        sys.stdout.write("\n")
        return 1
    except Exception:
        error = _error_payload(ExplorerError("internal_error", "unexpected explorer failure"))
        sys.stdout.write(json.dumps(error, ensure_ascii=False, separators=(",", ":")))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
