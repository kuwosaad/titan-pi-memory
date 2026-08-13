import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.save_pipeline import pipeline
from app.storage import traces


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
    def test_retry_append_recovers_from_interrupted_jsonl_tail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            retry_file = Path(tmp_dir) / "retry_queue.jsonl"
            retry_file.write_text('{"session_id": "old"', encoding="utf-8")

            with patch.object(traces, "RETRY_QUEUE_FILE", retry_file):
                traces.append_retry_entry(
                    {
                        "session_id": "s1",
                        "event_id": "e1",
                        "seq": 1,
                        "reason": "extraction_failed",
                    }
                )
                loaded = traces.load_retry_queue("s1")

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["event_id"], "e1")
            for line in retry_file.read_text(encoding="utf-8").splitlines():
                json.loads(line)

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
            patch.object(pipeline, "remove_retry_entries") as remove_retry_entries,
            patch.object(pipeline, "update_session_checkpoint") as update_session_checkpoint,
            patch.object(pipeline, "get_next_trace_turn", return_value=1),
        ):
            result = pipeline.process_session_events("default")

        append_retry_entry.assert_called_once()
        remove_retry_entries.assert_called_once_with("default", {"evt-10"})
        update_session_checkpoint.assert_called_with("default", 10)
        self.assertEqual(result["prompt_candidates"], 1)
        self.assertEqual(result["queued_retries"], 0)
        self.assertEqual(result["stored_memories"], 0)


if __name__ == "__main__":
    unittest.main()
