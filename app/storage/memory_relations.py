"""Explicit, additive correction links scoped to one memory repository.

Callers must establish that a claim explicitly replaces the target; similarity
alone is not evidence of replacement. Links never modify or delete memories.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterator

from .memories import JsonMemoryRepository, SqliteMemoryRepository, _MEMORIES_LOCK
from .sessions import atomic_write_text, interprocess_lock

_SCOPE_FIELDS = ("scope", "project", "project_id", "project_path", "repository", "entity_id", "subject", "attribute", "speaker_focus", "memory_kind")
_DDL = """CREATE TABLE IF NOT EXISTS memory_supersessions (
 source TEXT NOT NULL, old_id TEXT NOT NULL, new_id TEXT NOT NULL,
 evidence TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY (source, old_id), UNIQUE (source, old_id, new_id)
)"""


def _source(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source must identify the memory namespace")
    return value.strip()


def _validate(records: list[dict[str, Any]], source: str, old_id: str, new_id: str, evidence: str) -> None:
    if not old_id or not new_id or old_id == new_id:
        raise ValueError("a correction needs distinct memory IDs")
    found = [[r for r in records if str(r.get("id") or "") == ident] for ident in (old_id, new_id)]
    if any(len(items) != 1 for items in found):
        raise ValueError("correction target or replacement is missing or ambiguous")
    old, new = found[0][0], found[1][0]
    if any(r.get("source_agent") and r["source_agent"] != source for r in (old, new)):
        raise ValueError("memory source does not match correction source")
    for field in _SCOPE_FIELDS:
        if old.get(field) != new.get(field) and (old.get(field) is not None or new.get(field) is not None):
            raise ValueError(f"correction scope mismatch: {field}")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("correction requires a current source excerpt")
    provenance = new.get("provenance") or {}
    if not isinstance(provenance, dict) or not any(evidence in str(provenance.get(k) or "") for k in ("user", "assistant")):
        raise ValueError("evidence is not present in replacement provenance")
    if not new.get("scene_id") and not new.get("source_event_ids"):
        raise ValueError("replacement must have durable current evidence")


def _check_links(links: list[dict[str, Any]], source: str, old_id: str, new_id: str, evidence: str) -> dict[str, Any] | None:
    scoped = [link for link in links if link["source"] == source]
    for link in scoped:
        if link["old_id"] == old_id:
            if link["new_id"] == new_id and link["evidence"] == evidence:
                return link
            raise ValueError("target already has a competing correction")
    next_ids = {link["old_id"]: link["new_id"] for link in scoped}
    if new_id in next_ids:
        raise ValueError("replacement is already superseded")
    seen = {old_id}
    current = new_id
    while current in next_ids:
        if current in seen:
            raise ValueError("correction would create a cycle")
        seen.add(current)
        current = next_ids[current]
    if current in seen:
        raise ValueError("correction would create a cycle")
    return None


def _json_path(repo: JsonMemoryRepository) -> Path:
    return repo._path.with_name(repo._path.name + ".relations.json")


def _read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("invalid correction history")
    return data


@contextmanager
def _json_write_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _MEMORIES_LOCK, interprocess_lock(path.with_name(path.name + ".lock")):
        yield



def record_supersession(repo: Any, *, source: str, old_id: str, new_id: str, evidence: str) -> dict[str, Any]:
    """Persist a grounded explicit replacement, rejecting stale/ambiguous targets."""
    source = _source(source)
    if not isinstance(old_id, str) or not isinstance(new_id, str):
        raise ValueError("memory IDs must be strings")
    if isinstance(repo, SqliteMemoryRepository):
        if repo._read_only:
            raise ValueError("cannot write correction to a read-only repository")
        with repo._lock, repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(_DDL)
            rows = conn.execute("SELECT * FROM memories WHERE id IN (?, ?)", (old_id, new_id)).fetchall()
            records = [repo._row_to_memory(row, decode_embedding=False) for row in rows]
            _validate(records, source, old_id, new_id, evidence)
            links = [dict(row) for row in conn.execute("SELECT * FROM memory_supersessions WHERE source = ?", (source,))]
            existing = _check_links(links, source, old_id, new_id, evidence)
            if existing:
                return existing
            link = dict(source=source, old_id=old_id, new_id=new_id, evidence=evidence, created_at=datetime.now(timezone.utc).isoformat())
            conn.execute("INSERT INTO memory_supersessions VALUES (:source, :old_id, :new_id, :evidence, :created_at)", link)
            return link
    if isinstance(repo, JsonMemoryRepository):
        path = _json_path(repo)
        with _json_write_lock(path):
            _validate(repo.load_all_memories(), source, old_id, new_id, evidence)
            links = _read_json(path)
            existing = _check_links(links, source, old_id, new_id, evidence)
            if existing:
                return existing
            link = dict(source=source, old_id=old_id, new_id=new_id, evidence=evidence, created_at=datetime.now(timezone.utc).isoformat())
            links.append(link)
            atomic_write_text(path, json.dumps(links, ensure_ascii=False, indent=2))
            return link
    raise TypeError("unsupported memory repository")


def list_supersessions(repo: Any, *, source: str) -> list[dict[str, Any]]:
    source = _source(source)
    if isinstance(repo, SqliteMemoryRepository):
        with repo._lock, repo._connect() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_supersessions'").fetchone()
            if not exists:
                return []
            return [dict(row) for row in conn.execute("SELECT * FROM memory_supersessions WHERE source = ? ORDER BY created_at, old_id", (source,))]
    if isinstance(repo, JsonMemoryRepository):
        return [link for link in _read_json(_json_path(repo)) if link["source"] == source]
    raise TypeError("unsupported memory repository")


def filter_current(repo: Any, records: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    """Filter only records belonging to the named source, retaining all history in storage."""
    superseded = {link["old_id"] for link in list_supersessions(repo, source=source)}
    return [r for r in records if (r.get("source_agent") or source) != source or str(r.get("id") or "") not in superseded]


def history(repo: Any, *, source: str, memory_id: str) -> list[dict[str, Any]]:
    """Return all correction edges connected to a memory, in creation order."""
    links = list_supersessions(repo, source=source)
    connected = {memory_id}
    while True:
        before = len(connected)
        for link in links:
            if link["old_id"] in connected or link["new_id"] in connected:
                connected.update((link["old_id"], link["new_id"]))
        if len(connected) == before:
            return [link for link in links if link["old_id"] in connected and link["new_id"] in connected]
