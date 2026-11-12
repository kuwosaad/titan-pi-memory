"""Public save/recall behavior using isolated repositories and scripted providers."""
import json

import numpy as np
import pytest

from app.retrieval_pipeline.federated import FederatedRecall
from app.save_pipeline.pipeline import retrieve_memory_brief, run_memory_pipeline_outcome
from app.storage.memories import JsonMemoryRepository, SqliteMemoryRepository
from app.storage.models import Scene
from app.storage.scenes import JsonSceneRepository


@pytest.fixture(params=[JsonMemoryRepository, SqliteMemoryRepository])
def stores(tmp_path, monkeypatch, request):
    repo = request.param(tmp_path / ('memory.json' if request.param is JsonMemoryRepository else 'memory.db'))
    scenes = JsonSceneRepository(tmp_path / 'scenes.json')
    recall = FederatedRecall(active_agent='default', memory_repositories={'default': repo},
                             scene_repositories={'default': scenes}, titan_home=tmp_path)
    monkeypatch.setattr('app.storage.memories.get_memory_repository', lambda: repo)
    monkeypatch.setattr('app.storage.scenes.get_scene_repository', lambda: scenes)
    monkeypatch.setattr('app.storage.scenes.get_scene_write_repository', lambda: scenes)
    from app.storage.memories import create_memory_record
    def create_at_noon(**kwargs):
        return {**create_memory_record(**kwargs), 'ts': '2025-05-04T12:00:00+00:00'}
    monkeypatch.setattr('app.save_pipeline.pipeline.create_memory_record', create_at_noon)
    monkeypatch.setattr('app.retrieval_pipeline.federated.get_federated_recall', lambda **_: recall)
    settings = {'retrieval_top_k': 8, 'retrieval_min_similarity': 0, 'retrieval_recency_days': None,
                'retrieval_session_bias': False, 'retrieval': {'min_reliability': 0}, 'step2': {}}
    monkeypatch.setattr('app.retrieval_pipeline.config.load_settings', lambda: settings)
    monkeypatch.setattr('app.save_pipeline.pipeline.load_settings', lambda: settings)
    monkeypatch.setattr('app.save_pipeline.pipeline.embed', lambda texts: [np.array([1., 0.]) for _ in texts])
    monkeypatch.setattr('app.retrieval_pipeline.retriever.embed', lambda texts: [np.array([1., 0.]) for _ in texts])
    monkeypatch.setattr('app.patterns.retrieval.retrieve_accepted_patterns', lambda *a, **kw: [])
    return repo, scenes, recall


class ScriptedExtractor:
    def __init__(self, memories):
        self.memories = memories

    def chat(self, messages, **kwargs):
        return json.dumps({'memories': self.memories})


def save(monkeypatch, *, turn, text, memories, session='s', ts='2025-05-04T12:00:00Z'):
    scene = Scene(scene_id=f'{session}-scene-{turn}', session_id=session, turn=turn,
                  kind='message_exchange', ts=ts, source_event_ids=[f'e-{turn}'],
                  messages=[{'role': 'user', 'content': text, 'event_id': f'e-{turn}'}],
                  extraction_user_text=text, extraction_assistant_text='')
    monkeypatch.setattr('app.save_pipeline.pipeline.get_extraction_adapter', lambda: ScriptedExtractor(memories))
    return run_memory_pipeline_outcome(session, turn, text, '', scene=scene)['records']


def test_public_save_and_date_query_uses_occurrence_not_backfill_time(stores, monkeypatch):
    backfill = 'The observatory launch happened on 2024-02-29.'
    saved = save(monkeypatch, turn=1, text=backfill, memories=[{'text': backfill, 'type': 'fact',
        'stream': 'rough', 'source': 'user', 'event_date': '2024-02-29', 'event_evidence': backfill}])
    assert saved[0]['event_start'] == '2024-02-29'
    assert saved[0]['event_end'] == '2024-03-01'
    # Ingestion happens more than a year later, and undated claims must not be
    # assigned the scene/capture date by inference.
    unknown = 'The observatory launch used a blue rocket.'
    undated = save(monkeypatch, turn=2, text=unknown, memories=[{'text': unknown, 'type': 'fact',
        'stream': 'rough', 'source': 'user'}])
    assert not undated[0].get('event_start')
    other = 'The observatory launch happened on 2024-03-01.'
    save(monkeypatch, turn=3, text=other, memories=[{'text': other, 'type': 'fact',
        'stream': 'rough', 'source': 'user', 'event_date': '2024-03-01', 'event_evidence': other}])
    result = retrieve_memory_brief('2024-02-29', sources=['default'], max_chars=300)
    assert [m['id'] for m in result['memories']] == [saved[0]['id']]
    assert len(result['brief']) <= 300
    assert any(ref['scene_id'] == 's-scene-1' for ref in result['scene_refs'])
    # Date-only capture filtering must include the whole ending day, including noon.
    capture = retrieve_memory_brief('observatory launch', sources=['default'],
                                    date_from='2025-05-04', date_to='2025-05-04')
    assert {m['id'] for m in capture['memories'] if m.get('result_kind') != 'source_passage'} == {'s-scene-1:memory:0', 's-scene-2:memory:0', 's-scene-3:memory:0'}


def test_unverified_date_quote_and_cross_source_scope(stores, monkeypatch, tmp_path):
    text = 'The aurora project has a release milestone.'
    records = save(monkeypatch, turn=1, text=text, memories=[{'text': text, 'type': 'fact', 'source': 'user',
        'event_date': '2024-02-29', 'event_evidence': 'The aurora project shipped 2024-02-29.'}])
    assert not records[0].get('event_start')
    dated = 'The aurora project shipped 2024-02-29.'
    save(monkeypatch, turn=2, session='other', text=dated, memories=[{'text': dated, 'type': 'fact',
        'source': 'user', 'event_date': '2024-02-29', 'event_evidence': dated}])
    repo, scenes, recall = stores
    recall._memory_repositories['other-agent'] = JsonMemoryRepository(tmp_path / 'other-agent.json')
    result = retrieve_memory_brief('2024-02-29', sources=['other-agent'])
    assert result['memories'] == []


def test_public_source_passages_are_scoped_cited_and_bounded(stores, monkeypatch):
    repo, scenes, recall = stores
    scenes.append_scenes([{'scene_id': 'historical', 'session_id': 's', 'turn': 10,
        'ts': '2025-01-01T12:00:00Z', 'messages': [{'role': 'user', 'event_id': 'old-event',
        'content': 'Nebula migration was postponed pending amber approval.'}]},
        {'scene_id': 'unrelated-session', 'session_id': 'other', 'turn': 11,
        'ts': '2025-01-02T12:00:00Z', 'messages': [{'role': 'user', 'event_id': 'other-event',
        'content': 'Nebula migration amber approval was granted.'}]}])
    result = retrieve_memory_brief('nebula migration amber approval', session_id='s',
                                   sources=['default'], max_chars=180, max_items=1)
    assert len(result['brief']) <= 180
    assert len(result['memories']) == 1
    passage = result['memories'][0]
    assert passage['result_kind'] == 'source_passage'
    assert passage['scene_id'] == 'historical'
    assert passage['source_event_ids'] == ['old-event']
    assert any(ref['scene_id'] == 'historical' for ref in result['scene_refs'])
    assert 'source passage' in result['brief'].lower()
    assert 'current' not in result['brief'].lower()


def test_historical_source_passage_is_not_a_current_fact(stores, monkeypatch):
    repo, scenes, recall = stores
    old = 'Nebula migration was postponed pending amber approval.'
    new = 'Nebula migration proceeded after amber approval was granted.'
    old_record = save(monkeypatch, turn=1, text=old, memories=[{'text': old, 'type': 'fact', 'source': 'user'}])[0]
    new_record = save(monkeypatch, turn=2, text=new, memories=[{'text': new, 'type': 'fact', 'source': 'user'}])[0]
    from app.storage.memory_relations import record_supersession
    record_supersession(repo, source='default', old_id=old_record['id'], new_id=new_record['id'], evidence=new)
    result = retrieve_memory_brief('nebula migration amber approval', sources=['default'], max_chars=1000)
    facts = [m for m in result['memories'] if m.get('result_kind') != 'source_passage']
    assert old_record['id'] not in {m['id'] for m in facts}
    assert new_record['id'] in {m['id'] for m in facts}
    old_passages = [m for m in result['memories'] if m.get('result_kind') == 'source_passage' and m['scene_id'] == 's-scene-1']
    assert old_passages and old_passages[0]['source_event_ids'] == ['e-1']
    assert 'source passage' in result['brief'].lower()
