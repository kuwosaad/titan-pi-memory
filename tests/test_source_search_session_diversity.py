from app.retrieval_pipeline.source_search import search_source_passages


class Repository:
    def __init__(self, scenes):
        self.scenes = scenes

    def iter_source_scenes(self, **kwargs):
        yield from self.scenes


def scene(scene_id, session, *texts):
    return {'scene_id': scene_id, 'session_id': session, 'ts': '2025-01-01',
            'messages': [{'role': 'user', 'event_id': f'{scene_id}-{i}', 'content': text}
                         for i, text in enumerate(texts)]}


def search(scenes, **kwargs):
    return search_source_passages('amber lantern garden', repository=Repository(scenes),
                                  source_agent='agent', **kwargs)


def test_equal_coverage_distributes_three_slots_across_sessions():
    scenes = [scene('a', 'one', 'amber lantern garden'),
              scene('b', 'one', 'amber lantern garden'),
              scene('c', 'two', 'amber lantern garden'),
              scene('d', 'three', 'amber lantern garden')]
    hits = search(scenes, limit=3)
    assert [hit['session_id'] for hit in hits] == ['one', 'two', 'three']
    assert [hit['text'] for hit in hits] == ['amber lantern garden'] * 3
    assert [hit['source_event_ids'] for hit in hits] == [['a-0'], ['c-0'], ['d-0']]


def test_higher_score_precedes_session_diversity_in_each_phase():
    scenes = [scene('a', 'one', 'amber lantern garden', 'amber lantern garden'),
              scene('b', 'one', 'amber lantern garden', 'amber lantern garden'),
              scene('c', 'two', 'amber lantern', 'amber lantern')]
    hits = search(scenes, limit=6)
    assert [hit['scene_id'] for hit in hits] == ['a', 'b', 'c', 'a', 'b', 'c']
    assert [hit['score'] for hit in hits] == [1, 1, 2/3, 1, 1, 2/3]


def test_single_session_multi_passage_order_and_filter_unchanged():
    scenes = [scene('b', 'one', 'amber lantern garden', 'amber lantern garden'),
              scene('a', 'one', 'amber lantern garden', 'amber lantern garden'),
              scene('c', 'two', 'amber lantern garden')]
    hits = search(scenes, session_id='one', limit=5)
    assert [(h['scene_id'], h['source_event_ids'], h['score']) for h in hits] == [
        ('a', ['a-0'], 1), ('b', ['b-0'], 1), ('a', ['a-1'], 1), ('b', ['b-1'], 1)]
    assert hits == [
        {'result_kind': 'source_passage', 'text': 'amber lantern garden',
         'scene_id': sid, 'session_id': 'one', 'source_agent': 'agent',
         'source_event_ids': [event], 'ts': '2025-01-01', 'role': 'user', 'score': 1.0}
        for sid, event in [('a', 'a-0'), ('b', 'b-0'), ('a', 'a-1'), ('b', 'b-1')]
    ]


def test_ties_are_deterministic_and_round_robin_in_both_phases():
    scenes = [scene('d', 'two', 'amber lantern garden', 'amber lantern garden'),
              scene('c', 'one', 'amber lantern garden', 'amber lantern garden'),
              scene('b', 'two', 'amber lantern garden', 'amber lantern garden'),
              scene('a', 'one', 'amber lantern garden', 'amber lantern garden')]
    hits = search(scenes, limit=8)
    assert [(h['scene_id'], h['source_event_ids'][0]) for h in hits] == [
        ('a', 'a-0'), ('b', 'b-0'), ('c', 'c-0'), ('d', 'd-0'),
        ('a', 'a-1'), ('b', 'b-1'), ('c', 'c-1'), ('d', 'd-1')]
    assert hits == search(list(reversed(scenes)), limit=8)


def test_incremental_pruning_keeps_high_coverage_and_session_diversity():
    scenes = [scene(f'a{i:03}', 'one', 'amber lantern garden') for i in range(105)]
    scenes += [scene('z-low', 'two', 'amber lantern'),
               scene('z-high', 'three', 'amber lantern garden')]
    hits = search(scenes, limit=100)
    assert len(hits) == 100
    assert hits[0]['session_id'] == 'one'
    assert hits[1]['scene_id'] == 'z-high'
    assert all(hit['score'] == 1 for hit in hits)
