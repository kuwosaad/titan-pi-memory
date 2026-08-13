import unittest

from app.save_pipeline.extraction.prompts import build_extract_prompt


class ExtractionPromptTests(unittest.TestCase):
    def test_prompt_includes_negative_prompting_and_dual_lenses(self):
        prompt = build_extract_prompt(
            "I prefer concise explanations and want retrieval to remember durable decisions.",
            "I can update the extraction prompt and avoid saving filler.",
        )

        self.assertIn("<user_side_lens>", prompt)
        self.assertIn("<agent_side_lens>", prompt)
        self.assertIn("<negative_prompting>", prompt)
        self.assertIn("what not to remember", prompt)
        self.assertIn("User: I prefer concise explanations", prompt)
        self.assertIn("Assistant: I can update the extraction prompt", prompt)


if __name__ == "__main__":
    unittest.main()
