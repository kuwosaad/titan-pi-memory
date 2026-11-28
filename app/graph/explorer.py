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
from typing import Any, Iterable, Mapping, Optional


_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Unicode-aware so accented and non-Latin words survive tokenization; the FTS
# table's unicode61 tokenizer handles case and diacritics.
_FTS_TOKEN_RE = re.compile(r"[^\W_][\w./-]*")
_SURROGATE_RE = re.compile("[\ud800-\udfff]")

MAX_SOURCES = 64
MAX_CATALOG_TYPES = 256
MAX_SNAPSHOT_NODES = 150
MAX_SNAPSHOT_EDGES = 450
MAX_SNAPSHOT_BYTES = 480 * 1024
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
MAX_LINKED_EVIDENCE = 12
MAX_ANCILLARY_BYTES = 32 * 1024 * 1024
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
    # Used by the browser graph only; the BB request contract has no session filter.
    session_id: Optional[str] = None


# Explicit store paths that override ``<home>/agents/<agent>/out/memories``
# discovery (the runtime-resolved active store). A ``.json`` path selects the
# legacy JSON backend only; any other path is a SQLite store, with a sibling
# memories.json read only when that database does not exist.
StorePaths = Optional[Mapping[str, Path]]


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
    event_date: Optional[str] = None
    event_start: Optional[str] = None
    event_end: Optional[str] = None

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


def _clean_text(value: Any) -> str:
    """Decode stored bytes leniently and replace lone surrogates.

    Legacy rows can hold invalid UTF-8 or JSON-escaped lone surrogates; either
    would otherwise fail when the response is encoded.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return _SURROGATE_RE.sub("\ufffd", str(value))


def _bounded_text(value: Any, limit: int) -> str:
    text = _clean_text(value or "")
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
    text = _clean_text(value)
    return text if text else None


def _bounded_metadata(value: Any, limit: int) -> Optional[str]:
    text = _optional_string(value)
    return _bounded_text(text, limit) if text is not None else None


def _identifier(value: Any, limit: int = MAX_MEMORY_ID) -> Optional[str]:
    if (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and not any(char in value for char in "\r\n\x00")
        and not _SURROGATE_RE.search(value)
    ):
        return value
    return None


def _evidence_event_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:MAX_LINKED_EVIDENCE] if _identifier(item)]


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
    except (TypeError, ValueError, OverflowError):
        # OverflowError: a legacy infinite value (SQLite REAL Inf, JSON Infinity).
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
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
    except (TypeError, ValueError, OverflowError):
        # OverflowError: a JSON integer component too large for a float.
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
    except (TypeError, ValueError, OverflowError):
        return None
    if dimension <= 0 or dimension > MAX_VECTOR_DIM:
        return None
    if not isinstance(blob, (bytes, bytearray, memoryview)):
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
    memory_id = _identifier(_row_value(row, "id"))
    if not memory_id:
        if warnings:
            warnings.add(f"Skipped a {source_agent} memory with an invalid id")
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
        scene_id=_identifier(_row_value(row, "scene_id"), MAX_FILTER_VALUE),
        source_type=_bounded_metadata(_row_value(row, "source_type"), MAX_FILTER_VALUE),
        source_reliability=_optional_float(_row_value(row, "source_reliability")),
        verification_status=_bounded_metadata(_row_value(row, "verification_status"), MAX_FILTER_VALUE),
        fallback_generated=_optional_bool(_row_value(row, "fallback_generated")),
        speaker_focus=_bounded_metadata(_row_value(row, "speaker_focus"), MAX_FILTER_VALUE),
        memory_kind=_bounded_metadata(_row_value(row, "memory_kind"), MAX_FILTER_VALUE),
        embedding=embedding,
        **{key: _bounded_metadata(_row_value(row, key), MAX_TIMESTAMP) for key in ("event_date", "event_start", "event_end")},
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
    # Validate the raw id (not a cleaned copy) so every node id can be looked up again.
    memory_id = _identifier(str(raw["id"]) if raw.get("id") is not None else None)
    if not memory_id:
        if warnings:
            warnings.add(f"Skipped a {source_agent} memory with an invalid id")
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
        scene_id=_identifier(raw.get("scene_id"), MAX_FILTER_VALUE),
        source_type=_bounded_metadata(raw.get("source_type"), MAX_FILTER_VALUE),
        source_reliability=_optional_float(raw.get("source_reliability")),
        verification_status=_bounded_metadata(raw.get("verification_status"), MAX_FILTER_VALUE),
        fallback_generated=_optional_bool(raw.get("fallback_generated")),
        speaker_focus=_bounded_metadata(raw.get("speaker_focus"), MAX_FILTER_VALUE),
        memory_kind=_bounded_metadata(raw.get("memory_kind"), MAX_FILTER_VALUE),
        embedding=_valid_vector(raw.get("embedding")),
        **{key: _bounded_metadata(raw.get(key), MAX_TIMESTAMP) for key in ("event_date", "event_start", "event_end")},
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
        "event_date": record.event_date,
        "event_start": record.event_start,
        "event_end": record.event_end,
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


def _namespace_names(home: Path, warnings: WarningCollector, stores: StorePaths = None) -> list[str]:
    names = set(stores or ())
    agents_root = home / "agents"
    if agents_root.is_dir():
        try:
            for entry in agents_root.iterdir():
                if not entry.is_dir() or not _AGENT_NAME_RE.fullmatch(entry.name):
                    continue
                if _safe_agent_dir(home, entry.name) is None:
                    warnings.add(f"Skipped unsafe namespace {entry.name}")
                    continue
                names.add(entry.name)
        except OSError as exc:
            warnings.add(f"Could not inspect Titan namespaces: {exc}")
    return sorted(names)


def resolve_agent(home: Path, agent: Any, stores: StorePaths = None) -> Optional[str]:
    """Return the canonical namespace name for ``agent``, or None if unknown.

    An exact match wins; otherwise a single case-insensitive match is used, so
    ``Codex`` and ``codex`` label nodes and correction edges identically even
    on case-insensitive filesystems. Raises ``invalid_input`` for bad names.
    """
    name = _validate_agent(agent)
    if name is None:
        return None
    names = _namespace_names(home, WarningCollector(), stores)
    if name in names:
        return name
    folded = [candidate for candidate in names if candidate.casefold() == name.casefold()]
    return folded[0] if len(folded) == 1 else None


def known_agents(home: Path, stores: StorePaths = None) -> list[str]:
    """Namespaces that have a readable memory store."""
    warnings = WarningCollector()
    return [name for name in _namespace_names(home, warnings, stores) if _has_store(home, name, stores)]


def _has_store(home: Path, name: str, stores: StorePaths = None) -> bool:
    try:
        return _source_for_agent(home, name, WarningCollector(), stores=stores) is not None
    except ExplorerError:
        return False


def discover_agent_names(
    home: Path, agent: Optional[str], warnings: WarningCollector, *, stores: StorePaths = None,
) -> list[str]:
    if agent is not None:
        return [resolve_agent(home, agent, stores) or agent]
    names = _namespace_names(home, warnings, stores)
    if len(names) > MAX_SOURCES:
        warnings.add(f"Namespace list limited to {MAX_SOURCES} sources")
        # Apply the cap after preferring namespaces with a store, so empty or
        # stale directories earlier in the alphabet cannot hide a real agent.
        names = sorted(sorted(names, key=lambda name: not _has_store(home, name, stores))[:MAX_SOURCES])
    return names


def _source_for_agent(
    home: Path, name: str, warnings: WarningCollector, *, stores: StorePaths = None,
) -> Optional[Source]:
    configured = (stores or {}).get(name)
    if configured is not None:
        configured = Path(configured)
        missing = configured.parent / ".missing-memory-store"
        if configured.suffix.lower() == ".json":
            # JSON backend selected: never prefer a stale SQLite file beside it.
            if not configured.is_file():
                warnings.add(f"Namespace {name} has no readable memory store")
                return None
            return Source(name, missing, configured)
        db_path = configured
        json_path = db_path.with_name("memories.json")
        if not db_path.is_file() and not json_path.is_file():
            warnings.add(f"Namespace {name} has no readable memory store")
            return None
        return Source(name, db_path if db_path.is_file() else missing, json_path if json_path.is_file() else missing)
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


def discover_sources(
    home: Path, agent: Optional[str], warnings: WarningCollector, *, stores: StorePaths = None,
) -> list[Source]:
    sources: list[Source] = []
    for name in discover_agent_names(home, agent, warnings, stores=stores):
        source = _source_for_agent(home, name, warnings, stores=stores)
        if source is not None:
            sources.append(source)
    return sources


def _casefold(value: Any) -> Any:
    return value.casefold() if isinstance(value, str) else value


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
    # One undecodable legacy value must not fail the whole namespace query.
    # SQLite hands TEXT to Python as UTF-8 whatever the database encoding.
    connection.text_factory = _clean_text
    connection.execute("PRAGMA busy_timeout = 2000")
    connection.execute("PRAGMA query_only = ON")
    try:
        encoding = str(connection.execute("PRAGMA encoding").fetchone()[0])
    except (sqlite3.DatabaseError, TypeError, IndexError) as exc:
        connection.close()
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ExplorerError("database_busy", "memory store is busy") from exc
        raise ExplorerError("read_error", "could not open memory store") from exc
    codec = {"utf-16le": "utf-16-le", "utf-16be": "utf-16-be"}.get(encoding.casefold(), "utf-8")

    def valid_id(raw: Any) -> int:
        # Receives the id's raw bytes (in the database encoding), so an
        # embedded NUL, a newline or undecodable bytes are seen before any
        # LIMIT: a graph node id is always one detail lookup can find.
        if not isinstance(raw, bytes) or len(raw) > 4 * MAX_MEMORY_ID:
            return 0
        try:
            return int(_identifier(raw.decode(codec)) is not None)
        except UnicodeDecodeError:
            return 0

    connection.create_function("titan_valid_id", 1, valid_id, deterministic=True)
    # SQLite's julianday/strftime paths round fractional seconds. This
    # deterministic scalar gives SQL the same exact UTC-microsecond key used
    # by Python ordering, without schema/index mutations.
    connection.create_function("titan_ts_us", 1, _timestamp_value, deterministic=True)
    # SQLite's LOWER() only folds ASCII; match Python's casefold() instead.
    connection.create_function("titan_casefold", 1, _casefold, deterministic=True)
    return connection


def _sqlite_columns(connection: sqlite3.Connection) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute("PRAGMA table_info(memories)").fetchall()}
    except sqlite3.DatabaseError as exc:
        raise ExplorerError("read_error", "could not inspect memory schema") from exc


def _sqlite_projection(columns: set[str], *, text_limit: int = MAX_DETAIL_TEXT) -> str:
    # Bound untrusted text before it reaches Python. The extra byte lets the
    # detail response detect truncation without loading a giant source field.
    # Text is cut as bytes because SQLite's text substr() stops at an embedded
    # NUL; 4 bytes per character always covers ``text_limit`` characters in
    # UTF-8 or UTF-16, and any partial character lands past that limit (see
    # _clean_text). Casting the cut bytes back to TEXT makes SQLite convert
    # them from the database encoding, so UTF-16 stores reach Python as UTF-8.
    # Ids need no byte cut: titan_valid_id already excluded embedded NULs.
    fields = [
        f"substr(id, 1, {MAX_MEMORY_ID + 1}) AS id",
        f"CAST(substr(CAST(text AS BLOB), 1, {4 * text_limit}) AS TEXT) AS text",
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
        "event_date": MAX_TIMESTAMP + 1,
        "event_start": MAX_TIMESTAMP + 1,
        "event_end": MAX_TIMESTAMP + 1,
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

    # Validate the whole id before ORDER BY/LIMIT so rejected ids cannot take
    # candidate slots or counts. length() and substr() stop at an embedded NUL,
    # so the full bytes go to titan_valid_id (the same _identifier rule used
    # for every node, detail and cursor id). A non-text id would never match
    # the detail lookup's ``id = ?``.
    clauses: list[str] = [
        f"typeof(id) = 'text' AND length(id) BETWEEN 1 AND {MAX_MEMORY_ID}",
        f"titan_valid_id(substr(CAST(id AS BLOB), 1, {4 * MAX_MEMORY_ID + 1}))",
    ]
    params: list[Any] = []
    if filters.type is not None:
        if "type" not in columns:
            clauses.append("0 = 1")
        else:
            clauses.append("titan_casefold(COALESCE(type, '')) = titan_casefold(?)")
            params.append(filters.type)
    if filters.session_id is not None:
        if "session_id" not in columns:
            clauses.append("0 = 1")
        else:
            clauses.append("session_id = ?")
            params.append(filters.session_id)
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
        clauses.append("titan_casefold(COALESCE(text, '')) LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped_query}%")

    where = " AND ".join(clauses)
    # Ordering is by parsed UTC instant, so this scans and sorts every
    # matching row even under a 150-row LIMIT. ``ts`` is the only timestamp
    # column and idx_memories_ts orders its raw text, which disagrees with the
    # instant for legacy rows with other offsets, ``Z`` or naive values
    # (01:00+01:00 sorts after 00:30Z). An index-ordered fast path would need
    # a normalized timestamp column, i.e. a schema migration, so it is
    # deliberately not done in this read-only bridge.
    order_by = "titan_ts_us(ts) DESC, id DESC" if "ts" in columns else "rowid DESC"
    return columns, where, params, expression, query_mode, order_by


def _warn_like(source: Source, expression: Optional[str], query_mode: str, warnings: WarningCollector) -> None:
    if expression is not None and query_mode == "like":
        warnings.add(f"Namespace {source.agent} has no FTS index; used bounded text search")


def _warn_oversized_ids(connection: sqlite3.Connection, source: Source, warnings: WarningCollector) -> None:
    if connection.execute(f"SELECT 1 FROM memories WHERE length(id) > {MAX_MEMORY_ID} LIMIT 1").fetchone():
        warnings.add(f"Skipped oversized id values in namespace {source.agent}")
    invalid = (
        f"SELECT 1 FROM memories WHERE length(id) BETWEEN 1 AND {MAX_MEMORY_ID} AND NOT "
        f"(typeof(id) = 'text' AND titan_valid_id(substr(CAST(id AS BLOB), 1, {4 * MAX_MEMORY_ID + 1}))) LIMIT 1"
    )
    if connection.execute(invalid).fetchone():
        warnings.add(f"Skipped invalid id values in namespace {source.agent}")


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
        # Search pages only return summaries, so load no more text than they show.
        projection = _sqlite_projection(columns, text_limit=MAX_NODE_TEXT)
        candidate_sql = f"SELECT rowid FROM memories m WHERE {where} ORDER BY {order_by} LIMIT ?"
        sql = f"SELECT {projection} FROM memories WHERE rowid IN ({candidate_sql})"
        rows = connection.execute(sql, [*params, limit + 1]).fetchall()
        _warn_like(source, expression, query_mode, warnings)
        records: list[MemoryRecord] = []
        for row in rows:
            record = _record_from_row(source.agent, row, text_limit=MAX_NODE_TEXT, warnings=warnings)
            if record is not None:
                records.append(record)
        records.sort(key=_record_key, reverse=True)
        if len(records) < limit + 1:
            _warn_oversized_ids(connection, source, warnings)
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
        if len(rows) < limit:
            _warn_oversized_ids(connection, source, warnings)
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
    *,
    text_limit: int = MAX_DETAIL_TEXT,
) -> list[MemoryRecord]:
    if not rowids:
        return []
    connection = _open_read_only(source.db_path)
    try:
        columns = _sqlite_columns(connection)
        projection = _sqlite_projection(columns, text_limit=text_limit)
        placeholders = ", ".join("?" for _ in rowids)
        rows = connection.execute(
            f"SELECT {projection} FROM memories WHERE rowid IN ({placeholders})",
            rowids,
        ).fetchall()
        records: list[MemoryRecord] = []
        for row in rows:
            record = _record_from_row(source.agent, row, text_limit=text_limit, warnings=warnings)
            if record is not None:
                records.append(record)
        records.sort(key=_record_key, reverse=True)
        return records
    except sqlite3.DatabaseError as exc:
        raise ExplorerError("read_error", "could not fetch memory page") from exc
    finally:
        connection.close()


def _load_json(path: Path, *, max_bytes: int | None = None) -> list[dict[str, Any]]:
    try:
        if max_bytes is None:
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            with path.open("rb") as stream:
                data = stream.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ExplorerError("store_too_large", "legacy JSON evidence exceeds the read limit")
            payload = json.loads(data)
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
    if filters.session_id is not None and record.session_id != filters.session_id:
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
        # Offer only complete type names: a cropped name would be a filter
        # value that matches nothing.
        type_rows = connection.execute(
            "SELECT DISTINCT type FROM memories WHERE type IS NOT NULL AND type != '' AND length(type) <= ?",
            (MAX_MEMORY_TYPE,),
        ).fetchall()
        if connection.execute("SELECT 1 FROM memories WHERE length(type) > ? LIMIT 1", (MAX_MEMORY_TYPE,)).fetchone():
            warnings.add(f"Type names longer than {MAX_MEMORY_TYPE} characters are not offered as filters")
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
                names = {_optional_string(record.get("type")) for record in records if isinstance(record, dict)}
                source_types = {name for name in names if name and len(name) <= MAX_MEMORY_TYPE}
                if any(name and len(name) > MAX_MEMORY_TYPE for name in names):
                    warnings.add(f"Type names longer than {MAX_MEMORY_TYPE} characters are not offered as filters")
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


def _source_counts(sources: list[Source], filters: ExplorerFilters, warnings: WarningCollector) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        try:
            if source.db_path.is_file():
                counts[source.agent] = _sqlite_count(source, filters, warnings)
            else:
                counts[source.agent] = len(_filtered_json_records(source, filters, warnings))
        except ExplorerError as exc:
            warnings.add(f"Skipped namespace {source.agent}: {exc.message}")
            counts[source.agent] = 0
    return counts


def count_records(
    home: Path, filters: ExplorerFilters, warnings: WarningCollector, *, stores: StorePaths = None,
) -> int:
    """Total memories matching ``filters`` across namespaces."""
    return sum(_source_counts(discover_sources(home, filters.agent, warnings, stores=stores), filters, warnings).values())


def _candidate_records(
    sources: list[Source],
    selected: list[StorageCandidate],
    warnings: WarningCollector,
    *,
    text_limit: int = MAX_DETAIL_TEXT,
) -> list[MemoryRecord]:
    """Load full records for already-selected candidates, keeping their order."""
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
            for record in _fetch_sqlite_records(source, rowids, warnings, text_limit=text_limit):
                fetched[(record.source_agent, record.memory_id)] = record
        except ExplorerError as exc:
            warnings.add(f"Skipped namespace {source.agent}: {exc.message}")
    records: list[MemoryRecord] = []
    for candidate in selected:
        record = candidate.record or fetched.get((candidate.source_agent, candidate.memory_id))
        if record is not None:
            records.append(record)
    return records


def newest_records(
    home: Path,
    filters: ExplorerFilters,
    limit: int,
    warnings: WarningCollector,
    *,
    stores: StorePaths = None,
    text_limit: int = MAX_DETAIL_TEXT,
) -> tuple[list[MemoryRecord], bool]:
    """Return the newest ``limit`` records across namespaces and whether more exist.

    Only lightweight (timestamp, id, rowid) keys are merged across namespaces;
    text and vectors are loaded for the winners alone.
    """
    sources = discover_sources(home, filters.agent, warnings, stores=stores)
    candidates: list[StorageCandidate] = []
    for source in sources:
        try:
            if source.db_path.is_file():
                candidates.extend(_sqlite_candidates(source, filters, limit + 1, warnings))
            else:
                candidates.extend(_json_page_candidates(source, filters, limit + 1, warnings)[1])
        except ExplorerError as exc:
            warnings.add(f"Skipped namespace {source.agent}: {exc.message}")
    candidates.sort(key=_candidate_key, reverse=True)
    records = _candidate_records(sources, candidates[:limit], warnings, text_limit=text_limit)
    return records, len(candidates) > limit


def search_page(home: Path, filters: ExplorerFilters, page_value: Any) -> dict[str, Any]:
    page = _validate_page(page_value)
    warnings = WarningCollector()
    sources = discover_sources(home, filters.agent, warnings)
    counts = _source_counts(sources, filters, warnings)
    total = sum(counts.values())

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
    records = _candidate_records(sources, selected, warnings, text_limit=MAX_NODE_TEXT)
    return {
        "items": [_summary(record) for record in records],
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


def _ancillary_path(source: Source, filename: str) -> Path:
    directory = source.json_path.parent if source.json_path.is_file() else source.db_path.parent
    path = (directory / filename).resolve()
    if path.parent != directory.resolve():
        raise ExplorerError("read_error", "unsafe ancillary store path")
    return path


def _read_relations(source: Source, ids: list[str], warnings: WarningCollector, *, detail: bool) -> tuple[list[dict[str, Any]], bool]:
    if not ids:
        return [], False
    try:
        if source.db_path.is_file():
            conn = _open_read_only(source.db_path)
            try:
                # Cap virtual-machine work as well as result count, including on old
                # databases that lack a reverse (new_id) index.
                steps = 0
                def progress() -> int:
                    nonlocal steps
                    steps += 1
                    return int(steps > 2000)
                conn.set_progress_handler(progress, 1000)
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_supersessions'").fetchone():
                    return [], False
                marks = ",".join("?" for _ in ids)
                # Graph edges need both endpoints visible; filtering only one
                # end let out-of-view links consume the limit.
                joiner = "OR" if detail else "AND"
                condition = f"(old_id IN ({marks}) {joiner} new_id IN ({marks}))"
                params = [source.agent, *ids, *ids, ids[0]] if detail else [source.agent, *ids, *ids]
                ordering = "ORDER BY old_id != ?, created_at DESC, old_id" if detail else "ORDER BY created_at DESC, old_id, new_id"
                limit = MAX_LINKED_EVIDENCE if detail else MAX_SNAPSHOT_EDGES
                rows = conn.execute(
                    f"SELECT substr(old_id,1,257) old_id, substr(new_id,1,257) new_id, "
                    f"substr(evidence,1,1025) evidence, substr(created_at,1,129) created_at "
                    f"FROM memory_supersessions WHERE source = ? AND {condition} {ordering} LIMIT ?",
                    (*params, limit + 1),
                ).fetchall()
                return [dict(row) for row in rows[:limit]], len(rows) > limit
            finally:
                conn.close()
        path = _ancillary_path(source, "memories.json.relations.json")
        if not path.is_file():
            return [], False
        rows = _load_json(path, max_bytes=MAX_ANCILLARY_BYTES)
        wanted = set(ids)
        matches = []
        for r in rows:
            # _identifier also keeps malformed (e.g. unhashable) ids out of the set lookup.
            old, new = _identifier(r.get("old_id")) in wanted, _identifier(r.get("new_id")) in wanted
            if r.get("source") == source.agent and ((old or new) if detail else (old and new)):
                matches.append(r)
        if detail:
            matches.sort(key=lambda link: (link.get("old_id") == ids[0], str(link.get("created_at") or "")), reverse=True)
        else:
            matches.sort(key=lambda link: (str(link.get("old_id")), str(link.get("new_id"))))
            matches.sort(key=lambda link: str(link.get("created_at") or ""), reverse=True)
        limit = MAX_LINKED_EVIDENCE if detail else MAX_SNAPSHOT_EDGES
        return matches[:limit], len(matches) > limit
    except (ExplorerError, OSError, sqlite3.DatabaseError) as exc:
        warnings.add(f"Correction evidence unavailable for {source.agent}: {exc}")
        return [], True


def correction_edges(
    home: Path, memory_keys: Iterable[tuple[str, str]], warnings: WarningCollector, *, stores: StorePaths = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, set[str]] = {}
    for agent, memory_id in memory_keys:
        if _AGENT_NAME_RE.fullmatch(agent) and _identifier(memory_id):
            grouped.setdefault(agent, set()).add(memory_id)
    edges: list[dict[str, Any]] = []
    for agent, ids in sorted(grouped.items()):
        try:
            source = _source_for_agent(home, agent, warnings, stores=stores)
        except ExplorerError as exc:
            warnings.add(f"Skipped namespace {agent}: {exc.message}")
            continue
        if source is None:
            continue
        links, partial = _read_relations(source, sorted(ids), warnings, detail=False)
        if partial:
            warnings.add(f"Correction links limited for {agent}")
        for link in links:
            old, new = _identifier(link.get("old_id")), _identifier(link.get("new_id"))
            if old in ids and new in ids and old != new:
                edges.append({"source": f"{agent}::{new}", "target": f"{agent}::{old}", "kind": "supersedes", "weight": 1})
    edges.sort(key=lambda edge: (edge["source"], edge["target"]))
    return edges


def _detail_corrections(
    source: Source, memory_id: str, warnings: WarningCollector, json_records: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], bool]:
    links, partial = _read_relations(source, [memory_id], warnings, detail=True)
    result = []
    for link in links:
        direction = "replaces" if link.get("new_id") == memory_id else "replacedBy"
        other = _identifier(link.get("old_id") if direction == "replaces" else link.get("new_id"))
        if not other or other == memory_id:
            partial = True
            continue
        try:
            found, _, _ = (_detail_from_sqlite(source, other) if json_records is None else _detail_from_json(source, other, json_records))
            summary = _summary(found)
        except ExplorerError as exc:
            summary = None
            if exc.code != "not_found":
                partial = True
                warnings.add(f"Linked memory unavailable for {source.agent}: {exc.message}")
        result.append({"direction": direction, "memory": summary, "memoryId": other,
                       "evidence": _bounded_text(link.get("evidence"), 1024),
                       "timestamp": _bounded_metadata(link.get("created_at"), MAX_TIMESTAMP)})
    return result, partial


def _scene_detail(source: Source, scene_id: Optional[str], warnings: WarningCollector) -> Optional[dict[str, Any]]:
    if not scene_id:
        return None
    result: dict[str, Any] = {"id": scene_id, "status": "missing", "userText": "", "assistantText": "", "eventIds": []}
    try:
        if source.db_path.is_file():
            conn = _open_read_only(source.db_path)
            try:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scenes'").fetchone():
                    return result
                columns = {row[1] for row in conn.execute("PRAGMA table_info(scenes)")}
                names = {"evidence_status": 32, "evidence_version": 16, "extraction_user_text": 2049,
                         "extraction_assistant_text": 2049, "source_event_ids_json": 8193}
                projection = ", ".join(f"substr({name},1,{limit}) AS {name}" for name, limit in names.items() if name in columns)
                row = conn.execute(f"SELECT {projection or 'scene_id'} FROM scenes WHERE scene_id = ? LIMIT 1", (scene_id,)).fetchone()
                raw = dict(row) if row else None
                if raw is not None:
                    event_json = raw.get("source_event_ids_json") or "[]"
                    try:
                        if len(event_json) > 8192:
                            warnings.add("Source scene event IDs exceed the preview limit")
                        else:
                            raw["source_event_ids"] = json.loads(event_json)
                    except (ValueError, TypeError):
                        raw["evidence_status"] = "partial"
            finally:
                conn.close()
        else:
            path = _ancillary_path(source, "scenes.json")
            raw = next((row for row in _load_json(path, max_bytes=MAX_ANCILLARY_BYTES) if row.get("scene_id") == scene_id), None) if path.is_file() else None
        if raw is None:
            return result
        if raw.get("evidence_status") == "complete" and _optional_int(raw.get("evidence_version")) == 1:
            result["status"] = "complete"
        else:
            result["status"] = "partial"
        result["userText"] = _bounded_text(raw.get("extraction_user_text"), 2048)
        result["assistantText"] = _bounded_text(raw.get("extraction_assistant_text"), 2048)
        event_ids = raw.get("source_event_ids")
        result["eventIds"] = _evidence_event_ids(event_ids)
        if isinstance(event_ids, list):
            if len(result["eventIds"]) < len(event_ids):
                warnings.add("Some source scene event IDs are outside the preview limits")
        return result
    except (ExplorerError, OSError, sqlite3.DatabaseError) as exc:
        warnings.add(f"Scene evidence unavailable for {source.agent}: {exc}")
        result["status"] = "unavailable"
        return result


def _pattern_detail(source: Source, memory_id: str, warnings: WarningCollector) -> list[dict[str, str]]:
    if not source.db_path.is_file():
        return []
    try:
        conn = _open_read_only(source.db_path)
        try:
            steps = 0
            def progress() -> int:
                nonlocal steps
                steps += 1
                return int(steps > 2000)
            conn.set_progress_handler(progress, 1000)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('patterns','pattern_evidence')")}
            if len(tables) < 2:
                return []
            rows = conn.execute(
                "SELECT substr(p.id,1,257), substr(p.title,1,241), substr(p.status,1,65), substr(e.role,1,65) "
                "FROM pattern_evidence e JOIN patterns p ON p.id=e.pattern_id WHERE e.memory_id=? LIMIT ?",
                (memory_id, MAX_LINKED_EVIDENCE + 1),
            ).fetchall()
            if len(rows) > MAX_LINKED_EVIDENCE:
                warnings.add(f"Pattern evidence limited for {source.agent}")
            return [
                {"id": pattern_id, "title": _bounded_text(title, 240), "status": _bounded_text(status, 64), "role": _bounded_text(role, 64)}
                for raw_id, title, status, role in rows[:MAX_LINKED_EVIDENCE]
                if (pattern_id := _identifier(raw_id)) is not None
            ]
        finally:
            conn.close()
    except (ExplorerError, sqlite3.DatabaseError) as exc:
        warnings.add(f"Pattern evidence unavailable for {source.agent}: {exc}")
        return []


def snapshot(home: Path, filters: ExplorerFilters) -> dict[str, Any]:
    warnings = WarningCollector()
    records, more = newest_records(home, filters, MAX_SNAPSHOT_NODES, warnings, text_limit=MAX_NODE_TEXT)
    if more:
        warnings.add(f"Graph limited to {MAX_SNAPSHOT_NODES} memories")
    stored_edges = correction_edges(home, ((record.source_agent, record.memory_id) for record in records), warnings)
    edges = stored_edges + _similarity_edges(records, warnings)
    if len(edges) > MAX_SNAPSHOT_EDGES:
        warnings.add(f"Graph edges limited to {MAX_SNAPSHOT_EDGES}")
        edges = edges[:MAX_SNAPSHOT_EDGES]
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


def _memory_evidence(event_ids: Any, user_text: Any, assistant_text: Any) -> dict[str, Any] | None:
    evidence = {
        "eventIds": _evidence_event_ids(event_ids),
        "userText": _bounded_text(user_text, 2048),
        "assistantText": _bounded_text(assistant_text, 2048),
    }
    return evidence if any(evidence.values()) else None


def _detail_from_sqlite(source: Source, memory_id: str) -> tuple[MemoryRecord, str, dict[str, Any] | None]:
    connection = _open_read_only(source.db_path)
    try:
        columns = _sqlite_columns(connection)
        projection = _sqlite_projection(columns, text_limit=MAX_DETAIL_TEXT + 1)
        for name, limit in (("source_event_ids_json", 8192), ("provenance_user", 2049), ("provenance_assistant", 2049)):
            if name in columns:
                projection += f", substr({name}, 1, {limit}) AS {name}"
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
        try:
            event_ids = json.loads(_row_value(row, "source_event_ids_json") or "[]")
        except (TypeError, ValueError):
            event_ids = []
        evidence = _memory_evidence(event_ids, _row_value(row, "provenance_user"), _row_value(row, "provenance_assistant"))
        return record, _clean_text(raw_text or ""), evidence
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


def _detail_from_json(source: Source, memory_id: str, records: list[dict[str, Any]]) -> tuple[MemoryRecord, str, dict[str, Any] | None]:
    for raw in records:
        # Same identity as _record_from_json, so an id such as 0 still matches.
        if raw.get("id") is None or str(raw["id"]) != memory_id:
            continue
        record = _record_from_json(source.agent, raw)
        if record is None:
            raise ExplorerError("read_error", "memory record is invalid")
        provenance = raw.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        evidence = _memory_evidence(raw.get("source_event_ids"), provenance.get("user"), provenance.get("assistant"))
        return record, _clean_text(raw.get("text") or ""), evidence
    raise ExplorerError("not_found", "memory not found")


def detail(home: Path, source_agent: Any, memory_id: Any, *, stores: StorePaths = None) -> dict[str, Any]:
    agent = _validate_agent(source_agent)
    if agent is None:
        raise ExplorerError("invalid_input", "sourceAgent is required")
    if not _identifier(memory_id):
        raise ExplorerError("invalid_input", "memoryId is invalid")
    warnings = WarningCollector()
    sources = discover_sources(home, agent, warnings, stores=stores)
    if not sources:
        raise ExplorerError("not_found", "memory namespace not found")
    source = sources[0]
    json_records = None
    if source.db_path.is_file():
        record, full_text, evidence = _detail_from_sqlite(source, memory_id)
    elif source.json_path.is_file():
        json_records = _load_json(source.json_path)
        record, full_text, evidence = _detail_from_json(source, memory_id, json_records)
    else:
        raise ExplorerError("not_found", "memory namespace not found")
    bounded_text, truncated = _truncate_utf8(full_text, MAX_DETAIL_TEXT)
    corrections, partial = _detail_corrections(source, memory_id, warnings, json_records)
    scene = _scene_detail(source, record.scene_id, warnings)
    patterns = _pattern_detail(source, memory_id, warnings)
    return {
        "corrections": corrections,
        "correctionsPartial": partial,
        "scene": scene,
        "memoryEvidence": evidence,
        "patterns": patterns,
        "warnings": warnings.as_list(),
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


def _emit(payload: dict, *, allow_nan: bool = True) -> None:
    """Write one JSON line as UTF-8 bytes.

    Text-mode stdout uses the locale encoding, which on Windows pipes is a
    legacy code page that cannot encode most memory text.
    """
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=allow_nan) + "\n"
    sys.stdout.buffer.write(line.encode("utf-8"))
    sys.stdout.flush()


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
        _emit(result, allow_nan=False)
        return 0
    except ExplorerError as exc:
        _emit(_error_payload(exc))
        return 1
    except (UnicodeDecodeError, json.JSONDecodeError):
        _emit(_error_payload(ExplorerError("invalid_input", "stdin is not valid JSON")))
        return 1
    except Exception:
        _emit(_error_payload(ExplorerError("internal_error", "unexpected explorer failure")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
