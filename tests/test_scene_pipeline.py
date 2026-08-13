import unittest
from unittest.mock import Mock, patch

import app.save_pipeline.pipeline as pipeline
from app.save_pipeline.pipeline import run_memory_pipeline_outcome
from app.storage.models import Scene, SceneMessage, SceneToolCall


class ScenePipelineTests(unittest.TestCase):
    def test_run_memory_pipeline_outcome_links_records_to_scene_and_saves_scene(self):
        scene = Scene(
            scene_id="s1:scene:e-1",
            session_id="s1",
            turn=3,
            kind="message_exchange",
            scene_seq=11,
            start_event_seq=11,
            end_event_seq=11,
            anchor_event_id="e-1",
            source_event_ids=["e-1"],
            raw_events=[],
            messages=[
                SceneMessage(role="user", content="How should dedupe work?", message_id="u1", event_id=None),
                SceneMessage(role="assistant", content="Use session_id and event_id.", message_id="a1", event_id="e-1"),
            ],
            tool_calls=[SceneToolCall(name="read", summary="Read app/storage/memories.py", file_paths=["app/storage/memories.py"])],
            extraction_user_text="How should dedupe work?",
            extraction_assistant_text="Use session_id and event_id.",
            used_context_fallback=False,
            ts="2026-04-09T00:00:00+00:00",
        )

        verifier = Mock()
        verifier.verify_memory.return_value = Mock(verified=False, confidence=0.0)

        with (
            patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()),
            patch(
                "app.save_pipeline.pipeline.extract_atomic_memories",
                return_value=[{"text": "Use session_id and event_id for dedupe.", "stream": "learnings", "type": "decision"}],
            ),
            patch("app.save_pipeline.pipeline.embed", return_value=[]),
            patch("app.save_pipeline.pipeline.get_verifier", return_value=verifier),
            patch("app.save_pipeline.pipeline.append_memories") as mock_append_memories,
            patch("app.save_pipeline.pipeline.append_scene") as mock_append_scene,
            patch("app.save_pipeline.pipeline.append_memory_notes"),
        ):
            outcome = run_memory_pipeline_outcome(
                session_id="s1",
                turn=3,
                user_text=scene.extraction_user_text,
                assistant_text=scene.extraction_assistant_text,
                source_event_ids=scene.source_event_ids,
                fallback_enabled=True,
                scene=scene,
            )

        self.assertFalse(outcome["fallback_used"])
        self.assertEqual(outcome["records"][0]["scene_id"], "s1:scene:e-1")
        mock_append_memories.assert_called_once()
        mock_append_scene.assert_called_once_with(scene)

    def test_process_session_events_skips_raw_scene_for_non_memory_event(self):
        event = {
            "seq": 42,
            "session_id": "s1",
            "event_id": "idle-1",
            "event_type": "session_idle",
            "ts": "2026-04-09T00:00:00+00:00",
            "payload": {"raw_type": "session.idle", "body": {"status": "idle"}},
        }

        with (
            patch("app.save_pipeline.pipeline.load_unprocessed_events", return_value=[event]),
            patch("app.save_pipeline.pipeline.load_message_context", return_value=({}, {}, {})),
            patch("app.save_pipeline.pipeline.get_next_trace_turn", return_value=1),
            patch("app.save_pipeline.pipeline.append_scene") as mock_append_scene,
            patch("app.save_pipeline.pipeline.update_session_checkpoint") as mock_update_checkpoint,
        ):
            result = pipeline.process_session_events("s1")

        self.assertEqual(result["processed_events"], 1)
        self.assertEqual(result["prompt_candidates"], 0)
        mock_append_scene.assert_not_called()
        mock_update_checkpoint.assert_called_once_with("s1", 42)

    def test_process_session_events_attaches_compact_tool_calls_to_scene(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "tool-1",
                "event_type": "tool_execution",
                "ts": "2026-04-09T00:00:00+00:00",
                "payload": {
                    "raw_type": "tool.execute.after",
                    "tool": "read",
                    "call_id": "call-1",
                    "args": {"filePath": "app/storage/memories.py"},
                    "output": "x" * 2000,
                },
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "message",
                "ts": "2026-04-09T00:00:01+00:00",
                "payload": {
                    "raw_type": "message.updated",
                    "body": {"properties": {"info": {"id": "u1", "role": "user", "content": "How should memory storage work?"}}},
                },
            },
            {
                "seq": 3,
                "session_id": "s1",
                "event_id": "assistant-meta-1",
                "event_type": "message",
                "ts": "2026-04-09T00:00:02+00:00",
                "payload": {
                    "raw_type": "message.updated",
                    "body": {"properties": {"info": {"id": "a1", "role": "assistant", "parentID": "u1"}}},
                },
            },
            {
                "seq": 4,
                "session_id": "s1",
                "event_id": "assistant-part-1",
                "event_type": "message",
                "ts": "2026-04-09T00:00:03+00:00",
                "payload": {
                    "raw_type": "message.part.updated",
                    "body": {"properties": {"part": {"type": "text", "messageID": "a1", "text": "Use compact scenes with tool calls."}}},
                },
            },
        ]

        with (
            patch("app.save_pipeline.pipeline.load_unprocessed_events", return_value=events),
            patch("app.save_pipeline.pipeline.load_message_context", return_value=({}, {}, {})),
            patch("app.save_pipeline.pipeline.get_next_trace_turn", return_value=1),
            patch("app.save_pipeline.pipeline.append_scene") as mock_append_scene,
            patch("app.save_pipeline.pipeline.update_session_checkpoint"),
            patch(
                "app.save_pipeline.pipeline.run_memory_pipeline_outcome",
                return_value={"records": [{"id": "s1:1:0"}], "fallback_used": False, "skip_reason": None},
            ),
            patch("app.save_pipeline.pipeline.remove_retry_entries"),
        ):
            result = pipeline.process_session_events("s1")

        self.assertEqual(result["prompt_candidates"], 1)
        saved_scene = mock_append_scene.call_args.args[0]
        self.assertEqual(saved_scene.raw_events, [])
        self.assertEqual(saved_scene.tool_calls[0].name, "read")
        self.assertEqual(saved_scene.tool_calls[0].file_paths, ["app/storage/memories.py"])
        self.assertLessEqual(len(saved_scene.tool_calls[0].excerpt or ""), 500)

    def test_pi_message_pair_preserves_long_content_inside_one_bounded_scene(self):
        long_user = "bounded-user:" + ("u" * 10_000)
        long_assistant = "bounded-assistant:" + ("a" * 10_000)
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-long",
                "event_type": "user_message",
                "ts": "2026-07-12T00:00:00+00:00",
                "payload": {"content": long_user},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "assistant-long",
                "event_type": "assistant_message",
                "ts": "2026-07-12T00:00:01+00:00",
                "payload": {"content": long_assistant},
            },
        ]
        captured_scenes = []

        def _fake_run_memory_pipeline(**kwargs):
            captured_scenes.append(kwargs["scene"])
            return {"records": [{"id": "s1:1:0"}], "fallback_used": False, "skip_reason": None}

        with (
            patch("app.save_pipeline.pipeline.load_unprocessed_events", return_value=events),
            patch("app.save_pipeline.pipeline.load_message_context", return_value=({}, {}, {})),
            patch("app.save_pipeline.pipeline.get_pending_user_message", return_value=""),
            patch("app.save_pipeline.pipeline.get_next_trace_turn", return_value=1),
            patch("app.save_pipeline.pipeline.set_pending_user_message"),
            patch("app.save_pipeline.pipeline.clear_pending_user_message"),
            patch("app.save_pipeline.pipeline.append_scene"),
            patch("app.save_pipeline.pipeline.update_session_checkpoint"),
            patch("app.save_pipeline.pipeline.run_memory_pipeline_outcome", side_effect=_fake_run_memory_pipeline),
            patch("app.save_pipeline.pipeline.remove_retry_entries"),
        ):
            result = pipeline.process_session_events("s1")

        self.assertEqual(result["prompt_candidates"], 1)
        self.assertEqual(len(captured_scenes), 1)
        scene = captured_scenes[0]
        self.assertEqual(scene.scene_id, "s1:scene:assistant-long")
        self.assertEqual(scene.messages[0].content, long_user)
        self.assertEqual(scene.messages[1].content, long_assistant)
        self.assertEqual(scene.extraction_user_text, long_user)
        self.assertEqual(scene.extraction_assistant_text, long_assistant)

    def test_run_memory_pipeline_outcome_persists_immediately_when_dedup_enabled(self):
        verifier = Mock()
        verifier.verify_memory.return_value = Mock(verified=False, confidence=0.0)

        with (
            patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()),
            patch(
                "app.save_pipeline.pipeline.extract_atomic_memories",
                return_value=[{"text": "Use session_id and event_id for dedupe.", "stream": "learnings", "type": "decision"}],
            ),
            patch("app.save_pipeline.pipeline.embed", return_value=[]),
            patch("app.save_pipeline.pipeline.get_verifier", return_value=verifier),
            patch("app.save_pipeline.pipeline.load_settings", return_value={"verification": {"enabled": False}, "dedup": {"enabled": True}}),
            patch("app.save_pipeline.pipeline._is_dedup_active", return_value=True),
            patch("app.save_pipeline.pipeline.append_memories") as mock_append_memories,
            patch("app.save_pipeline.pipeline.add_to_dedup_buffer") as mock_add_to_dedup_buffer,
            patch("app.save_pipeline.pipeline.append_memory_notes"),
        ):
            outcome = run_memory_pipeline_outcome(
                session_id="s1",
                turn=3,
                user_text="How should dedupe work?",
                assistant_text="Use session_id and event_id.",
                source_event_ids=["e-1"],
                fallback_enabled=True,
            )

        self.assertEqual(len(outcome["records"]), 1)
        mock_append_memories.assert_called_once_with(outcome["records"])
        mock_add_to_dedup_buffer.assert_called_once_with(outcome["records"])


if __name__ == "__main__":
    unittest.main()
