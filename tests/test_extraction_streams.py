import unittest

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


if __name__ == "__main__":
    unittest.main()
