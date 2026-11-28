import pytest

from app.retrieval_pipeline.source_search import _passage, _terms, search_source_passages
from app.storage.scenes import JsonSceneRepository


def passage(text, query):
    return _passage(text, _terms(query))


def test_original_message_recovered_with_inflected_action(tmp_path):
    repo = JsonSceneRepository(tmp_path / 'scenes.json')
    repo.append_scenes([{'scene_id': 's', 'session_id': 'session', 'turn': 1,
                         'ts': '2026-01-01', 'messages': [
                             {'role': 'user', 'event_id': 'e',
                              'content': 'Mira baked a cake for the festival.'}]}])
    hits = search_source_passages('Mira cooking bake', repository=repo, source_agent='agent')
    assert len(hits) == 1
    assert hits[0]['source_event_ids'] == ['e']
    assert hits[0]['text'] == 'Mira baked a cake for the festival.'


@pytest.mark.parametrize('query,source', [
    ('move', 'moved'), ('moving', 'moved'), ('relocated', 'relocating'),
    ('plan', 'planned'), ('planned', 'planning'), ('bake', 'baked'),
])
def test_verb_inflections(query, source):
    assert passage('Mira ' + source, 'Mira ' + query)


def test_distinct_anchors_and_exact_ranking():
    assert passage('Mira moved', 'Mira moving moved')[1] == pytest.approx(2 / 3)
    assert passage('moved', 'move moving') is None
    assert passage('Mira baked', 'Mira bake')[1] < passage('Mira bake', 'Mira bake')[1]
    assert passage('Mira bake', 'Mira bake')[1] == 1


def test_identifier_short_and_compound_guards():
    assert passage('Mira abc123ed', 'Mira abc123') is None
    assert passage('Mira abc_defed', 'Mira abc_def') is None
    assert passage('Mira go', 'Mira going') is None
    assert passage('Zara baked tops', 'H&M bake tops') is None
    assert passage('A+B baked tops', 'A+B bake tops')
