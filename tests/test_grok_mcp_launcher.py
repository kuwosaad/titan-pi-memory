import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "integrations" / "grok_titan_plugin" / "scripts"


class GrokMcpLauncherTests(unittest.TestCase):
    def test_installed_launcher_runs_its_sibling_runtime_in_the_grok_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "plugin" / "scripts"
            plugin.mkdir(parents=True)
            for filename in ("titan_mcp_launcher.py", "titan_grok_env.py"):
                shutil.copy2(SCRIPTS / filename, plugin / filename)
            (plugin / "titan_plugin_runtime.py").write_text(
                "import json, os, sys\n"
                "print(json.dumps([sys.argv[1:], os.environ['TITAN_AGENT_NAME'], "
                "os.environ['TITAN_HOME'], os.environ['TITAN_SPOOL_DIR'], os.getenv('TITAN_MEMORY_DB_PATH')]))\n"
            )
            custom_home = Path(tmp) / "custom"
            for overrides, home in [({}, Path(tmp) / ".titan" / "agents" / "grok"),
                                    ({"GROK_TITAN_HOME": str(custom_home)}, custom_home)]:
                with self.subTest(overrides=overrides):
                    env = {"HOME": tmp, "TITAN_AGENT_NAME": "codex", "TITAN_HOME": "/unused",
                           "TITAN_SPOOL_DIR": "/unused", "TITAN_MEMORY_DB_PATH": "/unused.db", **overrides}
                    proc = subprocess.run([sys.executable, str(plugin / "titan_mcp_launcher.py")],
                                          env=env, check=True, capture_output=True, text=True)
                    self.assertEqual(json.loads(proc.stdout),
                                     [["mcp", "--agent", "grok"], "grok", str(home), str(home / "traces"), None])


if __name__ == "__main__":
    unittest.main()
