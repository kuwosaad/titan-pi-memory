"""Exercise extraction proposals through the real save and storage path."""
import json
import os

import pytest

from app.runtime.context import get_runtime_context
from app.save_pipeline import pipeline
from app.save_pipeline.extraction.adapters import ExtractionAdapter
from app.storage import memories
from app.storage.memories import JsonMemoryRepository, SqliteMemoryRepository
from app.storage.memory_relations import list_supersessions
from app.storage.models import Scene, SceneMessage


class ScriptedAdapter(ExtractionAdapter):
    def __init__(self):
        self.response = []
        self.calls = []

    def chat(self, messages, format_hint=None, temperature=None):
        self.calls.append(messages)
        assert format_hint == "json"
        return json.dumps({"memories": self.response})


@pytest.fixture(params=["sqlite", "json"])
def context(request, tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("TITAN_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TITAN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TITAN_MEMORY_BACKEND", request.param)
    repo = (SqliteMemoryRepository(tmp_path / "memories.db") if request.param == "sqlite"
            else JsonMemoryRepository(tmp_path / "memories.json"))
    adapter = ScriptedAdapter()
    monkeypatch.setattr(memories, "get_memory_repository", lambda: repo)
    monkeypatch.setattr(pipeline, "get_extraction_adapter", lambda: adapter)
    monkeypatch.setattr(pipeline, "embed", lambda texts: [])
    return repo, adapter, get_runtime_context().agent_name


def proposal(text, **extra):
    return {"text": text, "type": "decision", "source": "user", "stream": "learnings",
            "speaker_focus": "user", "memory_kind": "decision", "subject": "Titan",
            "scope": "local storage", "attribute": "database", **extra}


def save(adapter, turn, text, proposed, *, persist_scene=False):
    adapter.response = [proposed]
    event_id = f"event-{turn}"
    scene = Scene(scene_id=f"scene-{turn}", session_id="session", turn=turn,
                  kind="message_exchange", ts="2026-11-11T09:00:00+00:00",
                  source_event_ids=[event_id], anchor_event_id=event_id,
                  messages=[SceneMessage(role="user", content=text, event_id=event_id)],
                  extraction_user_text=text, extraction_assistant_text="")
    result = pipeline.run_memory_pipeline_outcome(session_id="session", turn=turn,
                user_text=text, assistant_text="", scene=scene, persist_scene=persist_scene)
    assert len(result["records"]) == 1
    return result["records"][0]


def test_correction_is_grounded_persisted_and_retry_safe(context):
    repo, adapter, source = context
    old_text = "Titan uses PostgreSQL for local storage."
    old = save(adapter, 1, old_text, proposal(old_text))
    assert (old["subject"], old["scope"], old["attribute"]) == ("Titan", "local storage", "database")
    original = repo.query_by_ids([old["id"]])[old["id"]]
    quote = "Correction: Titan local storage database uses SQLite instead of PostgreSQL."
    new_proposal = proposal("Titan uses SQLite for local storage.", supersedes=old["id"], correction_evidence=quote)
    new = save(adapter, 2, quote, new_proposal)
    comparison = " ".join(message["content"] for message in adapter.calls[-1] if message["role"] == "system")
    assert old["id"] in comparison
    links = list_supersessions(repo, source=source)
    assert [(item["old_id"], item["new_id"], item["evidence"]) for item in links] == [(old["id"], new["id"], quote)]
    assert repo.query_by_ids([old["id"]])[old["id"]]["provenance"] == original["provenance"]
    assert repo.query_by_ids([new["id"]])[new["id"]]["provenance"]["user"] == quote
    assert save(adapter, 2, quote, new_proposal)["id"] == new["id"]
    assert len(repo.load_all_memories()) == 2
    assert list_supersessions(repo, source=source) == links


@pytest.mark.parametrize("failure", ["scope", "quote"])
def test_bad_correction_retains_both(context, failure):
    repo, adapter, source = context
    old = save(adapter, 1, "Titan uses PostgreSQL for local storage.", proposal("Titan uses PostgreSQL for local storage."))
    quote = "Correction: Titan local storage database uses SQLite instead of PostgreSQL."
    fields = {"supersedes": old["id"], "correction_evidence": quote}
    if failure == "scope":
        fields["scope"] = "production storage"
    else:
        fields["correction_evidence"] = "SQLite replaced PostgreSQL yesterday"
    save(adapter, 2, quote, proposal("Titan uses SQLite for local storage.", **fields))
    assert len(repo.load_all_memories()) == 2
    assert list_supersessions(repo, source=source) == []


@pytest.mark.parametrize("date,quote,expected", [
    ("2025-04-13", "Titan switched on 2025-04-13.", "2025-04-13"),
    ("2025-04-12", "Titan switched on 2025-04-13.", None),
    ("2025-04-13", None, None),
    ("2025-04-13", "Titan switched on 2025-04-13 but this is fabricated.", None),
])
def test_event_date_requires_current_exact_quote(context, date, quote, expected):
    repo, adapter, _ = context
    user_text = "Titan switched on 2025-04-13. This is the local storage database decision."
    record = save(adapter, 1, user_text, proposal("Titan switched its local storage database.", event_date=date, event_evidence=quote))
    stored = repo.query_by_ids([record["id"]])[record["id"]]
    assert stored.get("event_date") == expected
    assert stored["ts"][:10] != "2025-04-13"
    if expected:
        assert stored["event_start"].startswith(expected)
        assert stored["event_end"].startswith("2025-04-14")


@pytest.mark.parametrize("quote", [
    'Correction: Titan local storage database previously said "SQLite instead of PostgreSQL".',
    "Correction: If Titan local storage database used SQLite instead of PostgreSQL that would help.",
    "Correction: Titan local storage database also uses SQLite alongside PostgreSQL.",
    "Correction: Titan local storage database uses PostgreSQL. The backup database uses SQLite.",
])
def test_overeager_extractor_does_not_replace_without_clear_correction(context, quote):
    repo, adapter, source = context
    old = save(adapter, 1, "Titan uses PostgreSQL for local storage.", proposal("Titan uses PostgreSQL for local storage."))
    save(adapter, 2, quote, proposal("Titan uses SQLite for local storage.", supersedes=old["id"], correction_evidence=quote))
    assert len(repo.load_all_memories()) == 2
    assert list_supersessions(repo, source=source) == []


def test_retry_recovers_link_after_fact_commit(context, monkeypatch):
    from app.storage import memory_relations
    from app.storage.traces import append_retry_entry, load_retry_queue

    repo, adapter, source = context
    old = save(adapter, 1, "Titan uses PostgreSQL for local storage.", proposal("Titan uses PostgreSQL for local storage."), persist_scene=True)
    quote = "Correction: Titan local storage database uses SQLite instead of PostgreSQL."
    proposed = proposal("Titan uses SQLite for local storage.", supersedes=old["id"], correction_evidence=quote)
    real_record = memory_relations.record_supersession
    def fail_once(*args, **kwargs):
        raise OSError("simulated link commit failure")
    monkeypatch.setattr(memory_relations, "record_supersession", fail_once)
    with pytest.raises(OSError, match="simulated"):
        save(adapter, 2, quote, proposed, persist_scene=True)
    assert len(repo.load_all_memories()) == 2
    assert list_supersessions(repo, source=source) == []
    append_retry_entry({"session_id": "session", "event_id": "event-2", "seq": 2, "reason": "save failed"})
    monkeypatch.setattr(memory_relations, "record_supersession", real_record)
    calls_before_retry = len(adapter.calls)
    result = pipeline._retry_failed_extractions("session")
    assert result["recovered_retries"] == 1
    assert result["retried_memories"] == 0
    assert len(adapter.calls) == calls_before_retry
    assert len(repo.load_all_memories()) == 2
    assert [(link["old_id"], link["new_id"]) for link in list_supersessions(repo, source=source)] == [(old["id"], "scene-2:memory:0")]
    assert load_retry_queue(session_id="session") == []


@pytest.mark.parametrize("user_text,evidence", [
    ("If we said Correction: Titan local storage database uses SQLite instead of PostgreSQL, would that be accurate?",
     "Correction: Titan local storage database uses SQLite instead of PostgreSQL"),
    ("Correction: Titan local storage database does not use SQLite instead of PostgreSQL.",
     "Correction: Titan local storage database does not use SQLite instead of PostgreSQL."),
])
def test_cropped_hypothetical_and_negation_do_not_supersede(context, user_text, evidence):
    repo, adapter, source = context
    old_text = "Titan uses PostgreSQL for local storage."
    old = save(adapter, 1, old_text, proposal(old_text))
    save(adapter, 2, user_text, proposal("Titan uses SQLite for local storage.",
        supersedes=old["id"], correction_evidence=evidence))
    assert len(repo.load_all_memories()) == 2
    assert list_supersessions(repo, source=source) == []


def test_wrong_entity_quote_does_not_supersede(context):
    repo, adapter, source = context
    old_text = "Titan uses PostgreSQL for local storage."
    old = save(adapter, 1, old_text, proposal(old_text))
    quote = "Correction: Nebula local storage database uses SQLite instead of PostgreSQL."
    save(adapter, 2, quote, proposal("Titan uses SQLite for local storage.",
        supersedes=old["id"], correction_evidence=quote))
    assert len(repo.load_all_memories()) == 2
    assert list_supersessions(repo, source=source) == []
