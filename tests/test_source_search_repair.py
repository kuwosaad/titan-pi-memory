import pytest

from app.retrieval_pipeline.source_search import canonical_scene_messages, search_source_passages
from app.storage.scenes import JsonSceneRepository, SqliteSceneRepository


def scene(scene_id, *, session='s', messages=None, raw_events=None, tool_calls=None):
    return {'scene_id': scene_id, 'session_id': session, 'turn': 1,
            'ts': '2025-01-01', 'messages': messages or [],
            'raw_events': raw_events or [], 'tool_calls': tool_calls or []}


def event(event_id, event_type, content):
    return {'event_id': event_id, 'event_type': event_type, 'payload': {'content': content}}


def search(repo, query, **kwargs):
    return search_source_passages(query, repository=repo, source_agent='agent-a', **kwargs)


@pytest.mark.parametrize('backend', ['json', 'sqlite'])
def test_searches_all_scenes_and_content_including_raw_events(tmp_path, backend):
    repo = (JsonSceneRepository(tmp_path / 'scenes.json') if backend == 'json'
            else SqliteSceneRepository(tmp_path / 'scenes.db'))
    repo.append_scenes([scene('old', raw_events=[event('e-old', 'user_message',
                        'filler ' * 16000 + 'orchid violet decision')])] + [
        scene(f'new-{i}', messages=[{'role': 'user', 'content': 'unrelated', 'event_id': f'e-{i}'}])
        for i in range(250)])
    hits = search(repo, 'orchid violet decision', scan_limit=7)
    assert len(hits) == 1
    assert hits[0]['scene_id'] == 'old'
    assert hits[0]['source_event_ids'] == ['e-old']
    assert hits[0]['role'] == 'user'
    assert search(repo, 'orchid violet decision', session_id='other') == []


def test_projection_keeps_roles_and_excludes_tools_and_system():
    data = scene('x', messages=[{'role': 'assistant', 'content': 'shared text', 'event_id': 'a'},
                                {'role': 'assistant', 'content': 'secret text', 'event_id': 't'}],
                 raw_events=[event('a', 'assistant_message', 'shared text'),
                             event('u', 'user_message', 'sanitized [REDACTED] text'),
                             event('t', 'tool_result', 'secret text'),
                             event('s', 'system_message', 'system text')])
    messages = canonical_scene_messages(data)
    assert [(m['event_id'], m['role']) for m in messages] == [('a', 'assistant'), ('u', 'user')]
    assert messages[1]['content'] == 'sanitized [REDACTED] text'


def test_read_only_store_is_not_modified(tmp_path):
    path = tmp_path / 'scenes.db'
    repo = SqliteSceneRepository(path)
    repo.append_scenes([scene('old', raw_events=[event('e', 'assistant_message', 'orchid violet')])])
    before = path.read_bytes()
    read_only = SqliteSceneRepository(path, initialize=False)
    assert search(read_only, 'orchid violet')[0]['role'] == 'assistant'
    assert path.read_bytes() == before


def test_legacy_sqlite_schema_needs_no_migration(tmp_path):
    import sqlite3

    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE scenes (
            scene_id TEXT PRIMARY KEY, session_id TEXT, turn INTEGER, kind TEXT,
            anchor_event_id TEXT, ts TEXT, source_event_ids_json TEXT,
            messages_json TEXT, extraction_user_text TEXT,
            extraction_assistant_text TEXT, used_context_fallback INTEGER)''')
        conn.execute('INSERT INTO scenes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (
            'legacy', 's', 1, 'message_exchange', 'e', '2024-01-01', '["e"]',
            '[{"role":"user","event_id":"e","content":"orchid violet"}]', '', '', 0))
    before = path.read_bytes()
    assert search(SqliteSceneRepository(path, initialize=False), 'orchid violet')[0]['scene_id'] == 'legacy'
    assert path.read_bytes() == before


def test_capture_dates_filter_before_candidate_limit(tmp_path):
    repo = SqliteSceneRepository(tmp_path / 'scenes.db')
    repo.append_scenes([
        {**scene(f'a-{i:03}', messages=[{'role': 'user', 'event_id': f'out-{i}',
                                         'content': 'orchid violet decision'}]),
         'ts': '2026-01-01T00:00:00Z'}
        for i in range(110)
    ] + [
        {**scene('z-in-range', messages=[{'role': 'assistant', 'event_id': 'in',
                                         'content': 'orchid violet decision'}]),
         'ts': '2025-01-01T23:59:59.999999Z'},
        {**scene('bad-ts', messages=[{'role': 'user', 'event_id': 'bad',
                                      'content': 'orchid violet decision'}]), 'ts': 'invalid'},
    ])
    hits = search(repo, 'orchid violet decision', date_from='2025-01-01', date_to='2025-01-01')
    assert [hit['scene_id'] for hit in hits] == ['z-in-range']
    assert search(repo, 'orchid violet decision', date_from='invalid') == []
    assert search(repo, 'orchid violet decision', date_to='2025-13-01') == []
    assert search(repo, 'orchid violet decision', date_to='2025-01-01T23:59:59Z') == []
    assert search(repo, 'orchid violet decision', date_from='2025-01-02T05:29:59+05:30',
                  date_to='2025-01-02T05:30:00+05:30')[0]['scene_id'] == 'z-in-range'
