"""Event-to-scene recovery tests using isolated, real local stores."""

import os

import pytest

from app.runtime.context import reset_runtime_context_cache
from app.save_pipeline import pipeline
from app.storage import memories, scenes, traces
from app.storage.models import TraceEvent


@pytest.fixture
def store(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith('TITAN_'):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('TITAN_HOME', str(tmp_path))
    monkeypatch.setenv('TITAN_BASE_DIR', str(tmp_path))
    monkeypatch.setenv('TITAN_SHARED_HOME', str(tmp_path))
    monkeypatch.setenv('TITAN_MEMORY_BACKEND', 'sqlite')
    reset_runtime_context_cache()
    monkeypatch.setattr(pipeline, 'get_extraction_adapter', lambda: object())
    monkeypatch.setattr(pipeline, 'embed', lambda texts: (_ for _ in ()).throw(OSError('embedding offline')))
    yield tmp_path
    reset_runtime_context_cache()


def add_pair(session, suffix):
    result = []
    for role, content in [
        ('user', 'Please remember the rule for durable event checkpoints.'),
        ('assistant', 'Commit the evidence scene before advancing the checkpoint.'),
    ]:
        event = TraceEvent(session_id=session, event_id=f'{role}-{suffix}',
                           event_type=f'{role}_message', payload={'content': content})
        result.append(pipeline.ingest_trace_event(event, process_new=False))
    return result


def extraction(*args, **kwargs):
    return [{'text': 'Commit the evidence scene before advancing the checkpoint.',
             'type': 'decision', 'stream': 'learnings', 'source': 'assistant'}]


def test_provider_failure_replays_scene_and_preserves_independent_evidence(store, monkeypatch):
    add_pair('session', '1')
    monkeypatch.setattr(pipeline, 'extract_atomic_memories', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('provider offline')))
    pipeline.process_session_events('session')

    scene = scenes.get_session_scenes('session')[0]
    assert scene.source_event_ids == ['user-1', 'assistant-1']
    assert [event['event_id'] for event in scene.raw_events] == scene.source_event_ids
    assert traces.load_retry_queue('session')
    assert memories.get_recent_memories(limit=None, session_id='session') == []
    assert not list(store.rglob('*.md'))

    monkeypatch.setattr(pipeline, 'extract_atomic_memories', extraction)
    pipeline.process_session_events('session')
    saved = memories.get_recent_memories(limit=None, session_id='session')
    assert len(saved) == 1
    assert saved[0].scene_id == scene.scene_id
    assert saved[0].source_event_ids == ['user-1', 'assistant-1']
    assert not saved[0].embedding
    assert not traces.load_retry_queue('session')

    # Repeated transport delivery is idempotent; new events with identical text
    # are independent evidence and remain independently stored.
    assert [item['status'] for item in add_pair('session', '1')] == ['duplicate', 'duplicate']
    pipeline.process_session_events('session')
    assert len(memories.get_recent_memories(limit=None, session_id='session')) == 1
    add_pair('session', '2')
    pipeline.process_session_events('session')
    saved = memories.get_memory_repository().get_recent_memories(limit=None, session_id='session')
    assert len(saved) == 2
    assert {tuple(item['source_event_ids']) for item in saved} == {('user-1', 'assistant-1'), ('user-2', 'assistant-2')}
    assert len({item['scene_id'] for item in saved}) == 2


def test_crash_after_scene_commit_recovers_without_duplicate_scene(store, monkeypatch):
    add_pair('crash', '1')
    monkeypatch.setattr(pipeline, 'extract_atomic_memories', extraction)
    real_append_retry = pipeline.append_retry_entry
    monkeypatch.setattr(pipeline, 'append_retry_entry', lambda entry: (_ for _ in ()).throw(SystemExit('simulated crash')))
    with pytest.raises(SystemExit):
        pipeline.process_session_events('crash')
    original = scenes.get_session_scenes('crash')
    assert len(original) == 1
    assert original[0].source_event_ids == ['user-1', 'assistant-1']
    monkeypatch.setattr(pipeline, 'append_retry_entry', real_append_retry)
    pipeline.process_session_events('crash')
    # A subsequent pass drains any recovered extraction marker.
    pipeline.process_session_events('crash')
    assert [scene.scene_id for scene in scenes.get_session_scenes('crash')] == [original[0].scene_id]
    saved = memories.get_recent_memories(limit=None, session_id='crash')
    assert len(saved) == 1
    assert saved[0].scene_id == original[0].scene_id
    assert saved[0].source_event_ids == ['user-1', 'assistant-1']
    assert not traces.load_retry_queue('crash')


def test_compact_read_recalls_both_streams_without_changing_store(store, monkeypatch):
    from app.retrieval_pipeline import retriever, config
    from app.patterns import retrieval as pattern_retrieval

    add_pair('read', '1')
    monkeypatch.setattr(pipeline, 'extract_atomic_memories', lambda *a, **k: [
        {'text': 'The checkpoint scene must remain durable.', 'stream': 'learnings', 'type': 'decision'},
        {'text': 'The checkpoint scene carries raw evidence.', 'stream': 'rough', 'type': 'fact'},
    ])
    pipeline.process_session_events('read')
    repo = memories.get_memory_repository()
    before = repo.get_recent_memories(limit=None, session_id='read')
    assert len(before) == 2
    settings = {'patterns': {'enabled': False}, 'retrieval_min_similarity': 0,
                'retrieval': {'min_reliability': 0}, 'retrieval_selection': {'enabled': False}}
    monkeypatch.setattr(config, 'load_settings', lambda: settings)
    monkeypatch.setattr(pattern_retrieval, 'load_settings', lambda: settings)
    monkeypatch.setattr(pipeline, 'load_settings', lambda: settings)
    monkeypatch.setattr(pipeline, 'route_query', lambda query: {'use_memory': True, 'mode': 'both'})
    monkeypatch.setattr(retriever, 'embed', lambda texts: (_ for _ in ()).throw(OSError('offline')))
    result = pipeline.retrieve_memory_brief(query='checkpoint scene', detail='compact', max_items=2, max_chars=300)
    assert result['count'] == 2
    assert len(result['brief']) <= 300
    assert result['pattern_refs'] == []
    assert {item['scene_id'] for item in result['memories']} == {before[0]['scene_id']}
    assert all(item['source_agent'] for item in result['memories'])
    assert repo.get_recent_memories(limit=None, session_id='read') == before
