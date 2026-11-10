"""Capture regressions; subprocesses guarantee imports cannot select live stores."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

from app.save_pipeline.extraction.extractor import (
    ExtractionError,
    assess_memory_worthiness,
    extract_atomic_memories,
    is_hidden_metadata_memory,
    sanitize_memories,
)
from app.save_pipeline.pipeline import _build_event_prompt, run_memory_pipeline_outcome


class LeanCaptureTests(unittest.TestCase):
    def test_social_prefix_does_not_discard_durable_content(self):
        for text in (
            "Thanks! Always use SQLite locally instead of PostgreSQL.",
            "Thanks, Aster. Remember that I prefer concise explanations.",
            "Thanks! The Telegram integration uses the outbox table.",
        ):
            with self.subTest(text=text):
                self.assertTrue(assess_memory_worthiness(text, "Agreed.")["should_extract"])
                prompt = _build_event_prompt({
                    "event_type": "trace_packet", "session_id": "s", "event_id": "e",
                    "payload": {"goal": f"Conversation: {text}", "thoughts": text,
                                "outcome": "User message in conversation with Aster",
                                "context": {"channel": "telegram", "direction": "inbound",
                                            "conversation_key": "telegram:test"}},
                })
                self.assertIsNone(prompt["skip_reason"])
                self.assertEqual(prompt["user_text"], text)

    def test_courtesy_only_is_skipped_but_courtesy_with_facts_is_admitted(self):
        for user, assistant in (("Thanks, that's helpful!", "You're welcome!"), ("Thank you, that’s clear.", "You’re welcome.")):
            self.assertFalse(assess_memory_worthiness(user, assistant)["should_extract"])
        self.assertTrue(assess_memory_worthiness("Thanks, that's helpful! Remember SQLite locally.", "You're welcome!")["should_extract"])

    def test_integration_vocabulary_is_not_metadata(self):
        for text in (
            "The Telegram bridge integration uses an outbox table for retries.",
            "The namespace boundary isolates each agent's memories.",
            "The assistant received a durable instruction to use SQLite locally.",
            "SQLite is local.",
            "The session.updated event caused the checkpoint regression.",
            "A user message was received in a conversation with the assistant. Remember to use SQLite locally.",
            "A message.updated event was captured and stored for memory processing. Always retain scenes.",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_hidden_metadata_memory(text))
                self.assertEqual(sanitize_memories([{"text": text}])[0]["text"], text)

    def test_malformed_response_raises_instead_of_fabricating_fragments(self):
        for content in (
            "provider is unavailable", "null", "{}", '{"memories":{}}',
            '{"memories":[42]}', '{"memories":[{}]}',
            '{"memories":[{"text":42}]}',
            '{"memories":[{"text":"Use SQLite locally.","memory_kind":"fabricated"}]}',
            '{"memories":[{"text":"Use SQLite locally.","speaker_focus":"nobody"}]}',
            '{"memories":[{"text":"Use SQLite locally.","stream":"invented"}]}',
            '{"memories":[{"text":"Use SQLite locally.","evidence_refs":42}]}',
            '[{"text":"Use SQLite locally."},null]',
        ):
            with self.subTest(content=content), patch("app.retrieval_pipeline.config.load_settings", return_value={}):
                with self.assertRaises(ExtractionError):
                    extract_atomic_memories("Remember to use SQLite locally.", "Agreed.", Mock(chat=Mock(return_value=content)))

    def test_legacy_list_response_retains_rough_stream_contract(self):
        with patch("app.retrieval_pipeline.config.load_settings", return_value={}):
            result = extract_atomic_memories("Use SQLite locally.", "Agreed.", Mock(chat=Mock(return_value='["Use SQLite locally."]')))
        self.assertEqual(result[0]["stream"], "rough")
        self.assertEqual(result[0]["source"], "user")

    def test_empty_response_is_intentional(self):
        with patch("app.retrieval_pipeline.config.load_settings", return_value={}):
            self.assertEqual(extract_atomic_memories("Remember SQLite.", "Agreed.", Mock(chat=Mock(return_value='{"memories":[]}'))), [])

    def test_default_empty_extraction_does_not_enable_legacy_fallback(self):
        with (
            patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()),
            patch("app.save_pipeline.pipeline.extract_atomic_memories", return_value=[]),
            patch("app.save_pipeline.pipeline.append_memories", side_effect=lambda records: records) as append,
            patch("app.save_pipeline.pipeline.append_memory_notes") as export,
        ):
            result = run_memory_pipeline_outcome("s", 1, "Remember to use SQLite locally.", "Agreed.")
        self.assertEqual(result["records"], [])
        self.assertFalse(result["fallback_used"])
        append.assert_not_called()
        export.assert_not_called()

    def test_save_reports_only_canonical_records_returned_by_storage(self):
        for accepted in ([], [{"id": "canonical-id", "text": "Use SQLite locally."}]):
            with (
                patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()),
                patch("app.save_pipeline.pipeline.extract_atomic_memories", return_value=[{"text": "Use SQLite locally."}]),
                patch("app.save_pipeline.pipeline.embed", return_value=[]),
                patch("app.save_pipeline.pipeline.append_memories", return_value=accepted),
                patch("app.save_pipeline.pipeline.append_memory_notes") as export,
            ):
                result = run_memory_pipeline_outcome("s", 1, "Use SQLite locally.", "Agreed.")
                self.assertEqual(result["records"], accepted)
                export.assert_not_called()

    def test_provider_exception_also_retains_retry(self):
        self._isolated_ingest(None, expect_failure=True)

    def test_failure_retains_scene_checkpoints_and_retry_until_recovered(self):
        self._isolated_ingest('not JSON', expect_failure=True)

    def test_intentional_empty_ingest_commits_evidence_without_retry(self):
        self._isolated_ingest('{"memories":[]}', expect_failure=False)

    def test_inherited_runtime_paths_cannot_escape_subprocess_store(self):
        with tempfile.TemporaryDirectory(prefix="titan-ambient-") as ambient:
            sentinel = Path(ambient) / "must-not-create"
            with patch.dict(os.environ, {
                "TITAN_BASE_DIR": str(sentinel), "TITAN_SHARED_HOME": str(sentinel),
                "TITAN_SPOOL_DIR": str(sentinel / "traces"),
                "TITAN_MEMORY_DB_PATH": str(sentinel / "memory.db"),
                "TITAN_SETTINGS_PATH": str(sentinel / "settings.yaml"),
            }):
                self._isolated_ingest('{"memories":[]}', expect_failure=False)
            self.assertFalse(sentinel.exists())

    def test_failed_scenes_and_later_success_have_stable_distinct_memory_ids(self):
        code = textwrap.dedent('''
            import json
            from unittest.mock import Mock, patch
            import app.save_pipeline.pipeline as p
            from app.storage.models import TraceEvent
            from app.storage.memories import get_memory_repository

            events = [
                TraceEvent(session_id='identity', event_id=f'{role}-{index}', event_type=f'{role}_message',
                           payload={'content':f'Remember rule-{index}: use SQLite locally.' if role == 'user' else f'Agreed to rule-{index}.'})
                for index in range(3) for role in ('user', 'assistant')
            ]
            adapter = Mock()
            def response(messages, **kwargs):
                content = messages[-1]['content']
                if 'rule-2' not in content:
                    return 'invalid extraction response'
                return json.dumps({'memories':[{'text':'The third rule selects SQLite locally.', 'source':'user'}]})
            adapter.chat.side_effect = response
            with patch.object(p, 'get_extraction_adapter', return_value=adapter), patch.object(p, 'embed', return_value=[]):
                for event in events:
                    p.ingest_trace_event(event, process_new=False)
                first = p.process_session_events('identity')
                assert first['queued_retries'] == 2, first
                assert first['stored_memories'] == 1, first
                scenes = p.get_session_scenes('identity')
                assert len(scenes) == 3
                assert len({scene.turn for scene in scenes}) == 1, 'fixture must reproduce legacy turn reuse'
                saved = get_memory_repository().get_recent_memories(limit=None, session_id='identity')
                successful_id = saved[0]['id']
                assert successful_id == f"{saved[0]['scene_id']}:memory:0"
                adapter.chat.side_effect = None
                adapter.chat.return_value = json.dumps({'memories':[{'text':'The recovered rule selects SQLite locally.', 'source':'user'}]})
                recovered = p.process_session_events('identity')
                assert recovered['recovered_retries'] == 2, recovered
                assert p.get_retry_queue_size('identity') == 0
                saved = get_memory_repository().get_recent_memories(limit=None, session_id='identity')
                assert len(saved) == 3, saved
                assert len({memory['id'] for memory in saved}) == 3, saved
                assert successful_id in {memory['id'] for memory in saved}
                scene_by_id = {scene.scene_id: scene for scene in scenes}
                for memory in saved:
                    assert memory['id'] == f"{memory['scene_id']}:memory:0"
                    assert memory['source_event_ids'] == scene_by_id[memory['scene_id']].source_event_ids
                for event in events:
                    assert p.ingest_trace_event(event)['status'] == 'duplicate'
                p.process_session_events('identity')
                assert get_memory_repository().get_recent_memories(limit=None, session_id='identity') == saved
        ''')
        self._run_isolated(code)

    def _isolated_ingest(self, first_response, *, expect_failure):
        code = textwrap.dedent('''
            import json, sys
            from pathlib import Path
            from unittest.mock import Mock, patch
            import app.save_pipeline.pipeline as p
            from app.storage.models import TraceEvent
            from app.storage.sessions import BASE_DIR
            from app.runtime.context import get_runtime_context

            context = get_runtime_context()
            assert context.memory_db_path.is_relative_to(context.base_dir)
            assert context.trace_dir.is_relative_to(context.base_dir)
            assert context.shared_home == context.base_dir

            first_response, expect_failure = json.loads(sys.argv[1])
            adapter = Mock(chat=Mock(return_value=first_response))
            if first_response is None:
                adapter.chat.side_effect = RuntimeError('provider unavailable')
            with patch.object(p, 'get_extraction_adapter', return_value=adapter), patch.object(p, 'embed', return_value=[]), patch.object(p, 'append_memory_notes') as export:
                p.ingest_trace_event(TraceEvent(session_id='capture', event_id='u', event_type='user_message', payload={'content':'Thanks! Remember to use SQLite locally.'}))
                result = p.ingest_trace_event(TraceEvent(session_id='capture', event_id='a', event_type='assistant_message', payload={'content':'Agreed; SQLite will be used locally.'}))
                scenes = p.get_session_scenes('capture')
                assert len(scenes) == 1, scenes
                original = scenes[0].model_dump()
                assert original['evidence_status'] == 'complete', original
                assert original['source_event_ids'] == ['u', 'a'], original
                assert len(original['raw_events']) == 2, original
                assert p.get_session_checkpoint('capture') == 2
                assert p.get_scene_checkpoint('capture') == 2
                assert not p.get_recent_memories(limit=None, session_id='capture')
                assert p.get_retry_queue_size('capture') == int(expect_failure)
                assert result['fallback_memories'] == 0
                if expect_failure:
                    p.prune_processed_events(['capture'])
                    assert any(event['event_id'] == 'a' for event in p.load_events_for_session('capture'))
                    failed_retry = p._retry_failed_extractions('capture')
                    assert failed_retry['recovered_retries'] == 0
                    assert p.get_retry_queue_size('capture') == 1
                    adapter.chat.side_effect = None
                    adapter.chat.return_value = json.dumps({'memories':[{'text':'SQLite is the chosen local database.', 'type':'decision', 'source':'user'}]})
                    recovered = p._retry_failed_extractions('capture')
                    assert recovered['recovered_retries'] == 1, recovered
                    assert p.get_retry_queue_size('capture') == 0
                    memories = p.get_recent_memories(limit=None, session_id='capture')
                    assert len(memories) == 1, memories
                    assert memories[0].scene_id == original['scene_id']
                    assert memories[0].source_event_ids == ['u', 'a'], memories[0]
                assert p.get_session_scenes('capture')[0].model_dump() == original
                export.assert_not_called()
                assert not list(Path(BASE_DIR).rglob('*.md'))
        ''')
        self._run_isolated(code, [first_response, expect_failure])

    def _run_isolated(self, code, args=None):
        with tempfile.TemporaryDirectory(prefix="titan-capture-") as directory:
            settings = Path(directory) / "settings.yaml"
            settings.write_text("{}\n", encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if not key.startswith("TITAN_")}
            env.update({
                "TITAN_HOME": directory, "TITAN_BASE_DIR": directory, "TITAN_SHARED_HOME": directory,
                "TITAN_AGENT_NAME": "capture-test", "TITAN_SPOOL_DIR": str(Path(directory) / "traces"),
                "TITAN_MEMORY_DB_PATH": str(Path(directory) / "memory_store.db"),
                "TITAN_MEMORY_BACKEND": "sqlite", "TITAN_MEMORY_READ_FALLBACK": "json",
                "TITAN_SETTINGS_PATH": str(settings), "PYTHONDONTWRITEBYTECODE": "1",
            })
            result = subprocess.run([sys.executable, "-c", code, json.dumps(args)],
                                    cwd=Path(__file__).parents[1], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
