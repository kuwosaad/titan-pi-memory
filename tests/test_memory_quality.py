import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.save_pipeline.extraction.extractor import assess_memory_worthiness, is_hidden_metadata_memory, sanitize_memories
from app.storage import traces


class MemoryQualityTests(unittest.TestCase):
    def test_rejects_transport_shaped_memories(self):
        sanitized = sanitize_memories(
            [
                {"text": "The agent's goal is to have a conversation with Karu."},
                {"text": "Kuwo asked Karu to remember his preference for simple explanations."},
            ]
        )

        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0]["speaker_focus"], "kuwo")

    def test_marks_meaningful_exchange_as_memory_worthy(self):
        result = assess_memory_worthiness(
            "Kuwo asked Karu to explain Titan in simple words and remember that preference.",
            "Karu agreed to keep explanations simple going forward.",
        )

        self.assertTrue(result["should_extract"])
        self.assertTrue(result["allow_fallback"])
        self.assertIsNone(result["skip_reason"])

    def test_rejects_trace_packet_that_is_only_inbound_banter(self):
        result = assess_memory_worthiness(
            "Goal: Conversation: Ur the best karu\nThoughts: Ur the best karu\nTool Calls: []\nIntent Phrase: telegram inbound memory capture\nContext: {'conversation_key': 'telegram:default:telegram:876708125'}",
            "Outcome: User message in conversation with Karu",
        )

        self.assertFalse(result["should_extract"])
        self.assertEqual(result["skip_reason"], "telegram_shallow_social")

    def test_redacts_secrets_before_trace_persistence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_file = Path(tmp_dir) / "trace_packets.json"
            with patch.object(traces, "TRACE_FILE", trace_file):
                traces.append_trace(
                    {
                        "session_id": "sess-1",
                        "goal": "Configure integrations",
                        "thoughts": "Use Notion token ntn_12345678901234567890, api key sk-live-1234567890 and bearer token Bearer abcdefghijklmnop",
                        "context": {"api_key": "sk-live-FAKEKEY890", "safe": "keep this"},
                        "notes": "Also have a Notion secret_FAKEKEY1234567890AbCdEf1234 and Stripe sk_live_FAKEKEYqrstuvwx",
                    }
                )

            contents = trace_file.read_text(encoding="utf-8")
            self.assertNotIn("ntn_12345678901234567890", contents)
            self.assertNotIn("sk-live-1234567890", contents)
            self.assertNotIn("sk-live-FAKEKEY890", contents)
            self.assertNotIn("secret_FAKEKEY1234567890AbCdEf1234", contents)
            self.assertNotIn("sk_live_FAKEKEYqrstuvwx", contents)
            self.assertNotIn("Bearer abcdefghijklmnop", contents)
            self.assertIn("[redacted]", contents)

    def test_hides_telegram_transport_memories_from_views(self):
        self.assertTrue(
            is_hidden_metadata_memory(
                {"text": "Karu received a telegram message from user 876708125, message id 1663, via the openclaw-hook:titan-karu-bridge integration."}
            )
        )
        self.assertFalse(
            is_hidden_metadata_memory(
                {"text": "Kuwo asked Karu to plan first and then implement efficiently."}
            )
        )


if __name__ == "__main__":
    unittest.main()
