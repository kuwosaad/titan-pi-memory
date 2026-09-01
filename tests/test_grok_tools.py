import json
import os
import subprocess
import sys
import tempfile
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
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = {**os.environ, "GROK_TITAN_HOME": str(Path(tmp_dir) / "grok")}
            result = subprocess.run(
                [sys.executable, str(CLI), "tools"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        for token in ("query", "recent", "scene", "save", "doctor", "clusters", "cortex", "patterns", "graph"):
            self.assertIn(token, result.stdout)

    def test_doctor_json_uses_grok_namespace(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            expected_home = Path(tmp_dir) / "grok"
            env = {**os.environ, "GROK_TITAN_HOME": str(expected_home)}
            result = subprocess.run(
                [sys.executable, str(CLI), "--json", "doctor"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["agent_name"], "grok")
        self.assertEqual(payload["workspace"], str(expected_home))
        self.assertIn("memory_count", payload)

    def test_doctor_ignores_ambient_titan_home(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = {
                **os.environ,
                "HOME": tmp_dir,
                "TITAN_HOME": str(Path(tmp_dir) / "shared-agent-home"),
                "TITAN_AGENT_NAME": "codex",
            }
            env.pop("GROK_TITAN_HOME", None)
            result = subprocess.run(
                [sys.executable, str(CLI), "--json", "doctor"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["agent_name"], "grok")
        self.assertEqual(payload["workspace"], str(Path(tmp_dir) / ".titan" / "agents" / "grok"))

    def test_doctor_honors_explicit_grok_home_override(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            explicit_home = Path(tmp_dir) / "grok-home"
            env = {
                **os.environ,
                "TITAN_HOME": str(Path(tmp_dir) / "shared-agent-home"),
                "GROK_TITAN_HOME": str(explicit_home),
                "TITAN_AGENT_NAME": "grok",
            }
            result = subprocess.run(
                [sys.executable, str(CLI), "--json", "doctor"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["workspace"], str(explicit_home))


if __name__ == "__main__":
    unittest.main()
