import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "integrations" / "grok_titan_plugin" / "scripts" / "titan_grok_tools.py"
_tools_spec = importlib.util.spec_from_file_location("titan_grok_tools", CLI)
assert _tools_spec and _tools_spec.loader
_titan_grok_tools = importlib.util.module_from_spec(_tools_spec)
_tools_spec.loader.exec_module(_titan_grok_tools)


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
            expected_home = Path(tmp_dir) / ".titan" / "agents" / "grok"
            env = {
                "TITAN_HOME": str(Path(tmp_dir) / "shared-agent-home"),
                "TITAN_AGENT_NAME": "codex",
            }
            stdout = io.StringIO()
            with patch.dict(os.environ, env, clear=True), patch.object(
                _titan_grok_tools.Path, "home", return_value=Path(tmp_dir)
            ), patch("sys.stdout", stdout):
                os.environ.pop("GROK_TITAN_HOME", None)
                code = _titan_grok_tools.main(["--json", "doctor"])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["agent_name"], "grok")
        self.assertEqual(Path(payload["workspace"]), expected_home)

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
