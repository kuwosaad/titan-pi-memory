"""Regression probe: recalled Titan tool output must not become fresh evidence."""
import importlib.util
import os
from pathlib import Path

import pytest

from app.runtime.context import reset_runtime_context_cache
from app.save_pipeline import pipeline
from app.storage import memories, scenes
from app.storage.models import TraceEvent


HOOK = Path(__file__).resolve().parents[1] / 'integrations/claude_titan_plugin/scripts/titan_claude_hook.py'
spec = importlib.util.spec_from_file_location('feedback_claude_hook', HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


@pytest.mark.parametrize('adapter', ['claude_full', 'pi'])
def test_recalled_tool_output_does_not_become_fresh_memory(tmp_path, monkeypatch, adapter):
    for name in list(os.environ):
        if name.startswith('TITAN_'):
            monkeypatch.delenv(name, raising=False)
    for name in ('TITAN_HOME', 'TITAN_BASE_DIR', 'TITAN_SHARED_HOME'):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setenv('TITAN_MEMORY_BACKEND', 'sqlite')
    reset_runtime_context_cache()
    monkeypatch.setattr(pipeline, 'get_extraction_adapter', lambda: object())
    monkeypatch.setattr(pipeline, 'embed', lambda _: [])
    claim = 'The project checkpoint must use durable scenes.'
    seen = []

    def extractor(user, assistant, adapter):
        seen.append((user, assistant))
        return ([{'text': claim, 'type': 'decision', 'stream': 'learnings'}]
                if claim in user or claim in assistant else [])

    monkeypatch.setattr(pipeline, 'extract_atomic_memories', extractor)
    # Original is persisted through the same event-first pipeline.
    for event_id, role, content in [
        ('original-user', 'user', 'Remember this project rule: ' + claim),
        ('original-assistant', 'assistant', 'I will retain that project checkpoint rule.'),
    ]:
        pipeline.ingest_trace_event(TraceEvent(session_id='original', event_id=event_id,
                                    event_type=role + '_message', payload={'content': content}), process_new=False)
    pipeline.process_session_events('original')
    original = memories.get_memory_repository().get_recent_memories(limit=None, session_id='original')
    assert len(original) == 1

    # Claude's full capture + tool I/O option captures real MCP retrieval results.
    # A later assistant answer adds no new assertion of the recalled rule.
    payloads = [
        {'session_id': 'replay', 'hook_event_name': 'UserPromptSubmit',
         'prompt': 'Please check the previously saved project checkpoint rule.'},
        {'session_id': 'replay', 'hook_event_name': 'PostToolUse',
         'tool_name': 'mcp__titan-memory__query_memories', 'tool_use_id': 'recall-1',
         'tool_input': {'query': 'project checkpoint'},
         'tool_response': {'memories': [{'id': original[0]['id'], 'scene_id': original[0]['scene_id'],
                                          'source_agent': 'claude-code', 'text': claim}]}},
        {'session_id': 'replay', 'hook_event_name': 'Stop',
         'last_assistant_message': 'I found the historical record; check its original scene.'},
    ]
    if adapter == 'claude_full':
        events = [event for payload in payloads for event in
                  hook.build_trace_events(payload, mode='full', include_tool_io=True)]
    else:
        # Mirrors Pi's passive message_end/tool_result spool shapes.
        events = [
            {'session_id': 'replay', 'event_id': 'user', 'event_type': 'user_message',
             'payload': {'raw_type': 'message_end.user', 'content': payloads[0]['prompt']}},
            {'session_id': 'replay', 'event_id': 'tool', 'event_type': 'tool_execution',
             'payload': {'raw_type': 'tool_result', 'tool': 'titan', 'call_id': 'recall-1',
                         'args': '{\"action\":\"query_memories\",\"query\":\"project checkpoint\"}',
                         'output': {'excerpt': '1. ' + claim + ' [scene: ' + original[0]['scene_id'] + ']'}}},
            {'session_id': 'replay', 'event_id': 'assistant', 'event_type': 'assistant_message',
             'payload': {'raw_type': 'message_end.assistant', 'content': payloads[2]['last_assistant_message']}},
        ]
    for event in events:
        pipeline.ingest_trace_event(TraceEvent(**event), process_new=False)
    pipeline.process_session_events('replay')
    scene = scenes.get_session_scenes('replay')[0]
    assert claim in str(scene.raw_events)  # raw evidence remains durable
    assert claim not in seen[-1][1]  # recalled tool output must not enter extraction
    assert memories.get_memory_repository().get_recent_memories(limit=None, session_id='replay') == []
    reset_runtime_context_cache()


@pytest.mark.parametrize('tool,args', [
    ('mcp__other-provider__query_memories', '{"query":"project checkpoint"}'),
    ('titan', '{"action":"store_trace_packet","outcome":"completed"}'),
])
def test_unrelated_tool_and_titan_write_evidence_remains_available(tmp_path, monkeypatch, tool, args):
    for name in list(os.environ):
        if name.startswith('TITAN_'):
            monkeypatch.delenv(name, raising=False)
    for name in ('TITAN_HOME', 'TITAN_BASE_DIR', 'TITAN_SHARED_HOME'):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setenv('TITAN_MEMORY_BACKEND', 'sqlite')
    reset_runtime_context_cache()
    seen = []
    monkeypatch.setattr(pipeline, 'get_extraction_adapter', lambda: object())
    monkeypatch.setattr(pipeline, 'extract_atomic_memories', lambda user, assistant, adapter: seen.append((user, assistant)) or [])
    inputs = [
        ('u', 'user_message', {'content': 'What happened to the project checkpoint rule?'}),
        ('t', 'tool_execution', {'tool': tool, 'args': args, 'output': {'excerpt': 'distinct fresh evidence'}}),
        ('a', 'assistant_message', {'content': 'The operation completed and evidence is available.'}),
    ]
    for event_id, event_type, payload in inputs:
        pipeline.ingest_trace_event(TraceEvent(session_id='control', event_id=event_id,
                                    event_type=event_type, payload=payload), process_new=False)
    pipeline.process_session_events('control')
    assert 'distinct fresh evidence' in seen[-1][1]
    reset_runtime_context_cache()


@pytest.fixture
def capture(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith('TITAN_'):
            monkeypatch.delenv(name, raising=False)
    for name in ('TITAN_HOME', 'TITAN_BASE_DIR', 'TITAN_SHARED_HOME'):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setenv('TITAN_MEMORY_BACKEND', 'sqlite')
    reset_runtime_context_cache()
    observed = []
    monkeypatch.setattr(pipeline, 'get_extraction_adapter', lambda: object())
    monkeypatch.setattr(pipeline, 'extract_atomic_memories', lambda user, assistant, adapter: observed.append((user, assistant)) or [])
    yield observed
    reset_runtime_context_cache()


def send_events(session, inputs):
    for event_id, event_type, payload in inputs:
        pipeline.ingest_trace_event(TraceEvent(session_id=session, event_id=event_id,
                                    event_type=event_type, payload=payload), process_new=False)
    pipeline.process_session_events(session)


def test_mixed_trace_packet_retains_write_and_original_tool_order(capture):
    calls = [
        {'name': 'titan', 'args': {'action': 'query_memories'}, 'result': 'old recalled fact'},
        {'name': 'titan', 'args': {'action': 'store_trace_packet'}, 'result': 'fresh write completed'},
    ]
    send_events('packet', [('packet-1', 'trace_packet', {
        'goal': 'Record the important project checkpoint outcome',
        'outcome': 'The project checkpoint work was completed successfully.',
        'tool_calls': calls,
    })])
    scene = scenes.get_session_scenes('packet')[0]
    assert scene.raw_events[0]['payload']['tool_calls'] == calls
    prompt = '\n'.join(capture[-1])
    assert 'old recalled fact' not in prompt
    assert 'fresh write completed' in prompt
    assert '[scene_tool t2 ' in prompt
    assert pipeline._scene_evidence_ref_map(scene).get('t1') is None
    assert pipeline._scene_evidence_ref_map(scene)['t2'] == 'packet-1'


def test_failed_recall_keeps_explicit_error_not_partial_result(capture):
    send_events('failure', [
        ('u', 'user_message', {'content': 'Check the project checkpoint history and report any failure.'}),
        ('t', 'tool_execution', {'tool': 'titan_query_memories',
                                 'output': {'error': 'retrieval timed out', 'excerpt': 'old recalled fact'}}),
        ('a', 'assistant_message', {'content': 'The historical lookup failed, so try it again later.'}),
    ])
    scene = scenes.get_session_scenes('failure')[0]
    assert 'old recalled fact' in str(scene.raw_events)
    prompt = '\n'.join(capture[-1])
    assert 'retrieval timed out' in prompt
    assert 'old recalled fact' not in prompt


@pytest.mark.parametrize('tool,args,metadata', [
    ('titan', '{"action":', {}),
    ('titan', {'action': ['query_memories']}, {}),
    ('query_memories', {}, {'server': ['titan-memory']}),
    ('titan_doctor', {}, {}),
])
def test_unknown_tool_identity_retains_evidence_and_does_not_crash(capture, tool, args, metadata):
    send_events('unknown', [
        ('u', 'user_message', {'content': 'Check the project checkpoint and report the actual outcome.'}),
        ('t', 'tool_execution', {'tool': tool, 'args': args, 'output': 'new operational observation', **metadata}),
        ('a', 'assistant_message', {'content': 'The operation produced an observation for the checkpoint.'}),
    ])
    assert 'new operational observation' in '\n'.join(capture[-1])


def test_user_correction_after_recall_remains_fresh_evidence(capture, monkeypatch):
    correction = 'Correction: the project checkpoint now requires two replicas.'
    old_fact = 'The project checkpoint requires one replica.'
    monkeypatch.setattr(pipeline, 'embed', lambda _: [])
    def extractor(user, assistant, adapter):
        capture.append((user, assistant))
        return [{'text': correction, 'source': 'user', 'type': 'decision'}] if correction in user else []
    monkeypatch.setattr(pipeline, 'extract_atomic_memories', extractor)
    send_events('correction', [
        ('u1', 'user_message', {'content': 'Check the checkpoint rule before I update it.'}),
        ('t', 'tool_execution', {'tool': 'titan_query_memories', 'output': old_fact}),
        ('u2', 'user_message', {'content': correction}),
        ('a', 'assistant_message', {'content': 'I will use the corrected two replica rule.'}),
    ])
    prompt = '\n'.join(capture[-1])
    assert correction in prompt
    assert old_fact not in prompt
    records = memories.get_memory_repository().get_recent_memories(limit=None, session_id='correction')
    assert [record['text'] for record in records] == [correction]
    assert 'u2' in records[0]['source_event_ids']
