import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.storage.traces as traces


class SpoolSessionIdTests(unittest.TestCase):
    def test_spool_ingest_applies_session_id_from_filename(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)

            session_id = "sess-42"
            spool_file = spool_dir / f"{session_id}.jsonl"
            spool_file.write_text(
                json.dumps({"event_id": "evt-1", "event_type": "user_message", "payload": {"save_intent": False}})
                + "\n",
                encoding="utf-8",
            )

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with patch.object(traces, "EVENT_LEDGER_FILE", ledger), patch.object(traces, "EVENT_INDEX_FILE", index), patch.object(traces, "CHECKPOINT_FILE", checkpoints), patch.object(traces, "SPOOL_CURSOR_FILE", cursors):
                result = traces.ingest_spool_file(session_id=session_id, spool_dir=spool_dir)

            self.assertEqual(result["ingested"], 1)
            line = ledger.read_text(encoding="utf-8").strip().splitlines()[0]
            data = json.loads(line)
            self.assertEqual(data["session_id"], session_id)

    def test_spool_ingest_prefers_nested_payload_session_id_over_default(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)

            spool_file = spool_dir / "default.jsonl"
            spool_file.write_text(
                json.dumps(
                    {
                        "session_id": "default",
                        "event_id": "evt-2",
                        "event_type": "message",
                        "payload": {
                            "raw_type": "message.part.updated",
                            "body": {
                                "properties": {
                                    "part": {
                                        "messageID": "msg-1",
                                        "sessionID": "sess-real-99",
                                        "type": "text",
                                        "text": "hello",
                                    }
                                }
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with patch.object(traces, "EVENT_LEDGER_FILE", ledger), patch.object(traces, "EVENT_INDEX_FILE", index), patch.object(traces, "CHECKPOINT_FILE", checkpoints), patch.object(traces, "SPOOL_CURSOR_FILE", cursors):
                result = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)

            self.assertEqual(result["ingested"], 1)
            self.assertEqual(result["sessions_touched"], ["sess-real-99"])
            line = ledger.read_text(encoding="utf-8").strip().splitlines()[0]
            data = json.loads(line)
            self.assertEqual(data["session_id"], "sess-real-99")


if __name__ == "__main__":
    unittest.main()
