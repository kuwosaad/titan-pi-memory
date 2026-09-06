import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "integrations" / "claude_titan_plugin"


@unittest.skipUnless(shutil.which("claude"), "Claude Code CLI is not installed")
class ClaudePluginLiveContractTests(unittest.TestCase):
    def _run_claude(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["claude", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )

    def test_strict_plugin_validation_passes(self):
        result = self._run_claude("plugin", "validate", "--strict", str(PLUGIN_ROOT))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_live_plugin_loader_reports_no_errors(self):
        result = self._run_claude(
            "--plugin-dir",
            str(PLUGIN_ROOT),
            "plugin",
            "list",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plugins = json.loads(result.stdout)
        titan = next((plugin for plugin in plugins if plugin.get("id") == "titan-memory@inline"), None)
        self.assertIsNotNone(titan, plugins)
        self.assertFalse(titan.get("errors"), titan)


if __name__ == "__main__":
    unittest.main()
