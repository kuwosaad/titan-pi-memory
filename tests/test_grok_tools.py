import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "integrations" / "grok_titan_plugin" / "scripts" / "titan_grok_tools.py"


class GrokToolsCliTests(unittest.TestCase):
    def test_cli_file_exists(self):
        self.assertTrue(CLI.exists())
        wrapper = CLI.with_name("titan-grok")
        self.assertTrue(wrapper.exists())

    def test_tools_help_lists_pi_parity_commands(self):
        result = subprocess.run(
            [sys.executable, str(CLI), "tools"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for token in ("query", "recent", "scene", "save", "doctor", "clusters", "cortex", "patterns", "graph"):
            self.assertIn(token, result.stdout)

    def test_doctor_json_uses_grok_namespace(self):
        result = subprocess.run(
            [sys.executable, str(CLI), "--json", "doctor"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["agent_name"], "grok")
        self.assertIn("/.titan/agents/grok", payload["workspace"])
        self.assertIn("memory_count", payload)


if __name__ == "__main__":
    unittest.main()
