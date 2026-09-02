import unittest
from types import MappingProxyType
from unittest.mock import patch

from app.save_pipeline.extraction.extractor import extract_atomic_memories


class FakeAdapter:
    def chat(self, messages, format_hint=None, temperature=None):
        return """{
  "memories": [
    {"text": "We worked on retrieval pipeline in the previous session.", "type": "fact", "stream": "rough", "source": "user"},
    {"text": "Memory retrieval should always dedupe by canonical text.", "type": "decision", "source": "user"}
  ]
}"""


class ExtractionStreamTests(unittest.TestCase):
    def test_extraction_outputs_rough_and_learnings_streams(self):
        adapter = FakeAdapter()
        memories = extract_atomic_memories(
            user_text="Last session we worked on retrieval. We should dedupe by canonical text.",
            assistant_text="Agreed.",
            adapter=adapter,
        )

        self.assertGreaterEqual(len(memories), 2)
        self.assertTrue(all(mem.get("stream") in {"rough", "learnings"} for mem in memories))

        streams = {mem.get("stream") for mem in memories}
        self.assertIn("rough", streams)
        self.assertIn("learnings", streams)

    def test_extraction_uses_namespace_identity_settings(self):
        class CapturingAdapter:
            messages = []

            def chat(self, messages, format_hint=None, temperature=None):
                self.messages = messages
                return """{
  "memories": [
    {"text": "Ayanokoji will keep Saad's answers direct.", "type": "commitment", "stream": "learnings", "source": "assistant", "memory_kind": "commitment"}
  ]
}"""

        adapter = CapturingAdapter()
        settings = {
            "identity": MappingProxyType({
                "user_display_name": "Saad",
                "assistant_display_name": "Ayanokoji",
            }),
            "source_reliability": {"assistant": 0.3},
        }

        with patch("app.retrieval_pipeline.config.load_settings", return_value=settings):
            memories = extract_atomic_memories(
                user_text="Remember that I prefer direct answers.",
                assistant_text="I will keep future answers direct.",
                adapter=adapter,
            )

        self.assertEqual([message["role"] for message in adapter.messages], ["system", "user"])
        self.assertIn("Saad", adapter.messages[0]["content"])
        self.assertIn("Ayanokoji", adapter.messages[0]["content"])
        self.assertNotIn("Remember that I prefer direct answers", adapter.messages[0]["content"])
        self.assertIn("Remember that I prefer direct answers", adapter.messages[1]["content"])
        self.assertEqual(memories[0]["speaker_focus"], "assistant")


if __name__ == "__main__":
    unittest.main()
