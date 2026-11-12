from pathlib import Path

import pytest

from app.retrieval_pipeline.source_search import MAX_PASSAGE_CHARS, search_source_passages
from app.storage.scenes import JsonSceneRepository, SqliteSceneRepository


@pytest.fixture(params=["json", "sqlite"])
def repo(request, tmp_path: Path):
    if request.param == "json":
        return JsonSceneRepository(tmp_path / "scenes.json")
    return SqliteSceneRepository(tmp_path / "scenes.db")


def scene(scene_id, messages, **extra):
    return {"scene_id": scene_id, "session_id": "session-1", "turn": 1,
            "ts": "2026-01-01T00:00:00Z", "messages": messages, **extra}


def msg(role, text, event_id):
    return {"role": role, "content": text, "event_id": event_id}


def search(repo, query, **kwargs):
    return search_source_passages(query, repository=repo, source_agent="agent-a", **kwargs)


def test_finds_original_detail_and_returns_event_citation(repo):
    repo.append_scenes([
        scene("relevant", [msg("user", "The nebula migration must wait until amber approval.", "event-1")]),
        scene("distractor", [msg("user", "The nebula is bright tonight.", "event-2")]),
    ])
    hits = search(repo, "Why did nebula migration wait for amber approval?")
    assert [hit["scene_id"] for hit in hits] == ["relevant"]
    assert hits[0]["result_kind"] == "source_passage"
    assert hits[0]["source_event_ids"] == ["event-1"]
    assert hits[0]["session_id"] == "session-1"
    assert hits[0]["source_agent"] == "agent-a"
    assert "amber approval" in hits[0]["text"]
    assert "memory" not in hits[0]


def test_does_not_search_metadata_system_or_tool_output(repo):
    repo.append_scenes([scene("secret nebula migration", [
        msg("system", "nebula migration system instruction", "system-event"),
        msg("assistant", "nebula migration recalled old memory", "tool-event"),
        msg("user", "A completely different topic.", "user-event"),
    ], tool_calls=[{"name": "titan_recall", "event_id": "tool-event", "summary": "nebula migration"}],
        extraction_user_text="nebula migration", source_event_ids=["nebula migration"])])
    assert search(repo, "nebula migration") == []


def test_long_message_and_session_filter(repo):
    repo.append_scenes([
        scene("long", [msg("user", "filler " * 1000 + "orchid release needs violet approval", "event-long")]),
        {**scene("other", [msg("user", "orchid release needs violet approval", "event-other")]), "session_id": "session-2"},
    ])
    hits = search(repo, "orchid release violet approval", session_id="session-1")
    assert len(hits) == 1
    assert "violet approval" in hits[0]["text"]
    assert len(hits[0]["text"]) <= MAX_PASSAGE_CHARS
    assert hits[0]["source_event_ids"] == ["event-long"]


def test_explicit_repository_isolation_and_limits(repo, tmp_path):
    other = JsonSceneRepository(tmp_path / "other.json")
    other.append_scenes([scene("other", [msg("user", "saffron railway decision", "other-event")])])
    assert search(repo, "saffron railway") == []
    assert search(other, "saffron railway", limit=0) == []
    assert search(other, "saffron railway")[0]["source_agent"] == "agent-a"


def test_short_compound_names_are_searchable_without_matching_generic_words(repo):
    repo.append_scenes([
        scene('brand', [msg('user', 'I now own five H&M tops.', 'brand-event')]),
        scene('generic', [msg('user', 'I own three other tops.', 'generic-event')]),
    ])
    assert [hit['scene_id'] for hit in search(repo, 'H&M')] == ['brand']
    assert [hit['scene_id'] for hit in search(repo, 'H&M tops')] == ['brand']
    assert search(repo, 'H') == []


def test_distinct_scenes_precede_assistant_echo_and_preserve_secondary_details(repo):
    repo.append_scenes([
        scene('a', [msg('user', 'Silver boots were discounted.', 'user-early'),
                    msg('assistant', 'The silver boots were discounted.', 'assistant-echo')]),
        scene('b', [msg('user', 'I bought silver boots; their original price was $300.', 'price')]),
    ])
    hits = search(repo, 'silver boots', limit=3)
    assert [hit['scene_id'] for hit in hits] == ['a', 'b', 'a']
    assert {hits[0]['source_event_ids'][0], hits[2]['source_event_ids'][0]} == {'user-early', 'assistant-echo'}
    assert '$300' in hits[1]['text']
    assert {hits[0]['role'], hits[2]['role']} == {'user', 'assistant'}


def test_scene_diversity_survives_candidate_cap(repo):
    repo.append_scenes([
        scene('a', [msg('user', 'silver boots', f'echo-{i:03}') for i in range(110)]),
        scene('z', [msg('user', 'silver boots cost $300', 'price')]),
    ])
    assert [hit['source_event_ids'][0] for hit in search(repo, 'silver boots', limit=2)] == ['echo-000', 'price']


def test_short_compound_anchor_is_required_and_whitespace_is_normalized(repo):
    repo.append_scenes([
        scene('brand', [msg('user', 'H & M tops color is red.', 'brand')]),
        scene('other', [msg('user', 'Zara tops color is red.', 'other')]),
        scene('plus', [msg('user', 'A+B shirts are blue.', 'plus')]),
    ])
    assert [hit['scene_id'] for hit in search(repo, 'H&M tops color')] == ['brand']
    assert [hit['scene_id'] for hit in search(repo, 'H & M')] == ['brand']
    assert [hit['scene_id'] for hit in search(repo, 'A + B shirts')] == ['plus']
