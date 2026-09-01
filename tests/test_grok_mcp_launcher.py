import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT_DIR / "integrations" / "grok_titan_plugin" / "scripts" / "titan_mcp_launcher.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("grok_titan_mcp_launcher", LAUNCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()


class ExecCaptured(Exception):
    def __init__(self, argv, env):
        self.argv = list(argv)
        self.env = dict(env)


class GrokMcpLauncherTests(unittest.TestCase):
    def test_runtime_env_ignores_ambient_adapter_home(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"HOME": tmp_dir}):
            env = launcher.build_runtime_env(
                "grok",
                {
                    "TITAN_AGENT_NAME": "codex",
                    "TITAN_HOME": str(Path(tmp_dir) / "codex-home"),
                    "TITAN_BASE_DIR": str(Path(tmp_dir) / "codex-home"),
                },
            )

        expected_home = str(Path(tmp_dir) / ".titan" / "agents" / "grok")
        self.assertEqual(env["TITAN_AGENT_NAME"], "grok")
        self.assertEqual(env["TITAN_HOME"], expected_home)
        self.assertEqual(env["TITAN_BASE_DIR"], expected_home)

    def test_runtime_env_honors_explicit_grok_home_override(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"HOME": tmp_dir}):
            explicit_home = str(Path(tmp_dir) / "grok-work")
            env = launcher.build_runtime_env(
                "grok",
                {
                    "TITAN_AGENT_NAME": "codex",
                    "TITAN_HOME": str(Path(tmp_dir) / "codex-home"),
                    "TITAN_BASE_DIR": str(Path(tmp_dir) / "codex-home"),
                    "GROK_TITAN_HOME": explicit_home,
                },
            )

        self.assertEqual(env["TITAN_AGENT_NAME"], "grok")
        self.assertEqual(env["TITAN_HOME"], explicit_home)
        self.assertEqual(env["TITAN_BASE_DIR"], explicit_home)

    def test_launcher_default_agent_ignores_ambient_agent(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"HOME": tmp_dir}):
            def exec_fn(_file, argv, env):
                raise ExecCaptured(argv, env)

            with self.assertRaises(ExecCaptured) as raised:
                launcher.run(
                    [],
                    base_env={
                        "HOME": tmp_dir,
                        "TITAN_AGENT_NAME": "codex",
                        "TITAN_HOME": str(Path(tmp_dir) / "codex-home"),
                    },
                    which_fn=lambda _name: None,
                    run_fn=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "ok", ""),
                    exec_fn=exec_fn,
                )

        self.assertEqual(raised.exception.argv[-2:], ["--agent", "grok"])
        expected_home = str(Path(tmp_dir) / ".titan" / "agents" / "grok")
        self.assertEqual(raised.exception.env["TITAN_AGENT_NAME"], "grok")
        self.assertEqual(raised.exception.env["TITAN_HOME"], expected_home)
        self.assertEqual(raised.exception.env["TITAN_BASE_DIR"], expected_home)


if __name__ == "__main__":
    unittest.main()
