import sqlite3

import pytest

from app.storage.memories import JsonMemoryRepository, SqliteMemoryRepository
from app.storage.models import Memory
from app.storage.repository import CandidateFilters


FIELDS = {
    "event_date": "2026-11-11",
    "event_start": "2026-11-11T09:00:00Z",
    "event_end": "2026-11-11T10:00:00Z",
    "subject": "Nora",
    "scope": "work",
    "attribute": "schedule",
}
INTENT = {"correction_target": "session:0:0", "correction_evidence": "I meant Tuesday"}
ALL_FIELDS = {**FIELDS, **INTENT}
FILTERS = CandidateFilters(None, None, False, None, "all", 0.0)


def record(**changes):
    return {
        "id": "session:1:0", "text": "Meeting", "ts": "2026-11-11T00:00:00+00:00",
        "session_id": "session", "turn": 1, "scene_id": "scene",
        "provenance": {"user": "Meeting", "assistant": ""}, **ALL_FIELDS, **changes,
    }


@pytest.mark.parametrize("adapter,filename", [(JsonMemoryRepository, "memories.json"), (SqliteMemoryRepository, "memories.db")])
def test_fields_persist_reopen_and_distinguish_evidence(tmp_path, adapter, filename):
    path = tmp_path / filename
    repo = adapter(path)
    first = record(subject=" Nora ")
    saved = repo.append_memories([first])[0]
    assert {k: saved[k] for k in ALL_FIELDS} == ALL_FIELDS
    repo = adapter(path)
    assert {k: repo.query_by_ids([first["id"]])[first["id"]][k] for k in ALL_FIELDS} == ALL_FIELDS
    assert {k: repo.query_candidates(FILTERS)[0][k] for k in ALL_FIELDS} == ALL_FIELDS
    retry = record(id="session:2:0", turn=2)
    assert repo.append_memories([retry])[0]["id"] == first["id"]
    changed = record(id="session:3:0", turn=3, event_start="2026-11-11T09:30:00Z")
    assert repo.append_memories([changed])[0]["id"] == changed["id"]
    intent_changed = record(id="session:4:0", turn=4, correction_evidence="I meant Wednesday")
    assert repo.append_memories([intent_changed])[0]["id"] == intent_changed["id"]
    assert repo.get_memory_count() == 3
    with pytest.raises(ValueError, match="collision"):
        repo.append_memories([record(event_start="2026-11-11T08:00:00Z")])
    if isinstance(repo, SqliteMemoryRepository):
        candidate = repo._query_retrieval_candidates(FILTERS)[0]
        assert all(k in candidate for k in ALL_FIELDS)
        hydrated = repo._hydrate_retrieval_candidates([first["id"]])[first["id"]]
        assert {k: hydrated[k] for k in ALL_FIELDS} == ALL_FIELDS
    public = Memory(**saved).model_dump()
    assert {k: public[k] for k in FIELDS} == FIELDS
    assert all(k not in public for k in INTENT)


def test_old_sqlite_read_only_and_additive_migration(tmp_path):
    path = tmp_path / "old.db"
    repo = SqliteMemoryRepository(path)
    repo.append_memories([record(**{k: None for k in ALL_FIELDS})])
    with sqlite3.connect(path) as conn:
        for field in ALL_FIELDS:
            conn.execute(f"ALTER TABLE memories DROP COLUMN {field}")
    readonly = SqliteMemoryRepository(path, initialize=False)
    assert all(readonly.query_by_ids(["session:1:0"])["session:1:0"][k] is None for k in ALL_FIELDS)
    assert all(readonly._query_retrieval_candidates(FILTERS)[0][k] is None for k in ALL_FIELDS)
    assert all(readonly._hydrate_retrieval_candidates(["session:1:0"])["session:1:0"][k] is None for k in ALL_FIELDS)
    migrated = SqliteMemoryRepository(path)
    migrated.append_memories([record(id="session:2:0", scene_id="other")])
    reopened = SqliteMemoryRepository(path, initialize=False)
    assert {k: reopened.query_by_ids(["session:2:0"])["session:2:0"][k] for k in ALL_FIELDS} == ALL_FIELDS
