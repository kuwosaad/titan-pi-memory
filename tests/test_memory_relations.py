import json

import pytest

from app.storage.memories import JsonMemoryRepository, SqliteMemoryRepository, create_memory_record
from app.storage.memory_relations import filter_current, history, list_supersessions, record_supersession


@pytest.fixture(params=["sqlite", "json"])
def store(request, tmp_path):
    if request.param == "sqlite":
        path = tmp_path / "memory.db"
        return SqliteMemoryRepository(path), lambda: SqliteMemoryRepository(path)
    path = tmp_path / "memories.json"
    return JsonMemoryRepository(path), lambda: JsonMemoryRepository(path)


def record(index, text, *, speaker="user", evidence=None):
    return create_memory_record(
        session_id="s", turn=index, index=0, text=text,
        user_text=evidence or text, assistant_text="", scene_id=f"scene-{index}",
        speaker_focus=speaker, memory_kind="decision",
    )


def link(repo, old, new, evidence):
    return record_supersession(repo, source="agent", old_id=old["id"], new_id=new["id"], evidence=evidence)


def test_persistent_history_retry_and_current_view(store):
    repo, reopen = store
    old = record(1, "Postgres")
    new = record(2, "SQLite", evidence="SQLite replaces Postgres")
    newer = record(3, "DuckDB", evidence="DuckDB replaces SQLite")
    repo.append_memories([old, new, newer])
    first = link(repo, old, new, "SQLite replaces Postgres")
    assert link(reopen(), old, new, "SQLite replaces Postgres") == first
    link(repo, new, newer, "DuckDB replaces SQLite")
    restored = reopen()
    assert len(restored.load_all_memories()) == 3
    assert [r["id"] for r in filter_current(restored, [old, new, newer], source="agent")] == [newer["id"]]
    assert len(history(restored, source="agent", memory_id=new["id"])) == 2
    assert list_supersessions(restored, source="other") == []
    assert filter_current(restored, [{**old, "source_agent": "other"}], source="agent") == [{**old, "source_agent": "other"}]


def test_rejects_invalid_unsupported_competing_and_cycles(store):
    repo, _ = store
    old = record(1, "old")
    new = record(2, "new", evidence="new replaces old")
    rival = record(3, "rival", evidence="rival replaces old")
    wrong_scope = record(4, "different person", speaker="assistant")
    repo.append_memories([old, new, rival, wrong_scope])
    with pytest.raises(ValueError):
        link(repo, old, wrong_scope, "different person")
    with pytest.raises(ValueError):
        link(repo, old, new, "fabricated excerpt")
    with pytest.raises(ValueError):
        link(repo, old, {"id": "missing"}, "x")
    link(repo, old, new, "new replaces old")
    with pytest.raises(ValueError):
        link(repo, old, rival, "rival replaces old")
    with pytest.raises(ValueError):
        link(repo, new, old, "old")
    assert len(list_supersessions(repo, source="agent")) == 1


def test_json_rejects_ambiguous_legacy_ids_and_corrupt_history(tmp_path):
    path = tmp_path / "memories.json"
    old, new = record(1, "old"), record(2, "new")
    path.write_text(json.dumps([old, old, new]))
    repo = JsonMemoryRepository(path)
    with pytest.raises(ValueError, match="ambiguous"):
        link(repo, old, new, "new")
    path.write_text(json.dumps([old, new]))
    path.with_name(path.name + ".relations.json").write_text("invalid")
    with pytest.raises(ValueError):
        link(repo, old, new, "new")
