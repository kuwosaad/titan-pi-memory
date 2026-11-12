import pytest

from app.retrieval_pipeline.brief import build_memory_notes
from app.retrieval_pipeline.federated import FederatedRecall
from app.retrieval_pipeline.retriever import retrieve_memories
from app.storage.memories import JsonMemoryRepository, SqliteMemoryRepository, create_memory_record
from app.storage.memory_relations import record_supersession
from app.storage.scenes import JsonSceneRepository


@pytest.fixture(params=[JsonMemoryRepository, SqliteMemoryRepository])
def memory_repo(tmp_path, request):
    return request.param(tmp_path / ('memory.json' if request.param is JsonMemoryRepository else 'memory.db'))


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr('app.retrieval_pipeline.config.load_settings', lambda: {
        'retrieval_top_k': 8, 'retrieval_min_similarity': 0,
        'retrieval': {'min_reliability': 0}, 'retrieval_session_bias': False,
    })
    monkeypatch.setattr('app.retrieval_pipeline.retriever.embed', lambda texts: [[1.0, 0.0] for _ in texts])


def fact(turn, text, **extra):
    return {**create_memory_record(session_id='s', turn=turn, index=0, text=text,
        user_text=text, assistant_text='', scene_id=f'scene-{turn}', memory_kind='decision'),
        'ts': '2026-11-01T00:00:00+00:00', 'embedding': [1.0, 0.0], **extra}


def test_current_and_historical_queries(memory_repo):
    old = fact(1, 'database choice postgres')
    new = fact(2, 'database choice sqlite replaces postgres')
    memory_repo.append_memories([old, new])
    record_supersession(memory_repo, source='agent', old_id=old['id'], new_id=new['id'],
                        evidence='database choice sqlite replaces postgres')
    current = retrieve_memories('database choice', repository=memory_repo, source_agent='agent')
    assert [h['memory']['id'] for h in current] == [new['id']]
    historical = retrieve_memories('database choice history', repository=memory_repo, source_agent='agent')
    assert {h['memory']['correction_status'] for h in historical} == {'current', 'superseded'}
    assert 'superseded' in build_memory_notes(historical, max_chars=2000)


def test_occurrence_date_excludes_unknown_but_explicit_capture_dates_remain_compatible(memory_repo):
    backfill = fact(1, 'project event', event_date='2025-01-12')
    unknown = fact(2, 'project unknown')
    memory_repo.append_memories([backfill, unknown])
    event = retrieve_memories('2025-01-12', repository=memory_repo, source_agent='agent')
    assert [h['memory']['id'] for h in event] == [backfill['id']]
    capture = retrieve_memories('project', repository=memory_repo, source_agent='agent', date_from='2026-11-01', date_to='2026-11-01')
    assert {h['memory']['id'] for h in capture} == {backfill['id'], unknown['id']}
    explicit = retrieve_memories('project', repository=memory_repo, source_agent='agent', date_from='2025-01-12', date_to='2025-01-12', date_basis='event')
    assert [h['memory']['id'] for h in explicit] == [backfill['id']]


def test_federated_source_passage_without_facts_or_embedding_calls(tmp_path, monkeypatch):
    repo = JsonMemoryRepository(tmp_path / 'memory.json')
    scenes = JsonSceneRepository(tmp_path / 'scenes.json')
    scenes.append_scenes([{'scene_id': 'scene', 'session_id': 's', 'turn': 1,
        'ts': '2026-01-01T00:00:00Z', 'messages': [{'role': 'user', 'event_id': 'event',
        'content': 'Nebula migration must wait for amber approval.'}]}])
    monkeypatch.setattr('app.retrieval_pipeline.retriever.embed', lambda _: pytest.fail('unexpected embedding'))
    recall = FederatedRecall(active_agent='agent', memory_repositories={'agent': repo}, scene_repositories={'agent': scenes})
    hits = recall.query_hits('nebula migration amber approval', limit=2)
    assert len(hits) == 1
    passage = hits[0]['memory']
    assert passage['result_kind'] == 'source_passage'
    assert (passage['source_agent'], passage['scene_id'], passage['source_event_ids']) == ('agent', 'scene', ['event'])
    assert len(build_memory_notes(hits, max_chars=200)) <= 200
    assert 'source passage' in build_memory_notes(hits, max_chars=200)


def test_source_does_not_displace_current_fact(memory_repo, tmp_path):
    old = fact(1, 'database choice postgres')
    new = fact(2, 'database choice sqlite replaces postgres')
    memory_repo.append_memories([old, new])
    record_supersession(memory_repo, source='agent', old_id=old['id'], new_id=new['id'],
                        evidence='database choice sqlite replaces postgres')
    scenes = JsonSceneRepository(tmp_path / 'scenes.json')
    scenes.append_scenes([{'scene_id': 'old-scene', 'session_id': 's', 'turn': 1,
        'ts': '2026-01-01T00:00:00Z', 'messages': [{'role': 'user', 'event_id': 'old-event',
        'content': 'database choice postgres'}]}])
    hits = retrieve_memories('database choice', repository=memory_repo, source_agent='agent',
                             scene_repository=scenes, top_k=1)
    assert [hit['memory']['id'] for hit in hits] == [new['id']]


@pytest.mark.parametrize('query', ['project file-2025-01-12.txt', 'project after 2025-01-12', 'project 2025-99-99'])
def test_date_mentions_do_not_silently_filter_facts(memory_repo, query):
    memory_repo.append_memories([fact(1, 'project choice')])
    hits = retrieve_memories(query, repository=memory_repo, source_agent='agent')
    assert [hit['memory']['text'] for hit in hits] == ['project choice']


def test_source_lane_respects_filters_and_month_interval(memory_repo, tmp_path):
    scenes = JsonSceneRepository(tmp_path / 'scenes.json')
    scenes.append_scenes([{'scene_id': 'scene', 'session_id': 's', 'turn': 1,
        'ts': '2026-01-01T00:00:00Z', 'messages': [{'role': 'user', 'event_id': 'event',
        'content': 'nebula migration amber approval'}]}])
    options = {'repository': memory_repo, 'source_agent': 'agent', 'scene_repository': scenes}
    assert retrieve_memories('nebula migration [2025-01]', **options) == []
    captured = retrieve_memories('nebula migration', date_from='2026-01-01', **options)
    assert [hit['memory']['source_event_ids'] for hit in captured] == [['event']]
    assert retrieve_memories('nebula migration', date_from='2026-01-02', **options) == []
    assert retrieve_memories('nebula migration', memory_types=['decision'], **options) == []
    assert retrieve_memories('nebula migration', mode='learnings', **options) == []


def test_occurrence_filter_retries_after_session_bias(memory_repo, monkeypatch):
    monkeypatch.setattr('app.retrieval_pipeline.config.load_settings', lambda: {
        'retrieval_top_k': 8, 'retrieval_min_similarity': 0,
        'retrieval': {'min_reliability': 0}, 'retrieval_session_bias': True,
    })
    unknown = fact(1, 'project unknown')
    matching = fact(2, 'project launch', session_id='other', event_date='2025-01-12')
    memory_repo.append_memories([unknown, matching])
    hits = retrieve_memories('2025-01-12', repository=memory_repo, source_agent='agent', session_id='s')
    assert [hit['memory']['id'] for hit in hits] == [matching['id']]


@pytest.mark.parametrize('detail', ['compact', 'full'])
def test_public_timeline_preserves_source_and_history_labels(monkeypatch, detail):
    from app.save_pipeline.pipeline import retrieve_memory_brief
    passage = {'result_kind': 'source_passage', 'text': 'Old launch quote.', 'scene_id': 'old-scene',
        'source_agent': 'agent', 'source_event_ids': ['event-1'], 'role': 'user', 'ts': '2025-01-01'}
    historical = {'id': 'old-fact', 'text': 'Old launch decision.', 'scene_id': 'fact-scene',
        'source_agent': 'agent', 'correction_status': 'superseded', 'ts': '2025-01-02'}
    monkeypatch.setattr('app.save_pipeline.pipeline.route_query', lambda *args, **kwargs: {
        'use_memory': True, 'mode': 'both', 'top_k': 8, 'summary_mode': 'timeline'})
    monkeypatch.setattr('app.retrieval_pipeline.retriever.retrieve_memories', lambda *args, **kwargs: [
        {'memory': passage}, {'memory': historical}])
    monkeypatch.setattr('app.patterns.retrieval.retrieve_accepted_patterns', lambda *args, **kwargs: [])
    result = retrieve_memory_brief('launch history', detail=detail, max_chars=1000)
    assert 'user source passage' in result['brief']
    assert 'superseded' in result['brief']
    if detail == 'full':
        entries = result['timeline']
        source = next(entry for entry in entries if entry.get('result_kind') == 'source_passage')
        assert source['evidence_ids'] == ['event-1']
        assert (source['scene_id'], source['source_event_ids'], source['role']) == ('old-scene', ['event-1'], 'user')
        assert source['stream'] is None
        assert next(entry for entry in entries if entry['evidence_ids'] == ['old-fact'])['correction_status'] == 'superseded'
        assert 'user source passage' in result['timeline_summary']
        assert 'superseded' in result['timeline_summary']
    else:
        assert 'timeline' not in result
        assert any(mem.get('source_event_ids') == ['event-1'] and mem.get('result_kind') == 'source_passage' for mem in result['memories'])
        assert any(mem.get('correction_status') == 'superseded' for mem in result['memories'])
