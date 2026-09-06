import unittest
from unittest.mock import Mock, patch

import app.save_pipeline.pipeline as pipeline
from app.save_pipeline.pipeline import run_memory_pipeline_outcome
from app.storage.models import Scene, SceneMessage, SceneToolCall


class ScenePipelineTests(unittest.TestCase):
    def test_tool_without_provenance_produces_partial_scene(self):
        events = [
            {"seq": 1, "session_id": "s1", "event_id": "u1", "event_type": "user_message", "payload": {"content": "question"}},
            {"seq": 2, "session_id": "s1", "event_id": "a1", "event_type": "assistant_message", "payload": {"content": "answer"}},
        ]
        scene = pipeline._build_scene_candidate(
            events[-1], 1, {"user_text": "question", "assistant_text": "answer"},
            scene_events=events,
            tool_calls=[{"name": "read", "summary": "unattributed tool output", "event_id": None}],
        )
        self.assertEqual(scene.evidence_status, "partial")
        self.assertIsNone(scene.tool_calls[0].event_id)

    def test_projection_preserves_distinct_repeated_messages(self):
        events = [
            {"seq": seq, "event_id": f"u{seq}", "event_type": "user_message",
             "payload": {"content": "same question"}}
            for seq in (1, 2)
        ]
        messages = pipeline._scene_message_projection(events, {})
        self.assertEqual([item.event_id for item in messages], ["u1", "u2"])
        self.assertEqual([item.content for item in messages], ["same question", "same question"])

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

    def test_scene_projection_preserves_all_ordered_messages_and_compact_tool_context(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"content": "Research the durable scene checkpoint."},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "tool-1",
                "event_type": "tool_execution",
                "payload": {
                    "tool": "read",
                    "call_id": "call-1",
                    "args": {"filePath": "app/storage/traces.py"},
                    "output": "checkpoint evidence",
                },
            },
            {
                "seq": 3,
                "session_id": "s1",
                "event_id": "user-2",
                "event_type": "user_message",
                "payload": {"content": "Please continue from that evidence."},
            },
            {
                "seq": 4,
                "session_id": "s1",
                "event_id": "assistant-1",
                "event_type": "assistant_message",
                "payload": {"content": "The checkpoint must follow durable scene commitment."},
            },
        ]

        scene = pipeline._build_scene_candidate(
            events[-1],
            1,
            {
                "user_text": events[2]["payload"]["content"],
                "assistant_text": events[3]["payload"]["content"],
            },
            scene_events=events,
            tool_calls=[
                {
                    "name": "read",
                    "call_id": "call-1",
                    "status": "success",
                    "summary": "read on app/storage/traces.py",
                    "file_paths": ["app/storage/traces.py"],
                    "excerpt": "checkpoint evidence",
                    "event_id": "tool-1",
                }
            ],
        )

        self.assertEqual(
            [(message.role, message.content) for message in scene.messages],
            [
                ("user", "Research the durable scene checkpoint."),
                ("user", "Please continue from that evidence."),
                ("assistant", "The checkpoint must follow durable scene commitment."),
            ],
        )
        self.assertIn("Research the durable scene checkpoint.", scene.extraction_user_text)
        self.assertIn("Please continue from that evidence.", scene.extraction_user_text)
        self.assertIn("app/storage/traces.py", scene.extraction_assistant_text)
        self.assertIn("checkpoint evidence", scene.extraction_assistant_text)
        self.assertEqual([event["event_id"] for event in scene.raw_events], ["user-1", "tool-1", "user-2", "assistant-1"])
        self.assertEqual(scene.source_event_ids, ["user-1", "tool-1", "user-2", "assistant-1"])

    def test_scene_projection_names_tool_evidence_omitted_from_bounded_context(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"content": "What did the tool report about the checkpoint?"},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "tool-result-1",
                "event_type": "tool_result",
                "payload": {"tool": "shell", "result": {"stdout": "raw result"}},
            },
            {
                "seq": 3,
                "session_id": "s1",
                "event_id": "assistant-1",
                "event_type": "assistant_message",
                "payload": {"content": "The result was retained as raw evidence."},
            },
        ]

        scene = pipeline._build_scene_candidate(
            events[-1],
            1,
            {"user_text": events[0]["payload"]["content"], "assistant_text": events[2]["payload"]["content"]},
            scene_events=events,
        )

        self.assertIn("omitted", scene.extraction_assistant_text)
        self.assertIn("scene_tools omitted: 1", scene.extraction_assistant_text)
        self.assertNotIn("tool-result-1", scene.extraction_assistant_text)
        self.assertEqual([event["event_id"] for event in scene.raw_events], ["user-1", "tool-result-1", "assistant-1"])

    def test_memory_lineage_maps_local_scene_refs_to_only_supporting_events(self):
        scene = Scene(
            scene_id="s1:scene:assistant-1",
            session_id="s1",
            turn=1,
            kind="message_exchange",
            scene_seq=3,
            start_event_seq=1,
            end_event_seq=3,
            anchor_event_id="assistant-1",
            source_event_ids=["user-1", "tool-1", "assistant-1"],
            raw_events=[
                {"seq": 1, "session_id": "s1", "event_id": "user-1", "event_type": "user_message", "payload": {"content": "How should the durable checkpoint implementation work?"}},
                {"seq": 2, "session_id": "s1", "event_id": "tool-1", "event_type": "tool_execution", "payload": {"tool": "read"}},
                {"seq": 3, "session_id": "s1", "event_id": "assistant-1", "event_type": "assistant_message", "payload": {"content": "The checkpoint implementation was completed."}},
            ],
            messages=[
                SceneMessage(role="user", content="How should the durable checkpoint implementation work?", event_id="user-1"),
                SceneMessage(role="assistant", content="The checkpoint implementation was completed.", event_id="assistant-1"),
            ],
            tool_calls=[SceneToolCall(name="read", summary="read checkpoint", event_id="tool-1")],
            extraction_user_text="How should the durable checkpoint implementation work?",
            extraction_assistant_text="The checkpoint implementation was completed.",
            used_context_fallback=False,
            ts="2026-09-05T00:00:00+00:00",
        )
        verifier = Mock()
        verifier.verify_memory.return_value = Mock(verified=False, confidence=0.0)

        with (
            patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()),
            patch(
                "app.save_pipeline.pipeline.extract_atomic_memories",
                return_value=[
                    {"text": "The user asked for checkpoint evidence.", "source": "user", "evidence_refs": ["m1"]},
                    {"text": "The read tool supplied checkpoint evidence.", "source": "assistant", "evidence_refs": ["t1"]},
                ],
            ),
            patch("app.save_pipeline.pipeline.embed", return_value=[]),
            patch("app.save_pipeline.pipeline.get_verifier", return_value=verifier),
            patch("app.save_pipeline.pipeline.load_settings", return_value={"verification": {"enabled": False}}),
            patch("app.save_pipeline.pipeline.append_memories") as append_memories,
            patch("app.save_pipeline.pipeline.append_scene"),
            patch("app.save_pipeline.pipeline.append_memory_notes"),
        ):
            outcome = run_memory_pipeline_outcome(
                session_id="s1",
                turn=1,
                user_text=scene.extraction_user_text,
                assistant_text=scene.extraction_assistant_text,
                source_event_ids=scene.source_event_ids,
                scene=scene,
            )

        self.assertEqual([record["source_event_ids"] for record in outcome["records"]], [["user-1"], ["tool-1"]])
        append_memories.assert_called_once_with(outcome["records"])

    def test_lineage_rejects_unseen_labels_and_unseen_tool_markers(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"content": "What happened during the checkpoint?"},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "tool-1",
                "event_type": "tool_result",
                "payload": {"tool": "shell", "result": {"stdout": "unavailable summary"}},
            },
            {
                "seq": 3,
                "session_id": "s1",
                "event_id": "assistant-1",
                "event_type": "assistant_message",
                "payload": {"content": "The raw result was retained."},
            },
        ]
        scene = pipeline._build_scene_candidate(
            events[-1],
            1,
            {"user_text": events[0]["payload"]["content"], "assistant_text": events[2]["payload"]["content"]},
            scene_events=events,
        )

        refs = pipeline._scene_evidence_ref_map(scene)
        self.assertNotIn("t1", refs)
        self.assertNotIn("e1", refs)
        self.assertNotIn("user-1", refs)
        self.assertEqual(
            pipeline._source_event_ids_for_memory(
                {"evidence_refs": ["t1"]}, scene=scene, fallback_source_event_ids=scene.source_event_ids
            ),
            [],
        )
        self.assertEqual(
            pipeline._source_event_ids_for_memory(
                {"evidence_refs": ["e99"]}, scene=scene, fallback_source_event_ids=scene.source_event_ids
            ),
            [],
        )

    def test_tool_character_bound_uses_one_aggregate_omission_notice(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"content": "Investigate the checkpoint implementation."},
            },
        ]
        tool_calls = []
        for index in range(20):
            event_id = f"tool-{index + 1}"
            events.append(
                {
                    "seq": index + 2,
                    "session_id": "s1",
                    "event_id": event_id,
                    "event_type": "tool_execution",
                    "payload": {"tool": "read"},
                }
            )
            tool_calls.append(
                {
                    "name": "read",
                    "status": "success",
                    "summary": "x" * 300,
                    "file_paths": ["app/storage/traces.py"],
                    "excerpt": "y" * 500,
                    "event_id": event_id,
                }
            )
        events.append(
            {
                "seq": 22,
                "session_id": "s1",
                "event_id": "assistant-1",
                "event_type": "assistant_message",
                "payload": {"content": "The implementation was investigated."},
            }
        )
        scene = pipeline._build_scene_candidate(
            events[-1],
            1,
            {"user_text": events[0]["payload"]["content"], "assistant_text": events[-1]["payload"]["content"]},
            scene_events=events,
            tool_calls=tool_calls,
        )

        tool_projection = scene.extraction_assistant_text.split("[scene_tool ", 1)[1]
        self.assertLessEqual(len("[scene_tool " + tool_projection), pipeline._MAX_SCENE_EXTRACTION_TOOL_CHARS)
        self.assertEqual(scene.extraction_assistant_text.count("[scene_tools omitted:"), 1)
        self.assertEqual(len(scene.raw_events), 22)
        self.assertEqual(len(scene.source_event_ids), 22)

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
            patch.object(pipeline, "clear_pending_user_message") as clear_pending,
            patch.object(pipeline, "append_scene", side_effect=lambda scene: order.append(("scene", scene.scene_id))),
            patch.object(pipeline, "mark_scene_events_finalized", side_effect=lambda session_id, seqs: order.append(("finalized", list(seqs)))),
            patch.object(pipeline, "update_session_checkpoint", side_effect=lambda session_id, seq: order.append(("processed", seq))),
        ):
            pipeline._process_session_events_impl("s1")

        scene_position = next(index for index, item in enumerate(order) if item[0] == "scene")
        boundary_position = order.index(("finalized", [2]))
        self.assertLess(scene_position, boundary_position)
        clear_pending.assert_called_once_with("s1")

    def test_session_close_flushes_unmatched_user_and_tools_and_clears_context(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"content": "Investigate the durable scene checkpoint."},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "tool-1",
                "event_type": "tool_execution",
                "payload": {
                    "tool": "read",
                    "call_id": "call-1",
                    "args": {"filePath": "app/storage/traces.py"},
                    "output": "checkpoint evidence",
                },
            },
            {
                "seq": 3,
                "session_id": "s1",
                "event_id": "close-1",
                "event_type": "session_closed",
                "payload": {"raw_type": "session_shutdown"},
            },
        ]
        captured_scenes = []

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_events_for_session", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_pending_user_message", return_value=""),
            patch.object(pipeline, "get_pending_user_message_seq", return_value=0),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "_load_pending_scene_events", return_value=[]),
            patch.object(pipeline, "_save_pending_scene_events"),
            patch.object(pipeline, "set_pending_user_message"),
            patch.object(pipeline, "clear_pending_user_message") as clear_pending,
            patch.object(pipeline, "append_scene", side_effect=captured_scenes.append),
            patch.object(pipeline, "mark_scene_events_finalized"),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(
                pipeline,
                "_retry_failed_extractions",
                return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0},
            ),
            patch.object(pipeline, "run_memory_pipeline_outcome") as run_pipeline,
        ):
            result = pipeline._process_session_events_impl("s1")

        self.assertEqual(result["prompt_candidates"], 0)
        self.assertEqual(len(captured_scenes), 1)
        self.assertEqual(captured_scenes[0].kind, "raw_event")
        self.assertEqual(
            [event["event_id"] for event in captured_scenes[0].raw_events],
            ["user-1", "tool-1"],
        )
        clear_pending.assert_called_once_with("s1")
        run_pipeline.assert_not_called()

    def test_later_close_does_not_filter_provider_context_needed_by_current_part(self):
        prior_batch = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-meta-1",
                "event_type": "message",
                "payload": {
                    "raw_type": "message.updated",
                    "body": {
                        "properties": {
                            "info": {"id": "u1", "role": "user", "content": "Question from prior batch"}
                        }
                    },
                },
            }
        ]
        current_batch = [
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "assistant-meta-1",
                "event_type": "message",
                "payload": {
                    "raw_type": "message.updated",
                    "body": {
                        "properties": {
                            "info": {"id": "a1", "role": "assistant", "parentID": "u1"}
                        }
                    },
                },
            },
            {
                "seq": 3,
                "session_id": "s1",
                "event_id": "assistant-part-1",
                "event_type": "message",
                "payload": {
                    "raw_type": "message.part.updated",
                    "body": {
                        "properties": {
                            "part": {"type": "text", "messageID": "a1", "text": "Answer from"}
                        }
                    },
                },
            },
            {
                "seq": 4,
                "session_id": "s1",
                "event_id": "assistant-part-2",
                "event_type": "message",
                "payload": {
                    "raw_type": "message.part.updated",
                    "body": {
                        "properties": {
                            "part": {"type": "text", "messageID": "a1", "text": "Answer from provider"}
                        }
                    },
                },
            },
            {
                "seq": 5,
                "session_id": "s1",
                "event_id": "close-1",
                "event_type": "session_closed",
                "payload": {"raw_type": "session_shutdown"},
            },
        ]
        captured_scenes = []
        load_batches = iter([prior_batch, current_batch])
        load_all_events = iter([prior_batch, prior_batch + current_batch])

        with (
            patch.object(pipeline, "load_unprocessed_events", side_effect=lambda *_args, **_kwargs: next(load_batches)),
            patch.object(pipeline, "load_events_for_session", side_effect=lambda *_args, **_kwargs: next(load_all_events)),
            patch.object(
                pipeline,
                "load_message_context",
                return_value=(
                    {"u1": "user"},
                    {},
                    {"u1": "Question from prior batch"},
                ),
            ),
            patch.object(pipeline, "get_pending_user_message", side_effect=["", "Question from prior batch"]),
            patch.object(pipeline, "get_pending_user_message_seq", return_value=0),
            patch.object(pipeline, "get_next_trace_turn", side_effect=[1, 2]),
            patch.object(pipeline, "_load_pending_scene_events", side_effect=[[], prior_batch]),
            patch.object(pipeline, "_save_pending_scene_events"),
            patch.object(pipeline, "set_pending_user_message"),
            patch.object(pipeline, "clear_pending_user_message"),
            patch.object(pipeline, "append_scene", side_effect=captured_scenes.append),
            patch.object(pipeline, "mark_scene_events_finalized"),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(
                pipeline,
                "_retry_failed_extractions",
                return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0},
            ),
            patch.object(
                pipeline,
                "run_memory_pipeline_outcome",
                return_value={"records": [{"id": "s1:2:0"}], "fallback_used": False, "skip_reason": None},
            ) as run_pipeline,
        ):
            first = pipeline._process_session_events_impl("s1")
            second = pipeline._process_session_events_impl("s1")

        self.assertEqual(first["prompt_candidates"], 0)
        self.assertEqual(second["prompt_candidates"], 1)
        self.assertEqual(len(captured_scenes), 1)
        self.assertEqual(run_pipeline.call_args.kwargs["user_text"], "Question from prior batch")
        self.assertEqual(run_pipeline.call_args.kwargs["assistant_text"], "Answer from provider")
        self.assertNotIn("approximate prior user context", run_pipeline.call_args.kwargs["user_text"])

    def test_closed_boundary_prevents_stale_context_and_tools_in_later_batch(self):
        first_batch = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"message_id": "provider-user-1", "content": "Old request"},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "tool-1",
                "event_type": "tool_execution",
                "payload": {"tool": "read", "output": "old output"},
            },
            {
                "seq": 3,
                "session_id": "s1",
                "event_id": "close-1",
                "event_type": "session_closed",
                "payload": {"raw_type": "session_shutdown"},
            },
        ]
        later_assistant = {
            "seq": 4,
            "session_id": "s1",
            "event_id": "assistant-2",
            "event_type": "assistant_message",
            "payload": {"content": "A later answer without a new user."},
        }
        all_events = first_batch + [later_assistant]
        captured_scenes = []
        load_batches = iter([first_batch, [later_assistant]])
        load_all_events = iter([first_batch, all_events])

        with (
            patch.object(pipeline, "load_unprocessed_events", side_effect=lambda *_args, **_kwargs: next(load_batches)),
            patch.object(pipeline, "load_events_for_session", side_effect=lambda *_args, **_kwargs: next(load_all_events)),
            patch.object(
                pipeline,
                "load_message_context",
                return_value=(
                    {"provider-user-1": "user"},
                    {},
                    {"provider-user-1": "Old request"},
                ),
            ),
            patch.object(pipeline, "get_pending_user_message", return_value="Old request"),
            patch.object(pipeline, "get_pending_user_message_seq", return_value=1),
            patch.object(pipeline, "get_next_trace_turn", side_effect=[1, 2]),
            patch.object(pipeline, "_load_pending_scene_events", return_value=[]),
            patch.object(pipeline, "_save_pending_scene_events"),
            patch.object(pipeline, "set_pending_user_message"),
            patch.object(pipeline, "clear_pending_user_message"),
            patch.object(pipeline, "append_scene", side_effect=captured_scenes.append),
            patch.object(pipeline, "mark_scene_events_finalized"),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(
                pipeline,
                "_retry_failed_extractions",
                return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0},
            ),
            patch.object(pipeline, "run_memory_pipeline_outcome") as run_pipeline,
        ):
            first = pipeline._process_session_events_impl("s1")
            second = pipeline._process_session_events_impl("s1")

        self.assertEqual(first["prompt_candidates"], 0)
        self.assertEqual(second["prompt_candidates"], 0)
        self.assertEqual(len(captured_scenes), 2)
        self.assertEqual([event["event_id"] for event in captured_scenes[0].raw_events], ["user-1", "tool-1"])
        self.assertEqual([event["event_id"] for event in captured_scenes[1].raw_events], ["assistant-2"])
        self.assertEqual(captured_scenes[1].tool_calls, [])
        run_pipeline.assert_not_called()

    def test_replayed_session_close_does_not_duplicate_flushed_scene(self):
        events = [
            {
                "seq": 1,
                "session_id": "s1",
                "event_id": "user-1",
                "event_type": "user_message",
                "payload": {"content": "An unmatched request"},
            },
            {
                "seq": 2,
                "session_id": "s1",
                "event_id": "close-1",
                "event_type": "session_closed",
                "payload": {"raw_type": "session_shutdown"},
            },
            {
                "seq": 3,
                "session_id": "s1",
                "event_id": "close-1",
                "event_type": "session_closed",
                "payload": {"raw_type": "session_shutdown"},
            },
        ]
        captured_scenes = []

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_events_for_session", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_pending_user_message", return_value=""),
            patch.object(pipeline, "get_pending_user_message_seq", return_value=0),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "_load_pending_scene_events", return_value=[]),
            patch.object(pipeline, "_save_pending_scene_events"),
            patch.object(pipeline, "set_pending_user_message"),
            patch.object(pipeline, "clear_pending_user_message"),
            patch.object(pipeline, "append_scene", side_effect=captured_scenes.append),
            patch.object(pipeline, "mark_scene_events_finalized"),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "run_memory_pipeline_outcome"),
            patch.object(
                pipeline,
                "_retry_failed_extractions",
                return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0},
            ),
        ):
            result = pipeline._process_session_events_impl("s1")

        self.assertEqual(result["prompt_candidates"], 0)
        self.assertEqual(len(captured_scenes), 1)
        self.assertEqual([event["event_id"] for event in captured_scenes[0].raw_events], ["user-1"])

    def test_session_end_boundary_alias_and_turn_completion_are_distinct(self):
        self.assertTrue(
            pipeline._is_explicit_scene_boundary_event(
                {"event_type": "session_end", "payload": {}}
            )
        )
        self.assertTrue(
            pipeline._is_explicit_scene_boundary_event(
                {"event_type": "unknown", "payload": {"raw_type": "session_shutdown"}}
            )
        )
        self.assertTrue(
            pipeline._is_explicit_scene_boundary_event(
                {"event_type": "unknown", "payload": {"raw_type": "session.ended"}}
            )
        )
        self.assertFalse(
            pipeline._is_explicit_scene_boundary_event(
                {"event_type": "turn_complete", "payload": {"raw_type": "turn_end"}}
            )
        )

        roles, parents, texts = pipeline._discard_message_context_before_boundary(
            {"a1": "assistant"},
            {"a1": "u1"},
            {"u1": "old user"},
            [
                {"seq": 1, "event_id": "assistant-meta-1", "event_type": "message", "payload": {}},
                {"seq": 2, "event_id": "close-1", "event_type": "session_closed", "payload": {}},
                {"seq": 3, "event_id": "part-1", "event_type": "message", "payload": {}},
            ],
            {"a1": "assistant-meta-1", "u1": "user-1"},
            event_lower_bound=3,
        )
        self.assertEqual(roles, {"a1": "assistant"})
        self.assertEqual(parents, {})
        self.assertEqual(texts, {})

    def test_session_end_boundary_flushes_open_evidence(self):
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
                "event_id": "end-1",
                "event_type": "session_end",
                "payload": {"raw_type": "session_end"},
            },
        ]
        captured_scenes = []

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_events_for_session", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_pending_user_message", return_value=""),
            patch.object(pipeline, "get_pending_user_message_seq", return_value=0),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "_load_pending_scene_events", return_value=[]),
            patch.object(pipeline, "_save_pending_scene_events"),
            patch.object(pipeline, "set_pending_user_message"),
            patch.object(pipeline, "clear_pending_user_message"),
            patch.object(pipeline, "append_scene", side_effect=captured_scenes.append),
            patch.object(pipeline, "mark_scene_events_finalized"),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "run_memory_pipeline_outcome"),
            patch.object(
                pipeline,
                "_retry_failed_extractions",
                return_value={"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0},
            ),
        ):
            result = pipeline._process_session_events_impl("s1")

        self.assertEqual(result["prompt_candidates"], 0)
        self.assertEqual(len(captured_scenes), 1)
        self.assertEqual([event["event_id"] for event in captured_scenes[0].raw_events], ["user-1"])

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

    def test_run_memory_pipeline_outcome_persists_immediately_when_legacy_dedup_enabled(self):
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
            patch("app.save_pipeline.pipeline.append_memories") as mock_append_memories,
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


if __name__ == "__main__":
    unittest.main()
