import unittest
from unittest.mock import patch

from app.save_pipeline.pipeline import run_memory_pipeline_outcome


class ExtractionFallbackTests(unittest.TestCase):
    def test_generates_fallback_when_extractor_returns_empty(self):
        with (
            patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()),
            patch("app.save_pipeline.pipeline.extract_atomic_memories", return_value=[]),
            patch("app.save_pipeline.pipeline.embed", return_value=[]),
            patch("app.save_pipeline.pipeline.append_memories"),
            patch("app.save_pipeline.pipeline.append_memory_notes"),
        ):
            outcome = run_memory_pipeline_outcome(
                session_id="default",
                turn=1,
                user_text="can you explain this in simple words?",
                assistant_text="yes, the fix makes sure memory opt-out really disables retrieval.",
                fallback_enabled=True,
            )

        self.assertTrue(outcome["fallback_used"])
        self.assertGreaterEqual(len(outcome["records"]), 1)
        texts = [record["text"] for record in outcome["records"]]
        self.assertTrue(any("simple" in text.lower() for text in texts))

    def test_skips_fallback_for_thin_trace_prompt(self):
        with (
            patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()),
            patch("app.save_pipeline.pipeline.extract_atomic_memories") as extract_atomic_memories,
            patch("app.save_pipeline.pipeline.embed", return_value=[]),
            patch("app.save_pipeline.pipeline.append_memories"),
            patch("app.save_pipeline.pipeline.append_memory_notes"),
        ):
            outcome = run_memory_pipeline_outcome(
                session_id="default",
                turn=1,
                user_text="Goal: Conversation: hey\nThoughts: hey\nTool Calls: []\nIntent Phrase: telegram inbound memory capture\nContext: {}",
                assistant_text="Outcome: User message in conversation with Karu",
                fallback_enabled=True,
            )

        self.assertFalse(outcome["fallback_used"])
        self.assertEqual(outcome["records"], [])
        self.assertEqual(outcome["skip_reason"], "telegram_transport_only")
        extract_atomic_memories.assert_not_called()


if __name__ == "__main__":
    unittest.main()
