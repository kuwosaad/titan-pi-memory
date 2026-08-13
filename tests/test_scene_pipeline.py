import unittest
from unittest.mock import Mock, patch

import app.save_pipeline.pipeline as pipeline
from app.save_pipeline.pipeline import run_memory_pipeline_outcome
from app.storage.models import Scene, SceneMessage, SceneToolCall


class ScenePipelineTests(unittest.TestCase):
    def test_single_event_ingest_keeps_user_evidence_open_for_later_assistant(self):
        user_event = {
            "seq": 1,
            "session_id": "s1",
            "event_id": "user-1",
            "event_type": "user_message",
            "payload": {"content": "Remember the checkpoint rule."},
        }
        assistant_event = {
            "seq": 2,
            "session_id": "s1",
            "event_id": "assistant-1",
            "event_type": "assistant_message",
            "payload": {"content": "Never prune before durable scene commitment."},
        }
        pending_events = []
        captured_scenes = []

        def load_pending(_session_id):
            return [dict(event) for event in pending_events]

        def save_pending(_session_id, events):
            pending_events[:] = [dict(event) for event in events]

        with (
            patch.object(pipeline, "_load_pending_scene_events", side_effect=load_pending),
            patch.object(pipeline, "_save_pending_scene_events", side_effect=save_pending),
            patch.object(pipeline, "load_unprocessed_events", side_effect=[[user_event], [assistant_event]]),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_pending_user_message", return_value=""),
            patch.object(pipeline, "get_pending_user_message_seq", return_value=0),
            patch.object(pipeline, "set_pending_user_message"),
            patch.object(pipeline, "clear_pending_user_message"),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "append_scene", side_effect=lambda scene: captured_scenes.append(scene)),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "mark_scene_events_finalized"),
            patch.object(pipeline, "_retry_failed_extractions", return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0}),
            patch.object(
                pipeline,
                "run_memory_pipeline_outcome",
                return_value={"records": [{"id": "s1:1:0"}], "fallback_used": False, "skip_reason": None},
            ),
            patch.object(pipeline, "remove_retry_entries"),
        ):
            first = pipeline._process_session_events_impl("s1")
            second = pipeline._process_session_events_impl("s1")

        self.assertEqual(first["prompt_candidates"], 0)
        self.assertEqual(len(captured_scenes), 1)
        scene = captured_scenes[0]
        self.assertEqual(scene.evidence_status, "complete")
        self.assertEqual([event["event_id"] for event in scene.raw_events], ["user-1", "assistant-1"])
        self.assertEqual(second["prompt_candidates"], 1)

    def test_retry_replays_memory_extraction_from_durable_scene(self):
        scene = Scene(
            scene_id="s1:scene:assistant-1",
            session_id="s1",
            turn=1,
            kind="message_exchange",
            scene_seq=2,
            start_event_seq=1,
            end_event_seq=2,
            anchor_event_id="assistant-1",
            source_event_ids=["user-1", "assistant-1"],
            raw_events=[
                {"seq": 1, "session_id": "s1", "event_id": "user-1", "event_type": "user_message", "payload": {"content": "Question"}},
                {"seq": 2, "session_id": "s1", "event_id": "assistant-1", "event_type": "assistant_message", "payload": {"content": "Answer"}},
            ],
            evidence_version=1,
            evidence_status="complete",
            messages=[
                SceneMessage(role="user", content="Question", event_id="user-1"),
                SceneMessage(role="assistant", content="Answer", event_id="assistant-1"),
            ],
            extraction_user_text="Question",
            extraction_assistant_text="Answer",
            ts="2026-08-14T00:00:00+00:00",
        )
        with (
            patch.object(pipeline, "load_retry_queue", return_value=[{"session_id": "s1", "event_id": "assistant-1", "seq": 2}]),
            patch.object(pipeline, "get_session_scenes", return_value=[scene]),
            patch.object(pipeline, "get_recent_memories", return_value=[]),
            patch.object(
                pipeline,
                "run_memory_pipeline_outcome",
                return_value={"records": [{"id": "s1:1:0"}], "fallback_used": False},
            ) as run_pipeline,
            patch.object(pipeline, "remove_retry_entries") as remove_retry,
        ):
            result = pipeline._retry_failed_extractions("s1")

        self.assertEqual(result["retried_memories"], 1)
        self.assertEqual(result["recovered_retries"], 1)
        self.assertFalse(run_pipeline.call_args.kwargs["persist_scene"])
        remove_retry.assert_called_once_with("s1", {"assistant-1"})

    def test_recovery_finalizes_persisted_scene_and_schedules_missing_extraction(self):
        scene = Scene(
            scene_id="s1:scene:assistant-1",
            session_id="s1",
            turn=1,
            kind="message_exchange",
            scene_seq=2,
            start_event_seq=1,
            end_event_seq=2,
            anchor_event_id="assistant-1",
            source_event_ids=["user-1", "assistant-1"],
            raw_events=[
                {"seq": 1, "session_id": "s1", "event_id": "user-1", "event_type": "user_message", "payload": {"content": "Question"}},
                {"seq": 2, "session_id": "s1", "event_id": "assistant-1", "event_type": "assistant_message", "payload": {"content": "Answer"}},
            ],
            evidence_version=1,
            evidence_status="complete",
            messages=[
                SceneMessage(role="user", content="Question", event_id="user-1"),
                SceneMessage(role="assistant", content="Answer", event_id="assistant-1"),
            ],
            extraction_user_text="Question",
            extraction_assistant_text="Answer",
            ts="2026-08-14T00:00:00+00:00",
        )
        saved_pending = [dict(event) for event in scene.raw_events]

        with (
            patch.object(pipeline, "_load_pending_scene_events", return_value=saved_pending),
            patch.object(pipeline, "_save_pending_scene_events") as save_pending,
            patch.object(pipeline, "get_session_scenes", return_value=[scene]),
            patch.object(pipeline, "mark_scene_events_finalized") as mark_finalized,
            patch.object(pipeline, "append_retry_entry") as append_retry,
            patch.object(pipeline, "_retry_failed_extractions", return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0}),
            patch.object(pipeline, "load_unprocessed_events", return_value=[]),
        ):
            result = pipeline._process_session_events_impl("s1")

        mark_finalized.assert_called_once_with("s1", [1, 2])
        append_retry.assert_called_once_with(
            {"session_id": "s1", "event_id": "assistant-1", "seq": 2, "reason": "recovered_after_scene_commit"}
        )
        self.assertEqual(save_pending.call_args.args[1], [])
        self.assertEqual(result["processed_events"], 0)

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

    def test_session_boundary_flushes_open_evidence_before_finalizing_boundary(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"content": "An unmatched final question"},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "idle-1",
                "event_type": "session_idle",
                "payload": {"raw_type": "session.idle"},
            },
        ]
        order = []

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_pending_user_message", return_value=""),
            patch.object(pipeline, "get_pending_user_message_seq", return_value=0),
            patch.object(pipeline, "load_events_for_session", return_value=events),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "_retry_failed_extractions", return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0}),
            patch.object(pipeline, "set_pending_user_message"),
            patch.object(pipeline, "set_pending_scene_events"),
            patch.object(pipeline, "append_scene", side_effect=lambda scene: order.append(("scene", scene.scene_id))),
            patch.object(pipeline, "mark_scene_events_finalized", side_effect=lambda session_id, seqs: order.append(("finalized", list(seqs)))),
            patch.object(pipeline, "update_session_checkpoint", side_effect=lambda session_id, seq: order.append(("processed", seq))),
        ):
            pipeline._process_session_events_impl("s1")

        scene_position = next(index for index, item in enumerate(order) if item[0] == "scene")
        boundary_position = order.index(("finalized", [2]))
        self.assertLess(scene_position, boundary_position)

    def test_session_boundary_does_not_flush_a_new_turn_that_follows_it(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "idle-1",
                "event_type": "session_idle",
                "payload": {"raw_type": "session.idle"},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "user-2",
                "event_type": "user_message",
                "payload": {"content": "This starts the next turn"},
            },
        ]

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_pending_user_message", return_value=""),
            patch.object(pipeline, "get_pending_user_message_seq", return_value=0),
            patch.object(pipeline, "load_events_for_session", return_value=events),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "_retry_failed_extractions", return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0}),
            patch.object(pipeline, "set_pending_user_message"),
            patch.object(pipeline, "set_pending_scene_events"),
            patch.object(pipeline, "append_scene") as append_scene,
            patch.object(pipeline, "mark_scene_events_finalized"),
            patch.object(pipeline, "update_session_checkpoint"),
        ):
            pipeline._process_session_events_impl("s1")

        append_scene.assert_not_called()

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
        self.assertEqual([event["event_id"] for event in saved_scene.raw_events], [
            "tool-1",
            "user-1",
            "assistant-meta-1",
            "assistant-part-1",
        ])
        self.assertEqual(saved_scene.source_event_ids, [
            "tool-1",
            "user-1",
            "assistant-meta-1",
            "assistant-part-1",
        ])
        self.assertEqual(saved_scene.evidence_version, 1)
        self.assertEqual(saved_scene.evidence_status, "complete")
        self.assertEqual(saved_scene.messages[0].event_id, "user-1")
        self.assertEqual(saved_scene.messages[1].event_id, "assistant-part-1")
        self.assertEqual(saved_scene.tool_calls[0].event_id, "tool-1")
        self.assertEqual(saved_scene.tool_calls[0].name, "read")
        self.assertEqual(saved_scene.tool_calls[0].file_paths, ["app/storage/memories.py"])
        self.assertLessEqual(len(saved_scene.tool_calls[0].excerpt or ""), 500)

    def test_scene_raw_events_are_sanitized_and_control_events_are_excluded(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"content": "Use token sk-test-secret-123456789 in the example."},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "idle-1",
                "event_type": "session_idle",
                "payload": {"status": "idle"},
            },
        ]

        raw_events = pipeline._scene_raw_events(*events)

        self.assertEqual([event["event_id"] for event in raw_events], ["user-1"])
        self.assertEqual(raw_events[0]["payload"]["content"], "Use token [redacted] in the example.")

    def test_scene_is_partial_when_user_evidence_is_not_recoverable(self):
        event = {
            "seq": 4,
            "session_id": "s1",
            "event_id": "assistant-1",
            "event_type": "assistant_message",
            "payload": {"content": "I can continue from the saved context."},
        }

        scene = pipeline._build_scene_candidate(
            event,
            1,
            {"user_text": "The earlier user message", "assistant_text": event["payload"]["content"]},
            scene_events=[event],
        )

        self.assertEqual(scene.evidence_version, 1)
        self.assertEqual(scene.evidence_status, "partial")
        self.assertEqual(scene.missing_source_event_ids, [])
        self.assertIsNone(scene.messages[0].event_id)
        self.assertEqual(scene.messages[1].event_id, "assistant-1")

    def test_partial_scene_records_exact_missing_source_event_id(self):
        event = {
            "seq": 4,
            "session_id": "s1",
            "event_id": "assistant-1",
            "event_type": "assistant_message",
            "payload": {"content": "I can continue from the saved context."},
        }
        scene = pipeline._build_scene_candidate(
            event,
            1,
            {"user_text": "Earlier question", "assistant_text": event["payload"]["content"]},
            parent_message_id="user-message-1",
            scene_events=[event],
            source_event_id_by_message_id={"user-message-1": "user-event-1"},
        )

        self.assertEqual(scene.evidence_status, "partial")
        self.assertEqual(scene.missing_source_event_ids, ["user-event-1"])

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
        self.assertEqual(scene.messages[0].event_id, "user-long")
        self.assertEqual(scene.messages[1].event_id, "assistant-long")
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
