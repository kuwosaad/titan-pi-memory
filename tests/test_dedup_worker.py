import unittest
from unittest.mock import patch

from app.save_pipeline import dedup_worker


class DedupWorkerTests(unittest.TestCase):
    def test_store_merged_reuses_source_metadata_from_original_entries(self):
        original_entries = [
            {
                "id": "s1:3:0",
                "session_id": "s1",
                "turn": 3,
                "scene_id": "s1:scene:e-1",
                "text": "Use session_id for dedupe.",
                "type": "decision",
                "stream": "learnings",
                "ts": "2026-04-09T00:00:00+00:00",
                "source_type": "user",
                "source_reliability": 0.9,
                "verification_status": "unverified",
                "fallback_generated": False,
                "source_event_ids": ["e-1"],
                "provenance": {"user": "How should dedupe work?", "assistant": "Use session_id."},
                "speaker_focus": "system",
                "memory_kind": "decision",
                "embedding": [0.1, 0.2],
            },
            {
                "id": "s1:4:0",
                "session_id": "s1",
                "turn": 4,
                "scene_id": "s1:scene:e-2",
                "text": "Use event_id for dedupe.",
                "type": "decision",
                "stream": "learnings",
                "ts": "2026-04-09T00:01:00+00:00",
                "source_type": "user",
                "source_reliability": 0.9,
                "verification_status": "unverified",
                "fallback_generated": False,
                "source_event_ids": ["e-2"],
                "provenance": {"user": "What else?", "assistant": "Use event_id."},
                "speaker_focus": "system",
                "memory_kind": "decision",
                "embedding": [0.3, 0.4],
            },
        ]

        merged = [
            {
                "text": "Use session_id and event_id for dedupe.",
                "stream": "learnings",
                "type": "decision",
                "speaker_focus": "system",
                "memory_kind": "decision",
                "merged_from_ids": ["s1:3:0", "s1:4:0"],
            }
        ]

        with patch("app.save_pipeline.dedup_worker.append_memories") as mock_append_memories:
            records = dedup_worker._store_merged(merged, original_entries)

        self.assertEqual(records[0]["id"], "s1:3:0")
        self.assertEqual(records[0]["session_id"], "s1")
        self.assertEqual(records[0]["scene_id"], "s1:scene:e-1")
        self.assertEqual(records[0]["source_event_ids"], ["e-1", "e-2"])
        self.assertEqual(records[0]["text"], "Use session_id and event_id for dedupe.")
        mock_append_memories.assert_called_once_with(records)


if __name__ == "__main__":
    unittest.main()
