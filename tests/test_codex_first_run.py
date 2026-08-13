import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
FIRST_RUN_PATH = ROOT_DIR / "integrations" / "codex_titan_plugin" / "scripts" / "titan_first_run.py"


def _load_first_run_module():
    spec = importlib.util.spec_from_file_location("titan_first_run", FIRST_RUN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


first_run = _load_first_run_module()


class CodexFirstRunTests(unittest.TestCase):
    def test_text_onboarding_names_manual_codex_steps_without_bypassing_trust(self):
        stdout = io.StringIO()
        result = first_run.run(["--agent", "codex"], stdout=stdout)

        rendered = stdout.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Titan Memory is installed", rendered)
        self.assertIn("/hooks", rendered)
        self.assertIn("/mcp", rendered)
        self.assertIn("/plugins", rendered)
        self.assertIn("python3 ${PLUGIN_ROOT}/scripts/titan_codex_hook.py", rendered)
        self.assertIn("Passive capture only starts after you trust hooks", rendered)
        self.assertNotIn("codex hook trust", rendered.lower())

    def test_json_onboarding_is_machine_readable(self):
        stdout = io.StringIO()
        result = first_run.run(["--agent", "codex", "--json"], stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["agent"], "codex")
        self.assertTrue(payload["agent_home"].endswith("/.titan/agents/codex"))
        self.assertGreaterEqual(len(payload["first_prompts"]), 5)
        self.assertEqual(payload["manual_steps"][0]["command"], "/hooks")


if __name__ == "__main__":
    unittest.main()
