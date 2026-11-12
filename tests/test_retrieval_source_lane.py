from app.retrieval_pipeline import retriever
from app.retrieval_pipeline.federated import FederatedRecall
from app.storage.scenes import JsonSceneRepository


def scenes(tmp_path, name, entries):
    repo = JsonSceneRepository(tmp_path / f'{name}.json')
    repo.append_scenes([
        {'scene_id': f'{name}-{i}', 'session_id': session, 'turn': i,
         'ts': timestamp, 'messages': [{'role': 'user', 'event_id': event, 'content': text}]}
        for i, (session, timestamp, event, text) in enumerate(entries, 1)
    ])
    return repo


def facts(count=8):
    return [{'memory': {'id': str(i), 'text': f'project memory {i}'}, 'score': 1.0} for i in range(count)]


def test_full_fact_results_do_not_starve_source_and_limit_is_preserved(tmp_path, monkeypatch):
    repo = scenes(tmp_path, 'one', [('s', '2026-01-01T12:00:00Z', 'e1', 'project discount is ten percent')])
    monkeypatch.setattr(retriever, '_retrieve_memories_impl', lambda *a, **kw: facts())
    hits = retriever.retrieve_memories('project discount', repository=object(), scene_repository=repo,
                                       source_agent='agent', top_k=8)
    assert len(hits) == 8
    assert hits[-1]['memory']['source_event_ids'] == ['e1']
    assert len([hit for hit in hits if hit['memory'].get('result_kind') == 'source_passage']) == 1
    assert retriever.retrieve_memories('project discount', repository=object(), scene_repository=repo,
                                      source_agent='agent', top_k=1)[0]['memory']['id'] == '0'


def test_source_filters_capture_dates_and_session_but_blocks_unprovable_filters(tmp_path, monkeypatch):
    repo = scenes(tmp_path, 'dated', [
        ('s', '2026-01-01T12:00:00Z', 'old', 'project discount ten percent'),
        ('s', '2026-02-01T23:59:59Z', 'new', 'project discount twelve percent'),
        ('other', '2026-02-01T12:00:00Z', 'other', 'project discount thirteen percent'),
    ])
    monkeypatch.setattr(retriever, '_retrieve_memories_impl', lambda *a, **kw: [])
    options = dict(repository=object(), scene_repository=repo, source_agent='agent', top_k=8)
    hits = retriever.retrieve_memories('project discount', session_id='s', date_from='2026-02-01', date_to='2026-02-01', **options)
    assert [hit['memory']['source_event_ids'] for hit in hits] == [['new']]
    for query, filters in [
        ('project discount', {'date_from': '2026-02-01', 'date_basis': 'event'}),
        ('project discount [2026-02]', {}),
        ('project discount', {'memory_types': ['decision']}),
        ('project discount', {'mode': 'learnings'}),
        ('project discount', {'min_reliability': 0.5}),
        ('project discount', {'min_similarity': 0.5}),
    ]:
        assert retriever.retrieve_memories(query, **options, **filters) == []


def test_federation_retains_foreign_source_when_facts_fill_limit(tmp_path, monkeypatch):
    repo = scenes(tmp_path, 'foreign', [('s', '2026-01-01T00:00:00Z', 'e1', 'project discount ten percent')])
    monkeypatch.setattr(retriever, '_retrieve_memories_impl', lambda *a, **kw: facts())
    recall = FederatedRecall(active_agent='active', memory_repositories={'active': object()},
                             scene_repositories={'foreign': repo})
    hits = recall.query_hits('project discount', sources=['active', 'foreign'], limit=4)
    assert len(hits) == 4
    assert hits[-1]['memory']['source_agent'] == 'foreign'
    assert hits[-1]['memory']['source_event_ids'] == ['e1']


def test_event_dedup_is_namespace_qualified():
    def passage(source, event):
        return {'memory': {'result_kind': 'source_passage', 'source_agent': source,
                           'source_event_ids': [event], 'text': 'project discount'}, 'score': 1}
    results = retriever._combine_source_hits(facts(), [passage('a', 'e'), passage('a', 'e'), passage('b', 'e')], 4)
    assert [hit['memory']['source_agent'] for hit in results[-2:]] == ['a', 'b']


def test_capture_filter_precedes_source_candidate_cap(tmp_path, monkeypatch):
    entries = [
        ('s', '2026-03-01T12:00:00Z', f'outside-{i}', 'project discount invoice')
        for i in range(120)
    ]
    entries.append(('s', '2026-02-01T12:00:00Z', 'inside', 'project discount'))
    repo = scenes(tmp_path, 'crowded', entries)
    monkeypatch.setattr(retriever, '_retrieve_memories_impl', lambda *a, **kw: [])
    hits = retriever.retrieve_memories('project discount invoice', repository=object(),
                                      scene_repository=repo, source_agent='agent', top_k=8,
                                      date_from='2026-02-01', date_to='2026-02-01')
    assert [hit['memory']['source_event_ids'] for hit in hits] == [['inside']]


def test_larger_requested_limit_exposes_more_distinct_source_scenes(tmp_path, monkeypatch):
    repo = scenes(tmp_path, 'items', [
        ('s', '2026-01-01T12:00:00Z', f'e{i}', f'collection count now {i}')
        for i in range(1, 9)
    ])
    monkeypatch.setattr(retriever, '_retrieve_memories_impl', lambda *a, **kw: facts(30))
    options = dict(repository=object(), scene_repository=repo, source_agent='agent')
    normal = retriever.retrieve_memories('collection count', top_k=8, **options)
    expanded = retriever.retrieve_memories('collection count', top_k=20, **options)
    source = lambda hits: [hit['memory'] for hit in hits if hit['memory'].get('result_kind') == 'source_passage']
    assert len(normal) == 8 and len(source(normal)) == 2
    assert len(expanded) == 20 and len(source(expanded)) == 5
    assert len({item['scene_id'] for item in source(expanded)}) == 5
    assert any('now 5' in item['text'] for item in source(expanded))
    assert all(item['source_agent'] == 'agent' for item in source(expanded))


def test_source_passages_fill_unused_result_capacity(tmp_path, monkeypatch):
    repo = scenes(tmp_path, 'fill', [
        ('s', '2026-01-01T12:00:00Z', f'e{i}', 'collection count')
        for i in range(20)
    ])
    current_facts = []
    monkeypatch.setattr(retriever, '_retrieve_memories_impl', lambda *a, **kw: current_facts)
    options = dict(repository=object(), scene_repository=repo, source_agent='agent', top_k=20)
    hits = retriever.retrieve_memories('collection count', **options)
    assert len(hits) == 20
    assert len({hit['memory']['scene_id'] for hit in hits}) == 20
    current_facts.extend(facts(12))
    hits = retriever.retrieve_memories('collection count', **options)
    assert len(hits) == 20
    assert len([hit for hit in hits if hit['memory'].get('result_kind') == 'source_passage']) == 8


def test_federation_restores_scene_diversity_after_ranking(tmp_path, monkeypatch):
    repo = JsonSceneRepository(tmp_path / 'federation.json')
    repo.append_scenes([
        {'scene_id': 'new', 'session_id': 's', 'turn': 1, 'ts': '2026-02-01T00:00:00Z',
         'messages': [{'role': 'user', 'event_id': 'e1', 'content': 'silver boots'},
                      {'role': 'assistant', 'event_id': 'e2', 'content': 'silver boots'}]},
        {'scene_id': 'old', 'session_id': 's', 'turn': 2, 'ts': '2026-01-01T00:00:00Z',
         'messages': [{'role': 'user', 'event_id': 'e3', 'content': 'silver boots'}]},
    ])
    monkeypatch.setattr(retriever, '_retrieve_memories_impl', lambda *a, **kw: facts())
    recall = FederatedRecall(active_agent='agent', memory_repositories={'agent': object()},
                             scene_repositories={'agent': repo})
    hits = recall.query_hits('silver boots', sources=['agent'], limit=8)
    sources = [hit['memory'] for hit in hits if hit['memory'].get('result_kind') == 'source_passage']
    assert [item['scene_id'] for item in sources] == ['new', 'old']
    monkeypatch.setattr(retriever, '_retrieve_memories_impl', lambda *a, **kw: [])
    hits = recall.query_hits('silver boots', sources=['agent'], limit=8)
    assert [hit['memory']['scene_id'] for hit in hits] == ['new', 'old', 'new']
    assert {hit['memory']['role'] for hit in hits if hit['memory']['scene_id'] == 'new'} == {'user', 'assistant'}


def test_scene_diversity_is_source_qualified():
    def passage(source, event):
        return {'memory': {'source_agent': source, 'session_id': 's', 'scene_id': 'same',
                           'source_event_ids': [event]}, 'score': 1}
    hits = retriever._combine_source_hits(facts(),
        [passage('a', 'e1'), passage('a', 'e2'), passage('b', 'e1')], 8)
    assert [(hit['memory']['source_agent'], hit['memory']['source_event_ids'][0]) for hit in hits[-2:]] == [('a', 'e1'), ('b', 'e1')]
