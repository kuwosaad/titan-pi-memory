from __future__ import annotations

from pathlib import Path
import sqlite3
import importlib
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from app.retrieval_pipeline.federated import FederatedRecall, namespace_memory_db_path
from app.storage.memories import SqliteMemoryRepository
import app.storage.memories as storage_memories


class MemoryRepo:
    def __init__(self, recent=None, hits=None):
        self.recent = recent or []
        self.hits = hits or []

    def get_recent_memories(self, limit=8, session_id=None):
        return self.recent[:limit] if limit is not None else list(self.recent)

    def query_candidates(self, filters):
        return []

    def query_candidates_with_text(self, query, filters):
        return []


class SceneRepo:
    def __init__(self, scenes):
        self.scenes = scenes

    def get_scene_references(self, scene_ids):
        return [self.scenes[sid]["reference"] for sid in scene_ids if sid in self.scenes]

    def get_scene(self, scene_id):
        return self.scenes.get(scene_id, {}).get("scene")


def memory(memory_id, text, **extra):
    return {"id": memory_id, "text": text, "ts": extra.pop("ts", "2026-01-01T00:00:00+00:00"), **extra}


def test_codex_defaults_to_codex_and_pi_and_prefers_active_duplicate():
    repos = {"codex": MemoryRepo(recent=[memory("c1", "same text", ts="2026-01-01T00:00:00+00:00")]),
             "pi": MemoryRepo(recent=[memory("p1", "  SAME   TEXT ", ts="2026-02-01T00:00:00+00:00"), memory("p2", "pi only")])}
    recall = FederatedRecall(active_agent="codex", memory_repositories=repos)

    result = recall.get_recent_memories(limit=2)

    assert [item["id"] for item in result] == ["c1", "p2"]
    assert all(item["source_agent"] in {"codex", "pi"} for item in result)


def test_source_override_and_limit_are_applied_after_merge():
    repos = {"codex": MemoryRepo(recent=[memory("c1", "codex")]), "pi": MemoryRepo(recent=[memory("p1", "pi")])}
    recall = FederatedRecall(active_agent="codex", memory_repositories=repos)

    assert [item["source_agent"] for item in recall.get_recent_memories(limit=5, sources=["pi"])] == ["pi"]
    assert len(recall.get_recent_memories(limit=1, sources=["codex", "pi"])) == 1


def test_query_uses_same_retrieval_seam_for_each_source_and_ranks_globally():
    repos = {"codex": MemoryRepo(), "pi": MemoryRepo()}
    recall = FederatedRecall(active_agent="codex", memory_repositories=repos)

    def retrieve(query, **kwargs):
        source = "codex" if kwargs["repository"] is repos["codex"] else "pi"
        score = 0.4 if source == "codex" else 0.9
        return [{"memory": memory(source, source), "score": score}]

    with patch("app.retrieval_pipeline.retriever.retrieve_memories", side_effect=retrieve) as mocked:
        result = recall.query_memories("needle", limit=1)

    assert result[0]["id"] == "pi"
    assert result[0]["source_agent"] == "pi"
    assert "_federated_score" not in result[0]
    assert mocked.call_count == 2


def test_scene_routing_carries_source_agent():
    scenes = {
        "pi-scene": {"scene": {"scene_id": "pi-scene"}, "reference": {"scene_id": "pi-scene"}},
    }
    recall = FederatedRecall(
        active_agent="codex",
        scene_repositories={"pi": SceneRepo(scenes)},
    )

    context = recall.get_scene_context("pi-scene", source_agent="pi")
    refs = recall.scene_references([memory("m", "x", scene_id="pi-scene", source_agent="pi")])

    assert context["scene"]["source_agent"] == "pi"
    assert refs == [{"scene_id": "pi-scene", "source_agent": "pi"}]


def test_namespace_path_is_canonical_and_rejects_traversal(tmp_path: Path):
    assert namespace_memory_db_path("pi", tmp_path) == tmp_path / "agents" / "pi" / "out" / "memories" / "memory_store.db"
    with pytest.raises(ValueError):
        namespace_memory_db_path("../escape", tmp_path)


def test_recent_recall_keeps_active_dedup_buffer(monkeypatch):
    # No injected repository: this models the genuine live active namespace
    # path where the process-local buffer is part of recent recall.
    monkeypatch.setattr("app.retrieval_pipeline.federated.get_memory_repository", lambda: MemoryRepo())
    recall = FederatedRecall(active_agent="codex")
    buffered = memory("pending", "fresh pending memory")
    monkeypatch.setattr(
        "app.save_pipeline.dedup_buffer.peek_dedup_buffer",
        lambda **kwargs: [{**buffered, "_buffer_ts": buffered["ts"]}],
    )

    result = recall.get_recent_memories(limit=8, sources=["codex"])

    assert result == [{**buffered, "source_agent": "codex"}]


def test_injected_repository_does_not_read_live_dedup_buffer(monkeypatch):
    repos = {"codex": MemoryRepo(recent=[memory("c1", "injected")])}
    recall = FederatedRecall(active_agent="codex", memory_repositories=repos)
    monkeypatch.setattr(
        "app.save_pipeline.dedup_buffer.peek_dedup_buffer",
        lambda **kwargs: [memory("live", "must not leak")],
    )

    result = recall.get_recent_memories(limit=8, sources=["codex"])

    assert [item["id"] for item in result] == ["c1"]


def test_storage_wrapper_honors_active_repository_override(monkeypatch):
    record = {
        **memory("s:1:0", "stored decision"),
        "type": "decision",
        "session_id": "s",
        "turn": 1,
        "provenance": {"user": "u", "assistant": "a"},
        "memory_kind": "constraint",
    }

    class StubRepo:
        def get_recent_memories(self, limit=8, session_id=None):
            return [record]

    monkeypatch.setattr(storage_memories, "get_memory_repository", lambda: StubRepo())
    monkeypatch.setattr(
        "app.runtime.context.get_runtime_context",
        lambda: SimpleNamespace(agent_name="codex"),
    )
    monkeypatch.setattr(
        "app.retrieval_pipeline.federated.get_memory_repository",
        lambda: (_ for _ in ()).throw(AssertionError("federated live repository used")),
    )
    monkeypatch.setattr("app.save_pipeline.dedup_buffer.peek_dedup_buffer", lambda **kwargs: [])

    result = storage_memories.get_recent_memories(limit=1)

    assert len(result) == 1
    assert result[0].id == "s:1:0"
    assert result[0].memory_kind == "decision"


def test_storage_override_stays_isolated_when_federation_imports_during_patch(monkeypatch):
    record = {
        **memory("s:1:0", "import-order decision"),
        "session_id": "s",
        "turn": 1,
        "provenance": {"user": "u", "assistant": "a"},
        "memory_kind": "constraint",
        "type": "decision",
    }

    class StubRepo:
        def get_recent_memories(self, limit=8, session_id=None):
            return [record]

    import app.retrieval_pipeline.federated as federated_module

    with monkeypatch.context() as patch:
        patch.setattr(storage_memories, "get_memory_repository", lambda: StubRepo())
        patch.setattr(
            "app.runtime.context.get_runtime_context",
            lambda: SimpleNamespace(agent_name="codex"),
        )
        # Recreate the import-order hazard: federation captures the patched
        # resolver while storage retains its stable production sentinel.
        importlib.reload(federated_module)
        result = storage_memories.get_recent_memories(limit=1)

    # Restore the module's captured resolver for following tests.
    importlib.reload(federated_module)
    assert [item.id for item in result] == ["s:1:0"]


def test_missing_namespace_does_not_create_files(tmp_path: Path):
    missing = tmp_path / "agents" / "pi"
    recall = FederatedRecall(active_agent="codex", titan_home=tmp_path)

    assert recall.get_recent_memories(sources=["pi"]) == []
    assert recall.scene_references([memory("m", "x", scene_id="scene", source_agent="pi")], sources=["pi"]) == []
    assert not missing.exists()


def test_uninitialized_sqlite_namespace_adapter_is_read_only(tmp_path: Path):
    db_path = tmp_path / "present.db"
    SqliteMemoryRepository(db_path).append_memories([])
    adapter = SqliteMemoryRepository(db_path, initialize=False)

    assert adapter.supports_lnn is False
    with pytest.raises(sqlite3.OperationalError):
        adapter.append_memories([memory("s:1:0", "must not write")])
    assert adapter.get_recent_memories() == []


def test_explicit_home_routes_active_namespace_without_live_repository(monkeypatch, tmp_path: Path):
    codex_db = namespace_memory_db_path("codex", tmp_path)
    codex_db.parent.mkdir(parents=True)
    codex_db.touch()
    repo = MemoryRepo(recent=[memory("c1", "isolated")])
    monkeypatch.setattr("app.retrieval_pipeline.federated._memory_repository_for_path", lambda path: repo)
    monkeypatch.setattr(
        "app.retrieval_pipeline.federated.get_memory_repository",
        lambda: (_ for _ in ()).throw(AssertionError("live repository used")),
    )

    recall = FederatedRecall(active_agent="codex", titan_home=tmp_path)

    assert recall.get_recent_memories(sources=["codex"])[0]["id"] == "c1"
