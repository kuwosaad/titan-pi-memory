import unittest
from unittest.mock import patch

from app.save_pipeline import pipeline


def _assistant_reply_event() -> dict:
    return {
        "seq": 10,
        "event_id": "evt-10",
        "event_type": "message",
        "payload": {
            "raw_type": "message.part.updated",
            "body": {
                "properties": {
                    "part": {
                        "messageID": "a1",
                        "type": "text",
                        "text": "Here is the answer.",
                    }
                }
            },
        },
    }


class RetryQueueTests(unittest.TestCase):
    def test_empty_extraction_does_not_queue_retry_or_keep_raw_backlog(self):
        events = [
            _assistant_reply_event(),
        ]

        with (
            patch.object(pipeline, "load_unprocessed_events", return_value=events),
            patch.object(
                pipeline,
                "load_message_context",
                return_value=(
                    {"a1": "assistant", "u1": "user"},
                    {"a1": "u1"},
                    {"u1": "user asked about retry behavior"},
                ),
            ),
            patch.object(pipeline, "run_memory_pipeline_outcome", return_value={"records": [], "fallback_used": False}),
            patch.object(pipeline, "append_retry_entry") as append_retry_entry,
            patch.object(pipeline, "update_session_checkpoint") as update_session_checkpoint,
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
        ):
            result = pipeline.process_session_events("default")

        append_retry_entry.assert_not_called()
        update_session_checkpoint.assert_called_with("default", 10)
        self.assertEqual(result["prompt_candidates"], 1)
        self.assertEqual(result["queued_retries"], 0)
        self.assertEqual(result["stored_memories"], 0)


if __name__ == "__main__":
    unittest.main()
