"""Read-only recall across isolated Titan agent namespaces.

Federation is deliberately a read seam.  Each namespace is queried through
the normal repository adapter and the normal retrieval engine; no runtime
environment is changed while another namespace is being inspected.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from app.storage.memories import JsonMemoryRepository, SqliteMemoryRepository, get_memory_repository
from app.storage.repository import MemoryStore
from app.storage.scenes import JsonSceneRepository, SceneRepository, SqliteSceneRepository, get_scene_repository


_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _validate_agent_name(agent_name: str) -> str:
    value = str(agent_name or "").strip()
    if not _AGENT_NAME_RE.fullmatch(value):
        raise ValueError("invalid agent namespace")
    return value


def namespace_memory_db_path(agent_name: str, titan_home: Optional[Path] = None) -> Path:
    """Return the canonical DB path for an agent namespace."""

    safe_agent = _validate_agent_name(agent_name)
    titan_root = (titan_home or (Path.home() / ".titan")).expanduser().resolve()
    root = (titan_root / "agents" / safe_agent).resolve()
    expected_parent = (titan_root / "agents").resolve()
    if expected_parent not in root.parents:
        raise ValueError("agent namespace escaped Titan home")
    return root / "out" / "memories" / "memory_store.db"


def namespace_memories_json_path(agent_name: str, titan_home: Optional[Path] = None) -> Path:
    return namespace_memory_db_path(agent_name, titan_home).with_name("memories.json")


def _scene_json_path_for_db(db_path: Path) -> Path:
    return db_path.with_name("scenes.json")


def _memory_repository_for_path(path: Path) -> Optional[MemoryStore]:
    if path.suffix.lower() == ".json":
        return JsonMemoryRepository(path) if path.exists() else None
    if path.exists():
        return SqliteMemoryRepository(path, initialize=False)
    json_path = path.with_name("memories.json")
    return JsonMemoryRepository(json_path) if json_path.exists() else None


def _scene_repository_for_path(path: Path) -> Optional[SceneRepository]:
    if path.suffix.lower() == ".json":
        return JsonSceneRepository(path) if path.exists() else None
    if path.exists():
        return SqliteSceneRepository(path, initialize=False)
    json_path = _scene_json_path_for_db(path)
    return JsonSceneRepository(json_path) if json_path.exists() else None


def _source_list(active_agent: str, sources: Optional[Sequence[str] | str]) -> list[str]:
    if sources is None:
        selected = [active_agent, "pi"] if active_agent == "codex" else [active_agent]
    elif isinstance(sources, str):
        raw = sources.strip()
        if raw.startswith("["):
            loaded = json.loads(raw)
            if not isinstance(loaded, list):
                raise ValueError("sources must be a list")
            selected = [str(source).strip() for source in loaded if str(source).strip()]
        else:
            selected = [part.strip() for part in re.split(r"[,\n]", raw) if part.strip()]
    else:
        selected = [str(source).strip() for source in sources if str(source).strip()]
    result: list[str] = []
    for source in selected:
        if source not in result:
            result.append(source)
    return result


def _timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


class FederatedRecall:
    """One explicit, read-only seam for multi-agent memory and scene recall."""

    def __init__(
        self,
        *,
        active_agent: str = "codex",
        memory_repositories: Optional[Mapping[str, MemoryStore]] = None,
        scene_repositories: Optional[Mapping[str, SceneRepository]] = None,
        memory_paths: Optional[Mapping[str, Path]] = None,
        scene_paths: Optional[Mapping[str, Path]] = None,
        titan_home: Optional[Path] = None,
        include_active_buffer: Optional[bool] = None,
    ) -> None:
        self.active_agent = str(active_agent or "codex")
        self._memory_repositories = dict(memory_repositories or {})
        self._scene_repositories = dict(scene_repositories or {})
        self._memory_paths = {str(key): Path(value) for key, value in (memory_paths or {}).items()}
        self._scene_paths = {str(key): Path(value) for key, value in (scene_paths or {}).items()}
        self._titan_home = titan_home
        self._include_active_buffer = include_active_buffer

    def sources(self, sources: Optional[Sequence[str] | str] = None) -> list[str]:
        selected = _source_list(self.active_agent, sources)
        for source in selected:
            _validate_agent_name(source)
        return selected

    def _memory_repository(self, source_agent: str) -> Optional[MemoryStore]:
        if source_agent in self._memory_repositories:
            return self._memory_repositories[source_agent]
        if source_agent in self._memory_paths:
            repository = _memory_repository_for_path(self._memory_paths[source_agent])
            if repository is not None:
                self._memory_repositories[source_agent] = repository
            return repository
        # An explicit home is used for every namespace, including the active
        # one.  This keeps isolated/test recalls from reaching the process
        # global repository (which may create or touch its live store).
        if source_agent == self.active_agent and self._titan_home is None:
            return get_memory_repository()
        return _memory_repository_for_path(namespace_memory_db_path(source_agent, self._titan_home))

    def _scene_repository(self, source_agent: str) -> Optional[SceneRepository]:
        if source_agent in self._scene_repositories:
            return self._scene_repositories[source_agent]
        if source_agent in self._scene_paths:
            repository = _scene_repository_for_path(self._scene_paths[source_agent])
            if repository is not None:
                self._scene_repositories[source_agent] = repository
            return repository
        if source_agent == self.active_agent and self._titan_home is None:
            return get_scene_repository()
        return _scene_repository_for_path(namespace_memory_db_path(source_agent, self._titan_home))

    def get_recent_memories(
        self,
        *,
        limit: Optional[int] = 8,
        session_id: Optional[str] = None,
        sources: Optional[Sequence[str] | str] = None,
    ) -> list[dict[str, Any]]:
        selected = self.sources(sources)
        records: list[dict[str, Any]] = []
        for source_agent in selected:
            repository = self._memory_repository(source_agent)
            if repository is None:
                continue
            for record in repository.get_recent_memories(limit=limit, session_id=session_id):
                item = dict(record)
                item["source_agent"] = source_agent
                records.append(item)

        # The active namespace may have freshly captured records waiting for
        # the dedup worker.  Keep the historical recent-memory behavior for
        # that namespace, while never treating the active process buffer as a
        # record from an explicitly requested foreign source.
        use_live_active_buffer = (
            self.active_agent in selected
            and self._titan_home is None
            and (
                self._include_active_buffer
                if self._include_active_buffer is not None
                else (
                    self.active_agent not in self._memory_repositories
                    and self.active_agent not in self._memory_paths
                )
            )
        )
        if use_live_active_buffer:
            try:
                from app.save_pipeline.dedup_buffer import peek_dedup_buffer

                buffer_limit = max((limit // 2) if limit is not None else 100, 2)
                buffered = peek_dedup_buffer(limit=buffer_limit, session_id=session_id)
                for record in buffered:
                    item = dict(record)
                    item.pop("_buffer_ts", None)
                    item["source_agent"] = self.active_agent
                    records.append(item)
            except Exception:
                # A transient/corrupt optional buffer must not make durable
                # namespace recall unavailable.
                pass

        return self._merge_records(records, limit)

    def query_memories(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        limit: int = 8,
        mode: Optional[str] = None,
        sources: Optional[Sequence[str] | str] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return [dict(hit.get("memory") or {}) for hit in self.query_hits(
            query,
            session_id=session_id,
            limit=limit,
            mode=mode,
            sources=sources,
            **kwargs,
        )]

    def query_hits(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        limit: int = 8,
        mode: Optional[str] = None,
        sources: Optional[Sequence[str] | str] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        from app.retrieval_pipeline.retriever import retrieve_memories

        selected = self.sources(sources)
        hits: list[dict[str, Any]] = []
        for source_agent in selected:
            repository = self._memory_repository(source_agent)
            if repository is None:
                continue
            source_hits = retrieve_memories(
                query=query,
                session_id=session_id,
                top_k=limit,
                mode=mode or "both",
                repository=repository,
                **kwargs,
            )
            for hit in source_hits:
                annotated = dict(hit)
                memory = dict(hit.get("memory") or {})
                memory["source_agent"] = source_agent
                annotated["memory"] = memory
                annotated["source_agent"] = source_agent
                hits.append(annotated)

        return self._merge_hits(hits, limit)

    def _merge_records(self, records: list[dict[str, Any]], limit: Optional[int]) -> list[dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for record in records:
            key = _normalized_text(record.get("text")) or f"id:{record.get('source_agent')}:{record.get('id')}"
            current = selected.get(key)
            if current is None or self._prefer(record, current):
                selected[key] = record
        merged = sorted(selected.values(), key=lambda item: _timestamp(item.get("ts")), reverse=True)
        return merged if limit is None else merged[: max(0, int(limit))]

    def _merge_hits(self, hits: list[dict[str, Any]], limit: Optional[int]) -> list[dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for hit in hits:
            memory = hit.get("memory") or {}
            key = _normalized_text(memory.get("text")) or f"id:{hit.get('source_agent')}:{memory.get('id')}"
            current = selected.get(key)
            if current is None or self._prefer_hit(hit, current):
                selected[key] = hit
        merged = sorted(
            selected.values(),
            key=lambda item: (float(item.get("score") or item.get("final_score") or 0.0), _timestamp((item.get("memory") or {}).get("ts"))),
            reverse=True,
        )
        return merged if limit is None else merged[: max(0, int(limit))]

    def _prefer(self, candidate: dict[str, Any], current: dict[str, Any]) -> bool:
        candidate_active = candidate.get("source_agent") == self.active_agent
        current_active = current.get("source_agent") == self.active_agent
        if candidate_active != current_active:
            return candidate_active
        return _timestamp(candidate.get("ts")) >= _timestamp(current.get("ts"))

    def _prefer_hit(self, candidate: dict[str, Any], current: dict[str, Any]) -> bool:
        candidate_active = candidate.get("source_agent") == self.active_agent
        current_active = current.get("source_agent") == self.active_agent
        if candidate_active != current_active:
            return candidate_active
        candidate_score = float(candidate.get("score") or candidate.get("final_score") or 0.0)
        current_score = float(current.get("score") or current.get("final_score") or 0.0)
        if candidate_score != current_score:
            return candidate_score > current_score
        return _timestamp((candidate.get("memory") or {}).get("ts")) >= _timestamp((current.get("memory") or {}).get("ts"))

    def scene_references(
        self,
        memories: Sequence[Mapping[str, Any]],
        *,
        sources: Optional[Sequence[str] | str] = None,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[str]] = {}
        order: list[tuple[str, str]] = []
        allowed = set(self.sources(sources))
        for memory in memories:
            scene_id = str(memory.get("scene_id") or "").strip()
            source_agent = str(memory.get("source_agent") or self.active_agent)
            if not scene_id or source_agent not in allowed:
                continue
            key = f"{source_agent}:{scene_id}"
            if key not in grouped:
                grouped[key] = []
                order.append((source_agent, scene_id))
            grouped[key].append(scene_id)

        result: list[dict[str, Any]] = []
        for source_agent, scene_id in order:
            repository = self._scene_repository(source_agent)
            if repository is None:
                continue
            try:
                refs = repository.get_scene_references([scene_id])
            except Exception:
                refs = []
            for reference in refs:
                item = dict(reference)
                item["source_agent"] = source_agent
                result.append(item)
        return result

    def get_scene_context(self, scene_id: str, *, source_agent: Optional[str] = None) -> dict[str, Any]:
        normalized_scene_id = str(scene_id or "").strip()
        if not normalized_scene_id:
            return {"error": "scene_id is required", "scene_id": normalized_scene_id}
        source = _validate_agent_name(str(source_agent or self.active_agent))
        repository = self._scene_repository(source)
        scene = repository.get_scene(normalized_scene_id) if repository is not None else None
        if not scene:
            return {"error": "scene not found", "scene_id": normalized_scene_id}
        payload = dict(scene)
        payload["source_agent"] = source
        return {"scene": payload}


def get_federated_recall(**kwargs: Any) -> FederatedRecall:
    return FederatedRecall(**kwargs)
