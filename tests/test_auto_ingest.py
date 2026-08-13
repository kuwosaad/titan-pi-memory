import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.save_pipeline.auto_ingest import discover_spool_sessions, ingest_available_sessions


class AutoIngestTests(unittest.TestCase):
    def test_discovers_spool_sessions_from_jsonl_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            spool_dir = Path(tmp_dir)
            (spool_dir / "default.jsonl").write_text("{}", encoding="utf-8")
            (spool_dir / "abc123.jsonl").write_text("{}", encoding="utf-8")
            (spool_dir / "ignore.txt").write_text("{}", encoding="utf-8")

            sessions = discover_spool_sessions(spool_dir)

        self.assertEqual(sessions, ["abc123", "default"])

    @patch("app.save_pipeline.auto_ingest.ingest_spool_session")
    def test_ingests_each_discovered_session(self, mock_ingest_spool_session):
        mock_ingest_spool_session.return_value = {
            "ingested": 1,
            "processed_events": 2,
            "prompt_candidates": 2,
            "stored_memories": 1,
            "fallback_memories": 1,
            "queued_retries": 0,
            "skipped_low_signal": 0,
            "retry_queue_size": 0,
            "processed_sessions": ["default", "sess-a"],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            spool_dir = Path(tmp_dir)
            (spool_dir / "default.jsonl").write_text("{}", encoding="utf-8")
            (spool_dir / "sess-a.jsonl").write_text("{}", encoding="utf-8")

            results = ingest_available_sessions(spool_dir)

        self.assertEqual(set(results.keys()), {"default", "sess-a"})
        self.assertIn("prompt_candidates", results["default"])
        self.assertIn("queued_retries", results["default"])
        self.assertEqual(mock_ingest_spool_session.call_count, 2)
        mock_ingest_spool_session.assert_any_call(session_id="default", spool_dir=str(spool_dir))
        mock_ingest_spool_session.assert_any_call(session_id="sess-a", spool_dir=str(spool_dir))


if __name__ == "__main__":
    unittest.main()
