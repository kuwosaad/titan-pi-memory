import unittest

from app.save_pipeline.extraction.extractor import sanitize_memories


class ExtractionSanitizationTests(unittest.TestCase):
    def test_drops_transport_metadata_memories(self):
        sanitized = sanitize_memories(
            [
                {"text": "A message.updated event was captured and stored for memory processing."},
                {"text": "A user message was received in a conversation with Karu."},
                {"text": "The user recently visited Vienna and presented at a conference."},
            ]
        )
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0]["text"], "The user recently visited Vienna and presented at a conference.")


if __name__ == "__main__":
    unittest.main()
