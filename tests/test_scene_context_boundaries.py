"""Adversarial boundaries for the opt-in scene expansion experiment."""

import json
from copy import deepcopy

import pytest

from app.retrieval_pipeline.scene_context_experiment import build_scene_context_result


def memory(source, ident, scene, text, **extra):
    return {'source_agent': source, 'id': ident, 'scene_id': scene, 'text': text, **extra}


def hit(record, score=0.8):
    return {'memory': record, 'score': score}


def build(seeds, siblings, **overrides):
    records = [h['memory'] for h in seeds] + siblings
    vectors = {(m['source_agent'], m['id']): [1.0, 0.0] for m in records}
    kwargs = {'query_vector': [1.0, 0.0], 'vectors': vectors, 'max_chars': 10000}
    kwargs.update(overrides)
    return build_scene_context_result(seeds, siblings, **kwargs)


def test_source_qualified_scene_identity_and_no_indirect_hops():
    seed = memory('pi', 'seed', 'same', 'selected decision', evidence_refs=[
        {'source_agent': 'other', 'memory_id': 'elsewhere', 'scene_id': 'second'},
    ])
    sibling = memory('pi', 'sibling', 'same', 'same source context', evidence_refs=[
        {'source_agent': 'pi', 'memory_id': 'second-seed', 'scene_id': 'second'},
    ])
    foreign = memory('codex', 'foreign', 'same', 'unrelated foreign scene')
    indirect = memory('pi', 'indirect', 'second', 'not a direct sibling')
    result = build([hit(seed)], [sibling, foreign, indirect])
    assert result['context_ids'] == [('pi', 'sibling')]
    assert result['inspected_sibling_ids'] == [('pi', 'sibling')]
    assert result['packets'][0]['linkage'] == 'scene_context_not_causation'
    assert result['hits'][1]['linked_seed'] == ['pi', 'seed']
    assert result['hits'][0]['memory']['evidence_refs'] == seed['evidence_refs']


def test_empty_admitted_seed_set_never_rescues_or_inspects_siblings():
    siblings = [memory('pi', 'sibling', 's1', 'highly relevant')]
    result = build([], siblings)
    assert result['hits'] == []
    assert result['inspected_sibling_ids'] == []
    assert result['rendered'] == ''


def test_repeated_sibling_cannot_add_votes_or_duplicate_output():
    seed = memory('pi', 'seed', 's1', 'decision')
    sibling = memory('pi', 'support', 's1', 'context')
    once = build([hit(seed)], [sibling], arm='contextual_selection')
    repeated = build([hit(seed)], [deepcopy(sibling) for _ in range(20)], arm='contextual_selection')
    assert repeated['packets'] == once['packets']
    assert repeated['context_ids'] == [('pi', 'support')]
    assert repeated['stats']['inspected_sibling_count'] == 1


def test_budget_charges_the_complete_rendered_packet_and_keeps_provenance():
    seed = memory('pi', 'seed', 's1', 'decision', source_event_ids=['event-1'],
                  evidence_refs=[{'source_agent': 'codex', 'memory_id': 'foreign', 'scene_id': 'foreign-scene'}])
    sibling = memory('pi', 'context', 's1', 'support', source_event_ids=['event-2'])
    full = build([hit(seed)], [sibling])
    cost = len(full['rendered'])
    fits = build([hit(seed)], [sibling], max_chars=cost, top_k=2)
    too_small = build([hit(seed)], [sibling], max_chars=cost-1, top_k=2)
    too_few_records = build([hit(seed)], [sibling], max_chars=cost, top_k=1)
    assert fits['stats']['rendered_chars'] == cost
    assert fits['rendered'] == full['rendered']
    packet = json.loads(fits['rendered'])
    assert packet['records'][0]['memory']['source_event_ids'] == ['event-1']
    assert packet['records'][0]['memory']['evidence_refs'] == seed['evidence_refs']
    assert packet['records'][1]['memory']['source_event_ids'] == ['event-2']
    for constrained in (too_small, too_few_records):
        assert constrained['selected_ids'] == [('pi', 'seed')]
        assert len(constrained['rendered']) <= cost
        assert constrained['packets'][0]['score'] == constrained['packets'][0]['base_score']


def test_conflicting_duplicate_identity_is_rejected():
    seed = memory('pi', 'seed', 's1', 'decision')
    with pytest.raises(ValueError, match='Conflicting'):
        build([hit(seed)], [memory('pi', 'same', 's1', 'one'), memory('pi', 'same', 's1', 'two')])


def test_independent_repeat_ids_do_not_increase_the_relationship_bonus():
    seed = memory('pi', 'seed', 's1', 'decision')
    duplicates = [memory('pi', f'copy-{n:02}', 's1', 'same repeated context') for n in range(20)]
    one = build([hit(seed)], duplicates[:1], arm='contextual_selection')
    many = build([hit(seed)], duplicates, arm='contextual_selection')
    assert many['packets'][0]['score'] == one['packets'][0]['score']
    assert len(many['context_ids']) == 2
    assert many['stats']['seed_scene_count'] == 1
