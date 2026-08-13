import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import app.storage.traces as traces


class IngestIdempotencyTests(unittest.TestCase):
    def test_valid_unterminated_jsonl_tail_is_separated_before_append(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            admitted = {
                "seq": 4,
                "session_id": "s1",
                "event_id": "evt-existing",
                "event_type": "user_message",
                "payload": {"content": "durable without trailing newline"},
            }
            ledger.write_text(json.dumps(admitted), encoding="utf-8")

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
            ):
                status, seq = traces.append_event(
                    {
                        "session_id": "s1",
                        "event_id": "evt-new",
                        "event_type": "assistant_message",
                        "payload": {"content": "new event"},
                    }
                )

            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual((status, seq), ("ingested", 5))
            self.assertEqual([row["event_id"] for row in rows], ["evt-existing", "evt-new"])
            self.assertTrue(ledger.read_bytes().endswith(b"\n"))

    def test_invalid_interrupted_jsonl_tail_is_truncated_before_append(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            admitted = {
                "seq": 1,
                "session_id": "s1",
                "event_id": "evt-existing",
                "event_type": "user_message",
                "payload": {"content": "durable"},
            }
            ledger.write_text(json.dumps(admitted) + "\n{\"seq\": 2, \"event_id\":", encoding="utf-8")

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
            ):
                status, seq = traces.append_event(
                    {
                        "session_id": "s1",
                        "event_id": "evt-new",
                        "event_type": "assistant_message",
                        "payload": {"content": "new event"},
                    }
                )

            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual((status, seq), ("ingested", 2))
            self.assertEqual([row["event_id"] for row in rows], ["evt-existing", "evt-new"])

    def test_sensitive_strings_are_redacted_before_event_admission(self):
        github_token = "_".join(["ghp", "abcdefghijklmnopqrstuvwxyz123456"])
        slack_token = "-".join(["xoxb", "1234567890", "abcdefghijklmnopqrstuvwxyz"])
        aws_access_key = "".join(["AKIA", "1234567890ABCDEF"])
        value = " ".join(
            [
                github_token,
                slack_token,
                aws_access_key,
                "AWS_SECRET_ACCESS_KEY=abcdefghijklmnopqrstuvwxyz0123456789ABCD",
                "DATABASE_PASSWORD=correct-horse-battery-staple",
                "-----BEGIN PRIVATE KEY-----private-material-----END PRIVATE KEY-----",
            ]
        )

        redacted = traces.sanitize_trace_value(value)

        self.assertNotIn("ghp_", redacted)
        self.assertNotIn("xoxb-", redacted)
        self.assertNotIn("AKIA", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789ABCD", redacted)
        self.assertNotIn("correct-horse", redacted)
        self.assertNotIn("private-material", redacted)
        self.assertGreaterEqual(redacted.count("[redacted]"), 6)

    def test_missing_index_entry_is_recovered_from_retained_ledger(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            admitted = {
                "seq": 7,
                "session_id": "s1",
                "event_id": "evt-crash",
                "event_type": "user_message",
                "payload": {"content": "already durable"},
            }
            ledger.write_text(json.dumps(admitted) + "\n", encoding="utf-8")

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
            ):
                status, seq = traces.append_event(admitted)

            self.assertEqual(status, "duplicate")
            self.assertIsNone(seq)
            self.assertEqual(json.loads(index.read_text(encoding="utf-8")), {"s1:evt-crash": 7})
            self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), 1)

    def test_duplicate_event_is_not_reingested(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            scene_checkpoints = tmp_path / "scene_checkpoints.json"
            retry_queue = tmp_path / "retry_queue.jsonl"

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SCENE_CHECKPOINT_FILE", scene_checkpoints),
                patch.object(traces, "COMMITTED_CHECKPOINT_FILE", scene_checkpoints),
                patch.object(traces, "RETRY_QUEUE_FILE", retry_queue),
            ):
                event = {
                    "session_id": "s1",
                    "event_id": "evt-1",
                    "event_type": "user_message",
                    "payload": {"text": "hello"},
                }

                status1, seq1 = traces.append_event(event)
                status2, seq2 = traces.append_event(event)

                self.assertEqual(status1, "ingested")
                self.assertEqual(seq1, 1)
                self.assertEqual(status2, "duplicate")
                self.assertIsNone(seq2)

                lines = ledger.read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(lines), 1)

    def test_duplicate_survives_committed_payload_pruning(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            scene_checkpoints = tmp_path / "scene_checkpoints.json"
            retry_queue = tmp_path / "retry_queue.jsonl"
            event = {
                "session_id": "s1",
                "event_id": "evt-1",
                "event_type": "user_message",
                "payload": {"text": "hello"},
            }

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SCENE_CHECKPOINT_FILE", scene_checkpoints),
                patch.object(traces, "COMMITTED_CHECKPOINT_FILE", scene_checkpoints),
                patch.object(traces, "RETRY_QUEUE_FILE", retry_queue),
            ):
                status, seq = traces.append_event(event)
                traces.mark_scene_events_finalized("s1", [seq])
                result = traces.prune_processed_events(["s1"])
                duplicate_status, duplicate_seq = traces.append_event(event)

                self.assertEqual(status, "ingested")
                self.assertEqual(seq, 1)
                self.assertEqual(result["removed"], 1)
                self.assertEqual(duplicate_status, "duplicate")
                self.assertIsNone(duplicate_seq)
                self.assertEqual(traces.load_event_index(), {"s1:evt-1": 1})
                self.assertEqual(ledger.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
