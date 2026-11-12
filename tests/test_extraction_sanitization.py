import unittest

from app.save_pipeline.extraction.extractor import sanitize_memories


class ExtractionSanitizationTests(unittest.TestCase):
    def test_preserves_leading_numeric_claims_and_their_distinct_evidence(self):
        claims = ["2026 is the target release year.", "2027 is the target release year.",
                  "-5 is the lower threshold.", "3.14 is the configured tolerance."]
        result = sanitize_memories([{"text": text, "evidence_refs": [f"m{index}"]} for index, text in enumerate(claims)])
        self.assertEqual([memory["text"] for memory in result], claims)
        self.assertEqual([memory["evidence_refs"] for memory in result], [[f"m{index}"] for index in range(len(claims))])

    def test_list_notation_is_removed_without_losing_repeated_claim_evidence(self):
        result = sanitize_memories([
            {"text": "1. Use SQLite locally.", "source": "user", "evidence_refs": ["m1"]},
            {"text": "- Use SQLite locally.", "source": "user", "evidence_refs": ["m2", "m1"]},
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "Use SQLite locally.")
        self.assertEqual(result[0]["evidence_refs"], ["m1", "m2"])

    def test_case_sensitive_code_claims_remain_distinct(self):
        claims = ["Set HTTP_PROXY to proxy.local.", "Set http_proxy to proxy.local."]
        result = sanitize_memories([
            {"text": text, "evidence_refs": [f"m{index}"]}
            for index, text in enumerate(claims)
        ])
        self.assertEqual([memory["text"] for memory in result], claims)
        self.assertEqual([memory["evidence_refs"] for memory in result], [["m0"], ["m1"]])

    def test_identical_text_with_different_metadata_is_preserved(self):
        variants = [
            {"source": "user", "stream": "learnings", "speaker_focus": "user", "memory_kind": "decision"},
            {"source": "assistant", "stream": "learnings", "speaker_focus": "user", "memory_kind": "decision"},
            {"source": "user", "stream": "rough", "speaker_focus": "user", "memory_kind": "decision"},
            {"source": "user", "stream": "learnings", "speaker_focus": "system", "memory_kind": "decision"},
            {"source": "user", "stream": "learnings", "speaker_focus": "user", "memory_kind": "workflow"},
        ]
        result = sanitize_memories([{"text": "Use SQLite locally.", **variant, "evidence_refs": [f"m{index}"]} for index, variant in enumerate(variants)])
        self.assertEqual(len(result), len(variants))
        self.assertEqual([memory["evidence_refs"] for memory in result], [[f"m{index}"] for index in range(len(variants))])

    def test_drops_transport_metadata_memories(self):
        sanitized = sanitize_memories(
            [
                {"text": "A message.updated event was captured and stored for memory processing."},
                {"text": "A user message was received in a conversation with the assistant."},
                {"text": "The user recently visited Vienna and presented at a conference."},
            ]
        )
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0]["text"], "The user recently visited Vienna and presented at a conference.")


if __name__ == "__main__":
    unittest.main()
