import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.storage.traces as traces


class IngestIdempotencyTests(unittest.TestCase):
    def test_duplicate_event_is_not_reingested(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"

            with patch.object(traces, "EVENT_LEDGER_FILE", ledger), patch.object(traces, "EVENT_INDEX_FILE", index), patch.object(traces, "CHECKPOINT_FILE", checkpoints):
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


if __name__ == "__main__":
    unittest.main()
