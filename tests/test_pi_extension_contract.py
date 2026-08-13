import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PI_EXTENSION = ROOT_DIR / "tools" / "pi_extension" / "index.ts"


class PiExtensionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PI_EXTENSION.read_text(encoding="utf-8")

    def test_primary_messages_are_not_truncated_before_scene_ingest(self):
        self.assertNotIn("content: compactText(text, 2000)", self.source)
        self.assertGreaterEqual(self.source.count("content: text,"), 2)

    def test_memory_queries_request_scene_pointers_without_scene_bodies(self):
        self.assertIn('params.set("include_scenes", "false")', self.source)
        self.assertNotIn('"--- Scene context ---"', self.source)

    def test_tool_outputs_remain_compact(self):
        self.assertIn("compactText(JSON.stringify(input), 500)", self.source)
        self.assertIn("compactText(extractTextContent(event.content), 1000)", self.source)

    def test_query_surfaces_explain_abstention_consistently(self):
        self.assertEqual(self.source.count("No sufficiently relevant memories found."), 3)
        self.assertNotIn("No relevant memories found.", self.source)

    def test_existing_startup_check_publishes_keyed_memory_status(self):
        self.assertIn('ctx.ui.setStatus("titan-memory", "TITAN CHECKING")', self.source)
        self.assertIn('ctx.ui.setStatus("titan-memory", "TITAN READY")', self.source)
        self.assertIn('ctx.ui.setStatus("titan-memory", "TITAN UNCONFIGURED")', self.source)
        self.assertIn('ctx.ui.setStatus("titan-memory", "TITAN OFFLINE")', self.source)


if __name__ == "__main__":
    unittest.main()
