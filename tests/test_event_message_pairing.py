import unittest
from unittest.mock import patch

from app.save_pipeline import pipeline


def _message_updated_event(seq: int, event_id: str, message_id: str, role: str, parent_id: str | None = None) -> dict:
    info = {
        "id": message_id,
        "role": role,
    }
    if parent_id:
        info["parentID"] = parent_id
    return {
        "seq": seq,
        "event_id": event_id,
        "event_type": "message",
        "payload": {
            "raw_type": "message.updated",
            "body": {"properties": {"info": info}},
        },
    }


def _message_part_event(seq: int, event_id: str, message_id: str, text: str) -> dict:
    return {
        "seq": seq,
        "event_id": event_id,
        "event_type": "message",
        "payload": {
            "raw_type": "message.part.updated",
            "body": {
                "properties": {
                    "part": {
                        "messageID": message_id,
                        "type": "text",
                        "text": text,
                    }
                }
            },
        },
    }


class EventMessagePairingTests(unittest.TestCase):
    def test_processes_final_assistant_reply_with_parent_user_message(self):
        events = [
            _message_updated_event(1, "e1", "u1", "user"),
            _message_part_event(2, "e2", "u1", "I visited Vienna recently."),
            _message_updated_event(3, "e3", "a1", "assistant", parent_id="u1"),
            _message_part_event(4, "e4", "a1", "That sounds amazing"),
            _message_part_event(5, "e5", "a1", "That sounds amazing. What was your highlight?"),
        ]

        captured_prompts = []

        def _fake_run_memory_pipeline(**kwargs):
            captured_prompts.append((kwargs["user_text"], kwargs["assistant_text"]))
            return {"records": [{"text": "memory"}], "fallback_used": False}

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "run_memory_pipeline_outcome", side_effect=_fake_run_memory_pipeline),
        ):
            result = pipeline.process_session_events("default")

        self.assertEqual(result["stored_memories"], 1)
        self.assertEqual(captured_prompts, [("I visited Vienna recently.", "That sounds amazing. What was your highlight?")])

    def test_uses_recent_user_context_when_parent_user_message_missing(self):
        events = [
            _message_updated_event(1, "e1", "u1", "user"),
            _message_part_event(2, "e2", "u1", "I need help with a sensitive issue."),
            _message_updated_event(3, "e3", "a1", "assistant"),
            _message_part_event(4, "e4", "a1", "I hear you, and I can help."),
        ]

        captured_prompts = []

        def _fake_run_memory_pipeline(**kwargs):
            captured_prompts.append((kwargs["user_text"], kwargs["assistant_text"]))
            return {"records": [{"text": "memory"}], "fallback_used": False}

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "run_memory_pipeline_outcome", side_effect=_fake_run_memory_pipeline),
        ):
            result = pipeline.process_session_events("default")

        self.assertEqual(result["stored_memories"], 1)
        self.assertEqual(
            captured_prompts,
            [("[approximate prior user context] I need help with a sensitive issue.", "I hear you, and I can help.")],
        )

    def test_skips_assistant_text_when_parent_and_user_context_missing(self):
        events = [
            _message_updated_event(1, "e1", "a1", "assistant"),
            _message_part_event(2, "e2", "a1", "Can you share more detail?"),
        ]

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "run_memory_pipeline_outcome") as run_memory_pipeline,
        ):
            result = pipeline.process_session_events("default")

        self.assertEqual(result["stored_memories"], 0)
        run_memory_pipeline.assert_not_called()

    def test_uses_historical_user_text_when_parent_message_is_in_previous_batch(self):
        events = [
            _message_updated_event(10, "e10", "a1", "assistant", parent_id="u1"),
            _message_part_event(11, "e11", "a1", "I can help summarize that."),
        ]

        captured_prompts = []

        def _fake_run_memory_pipeline(**kwargs):
            captured_prompts.append((kwargs["user_text"], kwargs["assistant_text"]))
            return {"records": [{"text": "memory"}], "fallback_used": False}

        with (
            patch.object(
                pipeline,
                "load_message_context",
                return_value=(
                    {"u1": "user"},
                    {"a1": "u1"},
                    {"u1": "I lost my AirPods in Vienna."},
                ),
            ),
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "run_memory_pipeline_outcome", side_effect=_fake_run_memory_pipeline),
        ):
            result = pipeline.process_session_events("default")

        self.assertEqual(result["stored_memories"], 1)
        self.assertEqual(captured_prompts, [("I lost my AirPods in Vienna.", "I can help summarize that.")])

    def test_uses_message_updated_summary_when_user_part_text_is_missing(self):
        events = [
            _message_updated_event(1, "e1", "u1", "user"),
            {
                "seq": 2,
                "event_id": "e2",
                "event_type": "message",
                "payload": {
                    "raw_type": "message.updated",
                    "body": {"properties": {"info": {"id": "u1", "role": "user", "summary": "User asked for a simple explanation."}}},
                },
            },
            _message_updated_event(3, "e3", "a1", "assistant", parent_id="u1"),
            _message_part_event(4, "e4", "a1", "Sure, here is a simple version."),
        ]

        captured_prompts = []

        def _fake_run_memory_pipeline(**kwargs):
            captured_prompts.append((kwargs["user_text"], kwargs["assistant_text"]))
            return {"records": [{"text": "memory"}], "fallback_used": False}

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "run_memory_pipeline_outcome", side_effect=_fake_run_memory_pipeline),
        ):
            result = pipeline.process_session_events("default")

        self.assertEqual(result["stored_memories"], 1)
        self.assertEqual(captured_prompts, [("User asked for a simple explanation.", "Sure, here is a simple version.")])

    def test_passes_scene_context_into_memory_pipeline(self):
        events = [
            _message_updated_event(1, "e1", "u1", "user"),
            _message_part_event(2, "e2", "u1", "Why did we change dedupe?"),
            _message_updated_event(3, "e3", "a1", "assistant", parent_id="u1"),
            _message_part_event(4, "e4", "a1", "We changed dedupe to use session_id and event_id together."),
        ]

        captured_scenes = []

        def _fake_run_memory_pipeline(**kwargs):
            captured_scenes.append(kwargs["scene"])
            return {"records": [{"text": "memory"}], "fallback_used": False}

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(pipeline, "load_message_context", return_value=({}, {}, {})),
            patch.object(pipeline, "get_next_trace_turn", return_value=7),
            patch.object(pipeline, "update_session_checkpoint"),
            patch.object(pipeline, "run_memory_pipeline_outcome", side_effect=_fake_run_memory_pipeline),
        ):
            result = pipeline.process_session_events("default")

        self.assertEqual(result["stored_memories"], 1)
        self.assertEqual(len(captured_scenes), 1)
        scene = captured_scenes[0]
        self.assertEqual(scene.scene_id, "default:scene:e4")
        self.assertEqual(scene.turn, 7)
        self.assertEqual([message.role for message in scene.messages], ["user", "assistant"])
        self.assertIn("Why did we change dedupe", scene.extraction_user_text)
        self.assertIn("session_id and event_id", scene.extraction_assistant_text)


if __name__ == "__main__":
    unittest.main()
