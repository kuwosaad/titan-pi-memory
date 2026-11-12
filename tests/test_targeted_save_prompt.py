import json
import unittest
from unittest.mock import patch

from app.save_pipeline.extraction.extractor import extract_atomic_memories
from app.save_pipeline.extraction.prompts import build_extract_messages, build_extract_prompt


class CapturingAdapter:
    def __init__(self, response):
        self.response = response
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        return json.dumps({"memories": self.response})


class TargetedSavePromptTests(unittest.TestCase):
    def test_extractor_receives_coverage_and_existing_guards_with_mixed_exchange(self):
        user = "I changed the garden layout; my pottery class now meets about twice a month."
        assistant = "I suggest a weekly class instead."
        adapter = CapturingAdapter([
            {"text": "The user's pottery class now meets about twice a month.",
             "type": "fact", "stream": "rough", "source": "user",
             "speaker_focus": "user", "memory_kind": "user_fact"},
            {"text": "The assistant suggested a weekly pottery class.",
             "type": "plan", "stream": "rough", "source": "assistant",
             "speaker_focus": "assistant", "memory_kind": "task"},
        ])
        with patch("app.retrieval_pipeline.config.load_settings", return_value={}):
            memories = extract_atomic_memories(user, assistant, adapter)

        system = adapter.messages[0]["content"]
        self.assertIn("secondary clauses in mixed-topic messages", system)
        self.assertIn("amounts, ages, dates, counts, rates, and frequencies", system)
        self.assertIn("whether a value is exact or approximate", system)
        self.assertIn("identity or state updates", system)
        self.assertIn("do not turn an assistant statement into a user fact", system)
        self.assertIn("An assistant suggestion is not the user's decision", system)
        self.assertIn("Emit no paraphrase duplicates", system)
        self.assertIn("State-promotion guardrail", system)
        self.assertIn("usually 0 to 4 memories and never more than 10", system)
        exchange = json.loads(adapter.messages[-1]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(exchange, {"user": user, "assistant": assistant})
        self.assertEqual([(m["source"], m["speaker_focus"]) for m in memories],
                         [("user", "user"), ("assistant", "assistant")])
        self.assertIn("about twice a month", memories[0]["text"])

    def test_legacy_prompt_and_message_prompt_share_the_same_rules(self):
        messages = build_extract_messages("My workshop starts on 2031-04-09.", "Noted.")
        combined = build_extract_prompt("My workshop starts on 2031-04-09.", "Noted.")
        self.assertEqual(combined, "\n\n".join(message["content"] for message in messages))
        self.assertIn("Apply the same durability and privacy filters", messages[0]["content"])
        self.assertIn("Never store API keys", messages[0]["content"])
        self.assertIn("evidence_refs", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
