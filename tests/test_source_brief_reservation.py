from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.retrieval_pipeline.brief import build_memory_notes
from app.retrieval_pipeline.retriever import _combine_source_hits
from app.save_pipeline.pipeline import retrieve_memory_brief


def fact(i, **extra):
    return {'memory': {'id': f'm{i}', 'text': f'Garden note {i}', **extra}, 'score': 1}


def passage(i, *, source='codex', text=None, session=None):
    return {'memory': {'id': f'p{i}', 'result_kind': 'source_passage',
                       'text': text or f'Garden source {i}', 'source_agent': source,
                       'scene_id': f'scene{i}', 'session_id': session or f'session{i}',
                       'source_event_ids': [f'event{i}'], 'role': 'user', 'ts': '2026-01-01'},
            'score': 1}


def assembled():
    return _combine_source_hits([fact(i) for i in range(20)], [passage(i) for i in range(5)], 20)


def test_source_reservation_survives_item_and_character_budgets():
    hits = assembled()
    included = []
    brief = build_memory_notes(hits, max_items=6, max_chars=700, included_hits=included)
    assert len(brief) <= 700
    assert len(included) == 6
    assert sum(h['memory'].get('result_kind') == 'source_passage' for h in included) == 2
    assert all(h['memory']['text'] in brief for h in included)


def test_long_reserved_passage_skipped_and_facts_fill_remaining_capacity():
    hits = [fact(i) for i in range(10)] + [passage(1, text='x' * 1000), passage(2)]
    included = []
    brief = build_memory_notes(hits, max_items=6, max_chars=300, included_hits=included)
    assert len(brief) <= 300
    assert 'Garden source 2' in brief
    assert all(h['memory']['id'] != 'p1' for h in included)
    assert any(h['memory']['id'].startswith('m') for h in included)


def test_controls_and_clustered_source_boundaries():
    hits = [fact(i) for i in range(8)]
    assert build_memory_notes(hits, max_items=1).endswith('Garden note 0')
    mixed = hits + [passage(1)]
    assert 'Garden note 0' in build_memory_notes(mixed, max_items=1)
    assert build_memory_notes(mixed, max_items=0) == ''
    assert build_memory_notes(mixed, max_chars=0) == ''
    first, second = passage(1, source='a', session='one'), passage(2, source='b', session='two')
    clustered = [fact(i, text=f'Cluster fact {i}') for i in range(8)] + [first, second]
    for h in clustered[:2]:
        h.update(cluster_id=0, cluster_size=2)
    included = []
    brief = build_memory_notes(clustered, max_items=4, max_chars=700, cluster_mode=True, included_hits=included)
    assert 'a · user source passage' in brief and 'b · user source passage' in brief
    assert 'INSIGHT 1' in brief
    assert [h['memory']['source_event_ids'] for h in included if h['memory'].get('result_kind') == 'source_passage'] == [['event1'], ['event2']]


@pytest.mark.parametrize('federated', [False, True])
def test_compact_pointers_match_rendered_and_full_keeps_all(monkeypatch, federated):
    hits = assembled()
    recall = Mock()
    recall.sources.return_value = ['codex', 'other'] if federated else ['codex']
    recall.query_hits.return_value = hits
    recall.scene_references.return_value = []
    monkeypatch.setattr('app.runtime.context.get_runtime_context', lambda: SimpleNamespace(agent_name='codex'))
    monkeypatch.setattr('app.retrieval_pipeline.federated.get_federated_recall', lambda **kw: recall)
    monkeypatch.setattr('app.retrieval_pipeline.retriever.retrieve_memories', lambda *a, **kw: hits)
    monkeypatch.setattr('app.save_pipeline.pipeline.route_query', lambda *a, **kw: {'use_memory': True, 'mode': 'both'})
    monkeypatch.setattr('app.save_pipeline.pipeline.load_settings', lambda: {'notes_max_items': 6, 'notes_max_chars': 700})
    monkeypatch.setattr('app.save_pipeline.pipeline.get_scene_references', lambda *a, **kw: [])
    monkeypatch.setattr('app.patterns.retrieval.retrieve_accepted_patterns', lambda *a, **kw: [])
    compact = retrieve_memory_brief('garden', detail='compact', limit=20)
    full = retrieve_memory_brief('garden', detail='full', limit=20)
    pointers = compact['memories']
    sources = [m for m in pointers if m.get('result_kind') == 'source_passage']
    assert len(sources) == 2
    assert all(m['source_event_ids'] and m['scene_id'] and m['source_agent'] for m in sources)
    assert all(next(h['memory']['text'] for h in hits if h['memory']['id'] == m['id']) in compact['brief'] for m in pointers)
    assert len(full['memories']) == 20
    assert len(full['brief']) <= 700
    # Rendering changes neither the full records nor their provenance.
    from app.retrieval_pipeline.federated import _qualify_evidence
    from app.save_pipeline.pipeline import serialize_public_memory
    expected = [serialize_public_memory(h['memory'] if federated else
                _qualify_evidence(h['memory'], h['memory'].get('source_agent') or 'codex')) for h in hits]
    assert full['memories'] == expected


def test_long_fitting_passage_keeps_a_fact_when_pair_fits():
    hits = [fact(i) for i in range(10)] + [passage(1, text='A' * 480), passage(2, text='B' * 180)]
    included = []
    brief = build_memory_notes(hits, max_items=6, max_chars=700, included_hits=included)
    assert len(brief) <= 700
    assert 'A' * 480 in brief
    assert any(h['memory']['id'].startswith('m') for h in included)
    assert sum(h['memory'].get('result_kind') == 'source_passage' for h in included) <= 2


def test_fact_floor_uses_a_later_pair_when_first_cannot_fit():
    hits = [fact(0, text='F' * 300), fact(1, text='short fact')] + [passage(1, text='S' * 180)]
    included = []
    brief = build_memory_notes(hits, max_items=3, max_chars=300, included_hits=included)
    assert 'S' * 180 in brief and 'short fact' in brief
    assert [h['memory']['id'] for h in included] == ['p1', 'm1']


def test_no_source_fits_with_leading_fact_keeps_fact():
    hits = [fact(0, text='F' * 200)] + [passage(1, text='S' * 180)]
    included = []
    brief = build_memory_notes(hits, max_items=2, max_chars=260, included_hits=included)
    assert len(brief) <= 260
    assert [h['memory']['id'] for h in included] == ['m0']
    assert 'F' * 200 in brief
    assert 'S' * 180 not in brief


def test_source_only_preserves_order():
    hits = [passage(1), passage(2)]
    included = []
    build_memory_notes(hits, max_items=2, max_chars=700, included_hits=included)
    assert included == hits


def test_pattern_receives_unused_shared_budget(monkeypatch):
    hits = [fact(0), passage(1)]
    recall = Mock(sources=Mock(return_value=['codex']))
    pattern = {'pattern': {'id': 'p1', 'title': 'Garden workflow', 'kind': 'workflow',
                           'scope': 'global', 'recommended_behavior': 'Record the date.'}}
    record = Mock()
    monkeypatch.setattr('app.runtime.context.get_runtime_context', lambda: SimpleNamespace(agent_name='codex'))
    monkeypatch.setattr('app.retrieval_pipeline.federated.get_federated_recall', lambda **kw: recall)
    monkeypatch.setattr('app.retrieval_pipeline.retriever.retrieve_memories', lambda *a, **kw: hits)
    monkeypatch.setattr('app.save_pipeline.pipeline.route_query', lambda *a, **kw: {'use_memory': True, 'mode': 'both'})
    monkeypatch.setattr('app.save_pipeline.pipeline.load_settings', lambda: {})
    monkeypatch.setattr('app.save_pipeline.pipeline.get_scene_references', lambda *a, **kw: [])
    monkeypatch.setattr('app.patterns.retrieval.retrieve_accepted_patterns', lambda *a, **kw: [pattern])
    monkeypatch.setattr('app.patterns.retrieval.record_pattern_applications', record)
    result = retrieve_memory_brief('garden', detail='compact', max_items=3, max_chars=700)
    assert len(result['memories']) == 2
    assert result['pattern_refs'] == [{'id': 'p1', 'source_agent': 'codex'}]
    assert 'PATTERN BRIEF:' in result['brief'] and len(result['brief']) <= 700
    record.assert_called_once()


def test_long_source_does_not_hide_leading_fact_and_shorter_source():
    hits = [fact(i) for i in range(15)] + [passage(1, text='L' * 600), passage(2, text='short source')]
    included = []
    brief = build_memory_notes(hits, max_items=6, max_chars=700, included_hits=included)
    assert len(brief) <= 700
    assert included[0]['memory']['id'] == 'm0'
    assert any(h['memory']['id'] == 'p2' for h in included)
    assert all(h['memory']['id'] != 'p1' for h in included)
    assert 'Garden note 0' in brief and 'short source' in brief
